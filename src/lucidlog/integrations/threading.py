from __future__ import annotations

import threading

from ..api import render_exception
from ..config import Config, default_config

_previous_thread_hook = None
_hook_config = default_config()
_default_thread_hook = threading.__excepthook__


def install_threading_hook(*, config: Config | None = None) -> None:
    global _previous_thread_hook, _hook_config
    if threading.excepthook is _thread_hook:
        return
    _previous_thread_hook = threading.excepthook
    _hook_config = config or default_config()
    threading.excepthook = _thread_hook


def uninstall_threading_hook() -> None:
    global _previous_thread_hook
    if threading.excepthook is _thread_hook:
        if _previous_thread_hook is not None:
            threading.excepthook = _previous_thread_hook
    _previous_thread_hook = None


def _thread_hook(args: threading.ExceptHookArgs) -> None:
    if args.exc_value is None:
        if _previous_thread_hook is not None:
            _previous_thread_hook(args)
        else:
            _default_thread_hook(args)
        return
    try:
        render_exception(
            args.exc_value,
            full=_hook_config.include_full_traceback,
            stream=_hook_config.output_stream,
            config=_hook_config,
        )
    except BaseException:
        if _previous_thread_hook is not None:
            _previous_thread_hook(args)
            return
        _default_thread_hook(args)
        return
    if _previous_thread_hook is not None and _previous_thread_hook is not _default_thread_hook:
        _previous_thread_hook(args)
