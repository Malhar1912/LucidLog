from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class SourceLine:
    line_number: int
    text: str
    is_error_line: bool = False
    start_column: int | None = None
    end_column: int | None = None


@dataclass(frozen=True)
class GitResponsibility:
    commit_id: str | None = None
    author: str | None = None
    email: str | None = None
    summary: str | None = None
    date: str | None = None
    team: str | None = None
    owner: str | None = None
    confidence: str = "unavailable"
    source: str = "unknown"
    pr_number: int | None = None
    pr_url: str | None = None
    issue_id: str | None = None
    issue_url: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "commit_id": self.commit_id,
            "author": self.author,
            "email": self.email,
            "summary": self.summary,
            "date": self.date,
            "team": self.team,
            "owner": self.owner,
            "confidence": self.confidence,
            "source": self.source,
            "pr_number": self.pr_number,
            "pr_url": self.pr_url,
            "issue_id": self.issue_id,
            "issue_url": self.issue_url,
        }


@dataclass(frozen=True)
class FrameInfo:
    filename: str
    line_number: int
    function: str
    source_line: str | None
    context: tuple[SourceLine, ...] = ()
    responsibility: GitResponsibility | None = None

    @property
    def location(self) -> str:
        return f"{self.filename}:{self.line_number} in {self.function}"


@dataclass(frozen=True)
class Diagnosis:
    summary: str
    explanation: str
    suggestion: str | None
    confidence: str
    origin: str
    evidence: tuple[str, ...] = ()


@dataclass(frozen=True)
class DiagnosticCause:
    exception_type: str
    message: str
    location: str
    summary: str
    relation: str = "cause"
    children: tuple["DiagnosticCause", ...] = ()
    frames: tuple[FrameInfo, ...] = ()


@dataclass(frozen=True)
class ExceptionContext:
    exception_type: str
    message: str
    frames: tuple[FrameInfo, ...] = ()
    primary_frame: FrameInfo | None = None
    formatted_traceback: str = ""
    causes: tuple[DiagnosticCause, ...] = ()
    causes_truncated: bool = False
    cause: "ExceptionContext | None" = None
    context: "ExceptionContext | None" = None
    exception_mro: tuple[str, ...] = ()


@dataclass(frozen=True)
class Diagnostic:
    schema_version: int = 1
    diagnostic_id: str = ""
    exception_type: str = ""
    message: str = ""
    frames: tuple[FrameInfo, ...] = ()
    primary_frame: FrameInfo | None = None
    causes: tuple[DiagnosticCause, ...] = ()
    diagnosis: Diagnosis = field(
        default_factory=lambda: Diagnosis(
            summary="No built-in diagnosis",
            explanation="No built-in diagnosis is available for this exception type.",
            suggestion=None,
            confidence="low",
            origin="none",
            evidence=(),
        )
    )
    formatted_traceback: str = ""
    severity: str = "error"
    causes_truncated: bool = False

    @property
    def location(self) -> str:
        if self.primary_frame is None:
            return ""
        return self.primary_frame.location

    @property
    def summary(self) -> str:
        return self.diagnosis.summary

    @property
    def explanation(self) -> str:
        return self.diagnosis.explanation

    @property
    def suggestion(self) -> str | None:
        return self.diagnosis.suggestion

    @property
    def confidence(self) -> str:
        return self.diagnosis.confidence

    @property
    def origin(self) -> str:
        return self.diagnosis.origin

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "diagnostic_id": self.diagnostic_id,
            "exception_type": self.exception_type,
            "message": self.message,
            "frames": [self._frame_to_dict(frame) for frame in self.frames],
            "primary_frame": self._frame_to_dict(self.primary_frame) if self.primary_frame else None,
            "causes": [
                self._cause_to_dict(cause)
                for cause in self.causes
            ],
            "diagnosis": {
                "summary": self.diagnosis.summary,
                "explanation": self.diagnosis.explanation,
                "suggestion": self.diagnosis.suggestion,
                "confidence": self.diagnosis.confidence,
                "origin": self.diagnosis.origin,
                "evidence": list(self.diagnosis.evidence),
            },
            "formatted_traceback": self.formatted_traceback,
            "severity": self.severity,
            "causes_truncated": self.causes_truncated,
        }

    def to_json(self, **kwargs: Any) -> str:
        return json.dumps(self.to_dict(), **kwargs)

    @staticmethod
    def _frame_to_dict(frame: FrameInfo | None) -> dict[str, Any] | None:
        if frame is None:
            return None
        return {
            "filename": frame.filename,
            "line_number": frame.line_number,
            "function": frame.function,
            "source_line": frame.source_line,
            "context": [
                {
                    "line_number": line.line_number,
                    "text": line.text,
                    "is_error_line": line.is_error_line,
                    "start_column": line.start_column,
                    "end_column": line.end_column,
                }
                for line in frame.context
            ],
            "responsibility": frame.responsibility.to_dict() if frame.responsibility else None,
        }

    @staticmethod
    def _cause_to_dict(cause: DiagnosticCause) -> dict[str, Any]:
        return {
            "exception_type": cause.exception_type,
            "message": cause.message,
            "location": cause.location,
            "summary": cause.summary,
            "relation": cause.relation,
            "children": [Diagnostic._cause_to_dict(child) for child in cause.children],
            "frames": [Diagnostic._frame_to_dict(frame) for frame in cause.frames],
        }
