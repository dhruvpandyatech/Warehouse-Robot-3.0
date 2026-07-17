"""Camera stream handling for Jetson CSI cameras using GStreamer, standard webcams, or IP streams."""
import logging
import time
from typing import Union
import cv2
import numpy as np

logger = logging.getLogger(__name__)


class CameraStream:
    """Wrapper around OpenCV VideoCapture supporting Jetson Argus pipeline, standard webcams, IP streams, and mock feeds."""

    def __init__(
        self,
        camera_index: Union[int, str] = 0,
        width: int = 1280,
        height: int = 720,
        use_jetson_csi: bool = False,
        mock_mode: bool = False,
    ):
        self.camera_index = camera_index
        self.width = width
        self.height = height
        self.use_jetson_csi = use_jetson_csi
        self.mock_mode = mock_mode
        self.cap = None
        self._mock_frame_count = 0

    def _gstreamer_pipeline(self):
        return (
            "nvarguscamerasrc sensor-id={} sensor-mode=2 ! "
            "video/x-raw(memory:NVMM), "
            "width=(int)1920, height=(int)1080, "
            "format=(string)NV12, framerate=(fraction)30/1 ! "
            "nvvidconv flip-method=0 ! "
            "video/x-raw, width=(int){}, height=(int){}, format=(string)BGRx ! "
            "videoconvert ! "
            "video/x-raw, format=(string)BGR ! "
            "appsink drop=true sync=false"
        ).format(
            self.camera_index,
            self.width,
            self.height,
        )

    def open(self) -> "CameraStream":
        if self.mock_mode:
            logger.info("Operating in MOCK camera mode.")
            return self

        if self.use_jetson_csi:
            pipeline = self._gstreamer_pipeline()
            logger.info("Opening CSI camera with pipeline:\n%s", pipeline)
            try:
                self.cap = cv2.VideoCapture(pipeline, cv2.CAP_GSTREAMER)
                if self.cap.isOpened():
                    logger.info(
                        "CSI Camera %s opened (%dx%d)",
                        self.camera_index,
                        self.width,
                        self.height,
                    )
                    return self
            except Exception as e:
                logger.warning(
                    "Failed to open Jetson CSI camera: %s. Trying standard webcam/IP stream...",
                    e,
                )

        # Fallback to standard webcam or IP stream
        logger.info("Opening standard webcam / IP stream (source: %s)", self.camera_index)
        try:
            # If camera_index is a digit string, convert it to integer
            cap_source = self.camera_index
            api_preference = cv2.CAP_ANY

            if isinstance(cap_source, str) and cap_source.isdigit():
                cap_source = int(cap_source)
            elif isinstance(cap_source, str) and (cap_source.startswith("http://") or cap_source.startswith("https://")):
                import platform
                if platform.system() == "Linux":
                    # L4T OpenCV lacks FFMPEG backend. Wrap in a GStreamer HTTP MJPEG reader pipeline.
                    cap_source = (
                        f"souphttpsrc location={self.camera_index} is-live=true ! "
                        f"multipartdemux ! "
                        f"image/jpeg ! "
                        f"jpegdec ! "
                        f"videoconvert ! "
                        f"video/x-raw, format=BGR ! "
                        f"appsink drop=true sync=false"
                    )
                    api_preference = cv2.CAP_GSTREAMER
                    logger.info("Linux/Jetson detected: Wrapping HTTP stream in GStreamer pipeline:\n%s", cap_source)

            self.cap = cv2.VideoCapture(cap_source, api_preference)
            if self.cap is not None and self.cap.isOpened():
                logger.info(
                    "Webcam/IP stream source '%s' opened (%dx%d)",
                    self.camera_index,
                    self.width,
                    self.height,
                )
                return self
        except Exception as e:
            logger.warning("Failed to open webcam/IP stream: %s", e)

        # Fallback to mock mode only if it was originally requested
        if self.mock_mode:
            logger.warning("No functional camera could be opened. Operating in MOCK mode.")
            return self
        else:
            raise RuntimeError(
                f"ERROR: Could not open camera source '{self.camera_index}' "
                f"and mock_mode is disabled. Please verify the phone stream is running "
                f"and reachable at this address."
            )

    def read(self):
        if self.mock_mode:
            # Generate a blank frame with text
            frame = np.zeros((self.height, self.width, 3), dtype=np.uint8)
            cv2.putText(
                frame,
                f"Mock Camera (Frame {self._mock_frame_count})",
                (50, self.height // 2),
                cv2.FONT_HERSHEY_SIMPLEX,
                1.0,
                (255, 255, 255),
                2,
            )
            self._mock_frame_count += 1
            time.sleep(0.03)  # Simulate ~30 FPS
            return frame

        if self.cap is None:
            raise RuntimeError("Camera not opened. Call open() first.")

        ret, frame = self.cap.read()

        if not ret:
            return None

        return frame

    def release(self):
        if self.cap is not None:
            self.cap.release()
            self.cap = None
            logger.info("Camera released")

    def __enter__(self):
        return self.open()

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.release()


