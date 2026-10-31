from __future__ import annotations

from dataclasses import dataclass
import re
from typing import TextIO


DEFAULT_REDACTION_PATTERNS = (
    r"(?im)(authorization|cookie)\s*[:=]\s*[^\r\n]+",
    r"""(?i)(password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key)["']?\s*[:=]\s*(?:"[^"]*"|'[^']*'|[^\s,;}\]]+)""",
    r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/-]+=*",
    r"\b(?:gh[pousr]_[A-Za-z0-9_]{20,}|sk[-_](?:live|test)?[A-Za-z0-9_-]{16,}|xox[baprs]-[A-Za-z0-9-]{16,}|AIza[0-9A-Za-z_-]{20,})\b",
    r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----[\s\S]*?-----END (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
    r"(?i)\bAKIA[0-9A-Z]{16}\b",
)


@dataclass(frozen=True)
class Config:
    max_chain_depth: int = 4
    max_source_context: int = 3
    max_frames: int = 20
    shorten_paths: bool = True
    output_stream: TextIO | None = None
    redact_patterns: tuple[str, ...] = DEFAULT_REDACTION_PATTERNS
    redact_paths: bool = True
    include_full_traceback: bool = False
    max_message_length: int = 4096
    include_exception_chain: bool = True
    include_local_variables: bool = False
    allow_network: bool = False
    root_path: str | None = None
    max_traceback_chars: int = 20000
    max_group_exceptions: int = 20
    max_context_line_length: int = 1000
    max_source_file_bytes: int = 5000000
    max_diagnosis_evidence: int = 20
    color: bool | None = None
    include_email: bool = True
    include_full_author_name: bool = True
    redact_private_identity: bool = False
    codeowners_path: str | None = None

    def __post_init__(self) -> None:
        for name in (
            "max_chain_depth",
            "max_frames",
            "max_message_length",
            "max_traceback_chars",
            "max_group_exceptions",
            "max_context_line_length",
            "max_source_file_bytes",
            "max_diagnosis_evidence",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{name} must be an integer")
            if value < 1:
                raise ValueError(f"{name} must be at least 1")
        if isinstance(self.max_source_context, bool) or not isinstance(self.max_source_context, int):
            raise TypeError("max_source_context must be an integer")
        if self.max_source_context < 0:
            raise ValueError("max_source_context cannot be negative")
        if not isinstance(self.redact_patterns, tuple) or not all(
            isinstance(pattern, str) for pattern in self.redact_patterns
        ):
            raise TypeError("redact_patterns must be a tuple of strings")
        for pattern in self.redact_patterns:
            re.compile(pattern)
        if self.color is not None and not isinstance(self.color, bool):
            raise TypeError("color must be True, False, or None")
        for name in (
            "shorten_paths",
            "redact_paths",
            "include_full_traceback",
            "include_exception_chain",
            "include_local_variables",
            "allow_network",
            "include_email",
            "include_full_author_name",
            "redact_private_identity",
        ):
            if not isinstance(getattr(self, name), bool):
                raise TypeError(f"{name} must be a boolean")
        if self.include_local_variables:
            raise ValueError("LucidLog does not capture local variables.")
        if self.allow_network:
            raise ValueError("The LucidLog core does not make network requests.")
        if self.root_path is not None and not isinstance(self.root_path, str):
            raise TypeError("root_path must be a string or None")
        if self.output_stream is not None and not callable(getattr(self.output_stream, "write", None)):
            raise TypeError("output_stream must provide a write() method")


def default_config() -> Config:
    return Config()
