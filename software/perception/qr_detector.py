"""
Warehouse Robotics - Computer Vision Module
Detects a target package via QR code from a live USB camera feed.

find_package() is the entry point that the robot controller
will call. It returns a DetectionResult object.
"""

import logging
import time
from typing import Optional

import cv2
import numpy as np

from .camera import CameraStream
from .config import VisionConfig
from .package_matcher import PackageMatcher
from .qr_scanner import QRScanner

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)
logger = logging.getLogger(__name__)


class DetectionResult:

    def __init__(self, found, package_id=None, elapsed_time=None):
        self.found = found
        self.package_id = package_id
        self.elapsed_time = elapsed_time

    def __repr__(self):
        return (
            "DetectionResult(found={}, package_id={}, elapsed_time={})"
            .format(
                self.found,
                self.package_id,
                self.elapsed_time
            )
        )


def find_package(target_id: str, config: Optional[VisionConfig] = None) -> DetectionResult:
    """
    Opens the camera and scans frames until:
        1. Target QR is found
        2. User presses 'q'
        3. Timeout occurs
    """

    config = config or VisionConfig()

    scanner = QRScanner()
    matcher = PackageMatcher(target_id)

    start_time = time.time()

    with CameraStream(
        config.camera_index,
        config.frame_width,
        config.frame_height,
        use_jetson_csi=config.use_jetson_csi,
        mock_mode=config.mock_mode
    ) as cam:

        is_mock = config.mock_mode or cam.mock_mode

        while True:

            frame = cam.read()

            if frame is None:
                logger.warning("Frame grab failed, retrying...")
                continue

            if is_mock:
                # Simulate finding the target package QR code after 2.0 seconds
                if time.time() - start_time > 2.0:
                    detections = [(target_id, np.array([[100, 100], [200, 100], [200, 200], [100, 200]], dtype=float))]
                else:
                    detections = []
            else:
                detections = scanner.scan(frame)

            match = matcher.check(detections)

            if config.show_display:
                display_frame = scanner.draw_detections(
                    frame.copy(),
                    detections,
                    match_id=target_id
                )

                cv2.imshow("Warehouse QR Scanner", display_frame)

            if match is not None:

                elapsed = time.time() - start_time

                logger.info("Package found in %.2f seconds", elapsed)

                if config.show_display:
                    cv2.waitKey(500)
                    cv2.destroyAllWindows()

                return DetectionResult(
                    found=True,
                    package_id=match,
                    elapsed_time=elapsed
                )

            elapsed = time.time() - start_time

            if (
                config.detection_timeout is not None
                and elapsed > config.detection_timeout
            ):
                logger.info("Detection timed out.")

                cv2.destroyAllWindows()

                return DetectionResult(
                    found=False,
                    elapsed_time=elapsed
                )

            if config.show_display:

                key = cv2.waitKey(1) & 0xFF

                if key == ord("q"):

                    logger.info("Scan cancelled by user.")

                    cv2.destroyAllWindows()

                    return DetectionResult(
                        found=False,
                        elapsed_time=elapsed
                    )


if __name__ == "__main__":

    TARGET_PACKAGE_ID = "7998e51d-3be8-4a44-8662-165a01585b23"

    result = find_package(TARGET_PACKAGE_ID)

    print(result)
