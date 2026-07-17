"""Compares decoded QR codes against a target package ID."""
import logging
from typing import List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)


class PackageMatcher:
    def __init__(self, target_id: str):
        self.target_id = target_id.strip()

    def check(self, detections: List[Tuple[str, np.ndarray]]) -> Optional[str]:
        """Returns the matched decoded string if the target is present, else None."""
        for text, _ in detections:
            if text.strip() == self.target_id:
                logger.info("Target package '%s' found!", self.target_id)
                return text
        return None


