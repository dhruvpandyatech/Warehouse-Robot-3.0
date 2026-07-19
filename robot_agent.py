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


def get_grid_location(x: float, y: float):
    # Map coordinate space (0.0 to 2.0 meters) to grid rows (1-2) and racks (1-5)
    # Centers: row 1 at y=0.5m, row 2 at y=1.5m; racks spaced every 0.4m from 0.2m
    rack = min(5, max(1, int(round((x - 0.2) / 0.4)) + 1))
    row = min(2, max(1, int(round((y - 0.5) / 1.0)) + 1))
    return row, rack


def get_slot_coordinates(row: int, rack: int):
    # Map row (1-2) and rack (1-5) back to physical target coordinates
    x = 0.2 + (rack - 1) * 0.4
    y = 0.5 + (row - 1) * 1.0
    return x, y


def solve_tsp(start_x: float, start_y: float, remaining_slots: list):
    # Solves the TSP for remaining slots using full permutation search
    # since number of slots is small (max 10), ensuring globally optimal shortest path.
    if not remaining_slots:
        return []
    
    import itertools
    best_path = None
    min_dist = float('inf')
    
    for perm in itertools.permutations(remaining_slots):
        dist = 0.0
        curr_x, curr_y = start_x, start_y
        for row, rack in perm:
            tx, ty = get_slot_coordinates(row, rack)
            dist += abs(tx - curr_x) + abs(ty - curr_y)
            curr_x, curr_y = tx, ty
            
        if dist < min_dist:
            min_dist = dist
            best_path = perm
            
    return list(best_path)


