from software.core.logger import RobotLogger
from software.core.state_machine import RobotState


class MissionManager:

    def __init__(self, state_machine):

        self.logger = RobotLogger.get_logger()

        self.state_machine = state_machine

        self.target_qr = None

        self.logger.info("Mission Manager initialized.")

    def assign_target(self, qr_code):

        self.target_qr = qr_code

        self.logger.info(
            f"Mission Assigned : {qr_code}"
        )

        self.state_machine.transition(
            RobotState.WAITING_FOR_TARGET
        )

    def get_target(self):

        return self.target_qr

    def clear_target(self):

        self.logger.info("Mission Cleared")

        self.target_qr = None

        self.state_machine.transition(
            RobotState.IDLE
        )
