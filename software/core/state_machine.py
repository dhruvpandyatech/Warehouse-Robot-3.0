from enum import Enum
from software.core.logger import RobotLogger


class RobotState(Enum):
    BOOTING = "BOOTING"
    IDLE = "IDLE"
    WAITING_FOR_TARGET = "WAITING_FOR_TARGET"
    PLAN_PATH = "PLAN_PATH"
    NAVIGATING = "NAVIGATING"
    SCANNING = "SCANNING"
    TARGET_FOUND = "TARGET_FOUND"
    RETURNING_HOME = "RETURNING_HOME"
    MISSION_COMPLETE = "MISSION_COMPLETE"
    ERROR = "ERROR"


class StateMachine:

    def __init__(self):
        self.logger = RobotLogger.get_logger()
        self.current_state = RobotState.BOOTING
        self._state_callbacks = []

        self.logger.info(f"State Machine initialized: {self.current_state.value}")

    def register_state_callback(self, cb):
        if cb not in self._state_callbacks:
            self._state_callbacks.append(cb)

    def transition(self, new_state):

        self.logger.info(
            f"{self.current_state.value} --> {new_state.value}"
        )

        self.current_state = new_state

        for cb in self._state_callbacks:
            try:
                cb(new_state.value)
            except Exception:
                pass

    def get_state(self):
        return self.current_state

