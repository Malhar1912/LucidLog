from __future__ import annotations

import os
import sys
from typing import TextIO

from .models import Diagnostic, DiagnosticCause

_RESET = "\x1b[0m"
_SEVERITY_COLORS = {
    "critical": "\x1b[1;31m",
    "error": "\x1b[31m",
    "warning": "\x1b[33m",
    "info": "\x1b[36m",
}
_EXCEPTION_COLORS = {
    "KeyError": "\x1b[34m",
    "IndexError": "\x1b[34m",
    "AttributeError": "\x1b[35m",
    "TypeError": "\x1b[35m",
    "ZeroDivisionError": "\x1b[35m",
    "ValueError": "\x1b[33m",
    "AssertionError": "\x1b[33m",
    "OverflowError": "\x1b[33m",
    "StopIteration": "\x1b[33m",
    "ImportError": "\x1b[32m",
    "ModuleNotFoundError": "\x1b[32m",
    "FileNotFoundError": "\x1b[36m",
    "PermissionError": "\x1b[36m",
    "OSError": "\x1b[36m",
    "IsADirectoryError": "\x1b[36m",
    "NotADirectoryError": "\x1b[36m",
    "ConnectionError": "\x1b[36m",
    "TimeoutError": "\x1b[36m",
    "EOFError": "\x1b[36m",
    "MemoryError": "\x1b[1;31m",
    "SystemError": "\x1b[1;31m",
    "RecursionError": "\x1b[1;31m",
    "RuntimeError": "\x1b[31m",
    "NotImplementedError": "\x1b[31m",
    "SyntaxError": "\x1b[1;33m",
    "NameError": "\x1b[34m",
    "UnboundLocalError": "\x1b[34m",
    "Warning": "\x1b[33m",
    "ExceptionGroup": "\x1b[1;35m",
    "BaseExceptionGroup": "\x1b[1;35m",
}


def _display(value: str, *, multiline: bool = False) -> str:
    output: list[str] = []
    for character in value:
        if character == "\n" and multiline:
            output.append(character)
        elif character == "\n":
            output.append("\\n")
        elif character == "\r":
            output.append("\\r")
        elif character == "\t":
            output.append("\\t")
        elif ord(character) < 32 or 127 <= ord(character) < 160:
            output.append(f"\\x{ord(character):02x}")
        else:
            output.append(character)
    return "".join(output)


def _color_enabled(stream: TextIO, requested: bool | None) -> bool:
    if requested is not None:
        return requested
    if "NO_COLOR" in os.environ or os.environ.get("TERM", "").lower() == "dumb":
        return False
    return bool(getattr(stream, "isatty", lambda: False)())


def _paint(text: str, color: str | None, enabled: bool) -> str:
    if not enabled or color is None:
        return text
    return f"{color}{text}{_RESET}"


def _render_cause(cause: DiagnosticCause, *, color: bool, depth: int = 0) -> list[str]:
    prefix = "  " * depth
    safe_type = _display(cause.exception_type)
    name = _paint(safe_type, _EXCEPTION_COLORS.get(cause.exception_type), color)
    relation = {
        "cause": "Caused by",
        "context": "During handling of",
        "group_member": "Group member",
    }.get(cause.relation, "Related exception")
    lines = [f"{prefix}{relation}: {name}: {_display(cause.message)}"]
    if cause.location != "<unknown>":
        lines.append(f"{prefix}  at {_display(cause.location)}")
    for child in cause.children:
        lines.extend(_render_cause(child, color=color, depth=depth + 1))
    return lines


def render_diagnostic(
    diagnostic: Diagnostic,
    *,
    full: bool = False,
    stream: TextIO | None = None,
    color: bool | None = None,
) -> str:
    target = stream if stream is not None else sys.stderr
    use_color = _color_enabled(target, color)
    severity = _display(diagnostic.severity.upper())
    badge = _paint(f"[{severity}]", _SEVERITY_COLORS.get(diagnostic.severity), use_color)
    exception_type = _paint(
        _display(diagnostic.exception_type),
        _EXCEPTION_COLORS.get(diagnostic.exception_type, _SEVERITY_COLORS.get(diagnostic.severity)),
        use_color,
    )
    lines = [f"{badge} {exception_type}: {_display(diagnostic.message)}"]
    if diagnostic.primary_frame is not None:
        lines.append(f"Location: {_display(diagnostic.location)}")
        if diagnostic.primary_frame.source_line:
            lines.append(f"Source: {_display(diagnostic.primary_frame.source_line)}")
    if diagnostic.summary:
        lines.append(f"Diagnosis: {_display(diagnostic.summary)}")
    if diagnostic.explanation:
        lines.append(f"Explanation: {_display(diagnostic.explanation)}")
    if diagnostic.suggestion:
        lines.append(f"Suggestion: {_display(diagnostic.suggestion)}")
    if diagnostic.primary_frame is not None and diagnostic.primary_frame.context:
        lines.append("Context:")
        for item in diagnostic.primary_frame.context:
            marker = ">>" if item.is_error_line else "  "
            lines.append(f"{marker} {item.line_number}: {_display(item.text)}")
    if diagnostic.causes:
        lines.append("Exception chain:")
        for cause in diagnostic.causes:
            lines.extend(_render_cause(cause, color=use_color))
    if diagnostic.causes_truncated:
        lines.append("Additional nested exceptions were omitted due to configured limits.")
    if full:
        lines.append(f"Diagnostic ID: {_display(diagnostic.diagnostic_id)}")
        if diagnostic.formatted_traceback:
            lines.append("Traceback:")
            lines.append(_display(diagnostic.formatted_traceback.rstrip(), multiline=True))
    elif diagnostic.formatted_traceback:
        lines.append("Enable full=True to include the formatted traceback.")
    rendered = "\n".join(lines)
    target.write(rendered + "\n")
    return rendered
