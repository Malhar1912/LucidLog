# LucidLog — Implementation Specification

**Status:** MVP implementation plan  
**Language:** Python  
**Package name:** `lucidlog` (working name; verify PyPI, GitHub, and trademark availability before publication)  
**Primary goal:** Turn Python exceptions into concise, source-aware, structured diagnostics with conservative explanations and optional AI assistance.

---

## 1. Product requirements

LucidLog is a diagnostic layer, not a replacement for Python's logging system. It must work locally, preserve normal exception behavior, and remain useful without an AI provider, network access, or non-standard dependencies.

### Core principles

1. **Useful first screen:** show exception type, message, relevant file and line, function, source context, and a short explanation when available.
2. **Preserve the original:** retain the full traceback and exception chaining; never hide the original exception if diagnostics fail.
3. **Conservative diagnosis:** distinguish observed facts from heuristics and AI-generated hypotheses.
4. **Privacy by default:** do not capture local variables or send source/diagnostic data over the network by default.
5. **Composable architecture:** separate capture, analysis, models, rendering, serialization, and integrations.
6. **Low friction:** a minimal setup call should improve uncaught-exception output without requiring application rewrites.
7. **Stable behavior:** avoid changing exception types, return values, control flow, or logging semantics.

## 2. Primary features

### P0 — Required for MVP

- Enhanced uncaught exception handler for the main thread.
- Explicit reporting inside `except` blocks.
- Structured diagnostic object and JSON-compatible export.
- Exception type and message.
- Traceback frame extraction and application-frame selection.
- File path, line number, function name, source snippet, and nearby context.
- Concise terminal rendering and opt-in full traceback.
- Conservative rule-based explanations for common exception types.
- Fallback to the previous exception hook if rendering fails.
- Standard-library-only core.
- Tests for common exceptions, source context, chained exceptions, missing source, and rendering.
- Packaging, README, type annotations, and basic CI.

### P1 — Important early follow-up

- Chained exceptions: `__cause__`, `__context__`, and `raise ... from ...`.
- `ExceptionGroup` support where available.
- `threading.excepthook` integration, opt-in or explicitly installed.
- Asyncio loop exception handler, opt-in or explicitly installed.
- `logging` integration without duplicate records.
- JSON output and machine-readable schema/version.
- Configurable source roots and framework/library frame filtering.
- Redaction of configured sensitive values and paths.
- Error fingerprint / diagnostic ID.
- Test-friendly, injectable output stream.
- Python-version compatibility matrix.

### P2 — Optional advanced features

- Optional Rich-based terminal renderer.
- Optional AI advisor for explanations and candidate fixes.
- Framework-specific rules for FastAPI, Django, Pydantic, SQLAlchemy, and asyncio.
- Plugin API for custom diagnosis rules.
- IDE and CI integrations.
- Performance benchmarks and telemetry only when explicitly enabled.

Do not make a web dashboard, hosted service, database, AI dependency, or remote reporting a requirement for the core package.

## 3. User stories and acceptance criteria

### Story A: uncaught exception

**As a developer**, I want uncaught errors to display the relevant source location and a readable explanation.

Acceptance:
- `lucidlog.install()` installs a main-thread exception hook.
- The handler prints exception type/message, relevant location, and source context when available.
- The process retains normal non-zero exit behavior for uncaught exceptions.
- If LucidLog itself fails, the previous hook receives the original exception.
- Repeated installation does not create nested duplicate hooks.

### Story B: handled exception

**As a developer**, I want to report an exception inside `except` without losing its traceback.

Acceptance:
- `lucidlog.exception("message")` works only when an active exception exists.
- It preserves and renders the original traceback.
- Calling it outside an exception handler raises a clear `RuntimeError`.
- The output destination and verbosity are configurable.

### Story C: structured diagnostics

**As a developer or tool author**, I want to inspect diagnostics programmatically.

Acceptance:
- `lucidlog.diagnose(exc)` returns a typed diagnostic model.
- `to_dict()` returns JSON-compatible data.
- `to_json()` serializes the model.
- The model distinguishes raw evidence from explanation and suggestion.
- It does not inspect local variables by default.

