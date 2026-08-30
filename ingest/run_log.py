"""Console-and-file logging for scripts that run both by hand and unattended.

Every pipeline entry point needs the same thing: a manual run should look
normal in the terminal, and a scheduled or detached run should still leave a
record. That was copied into each script; it lives here instead.

Detached jobs spawned by the web app have their stdio discarded entirely, so
the log file is the only account of what happened — which is what makes this
worth getting right rather than reaching for print().
"""

import sys
from datetime import datetime
from pathlib import Path

from config import DATA_DIR

LOG_DIR = DATA_DIR / "logs"
RETENTION_DAYS = 60


class Tee:
    """Write to two streams at once, flushing the file as it goes so a crash
    still leaves everything up to the failure on disk."""

    def __init__(self, stream, handle):
        self._stream = stream
        self._handle = handle

    def write(self, text: str) -> None:
        self._stream.write(text)
        self._handle.write(text)
        self._handle.flush()

    def flush(self) -> None:
        self._stream.flush()
        self._handle.flush()


def open_log(prefix: str):
    """Open today's log for `prefix`, pruning old ones for the same prefix."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    for old in sorted(LOG_DIR.glob(f"{prefix}_*.log"))[:-RETENTION_DAYS]:
        old.unlink(missing_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d")
    return (LOG_DIR / f"{prefix}_{stamp}.log").open("a", encoding="utf-8")


def tee_stdio(prefix: str):
    """Point stdout and stderr at both the console and today's log file.

    Returns the file handle so a caller can close it, though for a
    fire-and-forget script letting the process exit is enough.
    """
    handle = open_log(prefix)
    sys.stdout = Tee(sys.__stdout__, handle)
    sys.stderr = Tee(sys.__stderr__, handle)
    return handle
