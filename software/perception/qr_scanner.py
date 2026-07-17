"""QR code detection and decoding using pyzbar (ZBar) or OpenCV fallback."""
import logging
from typing import List, Optional, Tuple

import cv2
import numpy as np

logger = logging.getLogger(__name__)

# Try to import pyzbar for much faster and robust QR scanning
try:
    from pyzbar import pyzbar
    HAS_PYZBAR = True
    logger.info("pyzbar module successfully loaded. Using ZBar for QR detection.")
except ImportError:
    HAS_PYZBAR = False
    logger.warning("pyzbar module not found. Falling back to slow OpenCV QRCodeDetector. Run 'pip3 install pyzbar' for better efficiency.")


class QRScanner:
    """Detects and decodes a QR code using pyzbar or OpenCV 4.1.1."""

    def __init__(self):
        if not HAS_PYZBAR:
            self.detector = cv2.QRCodeDetector()

    def scan(self, frame: np.ndarray) -> List[Tuple[str, np.ndarray]]:
        """
        Returns a list containing (decoded_text, bbox_points)
        if a QR code is found.
        """
        results = []

        if HAS_PYZBAR:
            try:
                # pyzbar decodes directly from image
                decoded_objects = pyzbar.decode(frame)
                for obj in decoded_objects:
                    text = obj.data.decode("utf-8")
                    if text:
                        # Extract polygon corner points
                        pts = np.array([[pt.x, pt.y] for pt in obj.polygon], dtype=float)
                        if len(pts) > 0:
                            results.append((text, pts))
            except Exception as e:
                logger.warning("pyzbar scanning error: %s", e)
        else:
            try:
                text, points, _ = self.detector.detectAndDecode(frame)
                if text and points is not None:
                    results.append((text, points))
            except cv2.error as e:
                logger.warning("OpenCV QR detection error: %s", e)

        return results

    @staticmethod
    def draw_detections(
        frame: np.ndarray,
        detections: List[Tuple[str, np.ndarray]],
        match_id: Optional[str] = None,
    ) -> np.ndarray:

        for text, pts in detections:
            # Reshape points to a standard 2D array of (N, 2) to handle any OpenCV/ZBar formats
            try:
                pts_flat = np.array(pts).reshape(-1, 2)
            except Exception as e:
                logger.warning("Failed to reshape detection points: %s", e)
                continue

            pts_int = pts_flat.astype(int)

            color = (0, 255, 0) if text == match_id else (0, 0, 255)

            cv2.polylines(frame, [pts_int], True, color, 2)

            x = int(pts_flat[0][0])
            y = int(pts_flat[0][1])

            cv2.putText(
                frame,
                text,
                (x, y - 10),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.6,
                color,
                2,
            )

        return frame