### Story D: source-aware context

Acceptance:
- The diagnostic includes filename, line number, function, source line, and a configurable number of surrounding lines when available.
- Missing, generated, zipped, or inaccessible source does not crash diagnosis.
- The exact failing expression is only highlighted when trustworthy position metadata exists; otherwise highlight the line, not a guessed expression.
- Paths may be shortened for display but the structured representation retains a clearly defined path policy.

### Story E: explanation and suggestions

Acceptance:
- Rules are deterministic, bounded, and tested.
- Unsupported exception types return “no built-in diagnosis” rather than a fabricated explanation.
- Suggestions are framed as checks or possible fixes, not guaranteed solutions.
- The output indicates whether an explanation came from a rule, custom plugin, or AI.

## 4. Package architecture

Recommended layout:

```text
lucidlog/
├── pyproject.toml
├── README.md
├── LICENSE
├── src/
│   └── lucidlog/
│       ├── __init__.py
│       ├── api.py
│       ├── config.py
│       ├── models.py
│       ├── capture.py
│       ├── source.py
│       ├── chains.py
│       ├── diagnosis.py
│       ├── rules.py
│       ├── render.py
│       ├── serializers.py
│       ├── fingerprints.py
│       ├── redaction.py
│       └── integrations/
│           ├── __init__.py
│           ├── logging.py
│           ├── threading.py
│           └── asyncio.py
└── tests/
    ├── test_api.py
    ├── test_capture.py
    ├── test_source.py
    ├── test_chains.py
    ├── test_rules.py
    ├── test_render.py
    ├── test_serializers.py
    ├── test_redaction.py
    ├── test_integrations.py
    └── test_security.py
```

### Module responsibilities

- `api.py`: public API and hook installation.
- `config.py`: validated configuration with safe defaults.
- `models.py`: typed, renderer-independent diagnostic data structures.
- `capture.py`: extract frames and exception metadata.
- `source.py`: retrieve source context and source positions.
- `chains.py`: walk causes, contexts, and exception groups safely.
- `diagnosis.py`: orchestrate rules and create diagnosis results.
- `rules.py`: built-in and custom diagnostic rules.
- `render.py`: concise text output and optional verbose traceback.
- `serializers.py`: stable JSON-compatible representation.
- `fingerprints.py`: deterministic grouping key that excludes sensitive messages by default.
- `redaction.py`: redact configured secrets and sensitive paths.
- `integrations/*`: optional logging, thread, and asyncio hooks.

No module should import an AI SDK or Rich at core import time.

## 5. Public API contract

Target API:

```python
import lucidlog

lucidlog.install()

try:
    run_job()
except Exception:
    lucidlog.exception("Job execution failed")

try:
    run_job()
except Exception as exc:
    diagnostic = lucidlog.diagnose(exc)
    print(diagnostic.exception_type)
    print(diagnostic.location)
    print(diagnostic.explanation)
    print(diagnostic.to_json())
```

### Proposed signatures

```python
install(
    *,
    full: bool | None = None,
    stream: TextIO | None = None,
    config: Config | None = None,
) -> None

uninstall() -> None

configure(**options) -> Config

diagnose(
    exc: BaseException,
    *,
    tb: TracebackType | None = None,
    config: Config | None = None,
) -> Diagnostic

exception(message: str = "An exception occurred") -> Diagnostic

log_exception(
    logger: logging.Logger,
    message: str,
    *,
    exc_info: bool = True,
) -> Diagnostic
```

These are proposed contracts; implementation should keep signatures consistent once published. Avoid exposing unstable internal classes as public API until their schema is reviewed.

### Configuration defaults

```python
Config(
    output="terminal",
    show_source=True,
    context_lines=3,
    show_full_traceback=False,
    include_locals=False,       # False and unsupported in MVP
    ai_enabled=False,
    redact_patterns=(),
    source_roots=(),
    excluded_roots=(),           # e.g. site-packages filtering
    path_mode="absolute",        # absolute | project-relative | basename
)
```

Validate values eagerly. `include_locals=True` must not silently expose locals in the MVP; either reject it as unsupported or clearly state it has no effect until a safe implementation exists.

