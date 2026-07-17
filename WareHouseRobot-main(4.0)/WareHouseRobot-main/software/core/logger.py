import logging
import sys


class WebSocketLogHandler(logging.Handler):
    """Custom logging handler that forwards logs to web clients."""
    
    def __init__(self, callback):
        super().__init__()
        self.callback = callback

    def emit(self, record):
        try:
            msg = self.format(record)
            self.callback(msg)
        except Exception:
            self.handleError(record)


class RobotLogger:
    """Singleton logger used across the robot."""

    _logger = None
    _callbacks = []

    @classmethod
    def get_logger(cls):
        if cls._logger is None:
            logger = logging.getLogger("WarehouseRobot")

            logger.setLevel(logging.INFO)

            formatter = logging.Formatter(
                "[%(asctime)s] [%(levelname)s] %(message)s",
                datefmt="%H:%M:%S"
            )

            console = logging.StreamHandler(sys.stdout)
            console.setFormatter(formatter)

            logger.addHandler(console)

            # Add a handler that redirects logs to registered callbacks
            def broadcast_log(msg):
                for cb in cls._callbacks:
                    try:
                        cb(msg)
                    except Exception:
                        pass

            cb_handler = WebSocketLogHandler(broadcast_log)
            cb_handler.setFormatter(formatter)
            logger.addHandler(cb_handler)

            cls._logger = logger

        return cls._logger

    @classmethod
    def register_callback(cls, callback):
        if callback not in cls._callbacks:
            cls._callbacks.append(callback)

    @classmethod
    def unregister_callback(cls, callback):
        if callback in cls._callbacks:
            cls._callbacks.remove(callback)
