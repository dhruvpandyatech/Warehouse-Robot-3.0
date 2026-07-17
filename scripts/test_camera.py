"""
Camera verification script for Warehouse Robot.
Can be run on Windows (development PC) or Jetson Nano.
If a display GUI is available, shows a live preview.
If running headlessly (e.g., via SSH), captures a single frame and saves it as 'test_capture.png'.
"""

import sys
import os
import time

# Ensure workspace root is in path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cv2
from software.perception.camera import CameraStream

def main():
    print("==================================================")
    print("Warehouse Robot - Camera Test Utility")
    print("==================================================")
    
    use_csi = False
    if len(sys.argv) > 1 and sys.argv[1].lower() == "--csi":
        use_csi = True
        print("Requested Jetson CSI camera (nvarguscamerasrc).")
    else:
        print("Using standard webcam. Pass '--csi' to test Jetson CSI camera.")
        
    print("Attempting to open camera stream...")
    
    # Initialize Camera Stream
    # We do not use mock mode initially, so we can test the real camera.
    cam = CameraStream(camera_index=0, width=640, height=480, use_jetson_csi=use_csi, mock_mode=False)
    
    try:
        cam.open()
    except Exception as e:
        print(f"Error opening camera: {e}")
        print("Failed to initialize any hardware camera stream.")
        return

    # Check if stream opened or fell back to mock mode
    if cam.mock_mode:
        print("\n[WARNING] Camera fell back to MOCK mode (no physical camera detected).")
    else:
        print("\n[SUCCESS] Successfully initialized physical camera stream.")

    # Try to grab a frame
    print("Reading frame...")
    frame = cam.read()
    if frame is None:
        print("[ERROR] Failed to read frame from camera.")
        cam.release()
        return
        
    print(f"Captured frame successfully. Dimensions: {frame.shape[1]}x{frame.shape[0]}")

    # Check if GUI is supported
    has_display = False
    if sys.platform != "win32":
        # Check DISPLAY env var on Linux (Jetson)
        has_display = "DISPLAY" in os.environ
    else:
        has_display = True

    if has_display and not cam.mock_mode:
        print("Display detected. Opening preview window. Press 'q' to exit.")
        try:
            while True:
                frame = cam.read()
                if frame is None:
                    break
                cv2.imshow("Camera Verification Preview", frame)
                if cv2.waitKey(1) & 0xFF == ord('q'):
                    break
            cv2.destroyAllWindows()
        except Exception as e:
            print(f"Preview window error: {e}. Saving frame to disk instead.")
            cv2.imwrite("test_capture.png", frame)
            print("Saved captured frame to 'test_capture.png'.")
    else:
        # Headless mode or mock mode - save image
        output_path = "test_capture.png"
        cv2.imwrite(output_path, frame)
        print(f"Saved captured frame to '{os.path.abspath(output_path)}'.")

    cam.release()
    print("Camera released. Test completed.")

if __name__ == "__main__":
    main()
