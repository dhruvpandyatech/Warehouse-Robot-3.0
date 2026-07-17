"""
Configuration for the Warehouse Robot Perception Module.
"""


class VisionConfig:

    def __init__(self):

        # Camera
        self.camera_index = 0
        self.frame_width = 640
        self.frame_height = 480
        self.fps = 30
        self.use_jetson_csi = False
        self.mock_mode = False

        # Display
        self.show_display = False

        # Detection
        self.detection_timeout = None
        self.confidence_threshold = 0.8

        # Robot Behaviour
        self.scan_interval = 0.1
        self.lost_target_timeout = 3.0

        # Debug
        self.debug = True
