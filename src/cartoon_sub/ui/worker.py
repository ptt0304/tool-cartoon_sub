from threading import Event
from PySide6.QtCore import QThread, Signal
from cartoon_sub.media.process import CancelledError
import logging

class Worker(QThread):
    result = Signal(object)
    error = Signal(str)
    progress = Signal(str)

    def __init__(self, operation, parent=None):
        super().__init__(parent)
        self.operation = operation
        self.cancel_event = Event()

    def run(self):
        try:
            self.result.emit(self.operation(cancel=self.cancel_event, progress=self.progress.emit))
        except CancelledError:
            self.progress.emit("Job cancelled")
        except Exception as exc:
            logging.getLogger(__name__).exception("Job failed")
            self.error.emit(str(exc))

    def cancel(self):
        self.cancel_event.set()
