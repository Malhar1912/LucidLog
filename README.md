# LucidLog

![CI](https://github.com/Malhar1912/LucidLog/actions/workflows/ci.yml/badge.svg)
![PyPI](https://img.shields.io/pypi/v/lucidlog.svg)
![Python](https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12-blue)

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

## Responsibility intelligence (Git-aware)

When the code is running inside a Git repository, LucidLog can attach attribution metadata for the exact source line that raised the exception. The diagnostic records the author name, author email, commit ID, short summary, date, ownership metadata, confidence, and source for that line when available.

```python
try:
    raise RuntimeError("something broke")
except RuntimeError as exc:
    diagnostic = lucidlog.diagnose(exc)
    frame = diagnostic.primary_frame
    if frame and frame.responsibility:
        print(frame.responsibility.author)
        print(frame.responsibility.team)
        print(frame.responsibility.owner)
        print(frame.responsibility.commit_id)
        print(frame.responsibility.confidence)
```

Ownership metadata supports line-level blame, file-level fallback, and CODEOWNERS resolution. You can also configure privacy flags through `lucidlog.Config` such as `include_email`, `include_full_author_name`, and `redact_private_identity`.

### Ownership mappings and escalation

CODEOWNERS is resolved using the last matching rule, and multiple owners are retained. Explicit mappings can add teams and escalation contacts:

```python
config = lucidlog.Config(
    team_mapping=(("malhar", "platform"),),
    ownership_mapping=(("src/payments/*", ("@payments", "on-call-payments")),),
    escalation_paths=(("platform", ("platform-lead", "platform-on-call", "service-owner")),),
)
```

`OwnershipResolver` can also be used independently for configured CODEOWNERS files and arbitrary repositories.

### Change, PR, issue, deployment, and incident context

`analyze_responsibility(path, line)` returns a `ResponsibilityReport` with local Git blame, contribution timeline, a bounded diff preview, change-risk and ownership-freshness estimates, confidence-aware responsibility/actionability score, owner/reviewer/escalation recommendations, deployment correlation, and matching incident history. Pass `diagnostic=...` to include exception details and correlate a runtime diagnostic with incident history. It works offline by default. Change-risk values are deterministic heuristics over recent commit/churn data, not predictions or service-level guarantees.

GitHub/GitLab enrichment is explicit and requires a token passed in memory. Constructing clients or running `diagnose()` does not make network requests:

```python
import os

provider = lucidlog.GitHubProvider(token=os.environ["GITHUB_TOKEN"])
report = lucidlog.analyze_responsibility("src/app.py", 42, provider=provider)
```

Use `GitLabProvider` with the project path and its matching Git remote. Providers use HTTPS, bounded response sizes, and finite timeouts; provider failures raise `ProviderError`. Tokens should come from a secret manager or environment variable and must not be written to source code or logs.

For monorepos or multiple repositories, analyze paths together and key provider/service/environment configuration by the detected `owner/repository` remote:

```python
reports = lucidlog.analyze_responsibilities(
    [("services/payments.py", 42), ("services/search.py", 88)],
    providers={"acme/payments": provider},
    services={"acme/payments": "payments", "acme/search": "search"},
    environments={"acme/payments": "production"},
)
```

For multi-service runtime context, pass a `DeploymentRegistry` to correlate a known commit with service/environment deployments. `IncidentStore("incidents.sqlite3")` opts into local SQLite history; only identifiers, location, and ownership/runtime metadata are stored—exception messages and tracebacks are not. Use `ownership_heatmap()` and `responsibility_graph()` for structured data, or `render_responsibility_dashboard()` for a standalone escaped HTML dashboard.

Slack and Microsoft Teams webhooks are also explicit operations:

```python
notifier = lucidlog.SlackWebhookNotifier(os.environ["SLACK_WEBHOOK_URL"])
notifier.send(report)
```

Notification content is redacted using the supplied `Config` (or its safe defaults). Delivery failures raise `NotificationError`. Review organization policies before sending diagnostic metadata to third-party services.

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

The suite covers chained and grouped exceptions, cycle/size limits, redaction, terminal colors, hook restoration, threading, asyncio, structured logging, Git ownership and risk analysis, provider response handling, incident persistence, dashboard escaping, and uncaught-process exit behavior.

## Project structure

```text
LucidLog/
├── .github/workflows/   # CI and release automation
├── docs/                # project documentation and changelog
├── examples/            # end-to-end examples and demonstrations
├── scripts/             # local release and publishing helpers
├── src/lucidlog/        # library package source
├── tests/               # unit and integration tests
├── .gitignore
├── .pypirc.example      # local PyPI credentials template (do not commit secrets)
├── README.md
├── pyproject.toml
├── pytest.ini
└── LICENSE              # if you choose to add one in the future
```

## Release workflow

Use the local helper script to build, validate, and publish the package:

```bash
python scripts/publish_release.py --repository pypi
```

For a dry validation run without uploading:

```bash
python scripts/publish_release.py --repository pypi --no-upload
```

Store real PyPI credentials in your user-level `.pypirc` file outside the repo, never in Git.

## Notes

- The core package is standard-library only.
- It does not capture local variables by default.
- It keeps the original traceback available and does not replace the application's original exception flow.
