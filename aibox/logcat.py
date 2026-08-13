from __future__ import annotations

from PySide6.QtCore import QThread, Signal


class LogcatThread(QThread):
    line = Signal(str)
    stopped = Signal()
    failed = Signal(str)

    def __init__(self, popen) -> None:
        super().__init__()
        self.popen = popen

    def run(self) -> None:
        try:
            stdout = self.popen.stdout
            if stdout is None:
                self.failed.emit("logcat sem stdout")
                return
            while not self.isInterruptionRequested():
                ln = stdout.readline()
                if ln == "" or ln is None:
                    if self.popen.poll() is not None:
                        break
                    continue
                if isinstance(ln, bytes):
                    text = ln.decode("utf-8", "replace")
                else:
                    text = str(ln)
                self.line.emit(text.rstrip("\r\n"))
        except Exception as e:
            self.failed.emit(str(e))
        finally:
            try:
                self.popen.kill()
            except Exception:
                pass
            self.stopped.emit()

    def stop(self) -> None:
        self.requestInterruption()
        try:
            self.popen.terminate()
        except Exception:
            pass
