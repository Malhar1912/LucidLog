from __future__ import annotations

import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from .config import Config
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


def _infer_team(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = value.strip().strip("@")
    if not cleaned:
        return None
    if "/" in cleaned:
        cleaned = cleaned.split("/")[-1]
    if "@" in cleaned:
        cleaned = cleaned.split("@", 1)[0]
    cleaned = cleaned.replace("-", " ").replace("_", " ")
    if cleaned.lower() in {"maintainer", "maintainers", "owner", "owners"}:
        return "maintainers"
    if any(token in cleaned.lower() for token in ("team", "platform", "infra", "backend", "frontend", "security", "data")):
        return cleaned.strip().lower()
    return "maintainers"


def _codeowners_for_file(filename: str, repo_root: str | None = None) -> tuple[str | None, str | None]:
    if not filename:
        return None, None
    repo_root = repo_root or _git_root_for_path(filename)
    if repo_root is None:
        return None, None
    repo_path = Path(repo_root)
    candidates = [
        repo_path / ".github" / "CODEOWNERS",
        repo_path / "CODEOWNERS",
        repo_path / ".gitlab" / "CODEOWNERS",
    ]
    for candidate in candidates:
        if not candidate.exists():
            continue
        try:
            file_path = Path(filename).resolve()
            relative_path = file_path.relative_to(repo_path.resolve()) if file_path.is_relative_to(repo_path.resolve()) else file_path.name
            with candidate.open("r", encoding="utf-8", errors="replace") as handle:
                for raw_line in handle:
                    line = raw_line.strip()
                    if not line or line.startswith("#"):
                        continue
                    parts = line.split()
                    if len(parts) < 2:
                        continue
                    pattern = parts[0]
                    owner = parts[-1]
                    if pattern == "*" or relative_path.match(pattern.strip("/")) or relative_path.as_posix().endswith(pattern.strip("/")):
                        team = _infer_team(owner)
                        return owner, team
        except OSError:
            continue
    return None, None


def _normalize_personal_identity(value: str | None, *, include_full_name: bool, privacy_mode: bool) -> str | None:
    if value is None:
        return None
    if privacy_mode:
        if "@" in value:
            return "[REDACTED]"
        return value[:1] + "***" if len(value) > 1 else "[REDACTED]"
    if not include_full_name and " " in value:
        return value.split()[0]
    return value


def _git_blame_for_line(filename: str, line_number: int, *, config: Config | None = None) -> GitResponsibility | None:
    config = config or Config()
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
            return _git_file_fallback(filename, line_number, config=config)

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
        owner, team = _codeowners_for_file(filename, repo_root)
        author_value = _normalize_personal_identity(author, include_full_name=config.include_full_author_name, privacy_mode=config.redact_private_identity)
        email_value = email if config.include_email and not config.redact_private_identity else None if config.redact_private_identity else email
        return GitResponsibility(
            commit_id=commit_id,
            author=author_value,
            email=email_value,
            summary=summary,
            date=date,
            team=team or _infer_team(author or owner),
            owner=owner or author_value,
            confidence="exact_blame",
            source="git_blame",
        )
    except (OSError, ValueError, TypeError):
        return _git_file_fallback(filename, line_number, config=config)


def _git_file_fallback(filename: str, line_number: int, *, config: Config | None = None) -> GitResponsibility | None:
    config = config or Config()
    try:
        repo_root = _git_root_for_path(filename)
        if repo_root is None:
            return None
        file_path = Path(filename)
        repo_path = Path(repo_root)
        relative_path = file_path.resolve().relative_to(repo_path.resolve()) if file_path.resolve().is_relative_to(repo_path.resolve()) else file_path.name
        result = subprocess.run(
            ["git", "-C", str(repo_path), "log", "-1", "--format=%H%x09%an%x09%ae%x09%s%x09%cI", "--", str(relative_path)],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0 or not result.stdout.strip():
            owner, team = _codeowners_for_file(filename, repo_root)
            if owner or team:
                return GitResponsibility(
                    author=_normalize_personal_identity(owner, include_full_name=config.include_full_author_name, privacy_mode=config.redact_private_identity),
                    team=team,
                    owner=owner,
                    confidence="codeowner_fallback",
                    source="codeowners",
                )
            return None
        commit_id, author, email, summary, date = result.stdout.strip().split("\t", 4)
        owner, team = _codeowners_for_file(filename, repo_root)
        return GitResponsibility(
            commit_id=commit_id,
            author=_normalize_personal_identity(author, include_full_name=config.include_full_author_name, privacy_mode=config.redact_private_identity),
            email=email if config.include_email and not config.redact_private_identity else None,
            summary=summary,
            date=date,
            team=team or _infer_team(author or owner),
            owner=owner or author,
            confidence="file_fallback",
            source="git_log",
        )
    except (OSError, ValueError, TypeError):
        return None


def read_source_context(
    filename: str,
    line_number: int,
    context_lines: int = 3,
    *,
    max_file_bytes: int = 5000000,
    config: Config | None = None,
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
    responsibility = _git_blame_for_line(filename, line_number, config=config)
    return source_text, tuple(context), responsibility


def shorten_path(path: str, root: str | None = None) -> str:
    if not path:
        return "<unknown>"
    if root and path.startswith(root):
        return path[len(root):].lstrip("/\\")
    return Path(path).as_posix() if "\\" in path else path
