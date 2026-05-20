"""CLI logging bootstrap."""

from __future__ import annotations

import logging
import os


_DEFAULT_LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s:%(lineno)d %(message)s"


def configure_logging() -> None:
    level_name = os.environ.get("MAGIPI_LOG_LEVEL", "WARNING").upper()
    level = getattr(logging, level_name, logging.WARNING)
    logging.basicConfig(level=level, format=_DEFAULT_LOG_FORMAT)
    logging.captureWarnings(True)


__all__ = ["configure_logging"]
