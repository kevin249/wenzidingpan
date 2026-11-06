"""Persist native crashes and uncaught exceptions without recording secrets."""

from __future__ import annotations

import faulthandler
import sys
import tempfile
import threading
import traceback
from datetime import datetime
from pathlib import Path

_stream = None
_path: Path | None = None


def install_crash_log(path: Path | None = None) -> Path | None:
    """Install once; keep the fault-handler file open for the process lifetime."""
    global _stream, _path
    if _stream is not None:
        return _path
    destinations = [path] if path is not None else [
        Path("logs/crash.log"), Path(tempfile.gettempdir()) / "stockwidget-crash.log",
    ]
    for destination in destinations:
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            stream = destination.open("a", encoding="utf-8", buffering=1)
        except OSError:
            continue
        try:
            stream.write(f"\n=== {datetime.now().isoformat(timespec='seconds')} "
                         f"Python {sys.version.split()[0]} {sys.platform} ===\n")
            stream.flush()
            faulthandler.enable(file=stream, all_threads=True)
        except (OSError, RuntimeError, ValueError):
            stream.close()
            continue
        _stream, _path = stream, destination.resolve()
        break
    if _stream is None:
        return None

    def record(exc_type, exc_value, exc_traceback):
        try:
            traceback.print_exception(exc_type, exc_value, exc_traceback, file=_stream)
            _stream.flush()
        except (OSError, ValueError):
            pass

    previous = sys.excepthook

    def exception_hook(exc_type, exc_value, exc_traceback):
        record(exc_type, exc_value, exc_traceback)
        previous(exc_type, exc_value, exc_traceback)

    previous_thread = threading.excepthook

    def thread_exception_hook(args):
        record(args.exc_type, args.exc_value, args.exc_traceback)
        previous_thread(args)

    sys.excepthook = exception_hook
    threading.excepthook = thread_exception_hook
    return _path