## 6. Data model

Use typed dataclasses or another lightweight standard-library model. Example conceptual schema:

```python
@dataclass(frozen=True)
class SourceLine:
    line_number: int
    text: str
    is_error_line: bool = False
    start_column: int | None = None
    end_column: int | None = None

@dataclass(frozen=True)
class FrameInfo:
    filename: str
    line_number: int
    function: str
    source_line: str | None
    context: tuple[SourceLine, ...]

@dataclass(frozen=True)
class Diagnosis:
    summary: str
    explanation: str
    suggestion: str | None
    confidence: str               # low | medium | high
    origin: str                   # builtin_rule | plugin | ai | none
    evidence: tuple[str, ...]

@dataclass(frozen=True)
class Diagnostic:
    schema_version: int
    diagnostic_id: str
    exception_type: str
    message: str
    frames: tuple[FrameInfo, ...]
    primary_frame: FrameInfo | None
    causes: tuple["DiagnosticCause", ...]
    diagnosis: Diagnosis
    formatted_traceback: str
```

Avoid storing arbitrary live frame objects in the final diagnostic model. Extract the necessary metadata, then release references so frames do not retain application objects in memory.

The exact schema should be finalized before guaranteeing long-term JSON compatibility.

## 7. Capture and frame selection

### Capture

Use `traceback.extract_tb()` or `TracebackException` to extract traceback data. Do not manually parse the formatted traceback string.

Capture:
- exception class name;
- safe string representation of the message;
- extracted frames;
- `__cause__` and `__context__`;
- source text if accessible;
- formatted traceback for fallback/debugging.

Defensive behavior:
- If `str(exc)` itself fails, use a safe placeholder.
- If source retrieval fails, omit source context.
- Avoid recursively traversing circular or repeated exception references.
- Bound chain depth and output size.
- Never let an error in diagnosis replace the original application exception.

### Primary-frame policy

The final traceback frame is the immediate raise/error location, not necessarily the root cause. Therefore:
1. Preserve all frames.
2. Identify the immediate failing frame.
3. Mark likely application frames separately from framework and dependency frames.
4. When exception chaining exists, display the causal chain.
5. Never silently discard a frame because it looks internal.
6. Make frame filtering configurable.

For expression highlighting, use source-position metadata available in supported Python versions where reliable. Otherwise highlight only the failing line.

## 8. Deterministic diagnosis engine

The rule engine should accept a structured exception and return a `Diagnosis`. Rules must be deterministic, independently tested, and prevented from executing arbitrary user code.

### Initial built-in rules

| Exception | Diagnostic direction |
|---|---|
| `KeyError` | A mapping key is missing; inspect the input schema and key access. |
| `AttributeError` | An object lacks an attribute; inspect object type and optional values. |
| `TypeError` | Argument type, operand type, or callable signature may be incompatible. |
| `IndexError` | Sequence index may be outside valid bounds. |
| `ZeroDivisionError` | Denominator is zero. |
| `FileNotFoundError` | File path, working directory, or deployment artifact may be incorrect. |
| `ImportError` / `ModuleNotFoundError` | Import name, environment, or package installation may be incorrect. |
| `ValueError` | A value is invalid for an operation; avoid guessing which constraint failed. |
| `TimeoutError` | An operation exceeded its time limit; investigate the dependency and timeout configuration. |
| `ConnectionError` | A connection failed; investigate endpoint, availability, and network configuration. |

Rules must not claim facts that cannot be inferred from the exception and captured evidence. For example, `AttributeError` alone does not prove a variable is `None`.

### Rule interface

A rule should conceptually expose:

```python
class DiagnosticRule(Protocol):
    name: str

    def matches(self, error: ExceptionContext) -> bool:
        ...

    def diagnose(self, error: ExceptionContext) -> Diagnosis:
        ...
```

For the initial implementation, a simple ordered list of pure functions is sufficient. Add a plugin registry only after the rule interface stabilizes.

## 9. Exception chaining and groups

Support these Python patterns:

```python
raise RuntimeError("Request failed") from original_error
```

