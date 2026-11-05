from __future__ import annotations

import threading

from PySide6.QtCore import QThread, Signal


class Worker(QThread):
    result = Signal(object)
    error = Signal(str)
    progress = Signal(int, int, float)
    delta = Signal(str)

    def __init__(self, function, parent=None):
        super().__init__(parent)
        self.function = function
        self.cancelled = threading.Event()

    def run(self):
        try:
            result = self.function(self)
            if not self.cancelled.is_set():
                self.result.emit(result)
        except InterruptedError:
            pass
        except Exception as error:
            if not self.cancelled.is_set():
                self.error.emit(str(error))
