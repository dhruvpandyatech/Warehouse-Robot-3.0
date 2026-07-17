import os
import sys
import time
import math
import asyncio
import logging
import base64
import argparse
import threading
import cv2
import numpy as np
import websockets
import json

from software.core.logger import RobotLogger
from software.core.robot_controller import RobotController
from software.core.state_machine import StateMachine, RobotState
from software.core.mission_manager import MissionManager
from software.perception.config import VisionConfig
from software.perception.camera import CameraStream
from software.perception.qr_scanner import QRScanner

# Global task loop references
active_runner = None
runner_lock = threading.Lock()
websocket_client = None
websocket_lock = threading.Lock()

# Thread-safe queue or event loop caller for outbound websocket messages
loop_ref = None

def send_message_to_server(msg: dict):
    global websocket_client, loop_ref
    if websocket_client is None or loop_ref is None:
        return
    
    async def async_send():
        try:
            await websocket_client.send(json.dumps(msg))
        except Exception as e:
            pass

    asyncio.run_coroutine_threadsafe(async_send(), loop_ref)


class LocalMissionRunner:
    def __init__(self, target_package: str, mock_mode: bool):
        self.target_package = target_package
        self.mock_mode = mock_mode
        self._stop_event = threading.Event()
        self.thread = None
        self.robot = None
        self.state_machine = None
        self.mission = None

    def start(self):
        self.thread = threading.Thread(target=self._run)
        self.thread.daemon = True
        self.thread.start()

    def stop(self):
        self._stop_event.set()
        if self.thread:
            self.thread.join(timeout=3.0)

    def _run(self):
        logger = RobotLogger.get_logger()
        logger.info("Initializing local mission runner...")

        send_message_to_server({"type": "status", "data": "running"})
        send_message_to_server({"type": "target", "data": self.target_package})

        self.robot = RobotController()
        self.state_machine = StateMachine()
        self.mission = MissionManager(self.state_machine)

        # Register callbacks
        self.robot.register_telemetry_callback(
            lambda tele: send_message_to_server({"type": "telemetry", "data": tele})
        )
        self.state_machine.register_state_callback(
            lambda state: send_message_to_server({"type": "state", "data": state})
        )

        def log_cb(msg):
            send_message_to_server({"type": "log", "data": msg})
        
        RobotLogger.register_callback(log_cb)

        try:
            self._execute_mission()
        except Exception as e:
            logger.error(f"Mission loop failed: {e}")
            if self.state_machine:
                self.state_machine.transition(RobotState.ERROR)
        finally:
            RobotLogger.unregister_callback(log_cb)
            if self.robot:
                self.robot.stop()
            send_message_to_server({"type": "status", "data": "idle"})
            logger.info("Local mission runner finished.")

    def _navigate_to(self, target_x, target_y, speed=0.20, tolerance=0.15, dt=0.5):
        logger = RobotLogger.get_logger()
        logger.info(f"Navigating to ({target_x:.2f}, {target_y:.2f})...")
        
        while not self._stop_event.is_set():
            curr_x, curr_y = self.robot.get_position()
            dist = math.hypot(target_x - curr_x, target_y - curr_y)
            
            if dist <= tolerance:
                self.robot.stop()
                logger.info(f"Arrived at destination: ({curr_x:.2f}, {curr_y:.2f})")
                break
                
            heading = math.atan2(target_y - curr_y, target_x - curr_x)
            new_x = curr_x + speed * math.cos(heading) * dt
            new_y = curr_y + speed * math.sin(heading) * dt
            
            self.robot.move(speed, 0.0)
            self.robot.update_position(new_x, new_y, heading)
            
            logger.info(f"Position: ({new_x:.2f}, {new_y:.2f}), Heading: {heading:.2f} rad")
            
            for _ in range(int(dt / 0.05)):
                if self._stop_event.is_set():
                    break
                time.sleep(0.05)

    def _execute_mission(self):
        logger = RobotLogger.get_logger()

        self.state_machine.transition(RobotState.IDLE)
        time.sleep(0.5)

        self.mission.assign_target(self.target_package)
        time.sleep(0.5)

        if self._stop_event.is_set(): return

        self.state_machine.transition(RobotState.PLAN_PATH)
        logger.info("Planning search path...")
        for _ in range(20):
            if self._stop_event.is_set(): return
            time.sleep(0.05)

        self.state_machine.transition(RobotState.NAVIGATING)
        logger.info("Starting navigation search. Opening camera stream...")

        # Setup Camera configuration
        vision_config = VisionConfig()
        vision_config.mock_mode = self.mock_mode

        scanner = QRScanner()
        cam = CameraStream(
            camera_index=vision_config.camera_index,
            width=vision_config.frame_width,
            height=vision_config.frame_height,
            use_jetson_csi=vision_config.use_jetson_csi,
            mock_mode=vision_config.mock_mode
        )

        try:
            cam.open()
        except Exception as e:
            logger.error(f"Failed to open camera: {e}")
            self.state_machine.transition(RobotState.ERROR)
            self.mission.clear_target()
            return

        is_mock = vision_config.mock_mode or cam.mock_mode
        if is_mock:
            logger.warning("Operating in MOCK camera mode.")
        else:
            logger.info("Operating in REAL camera mode.")

        start_time = time.time()
        dt = 0.05
        speed = 0.20
        last_log_time = 0.0
        last_frame_sent_time = 0.0
        
        search_x, search_y = 2.0, 2.0
        found_target = False
        qr_location = None
        wrong_package_id = "176f57db-42c7-486e-8fce-4661f650ea57"

        try:
            while not self._stop_event.is_set():
                # 1. Update Position
                curr_x, curr_y = self.robot.get_position()
                dist_to_search = math.hypot(search_x - curr_x, search_y - curr_y)

                if dist_to_search <= 0.15:
                    if self.robot.linear_velocity != 0.0:
                        self.robot.stop()
                        logger.info("Reached end of search path. Pausing movement...")
                    new_x, new_y = curr_x, curr_y
                else:
                    heading = math.atan2(search_y - curr_y, search_x - curr_x)
                    new_x = curr_x + speed * math.cos(heading) * dt
                    new_y = curr_y + speed * math.sin(heading) * dt

                    self.robot.move(speed, 0.0)
                    self.robot.update_position(new_x, new_y, heading)

                # 2. Camera Frame Capture
                frame = cam.read()
                if frame is None:
                    logger.warning("Failed to grab camera frame, retrying...")
                    time.sleep(dt)
                    continue

                # 3. QR Detection logic
                detections = []
                if is_mock:
                    elapsed = time.time() - start_time
                    if 1.5 <= elapsed < 3.0:
                        detections = [(wrong_package_id, np.array([[100, 100], [200, 100], [200, 200], [100, 200]], dtype=float))]
                    elif elapsed >= 4.0:
                        detections = [(self.target_package, np.array([[100, 100], [200, 100], [200, 200], [100, 200]], dtype=float))]
                else:
                    detections = scanner.scan(frame)

                display_frame = scanner.draw_detections(frame.copy(), detections, match_id=self.target_package)

                # Upload frame to cloud at ~12 FPS to conserve internet upload bandwidth
                curr_time = time.time()
                if curr_time - last_frame_sent_time >= 0.08:
                    ret, encoded_img = cv2.imencode('.jpg', display_frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
                    if ret:
                        b64_frame = base64.b64encode(encoded_img.tobytes()).decode('utf-8')
                        send_message_to_server({"type": "frame", "data": b64_frame})
                    last_frame_sent_time = curr_time

                # 5. Process Detections
                if detections:
                    detected_qr, _ = detections[0]
                    logger.info(f"QR Code Detected: '{detected_qr}' at position ({new_x:.2f}, {new_y:.2f})")

                    if detected_qr.strip() == self.target_package.strip():
                        self.robot.stop()
                        found_target = True
                        qr_location = (new_x, new_y)
                        logger.info(f"[MATCH] Found target package '{self.target_package}' at location ({new_x:.2f}, {new_y:.2f})!")
                        break
                    else:
                        logger.info(f"[NO MATCH] Decoded package ID: '{detected_qr}'. Expected: '{self.target_package}'. Continuing search...")

                else:
                    curr_t = time.time()
                    if curr_t - last_log_time >= 1.0:
                        logger.info(f"Scanning... No QR code in frame (Position: {new_x:.2f}, {new_y:.2f})")
                        last_log_time = curr_t

                time.sleep(dt)

        finally:
            cam.release()

        if self._stop_event.is_set():
            logger.warning("Mission was cancelled by user request.")
            return

        if found_target:
            self.state_machine.transition(RobotState.TARGET_FOUND)
            logger.info(f"Target verification complete at location: ({qr_location[0]:.2f}, {qr_location[1]:.2f})")
            
            for _ in range(20):
                if self._stop_event.is_set(): return
                time.sleep(0.05)

            self.state_machine.transition(RobotState.RETURNING_HOME)
            self._navigate_to(target_x=0.0, target_y=0.0, speed=speed, dt=dt)
            
            if self._stop_event.is_set(): return
            time.sleep(0.5)

            self.state_machine.transition(RobotState.MISSION_COMPLETE)
            logger.info("Returned home successfully. Mission complete!")
            time.sleep(0.5)
        else:
            self.state_machine.transition(RobotState.ERROR)
            logger.error("Mission failed. Target package was not found.")

        self.mission.clear_target()


async def client_listener(server_url):
    global websocket_client, active_runner
    
    print(f"Connecting to Render Cloud Broker at {server_url}...")
    
    while True:
        try:
            async with websockets.connect(server_url) as ws:
                print("Connected to server successfully. Standby for commands...")
                with websocket_lock:
                    websocket_client = ws
                    
                async for raw_msg in ws:
                    msg = json.loads(raw_msg)
                    
                    # Check for start/stop commands from backend server
                    if "command" in msg:
                        cmd = msg["command"]
                        if cmd == "start":
                            target_qr = msg["target_qr"]
                            mock_mode = msg["mock_mode"]
                            print(f"Received START command. Target: {target_qr}, Mock: {mock_mode}")
                            
                            with runner_lock:
                                if active_runner is not None and active_runner.thread and active_runner.thread.is_alive():
                                    print("Warning: A mission is already running locally.")
                                    continue
                                active_runner = LocalMissionRunner(target_qr, mock_mode)
                                active_runner.start()
                                
                        elif cmd == "stop":
                            print("Received STOP command. Halting mission...")
                            with runner_lock:
                                if active_runner:
                                    active_runner.stop()
                                    active_runner = None
                                    
        except websockets.ConnectionClosed:
            print("Connection to cloud broker closed. Attempting reconnect...")
        except Exception as e:
            print(f"Client listener exception: {e}")
        finally:
            with websocket_lock:
                websocket_client = None
        
        await asyncio.sleep(2)


def main():
    global loop_ref
    parser = argparse.ArgumentParser(description="Warehouse Robot Jetson Tunnel Agent")
    parser.add_argument(
        "--server", 
        default="ws://localhost:8000/api/ws/robot", 
        help="Target WebSocket broker URL (e.g. wss://my-render-app.onrender.com/api/ws/robot)"
    )
    args = parser.parse_args()
    
    loop_ref = asyncio.get_event_loop()
    loop_ref.run_until_complete(client_listener(args.server))


if __name__ == "__main__":
    main()