and implicit context:

```python
try:
    perform_operation()
except ValueError:
    raise RuntimeError("Operation failed")
```

Requirements:
- Preserve the distinction between explicit cause and implicit context.
- Render the most relevant cause near the summary.
- Keep the outer exception visible.
- Detect repeated exception objects and cap recursion depth.
- Add `ExceptionGroup` support when the runtime provides it.
- Bound the number of displayed nested errors; indicate when output is truncated.

## 10. Rendering

### Compact mode

Display:
1. Exception type and message.
2. File, line, and function.
3. A small source context with the failing line marked.
4. Concise explanation and a possible next step.
5. A short cause summary when relevant.
6. A hint to enable full traceback.

### Full mode

Add:
- every traceback frame;
- explicit and implicit exception chains;
- additional source context;
- diagnostic ID and optional structured fields.

### Renderer rules

- Plain-text output must work without dependencies.
- ANSI colors should be optional and disabled or adapted for non-interactive output.
- Avoid terminal-width assumptions.
- Never depend on output color to convey meaning.
- Write to an injectable stream to make tests deterministic.
- Keep the renderer separate from the diagnostic data model.
- Add Rich as an optional extra only after the plain renderer works.

## 11. Logging and runtime integrations

### Standard `logging`

Provide a helper that accepts an existing logger. Do not automatically replace logging handlers or duplicate a record.

Expected usage:

```python
try:
    process()
except Exception:
    lucidlog.exception("Processing failed")
```

An optional `log_exception(logger, message)` can send the structured diagnostic through the application's existing logging setup.

### Main-thread uncaught exceptions

`install()` should:
- save the previous `sys.excepthook`;
- install a handler only once;
- render the exception;
- fall back to the previous handler if rendering fails;
- support `uninstall()` restoring the previous handler;
- avoid swallowing the exception or changing process termination semantics.

### Threads and asyncio

Implement separately and explicitly:
- `threading.excepthook` integration for uncaught thread exceptions;
- asyncio loop exception handler integration;
- documented installation and restoration;
- tests for interaction with existing hooks.

Do not claim that `sys.excepthook` covers worker threads, every asyncio task, or child processes.

## 12. Security and privacy requirements

Security is a release blocker, not a future enhancement.

- Never capture local variables by default.
- Never make network calls in core diagnostics.
- Never send source code to an AI service without explicit opt-in.
- Support configurable redaction of known secrets and sensitive paths.
- Avoid including environment variables, request headers, tokens, passwords, or full payloads.
- Treat exception messages as potentially sensitive.
- Limit diagnostic size and exception-chain depth.
- Avoid keeping live traceback frames longer than necessary.
- Document that redaction patterns are best-effort and cannot guarantee removal of every secret.
- Test that configured secrets do not appear in terminal or JSON output.
- Ensure a redaction or rendering failure does not mask the original exception.

A diagnostic library cannot guarantee that arbitrary exception messages contain no sensitive information. Make the privacy boundary explicit and allow users to choose the data they retain.

## 13. Optional AI advisor

AI must be an optional component layered on top of a complete deterministic diagnostic.

Input should be a minimized, user-approved payload containing:
- exception type and redacted message;
- selected source lines, if explicitly permitted;
- relevant frame names and paths after redaction;
- deterministic findings already established.

Output should be structured:
- explanation;
- possible causes;
- suggested checks or changes;
- uncertainty and confidence;
- evidence used.

Requirements:
- AI output must never be represented as a proven root cause.
- Do not execute generated fixes automatically.
- Do not send requests by default.
- Set request timeouts and output limits.
- Handle provider errors gracefully.
- Keep provider SDKs optional and isolated from core imports.
- Allow a local model adapter in the future without changing the core data model.

## 14. Error fingerprints

Create a stable grouping key for repeated failures using normalized exception type, relevant frame/function information, and optionally a normalized message.

Do not include raw messages by default because they may contain user data, IDs, or secrets. A fingerprint is a grouping aid, not a unique incident identifier or proof that two failures share the same root cause.

Test that repeated equivalent errors produce the same fingerprint and that common variable message values do not unnecessarily fragment groups.

