"""
Robot Controller

Acts as the interface between high-level software
and the robot hardware.
"""

from software.core.logger import RobotLogger


class RobotController:

    def __init__(self):

        self.logger = RobotLogger.get_logger()

        self.position = (0.0, 0.0)
        self.heading = 0.0

        self.linear_velocity = 0.0
        self.angular_velocity = 0.0
        
        self._telemetry_callbacks = []

        self.logger.info("Robot Controller initialized.")

    def register_telemetry_callback(self, cb):
        if cb not in self._telemetry_callbacks:
            self._telemetry_callbacks.append(cb)

    def trigger_telemetry(self):
        data = {
            "x": self.position[0],
            "y": self.position[1],
            "heading": self.heading,
            "linear_velocity": self.linear_velocity,
            "angular_velocity": self.angular_velocity,
        }
        for cb in self._telemetry_callbacks:
            try:
                cb(data)
            except Exception:
                pass

    # ---------------------------
    # Robot Motion
    # ---------------------------

    def move(self, linear_speed, angular_speed):

        self.linear_velocity = linear_speed
        self.angular_velocity = angular_speed

        self.logger.info(
            f"Moving | Linear={linear_speed:.2f} m/s "
            f"| Angular={angular_speed:.2f} rad/s"
        )
        self.trigger_telemetry()

    def stop(self):

        self.linear_velocity = 0.0
        self.angular_velocity = 0.0

        self.logger.info("Robot stopped.")
        self.trigger_telemetry()

    # ---------------------------
    # Position
    # ---------------------------

    def update_position(self, x, y, heading):

        self.position = (x, y)
        self.heading = heading
        self.trigger_telemetry()

    def get_position(self):

        return self.position

    # ---------------------------
    # State
    # ----------------------
