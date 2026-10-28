from __future__ import annotations

import hashlib
from dataclasses import replace
from itertools import islice

from .capture import capture_exception
from .config import Config, default_config
from .models import Diagnostic, Diagnosis
from .redaction import sanitize_text
from .rules import diagnose_with_rules


def severity_for_exception(exc: BaseException) -> str:
    if isinstance(exc, Warning):
        return "warning"
    if isinstance(exc, (KeyboardInterrupt, SystemExit)) or type(exc).__name__ == "CancelledError":
        return "info"
    if isinstance(exc, (MemoryError, SystemError)):
        return "critical"
    members = getattr(exc, "exceptions", ())
    if members:
        if any(severity_for_exception(member) == "critical" for member in islice(members, 20)):
            return "critical"
        return "error"
    return "error"


def build_diagnostic(exc: BaseException, *, config: Config | None = None) -> Diagnostic:
    config = config or default_config()
    context = capture_exception(exc, config=config)
    diagnosis = diagnose_with_rules(context)
    if not isinstance(diagnosis, Diagnosis):
        raise TypeError("A diagnostic rule must return a Diagnosis instance.")
    if not all(
        isinstance(value, str)
        for value in (diagnosis.summary, diagnosis.explanation, diagnosis.confidence, diagnosis.origin)
    ):
        raise TypeError("A Diagnosis must use strings for its text fields.")
    if diagnosis.suggestion is not None and not isinstance(diagnosis.suggestion, str):
        raise TypeError("A Diagnosis suggestion must be a string or None.")
    if not isinstance(diagnosis.evidence, tuple) or not all(
        isinstance(item, str) for item in diagnosis.evidence
    ):
        raise TypeError("A Diagnosis evidence field must be a tuple of strings.")
    if diagnosis.confidence not in {"low", "medium", "high"}:
        raise ValueError("A Diagnosis confidence must be low, medium, or high.")
    evidence = list(diagnosis.evidence[: config.max_diagnosis_evidence])
    if len(diagnosis.evidence) > config.max_diagnosis_evidence:
        evidence[-1] = "Additional rule evidence omitted due to configured limits."
    diagnosis = replace(
        diagnosis,
        summary=sanitize_text(diagnosis.summary, config),
        explanation=sanitize_text(diagnosis.explanation, config),
        suggestion=(
            sanitize_text(diagnosis.suggestion, config)
            if diagnosis.suggestion is not None
            else None
        ),
        confidence=sanitize_text(diagnosis.confidence, config, limit=64),
        origin=sanitize_text(diagnosis.origin, config, limit=128),
        evidence=tuple(sanitize_text(item, config) for item in evidence),
    )
    digest = hashlib.sha256()
    location = context.primary_frame
    fingerprint_parts = (
        type(exc).__name__,
        location.filename if location else "",
        str(location.line_number) if location else "",
        location.function if location else "",
    )
    digest.update(":".join(fingerprint_parts).encode("utf-8", errors="replace"))
    diagnostic_id = digest.hexdigest()[:16]
    return Diagnostic(
        schema_version=1,
        diagnostic_id=diagnostic_id,
        exception_type=context.exception_type,
        message=context.message,
        frames=context.frames,
        primary_frame=context.primary_frame,
        causes=context.causes,
        diagnosis=diagnosis,
        formatted_traceback=context.formatted_traceback,
        severity=severity_for_exception(exc),
        causes_truncated=context.causes_truncated,
    )
