from __future__ import annotations

import subprocess
from datetime import datetime, timezone
from pathlib import Path

from .models import GitResponsibility, SourceLine


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


def _git_root_for_path(path: str) -> str | None:
    try:
        resolved = Path(path).resolve()
        for search_path in (resolved, *resolved.parents):
            git_dir = search_path / ".git"
            if git_dir.exists():
                return str(search_path)
    except OSError:
        return None

    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            cwd=str(Path(path).resolve().parent),
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except (OSError, ValueError):
        return None
    return None


def _git_blame_for_line(filename: str, line_number: int) -> GitResponsibility | None:
    if not filename or line_number <= 0:
        return None
    try:
        repo_root = _git_root_for_path(filename)
        if repo_root is None:
            return None
        file_path = Path(filename)
        repo_path = Path(repo_root)
        relative_path = file_path.resolve().relative_to(repo_path.resolve()) if file_path.resolve().is_relative_to(repo_path.resolve()) else file_path.name
        result = subprocess.run(
            [
                "git",
                "-C",
                str(repo_path),
                "blame",
                "--line-porcelain",
                "-L",
                f"{line_number},{line_number}",
                "--",
                str(relative_path),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return None

        commit_id = None
        author = None
        email = None
        summary = None
        date = None
        for raw_line in result.stdout.splitlines():
            if not raw_line:
                continue
            if commit_id is None and len(raw_line) >= 40 and raw_line[:40].isalnum():
                commit_id = raw_line.split()[0][:40]
            elif raw_line.startswith("author "):
                author = raw_line[7:]
            elif raw_line.startswith("author-mail "):
                email = raw_line[12:].strip().strip("<>")
            elif raw_line.startswith("summary "):
                summary = raw_line[8:]
            elif raw_line.startswith("author-time "):
                try:
                    date = datetime.fromtimestamp(int(raw_line.split()[1]), tz=timezone.utc).isoformat()
                except (TypeError, ValueError):
                    date = raw_line.split()[1]
        return GitResponsibility(
            commit_id=commit_id,
            author=author,
            email=email,
            summary=summary,
            date=date,
        )
    except (OSError, ValueError, TypeError):
        return None


def read_source_context(
    filename: str,
    line_number: int,
    context_lines: int = 3,
    *,
    max_file_bytes: int = 5000000,
) -> tuple[str | None, tuple[SourceLine, ...], GitResponsibility | None]:
    if not filename or line_number <= 0:
        return None, (), None
    start = max(1, line_number - context_lines)
    end = line_number + context_lines
    lines = _read_source_lines(filename, start, end, max_file_bytes)
    if not lines:
        return None, (), None
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
    responsibility = _git_blame_for_line(filename, line_number)
    return source_text, tuple(context), responsibility


def shorten_path(path: str, root: str | None = None) -> str:
    if not path:
        return "<unknown>"
    if root and path.startswith(root):
        return path[len(root):].lstrip("/\\")
    return Path(path).as_posix() if "\\" in path else path
