import time
import math
import sys
import cv2
import numpy as np
from software.core.logger import RobotLogger
from software.core.robot_controller import RobotController
from software.core.state_machine import StateMachine, RobotState
from software.core.mission_manager import MissionManager
from software.perception.config import VisionConfig
from software.perception.camera import CameraStream
from software.perception.qr_scanner import QRScanner


def navigate_to(robot, target_x, target_y, speed=0.20, tolerance=0.15, dt=0.5):
    logger = RobotLogger.get_logger()
    logger.info(f"Navigating to target location ({target_x:.2f}, {target_y:.2f})...")
    
    while True:
        curr_x, curr_y = robot.get_position()
        dist = math.hypot(target_x - curr_x, target_y - curr_y)
        
        if dist <= tolerance:
            robot.stop()
            logger.info(f"Arrived at destination within tolerance: ({curr_x:.2f}, {curr_y:.2f})")
            break
            
        heading = math.atan2(target_y - curr_y, target_x - curr_x)
        new_x = curr_x + speed * math.cos(heading) * dt
        new_y = curr_y + speed * math.sin(heading) * dt
        
        robot.move(speed, 0.0)
        robot.update_position(new_x, new_y, heading)
        
        logger.info(f"Current position: ({new_x:.2f}, {new_y:.2f}), Heading: {heading:.2f} rad")
        time.sleep(dt)