## 15. Packaging and developer experience

Use a `src/` package layout and `pyproject.toml`.

Suggested packaging requirements:
- supported Python versions declared explicitly;
- no mandatory third-party dependencies for core;
- optional extras such as `lucidlog[rich]` and, later, provider-specific AI extras;
- type annotations;
- a documented public API;
- license and changelog;
- CI across supported Python versions;
- linting and formatting;
- unit tests and a small integration suite;
- semantic versioning after the first public API release.

Suggested commands once project configuration exists:

```bash
python -m pip install -e ".[dev]"
python -m pytest
python -m build
python -m twine check dist/*
```

Only publish after package name, metadata, license, tests, and dependency declarations have been verified.

## 16. Testing strategy

### Unit tests
- every built-in rule;
- no active exception for `exception()`;
- correct frame and line selection;
- source unavailable;
- source context boundaries;
- safe exception-message formatting;
- cause and context handling;
- repeated exception references;
- JSON serialization;
- redaction;
- stable fingerprints.

### Integration tests
- uncaught exception in a subprocess;
- exit code and stderr behavior;
- hook restoration;
- repeated installation;
- custom logging handler compatibility;
- worker-thread exception hook;
- asyncio loop exception handling;
- no duplicate output.

### Security tests
- secrets in exception messages;
- sensitive absolute paths;
- hostile or malformed exception strings;
- oversized messages and nested chains;
- renderer failure fallback;
- ensure locals are not collected.

### Performance tests
Benchmark normal execution separately from exception reporting. Measure diagnostic overhead on representative shallow and deep tracebacks. Do not impose a performance claim before obtaining reproducible measurements.

## 17. Definition of done for MVP

The MVP is ready when all of the following are true:

- [ ] `pip install lucidlog` works in a clean environment.
- [ ] `lucidlog.install()` provides readable uncaught-exception output.
- [ ] `lucidlog.exception()` works inside `except`.
- [ ] `lucidlog.diagnose(exc)` returns a stable structured object.
- [ ] JSON serialization is tested.
- [ ] Source context is included when available and omitted safely otherwise.
- [ ] Common exception explanations are covered by unit tests.
- [ ] Full traceback remains accessible.
- [ ] Chained exceptions are preserved and tested.
- [ ] A failure inside LucidLog does not hide the original exception.
- [ ] No network calls or local-variable capture occur by default.
- [ ] The original exception type and application termination behavior are preserved.
- [ ] Documentation states the supported Python versions and known limitations.
- [ ] CI passes on every supported Python version.

## 18. Implementation order

1. Create package metadata and the diagnostic data model.
2. Implement safe exception capture and source context.
3. Implement deterministic rules.
4. Implement compact and full renderers.
5. Add `diagnose()`, `exception()`, `install()`, and `uninstall()`.
6. Add exception-chain handling and JSON output.
7. Add tests for security and failure fallbacks.
8. Add standard-library logging integration.
9. Add thread and asyncio integrations.
10. Publish an alpha release and collect feedback.
11. Only then add Rich rendering, custom rule plugins, and AI explanations.

## 19. Non-goals for the first release

- Automatic code repair or code execution.
- Replacing the standard logging framework.
- Guaranteed identification of the true root cause.
- Automatic variable/local capture.
- Hosted dashboards or remote telemetry.
- Mandatory LLM providers.
- Capturing exceptions from every subprocess automatically.
- Claims of lower debugging time without user testing.

## 20. Product success criteria

Evaluate LucidLog against standard tracebacks and established traceback formatters using the same set of representative failures.

Track:
- correct location rate;
- cause-chain preservation;
- developer ability to identify the next debugging step;
- false or misleading suggestion rate;
- diagnostic latency;
- privacy-test failures;
- installation and integration friction.

The key measure is whether developers can understand and investigate failures faster without losing important evidence or exposing sensitive information.

---

**Recommended MVP boundary:** ship a lightweight, deterministic, source-aware Python diagnostic package first. Keep the diagnosis model stable and make every output interface consume it. Add AI only after the local diagnostic pipeline is reliable, secure, and useful on its own.
