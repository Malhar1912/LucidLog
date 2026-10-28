from __future__ import annotations

import logging
import sys
import traceback
from typing import TextIO

from .config import Config, default_config
from .diagnosis import build_diagnostic
from .models import Diagnostic
from .redaction import sanitize_text
from .render import render_diagnostic as _render_diagnostic
from .rules import DiagnosticRule, clear_rules, list_rules, register_rule

_previous_excepthook = None
_installed = False
_installed_config = default_config()


def diagnose(exc: BaseException, *, config: Config | None = None) -> Diagnostic:
    return build_diagnostic(exc, config=config)


def register_diagnostic_rule(rule: DiagnosticRule) -> None:
    register_rule(rule)


def clear_diagnostic_rules() -> None:
    clear_rules()


def get_registered_rules() -> tuple[DiagnosticRule, ...]:
    return list_rules()


def _handle_uncaught(exc_type, exc_value, exc_traceback) -> None:
    global _previous_excepthook
    try:
        diagnostic = build_diagnostic(exc_value, config=_installed_config)
        _render_diagnostic(
            diagnostic,
            full=_installed_config.include_full_traceback,
            stream=_installed_config.output_stream,
            color=_installed_config.color,
        )
    except BaseException:
        if _previous_excepthook is not None:
            _previous_excepthook(exc_type, exc_value, exc_traceback)
            return
        traceback.print_exception(exc_type, exc_value, exc_traceback)


def install(*, config: Config | None = None) -> None:
    global _previous_excepthook, _installed, _installed_config
    if _installed:
        return
    _installed_config = config or default_config()
    _previous_excepthook = sys.excepthook
    sys.excepthook = _handle_uncaught
    _installed = True


def uninstall() -> None:
    global _previous_excepthook, _installed, _installed_config
    if not _installed:
        return
    if sys.excepthook is _handle_uncaught:
        sys.excepthook = _previous_excepthook
    _previous_excepthook = None
    _installed = False
    _installed_config = default_config()


def exception(
    message: str | None = None,
    *,
    stream: TextIO | None = None,
    full: bool = False,
    config: Config | None = None,
) -> Diagnostic:
    exc = sys.exc_info()[1]
    if exc is None:
        raise RuntimeError("lucidlog.exception() must be called from within an active exception handler.")
    selected_config = config or default_config()
    diagnostic = build_diagnostic(exc, config=selected_config)
    _render_diagnostic(
        diagnostic,
        full=full,
        stream=stream if stream is not None else selected_config.output_stream,
        color=selected_config.color,
    )
    if message:
        target = stream if stream is not None else selected_config.output_stream
        if target is None:
            target = sys.stderr
        target.write(sanitize_text(message, selected_config) + "\n")
    return diagnostic


def log_exception(
    logger: logging.Logger,
    message: str,
    *,
    full: bool = False,
    config: Config | None = None,
) -> Diagnostic:
    exc = sys.exc_info()[1]
    if exc is None:
        raise RuntimeError("No active exception to log.")
    selected_config = config or default_config()
    diagnostic = build_diagnostic(exc, config=selected_config)
    level = {
        "critical": logging.CRITICAL,
        "error": logging.ERROR,
        "warning": logging.WARNING,
        "info": logging.INFO,
    }[diagnostic.severity]
    logger.log(
        level,
        sanitize_text(message, selected_config),
        extra={"lucidlog": diagnostic.to_dict(), "lucidlog_full": full},
    )
    return diagnostic


def render_exception(
    exc: BaseException,
    *,
    full: bool = False,
    stream: TextIO | None = None,
    color: bool | None = None,
    config: Config | None = None,
) -> str:
    selected_config = config or default_config()
    diagnostic = build_diagnostic(exc, config=selected_config)
    return _render_diagnostic(
        diagnostic,
        full=full,
        stream=stream if stream is not None else selected_config.output_stream,
        color=selected_config.color if color is None else color,
    )


def render_diagnostic(
    diag: Diagnostic,
    *,
    full: bool = False,
    stream: TextIO | None = None,
    color: bool | None = None,
) -> str:
    return _render_diagnostic(diag, full=full, stream=stream, color=color)
