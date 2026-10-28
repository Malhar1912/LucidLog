from __future__ import annotations

import os
import re

from .config import Config, DEFAULT_REDACTION_PATTERNS


def default_redaction_patterns() -> tuple[str, ...]:
    return DEFAULT_REDACTION_PATTERNS


def redact_text(value: str, patterns: tuple[str, ...] | None = None, *, limit: int | None = None) -> str:
    text = value or ""
    for pattern in DEFAULT_REDACTION_PATTERNS:
        text = re.sub(pattern, "[REDACTED]", text)
    was_truncated = limit is not None and len(text) > limit
    if was_truncated:
        text = text[:limit] + "... [truncated]"
    for pattern in dict.fromkeys(patterns or ()):
        if pattern not in DEFAULT_REDACTION_PATTERNS:
            text = re.sub(pattern, "[REDACTED]", text)
    return text


def redact_path(path: str, config: Config) -> str:
    if not path:
        return "<unknown>"
    if path.startswith("<") and path.endswith(">"):
        return path
    result = path
    if config.redact_paths:
        roots = [os.path.expanduser("~")]
        if config.root_path:
            roots.insert(0, config.root_path)
        for root in roots:
            normalized_root = os.path.normcase(os.path.abspath(root)).rstrip("\\/")
            normalized_path = os.path.normcase(os.path.abspath(result))
            if normalized_path == normalized_root:
                result = "." if root == config.root_path else "~"
                break
            if normalized_path.startswith(normalized_root + os.sep):
                suffix = os.path.abspath(result)[len(os.path.abspath(root)):].lstrip("\\/")
                result = os.path.join("." if root == config.root_path else "~", suffix)
                break
    if config.shorten_paths and config.root_path:
        root = os.path.abspath(config.root_path).rstrip("\\/")
        absolute = os.path.abspath(result)
        if os.path.normcase(absolute).startswith(os.path.normcase(root + os.sep)):
            result = os.path.relpath(absolute, root)
    return result


def sanitize_text(value: str, config: Config, *, limit: int | None = None) -> str:
    selected_limit = config.max_message_length if limit is None else limit
    return redact_text(value, config.redact_patterns, limit=selected_limit)
