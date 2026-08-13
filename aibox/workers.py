from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Slot


class WorkerSignals(QObject):
    finished = Signal(object)
    failed = Signal(str)


@dataclass(frozen=True)
class WorkerResult:
    value: Any


class Worker(QRunnable):
    def __init__(self, fn: Callable[[], Any]) -> None:
        super().__init__()
        self.setAutoDelete(False)
        self.fn = fn
        self.signals = WorkerSignals()

    @Slot()
    def run(self) -> None:
        try:
            val = self.fn()
        except Exception as e:
            try:
                self.signals.failed.emit(str(e))
            except RuntimeError:
                pass
            return
        try:
            self.signals.finished.emit(WorkerResult(val))
        except RuntimeError:
            pass


class Background:
    def __init__(self) -> None:
        self.pool = QThreadPool.globalInstance()
        self._active: set[Worker] = set()

    def run(self, fn: Callable[[], Any], on_ok: Callable[[Any], None], on_err: Callable[[str], None]) -> None:
        w = Worker(fn)
        self._active.add(w)

        def _ok(r: WorkerResult) -> None:
            self._active.discard(w)
            on_ok(r.value)

        def _err(msg: str) -> None:
            self._active.discard(w)
            on_err(msg)

        w.signals.finished.connect(_ok)
        w.signals.failed.connect(_err)
        self.pool.start(w)

    def wait_idle(self, timeout_ms: int = 3000) -> None:
        self.pool.waitForDone(timeout_ms)
        self._active.clear()
