from __future__ import annotations

import logging

from ..api import log_exception as _log_exception
from ..config import Config
from ..models import Diagnostic


def log_exception_with_diagnostic(
    logger: logging.Logger,
    message: str,
    *,
    full: bool = False,
    config: Config | None = None,
) -> Diagnostic:
    return _log_exception(logger, message, full=full, config=config)


def log_exception(
    logger: logging.Logger,
    message: str,
    *,
    full: bool = False,
    config: Config | None = None,
) -> Diagnostic:
    return log_exception_with_diagnostic(logger, message, full=full, config=config)