def main():
    logger = RobotLogger.get_logger()

    logger.info("======================================")
    logger.info("Warehouse Robot Mission Initializing...")
    logger.info("======================================")

    # Initialize Core Modules
    robot = RobotController()
    state_machine = StateMachine()
    mission = MissionManager(state_machine)

    # Transition from BOOTING to IDLE
    state_machine.transition(RobotState.IDLE)
    time.sleep(0.5)

    # Determine target package QR dynamically
    if len(sys.argv) > 1:
        target_package = sys.argv[1]
        logger.info(f"Target package read from command line argument: {target_package}")
    else:
        print("\n--- Package Target Assignment ---")
        user_input = input("Enter target QR code (e.g. e6d237a5-6417-4bf6-b893-64506cfd3b1f) [Or press Enter for default]: ").strip()
        target_package = user_input if user_input else "e6d237a5-6417-4bf6-b893-64506cfd3b1f"
        print("---------------------------------\n")

    mission.assign_target(target_package)
    time.sleep(0.5)

    # Transition to Path Planning
    state_machine.transition(RobotState.PLAN_PATH)
    logger.info("Planning search path...")
    time.sleep(1.0)  # Simulate path planning time

    # Transition to Navigating (Moving while Scanning)
    state_machine.transition(RobotState.NAVIGATING)
    logger.info("Starting navigation search. Opening camera stream...")

    # Configure vision parameters (loaded from config.py)
    vision_config = VisionConfig()

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
        state_machine.transition(RobotState.ERROR)
        mission.clear_target()
        return

    # Check if camera initialization automatically fell back to mock mode
    is_mock = vision_config.mock_mode or cam.mock_mode
    if is_mock:
        logger.warning("Operating in MOCK camera mode because no physical camera was found.")
    else:
        logger.info("Operating in REAL camera mode. Show your QR code to the webcam!")

    start_time = time.time()
    dt = 0.05
    speed = 0.20
    last_log_time = 0.0
    
    # Coordinates of search path endpoint
    search_x, search_y = 2.0, 2.0
    found_target = False
    qr_location = None

    # Simulate encountering a wrong QR code before finding the correct one (in mock mode only)
    wrong_package_id = "176f57db-42c7-486e-8fce-4661f650ea57"

    try:
        while True:
            # 1. Update Position
            curr_x, curr_y = robot.get_position()
            dist_to_search = math.hypot(search_x - curr_x, search_y - curr_y)

            # If we reach the end of the search path, pause movement but keep scanning
            if dist_to_search <= 0.15:
                if robot.linear_velocity != 0.0:
                    robot.stop()
                    logger.info("Reached end of search path. Pausing movement and continuing to scan...")
                new_x, new_y = curr_x, curr_y
            else:
                heading = math.atan2(search_y - curr_y, search_x - curr_x)
                new_x = curr_x + speed * math.cos(heading) * dt
                new_y = curr_y + speed * math.sin(heading) * dt

                robot.move(speed, 0.0)
                robot.update_position(new_x, new_y, heading)

            # 2. Camera Capture
            frame = cam.read()
            if frame is None:
                logger.warning("Failed to grab camera frame, retrying...")
                time.sleep(dt)
                continue

            # 3. QR Detection logic
            detections = []
            if is_mock:
                elapsed = time.time() - start_time
                # At 1.5 - 3.0 seconds, simulate finding a WRONG package QR code
                if 1.5 <= elapsed < 3.0:
                    detections = [(wrong_package_id, np.array([[100, 100], [200, 100], [200, 200], [100, 200]], dtype=float))]
                # At 4.0+ seconds, simulate finding the CORRECT target package QR code
                elif elapsed >= 4.0:
                    detections = [(target_package, np.array([[100, 100], [200, 100], [200, 200], [100, 200]], dtype=float))]
            else:
                detections = scanner.scan(frame)

            # 4. Show preview window if enabled
            if vision_config.show_display:
                display_frame = scanner.draw_detections(frame.copy(), detections, match_id=target_package)
                cv2.imshow("Robot Camera View", display_frame)
                # cv2.waitKey(1) triggers window event processing, return value can detect key press 'q'
                if cv2.waitKey(1) & 0xFF == ord('q'):
                    logger.info("Scan cancelled by user press 'q'.")
                    break

            # 5. Process Detections
            if detections:
                detected_qr, _ = detections[0]
                logger.info(f"QR Code Detected: '{detected_qr}' at position ({new_x:.2f}, {new_y:.2f})")

                if detected_qr.strip() == target_package.strip():
                    robot.stop()
                    found_target = True
                    qr_location = (new_x, new_y)
                    logger.info(f"[MATCH] Found target package '{target_package}' at location ({new_x:.2f}, {new_y:.2f})!")
                    break
                else:
                    logger.info(f"[NO MATCH] Decoded package ID: '{detected_qr}'. Expected: '{target_package}'. Continuing search...")

            else:
                curr_t = time.time()
                if curr_t - last_log_time >= 1.0:
                    logger.info(f"Scanning... No QR code in frame (Position: {new_x:.2f}, {new_y:.2f})")
                    last_log_time = curr_t

            time.sleep(dt)

    finally:
        cam.release()
        if vision_config.show_display:
            cv2.destroyAllWindows()

    # Transition states and handle return journey
    if found_target:
        # Transition to TARGET_FOUND
        state_machine.transition(RobotState.TARGET_FOUND)
        logger.info(f"Target verification complete at location: ({qr_location[0]:.2f}, {qr_location[1]:.2f})")
        time.sleep(1.0)

        # Transition to RETURNING_HOME and return back to origin (0.0, 0.0)
        state_machine.transition(RobotState.RETURNING_HOME)
        navigate_to(robot, target_x=0.0, target_y=0.0, speed=speed, dt=dt)
        time.sleep(0.5)

        # Transition to MISSION_COMPLETE
        state_machine.transition(RobotState.MISSION_COMPLETE)
        logger.info("Returned home successfully. Mission complete!")
        time.sleep(0.5)
    else:
        state_machine.transition(RobotState.ERROR)
        logger.error("Mission failed. Target package was not found.")

    # Reset target and return to IDLE
    mission.clear_target()

    logger.info("======================================")
    logger.info("Warehouse Robot Mission Stopped.")
    logger.info("======================================")


if __name__ == "__main__":
    main()


