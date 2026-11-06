"""Run cyclic garbage collection between GUI events, never in feed workers."""

from __future__ import annotations

import gc

from PySide6.QtCore import QObject, QThread, QTimer, Slot


class GuiGarbageCollector(QObject):
    """Keep Qt wrapper finalizers on the thread that owns the GUI.

    Python's automatic cyclic collector runs in whichever thread crosses its
    allocation threshold. A transaction download can therefore collect Qt
    wrappers while the main thread is inside native painting code. Reference
    counting remains enabled; only cyclic collection moves to this timer.
    """

    def __init__(self, application, *, interval_ms: int = 5000) -> None:
        if QThread.currentThread() != application.thread():
            raise RuntimeError("GUI garbage collector must be created on the GUI thread")
        super().__init__(application)
        # Install before constructing windows or starting any background worker.
        gc.disable()
        self._timer = QTimer(self)
        self._timer.setInterval(interval_ms)
        self._timer.timeout.connect(self.collect)
        self._timer.start()
        application.aboutToQuit.connect(self.stop)

    @Slot()
    def collect(self) -> None:
        if QThread.currentThread() != self.thread():
            raise RuntimeError("Cyclic collection must run on the GUI thread")
        # A full collection also releases long-lived cycles on Python 3.14,
        # whose generation layout differs from older Python versions.
        gc.collect()

    @Slot()
    def stop(self) -> None:
        self._timer.stop()
        # Do not re-enable automatic collection during shutdown: outstanding
        # HTTP executor tasks can outlive the Qt poller and still allocate.
        # Interpreter shutdown performs its final collection on the main thread.
