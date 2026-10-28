from __future__ import annotations

from pathlib import Path

from .models import SourceLine


def _read_source_lines(path: str, start: int, end: int, max_file_bytes: int) -> list[tuple[int, str]]:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            if handle.seek(0, 2) > max_file_bytes:
                return []
            handle.seek(0)
            selected: list[tuple[int, str]] = []
            for number, line in enumerate(handle, start=1):
                if number >= start:
                    selected.append((number, line.rstrip("\r\n")))
                if number >= end:
                    break
            return selected
    except (OSError, ValueError, TypeError):
        return []


def read_source_context(
    filename: str,
    line_number: int,
    context_lines: int = 3,
    *,
    max_file_bytes: int = 5000000,
) -> tuple[str | None, tuple[SourceLine, ...]]:
    if not filename or line_number <= 0:
        return None, ()
    start = max(1, line_number - context_lines)
    end = line_number + context_lines
    lines = _read_source_lines(filename, start, end, max_file_bytes)
    if not lines:
        return None, ()
    context: list[SourceLine] = []
    source_text = None
    for idx, text in lines:
        context.append(
            SourceLine(
                line_number=idx,
                text=text,
                is_error_line=idx == line_number,
            )
        )
        if idx == line_number:
            source_text = text
    return source_text, tuple(context)


def shorten_path(path: str, root: str | None = None) -> str:
    if not path:
        return "<unknown>"
    if root and path.startswith(root):
        return path[len(root):].lstrip("/\\")
    return Path(path).as_posix() if "\\" in path else path
