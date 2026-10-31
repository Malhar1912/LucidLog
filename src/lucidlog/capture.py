from __future__ import annotations

import traceback
import builtins
from typing import Any

from .config import Config, default_config
from .models import DiagnosticCause, ExceptionContext, FrameInfo, SourceLine
from .redaction import redact_path, sanitize_text
from .source import read_source_context


def _safe_message(exc: BaseException, config: Config) -> str:
    try:
        message = str(exc)
    except BaseException:
        message = "<exception message could not be rendered>"
    return sanitize_text(message, config)


def _build_frame(frame: traceback.FrameSummary, config: Config) -> FrameInfo:
    source_line, context, responsibility = read_source_context(
        frame.filename,
        frame.lineno,
        context_lines=config.max_source_context,
        max_file_bytes=config.max_source_file_bytes,
        config=config,
    )
    safe_context = tuple(
        SourceLine(
            line_number=line.line_number,
            text=sanitize_text(line.text, config, limit=config.max_context_line_length),
            is_error_line=line.is_error_line,
            start_column=line.start_column,
            end_column=line.end_column,
        )
        for line in context
    )
    return FrameInfo(
        filename=sanitize_text(
            redact_path(frame.filename, config),
            config,
            limit=config.max_message_length,
        ),
        line_number=frame.lineno,
        function=sanitize_text(frame.name, config, limit=256),
        source_line=(
            sanitize_text(source_line, config, limit=config.max_context_line_length)
            if source_line is not None
            else None
        ),
        context=safe_context,
        responsibility=responsibility,
    )


def _group_members(exc: BaseException) -> tuple[BaseException, ...]:
    group_type: Any = getattr(builtins, "BaseExceptionGroup", None)
    if group_type is None or not isinstance(exc, group_type):
        return ()
    return tuple(exc.exceptions)


def _capture_relationships(
    exc: BaseException,
    *,
    config: Config,
    seen: set[int],
    budget: list[int],
    depth: int = 0,
) -> tuple[tuple[DiagnosticCause, ...], bool]:
    if not config.include_exception_chain:
        return (), False
    if depth >= config.max_chain_depth:
        return (), bool(exc.__cause__ or (exc.__context__ and not exc.__suppress_context__) or _group_members(exc))

    related: list[DiagnosticCause] = []
    truncated = False
    next_exc = exc.__cause__
    relation = "cause"
    if next_exc is None and not exc.__suppress_context__:
        next_exc = exc.__context__
        relation = "context"
    def related_exceptions():
        if next_exc is not None:
            yield next_exc, relation
        for member in _group_members(exc):
            yield member, "group_member"

    for candidate_count, (child, child_relation) in enumerate(related_exceptions()):
        if candidate_count >= config.max_group_exceptions:
            truncated = True
            break
        child_id = id(child)
        if child_id in seen or budget[0] >= config.max_group_exceptions:
            truncated = True
            continue
        seen.add(child_id)
        budget[0] += 1
        tb = child.__traceback__
        child_frames: tuple[FrameInfo, ...] = ()
        location = "<unknown>"
        if tb is not None:
            extracted = traceback.extract_tb(tb, limit=-config.max_frames)
            child_frames = tuple(_build_frame(frame, config) for frame in extracted)
            if child_frames:
                location = child_frames[-1].location
        children, child_truncated = _capture_relationships(
            child,
            config=config,
            seen=seen,
            budget=budget,
            depth=depth + 1,
        )
        related.append(
            DiagnosticCause(
                exception_type=type(child).__name__,
                message=_safe_message(child, config),
                location=location,
                summary="Nested exception" if child_relation != "group_member" else "Exception group member",
                relation=child_relation,
                children=children,
                frames=child_frames,
            )
        )
        truncated = truncated or child_truncated
    return tuple(related), truncated


def _format_frame(frame: FrameInfo) -> list[str]:
    lines = [f'  File "{frame.filename}", line {frame.line_number}, in {frame.function}']
    if frame.source_line:
        lines.append(f"    {frame.source_line.strip()}")
    return lines


def _format_cause(cause: DiagnosticCause, depth: int = 0) -> list[str]:
    lines: list[str] = []
    relation = {
        "cause": "Caused by",
        "context": "During handling of",
        "group_member": "Exception group member",
    }.get(cause.relation, "Related exception")
    lines.append(f"{'  ' * depth}{relation}:")
    for frame in cause.frames:
        lines.extend(f"{'  ' * depth}{line}" for line in _format_frame(frame))
    lines.append(f"{'  ' * depth}{cause.exception_type}: {cause.message}")
    for child in cause.children:
        lines.extend(_format_cause(child, depth + 1))
    return lines


def _format_traceback(
    exception_type: str,
    message: str,
    frames: tuple[FrameInfo, ...],
    causes: tuple[DiagnosticCause, ...],
    config: Config,
) -> str:
    lines = ["Traceback (most recent call last):"]
    for frame in frames:
        lines.extend(_format_frame(frame))
    lines.append(f"{exception_type}: {message}")
    for cause in causes:
        lines.extend(_format_cause(cause))
    return sanitize_text("\n".join(lines), config, limit=config.max_traceback_chars)


def capture_exception(exc: BaseException, *, config: Config | None = None) -> ExceptionContext:
    config = config or default_config()
    frames: list[FrameInfo] = []
    if exc.__traceback__ is not None:
        extracted = traceback.extract_tb(exc.__traceback__, limit=-config.max_frames)
        frames = [_build_frame(frame, config) for frame in extracted]
    primary_frame = frames[-1] if frames else None
    causes, causes_truncated = _capture_relationships(
        exc,
        config=config,
        seen={id(exc)},
        budget=[0],
    )
    message = _safe_message(exc, config)
    formatted_traceback = _format_traceback(
        type(exc).__name__,
        message,
        tuple(frames),
        causes,
        config,
    )
    return ExceptionContext(
        exception_type=type(exc).__name__,
        message=message,
        exception_mro=tuple(base.__name__ for base in type(exc).__mro__[1:]),
        frames=tuple(frames),
        primary_frame=primary_frame,
        formatted_traceback=formatted_traceback,
        causes=causes,
        causes_truncated=causes_truncated,
    )
