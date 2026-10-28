from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any
from weakref import WeakKeyDictionary

from ..api import render_exception
from ..config import Config, default_config

_Handler = Callable[[asyncio.AbstractEventLoop, dict[str, Any]], None]
_installed_handlers: WeakKeyDictionary[asyncio.AbstractEventLoop, tuple[_Handler | None, _Handler]] = (
    WeakKeyDictionary()
)


def install_asyncio_exception_handler(
    loop: asyncio.AbstractEventLoop | None = None,
    *,
    config: Config | None = None,
    chain_previous: bool = False,
) -> None:
    loop = loop or asyncio.get_running_loop()
    if loop in _installed_handlers:
        return
    previous = loop.get_exception_handler()
    selected_config = config or default_config()

    def handler(current_loop: asyncio.AbstractEventLoop, context: dict[str, Any]) -> None:
        exception = context.get("exception")
        if not isinstance(exception, BaseException):
            _delegate(current_loop, context, previous)
            return
        if previous is not None and not chain_previous:
            previous(current_loop, context)
            return
        try:
            render_exception(
                exception,
                full=selected_config.include_full_traceback,
                stream=selected_config.output_stream,
                config=selected_config,
            )
        except BaseException:
            _delegate(current_loop, context, previous)
            return
        if previous is not None:
            previous(current_loop, context)

    _installed_handlers[loop] = (previous, handler)
    loop.set_exception_handler(handler)


def _delegate(
    loop: asyncio.AbstractEventLoop,
    context: dict[str, Any],
    previous: _Handler | None,
) -> None:
    if previous is not None:
        previous(loop, context)
    else:
        loop.default_exception_handler(context)


def uninstall_asyncio_exception_handler(loop: asyncio.AbstractEventLoop | None = None) -> None:
    loop = loop or asyncio.get_running_loop()
    installed = _installed_handlers.pop(loop, None)
    if installed is None:
        return
    previous, handler = installed
    if loop.get_exception_handler() is handler:
        loop.set_exception_handler(previous)
