"""Logging setup: console + optional file, no external dependencies."""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Optional

_FORMAT = "%(asctime)s | %(levelname)-7s | %(name)s | %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"


def get_logger(name: str) -> logging.Logger:
    """Return a logger namespaced under the app."""
    return logging.getLogger(f"app.{name}")


def setup_logging(level: str = "INFO", logfile: Optional[Path] = None) -> None:
    """Configure root logging once. Safe to call multiple times."""
    root = logging.getLogger("app")
    if root.handlers:  # already configured
        for h in root.handlers:
            if hasattr(h, "_level"):
                h.setLevel(getattr(logging, level.upper(), logging.INFO))
        root.setLevel(getattr(logging, level.upper(), logging.INFO))
        return
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    fmt = logging.Formatter(_FORMAT, _DATEFMT)

    console = logging.StreamHandler(sys.stderr)
    console.setFormatter(fmt)
    root.addHandler(console)

    if logfile is not None:
        logfile = Path(logfile)
        logfile.parent.mkdir(parents=True, exist_ok=True)
        fh = logging.FileHandler(logfile, encoding="utf-8")
        fh.setFormatter(fmt)
        root.addHandler(fh)

    # Quiet down noisy third-party loggers
    logging.getLogger("matplotlib").setLevel(logging.WARNING)
