# LucidLog

LucidLog is a lightweight, source-aware exception diagnostic library for Python.

It keeps the original exception behavior intact, adds a clearer reading of the relevant traceback frame, and provides deterministic built-in explanations without requiring a remote service.

## Quick start

```python
import lucidlog

lucidlog.install()

try:
    value = {}["missing"]
except Exception:
    lucidlog.exception("Lookup failed")
```

## Complete example

Run the bundled demo for a full tour of the library's features:

```bash
python examples/complete_demo.py
```

The demo covers chained exceptions, redaction, render output, custom rules, logging payloads, and the threading/asyncio integrations.

## Public API

- `lucidlog.install()`
- `lucidlog.uninstall()`
- `lucidlog.exception(message, *, stream=None, full=False, config=None)`
- `lucidlog.diagnose(exc, *, config=None)`
- `lucidlog.log_exception(logger, message, *, full=False, config=None)`
- `lucidlog.render_diagnostic(diagnostic, *, full=False, stream=None, color=None)`

## Configuration and output

Pass a frozen `lucidlog.Config` to `install`, `diagnose`, `exception`, or `log_exception` to configure capture and output:

```python
import lucidlog
from lucidlog import Config

lucidlog.install(
    config=Config(
        color=True,
        include_full_traceback=True,
        max_chain_depth=6,
        redact_patterns=(r"(?i)customer-id=[A-Z0-9]+",),
    )
)
```

Colors are disabled for non-interactive streams by default and honor `NO_COLOR` and `TERM=dumb`. `color=True` forces ANSI output; `color=False` disables it. Exception colors distinguish common diagnostic families, while the severity badge uses a separate palette (`critical`, `error`, `warning`, or `info`). The JSON representation contains the plain text values, never ANSI codes.

Cause/context chains and exception groups are represented as bounded nested data and included in the compact output. Limits (`max_chain_depth`, `max_group_exceptions`, `max_frames`, `max_message_length`, `max_traceback_chars`, `max_source_file_bytes`, and `max_diagnosis_evidence`) constrain diagnostic capture. `Diagnostic.to_dict()` and `to_json()` include a schema version, severity, fingerprint-like ID, cause relationships, and truncation indicator. Setting `include_local_variables=True` or `allow_network=True` is rejected; those capabilities are deliberately not part of the core.

The optional runtime integrations are explicit:

```python
from lucidlog.integrations.threading import install_threading_hook
from lucidlog.integrations.asyncio import install_asyncio_exception_handler

install_threading_hook()
install_asyncio_exception_handler()
```

Each has a corresponding `uninstall_*` function. Asyncio leaves an already-installed handler in place unless `chain_previous=True`; thread and main hooks restore prior handlers when removed. Worker-thread and event-loop hooks do not cover child processes.

## Privacy notes

LucidLog never reads local variables or makes network requests. It redacts common credential-shaped strings and user-configured patterns from messages, source context, causes, and traceback text before those values enter the diagnostic model. Absolute paths under the home directory are shortened by default; `root_path` can shorten project paths. Redaction is best-effort: exception messages and source code are arbitrary application data, so review output before storing or forwarding it and supply additional patterns for your environment. `Config` validates regex syntax and output limits up front.

Logging integration writes one structured log record with a `lucidlog` dictionary in `LogRecord` extra fields. It deliberately omits `exc_info` to avoid bypassing redaction with a raw traceback. The original exception is not modified, and `log_exception` requires an active exception handler.

## Tests

Run the unit and integration tests with:

```bash
python -m pytest
```

The suite covers chained and grouped exceptions, cycle/size limits, redaction, terminal colors, hook restoration, threading, asyncio, structured logging, and uncaught-process exit behavior.

## Notes

- The core package is standard-library only.
- It does not capture local variables by default.
- It keeps the original traceback available and does not replace the application's original exception flow.