class LocalMissionRunner:
    def __init__(self, target_package: str, mock_mode: bool, expected_slot: dict = None, is_audit: bool = False):
        self.target_package = target_package
        self.mock_mode = mock_mode
        self.expected_slot = expected_slot
        self.is_audit = is_audit
        self._stop_event = threading.Event()
        self.thread = None
        self.robot = None
        self.state_machine = None
        self.mission = None

    def _capture_and_send_frame(self, cam, scanner, detections=[]):
        frame = cam.read()
        if frame is None:
            return None
            
        display_frame = scanner.draw_detections(frame.copy(), detections, match_id=self.target_package or "")
        
        curr_time = time.time()
        if not hasattr(self, "_last_frame_sent_time"):
            self._last_frame_sent_time = 0.0
            
        if curr_time - self._last_frame_sent_time >= 0.12:
            try:
                small_frame = cv2.resize(display_frame, (320, 240))
                ret, encoded_img = cv2.imencode('.jpg', small_frame, [cv2.IMWRITE_JPEG_QUALITY, 50])
                if ret:
                    b64_frame = base64.b64encode(encoded_img.tobytes()).decode('utf-8')
                    send_message_to_server({"type": "frame", "data": b64_frame})
                self._last_frame_sent_time = curr_time
            except Exception:
                pass
                
        return frame

    def _scan_slot_robust(self, cam, scanner, is_mock, row, rack, num_frames=5):
        logger = RobotLogger.get_logger()
        logger.info(f"Performing precision scan at Row {row}, Rack {rack}...")
        
        # Stop robot to prevent motion blur
        self.robot.stop()
        time.sleep(0.15)
        
        reads = []
        for _ in range(num_frames):
            if self._stop_event.is_set():
                return None
                
            frame = cam.read()
            if frame is None:
                time.sleep(0.05)
                continue
                
            detections = []
            if is_mock:
                if row == 1 and rack == 3:
                    detections = [(self.target_package, None)]
                elif row == 1 and rack == 1:
                    detections = [("176f57db-42c7-486e-8fce-4661f650ea57", None)]
                else:
                    detections = []
            else:
                detections = scanner.scan(frame)
                
            if detections:
                detected_qr, _ = detections[0]
                reads.append(detected_qr.strip())
                
            # Stream this scanned frame with outlines immediately to dashboard
            display_frame = scanner.draw_detections(frame.copy(), detections, match_id=self.target_package or "")
            try:
                small_frame = cv2.resize(display_frame, (320, 240))
                ret, encoded_img = cv2.imencode('.jpg', small_frame, [cv2.IMWRITE_JPEG_QUALITY, 50])
                if ret:
                    b64_frame = base64.b64encode(encoded_img.tobytes()).decode('utf-8')
                    send_message_to_server({"type": "frame", "data": b64_frame})
            except Exception:
                pass
                
            time.sleep(0.05)
            
        if not reads:
            logger.warning(f"No QR code detected at Row {row}, Rack {rack}.")
            return None
            
        from collections import Counter
        counts = Counter(reads)
        most_common_qr, freq = counts.most_common(1)[0]
        
        if freq >= 2:
            logger.info(f"Verified QR code at Row {row}, Rack {rack}: '{most_common_qr}' (confidence: {freq}/{num_frames})")
            return most_common_qr
        else:
            logger.warning(f"Inconsistent QR reads at Row {row}, Rack {rack}: {counts}. Ignoring read.")
            return None

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

    def _navigate_to(self, target_x, target_y, cam, scanner, speed=0.20, tolerance=0.15, dt=0.5):
        logger = RobotLogger.get_logger()
        logger.info(f"Navigating orthogonally to ({target_x:.2f}, {target_y:.2f})...")
        
        start_x, start_y = self.robot.get_position()
        
        waypoints = []
        if abs(target_x - start_x) > tolerance:
            waypoints.append((target_x, start_y))
        if abs(target_y - start_y) > tolerance:
            waypoints.append((target_x, target_y))
            
        if not waypoints:
            waypoints = [(target_x, target_y)]
            
        for wp_x, wp_y in waypoints:
            logger.info(f"Navigating to waypoint ({wp_x:.2f}, {wp_y:.2f})...")
            while not self._stop_event.is_set():
                curr_x, curr_y = self.robot.get_position()
                dist = math.hypot(wp_x - curr_x, wp_y - curr_y)
                
                if dist <= tolerance:
                    self.robot.stop()
                    logger.info(f"Reached waypoint: ({curr_x:.2f}, {curr_y:.2f})")
                    break
                    
                heading = math.atan2(wp_y - curr_y, wp_x - curr_x)
                new_x = curr_x + speed * math.cos(heading) * dt
                new_y = curr_y + speed * math.sin(heading) * dt
                
                self.robot.move(speed, 0.0)
                self.robot.update_position(new_x, new_y, heading)
                
                logger.info(f"Position: ({new_x:.2f}, {new_y:.2f}), Heading: {heading:.2f} rad")
                
                start_drive_wait = time.time()
                while time.time() - start_drive_wait < dt:
                    if self._stop_event.is_set():
                        break
                    self._capture_and_send_frame(cam, scanner)
                    time.sleep(0.05)

    def _execute_mission(self):
        logger = RobotLogger.get_logger()

        self.state_machine.transition(RobotState.IDLE)
        time.sleep(0.5)

        # 1. Start camera stream
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
            return

        is_mock = vision_config.mock_mode or cam.mock_mode
        if is_mock:
            logger.warning("Operating in MOCK camera mode.")
        else:
            logger.info("Operating in REAL camera mode.")

        # Keep a local cache of slot contents read during this session
        session_cache = {}

        # Set up parameters
        speed = 0.20
        dt = 0.5
        found_target = False
        final_slot = None

        try:
            # Check if this is an audit mission
            if self.is_audit:
                self.state_machine.transition(RobotState.NAVIGATING)
                logger.info("Starting Full Inventory Audit Sweep...")
                
                # Full 10-slot snake sweep path
                # Row 1: slots 1 to 5; Row 2: slots 10 down to 6
                audit_slots = [(1, 1), (1, 2), (1, 3), (1, 4), (1, 5),
                               (2, 5), (2, 4), (2, 3), (2, 2), (2, 1)]
                
                for row, rack in audit_slots:
                    if self._stop_event.is_set():
                        return
                    
                    # Navigate to slot
                    target_x, target_y = get_slot_coordinates(row, rack)
                    self._navigate_to(target_x, target_y, cam, scanner, speed=speed, dt=dt)
                    
                    # Perform precision scan
                    scanned_id = self._scan_slot_robust(cam, scanner, is_mock, row, rack)
                    
                    # Send scan update to server
                    send_message_to_server({
                        "type": "slot_scanned",
                        "data": {
                            "row": row,
                            "rack": rack,
                            "package_id": scanned_id
                        }
                    })
                    time.sleep(0.5)
                
                self.state_machine.transition(RobotState.RETURNING_HOME)
                self._navigate_to(0.0, 0.0, cam, scanner, speed=speed, dt=dt)
                self.state_machine.transition(RobotState.MISSION_COMPLETE)
                logger.info("Audit sweep completed. Returned home successfully.")
                return

            # Otherwise, this is a target retrieval mission
            self.mission.assign_target(self.target_package)
            time.sleep(0.5)

            # Determine where we expect the target to be (Tier 1)
            target_row = None
            target_rack = None
            
            if self.expected_slot:
                target_row = self.expected_slot["row"]
                target_rack = self.expected_slot["rack"]
                logger.info(f"Target expected at Row {target_row}, Rack {target_rack} (from database).")
            else:
                logger.info("Target location unknown. Proceeding directly to full sweep search.")

            # Run search tiers
            # Tier 1: Direct check if target_row/rack is known
            if target_row is not None and target_rack is not None:
                self.state_machine.transition(RobotState.NAVIGATING)
                tx, ty = get_slot_coordinates(target_row, target_rack)
                self._navigate_to(tx, ty, cam, scanner, speed=speed, dt=dt)
                
                # Precision Scan
                scanned_id = self._scan_slot_robust(cam, scanner, is_mock, target_row, target_rack)
                session_cache[(target_row, target_rack)] = scanned_id
                
                # Send update to server
                send_message_to_server({
                    "type": "slot_scanned",
                    "data": {"row": target_row, "rack": target_rack, "package_id": scanned_id}
                })

                if scanned_id == self.target_package:
                    found_target = True
                    final_slot = (target_row, target_rack)
                else:
                    logger.warning(f"Target not at expected slot. Expected: '{self.target_package}'. Found: '{scanned_id}'.")
                    
                    # Tier 2: Nearest-Neighbor Local Check
                    logger.info("Initiating Tier 2: Checking adjacent slots...")
                    neighbors = []
                    if target_rack > 1:
                        neighbors.append((target_row, target_rack - 1))
                    if target_rack < 5:
                        neighbors.append((target_row, target_rack + 1))
                    
                    # We check the closest neighbor first. Since start is target_rack, we sort by distance
                    for nrow, nrack in neighbors:
                        if self._stop_event.is_set():
                            return
                        logger.info(f"Checking neighbor: Row {nrow}, Rack {nrack}...")
                        ntx, nty = get_slot_coordinates(nrow, nrack)
                        self._navigate_to(ntx, nty, cam, scanner, speed=speed, dt=dt)
                        
                        n_scanned_id = self._scan_slot_robust(cam, scanner, is_mock, nrow, nrack)
                        session_cache[(nrow, nrack)] = n_scanned_id
                        
                        send_message_to_server({
                            "type": "slot_scanned",
                            "data": {"row": nrow, "rack": nrack, "package_id": n_scanned_id}
                        })
                        
                        if n_scanned_id == self.target_package:
                            found_target = True
                            final_slot = (nrow, nrack)
                            break
                        time.sleep(0.5)

            # Tier 3: Global Sweep (if target is still not found or expected slot was unknown)
            if not found_target:
                logger.info("Target still not found. Initiating Tier 3: Global Shortest Path Sweep...")
                self.state_machine.transition(RobotState.NAVIGATING)
                
                # Find all remaining unscanned slots
                all_slots = [(r, c) for r in [1, 2] for c in range(1, 6)]
                unscanned_slots = [s for s in all_slots if s not in session_cache]
                
                # Get current robot position
                curr_x, curr_y = self.robot.get_position()
                
                # Solve TSP for remaining slots
                optimal_path = solve_tsp(curr_x, curr_y, unscanned_slots)
                logger.info(f"Optimal remaining search path: {optimal_path}")
                
                for row, rack in optimal_path:
                    if self._stop_event.is_set():
                        return
                    
                    tx, ty = get_slot_coordinates(row, rack)
                    self._navigate_to(tx, ty, cam, scanner, speed=speed, dt=dt)
                    
                    scanned_id = self._scan_slot_robust(cam, scanner, is_mock, row, rack)
                    session_cache[(row, rack)] = scanned_id
                    
                    send_message_to_server({
                        "type": "slot_scanned",
                        "data": {"row": row, "rack": rack, "package_id": scanned_id}
                    })
                    
                    if scanned_id == self.target_package:
                        found_target = True
                        final_slot = (row, rack)
                        break
                    time.sleep(0.5)

            # Return home leg
            if found_target:
                self.state_machine.transition(RobotState.TARGET_FOUND)
                logger.info(f"[MATCH] Target verification complete: Target found at Row {final_slot[0]}, Rack {final_slot[1]}!")
                
                # Send verification pickup message to server to clear slot
                send_message_to_server({
                    "type": "target_verified",
                    "data": {
                        "package": self.target_package,
                        "row": final_slot[0],
                        "rack": final_slot[1]
                    }
                })
                
                # Wait 1 second to simulate picking up
                time.sleep(1.0)
                
                self.state_machine.transition(RobotState.RETURNING_HOME)
                self._navigate_to(0.0, 0.0, cam, scanner, speed=speed, dt=dt)
                
                self.state_machine.transition(RobotState.MISSION_COMPLETE)
                logger.info("Returned home successfully. Mission complete!")
            else:
                self.state_machine.transition(RobotState.ERROR)
                logger.error("Mission failed. Target package was not found in the warehouse.")
                
                self.state_machine.transition(RobotState.RETURNING_HOME)
                self._navigate_to(0.0, 0.0, cam, scanner, speed=speed, dt=dt)

        finally:
            cam.release()
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
                            expected_slot = msg.get("expected_slot")
                            print(f"Received START command. Target: {target_qr}, Mock: {mock_mode}, Expected Slot: {expected_slot}")
                            
                            with runner_lock:
                                if active_runner is not None and active_runner.thread and active_runner.thread.is_alive():
                                    print("Warning: A mission is already running locally.")
                                    continue
                                active_runner = LocalMissionRunner(target_qr, mock_mode, expected_slot=expected_slot)
                                active_runner.start()
                                
                        elif cmd == "audit":
                            mock_mode = msg.get("mock_mode", True)
                            print(f"Received AUDIT command. Mock: {mock_mode}")
                            
                            with runner_lock:
                                if active_runner is not None and active_runner.thread and active_runner.thread.is_alive():
                                    print("Warning: A mission is already running locally.")
                                    continue
                                active_runner = LocalMissionRunner(None, mock_mode, is_audit=True)
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
