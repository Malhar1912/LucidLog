from __future__ import annotations

import asyncio
import logging
import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

import lucidlog
from lucidlog import Config
from lucidlog.integrations.asyncio import (
    install_asyncio_exception_handler,
    uninstall_asyncio_exception_handler,
)
from lucidlog.integrations.threading import (
    install_threading_hook,
    uninstall_threading_hook,
)
from lucidlog.models import Diagnosis
from lucidlog.rules import DiagnosticRule


def show(title: str) -> None:
    print(f"\n{'=' * 84}\n{title}\n{'=' * 84}")


def broken_lookup() -> None:
    try:
        raise KeyError("customer_id=customer-48291")
    except KeyError as source:
        raise ValueError("request failed for api_key=prod-secret-123") from source


async def async_failure() -> None:
    await asyncio.sleep(0.01)
    raise TimeoutError("token=async-session-token")


config = Config(
    color=True,
    include_full_traceback=True,
    max_chain_depth=5,
    max_message_length=300,
    redact_patterns=(
        r"(?i)(customer[_ -]?id|api[_ -]?key|token)\s*[:=]\s*[^,\s;]+",
    ),
)


custom_rule = DiagnosticRule(
    name="BusinessLookupRule",
    matches=lambda ctx: ctx.exception_type == "LookupError",
    diagnose=lambda ctx: Diagnosis(
        summary="A business lookup failed.",
        explanation="The application could not resolve a required business object from the expected source of truth.",
        suggestion="Verify the input key, upstream data source, and service configuration before retrying.",
        confidence="high",
        origin="plugin",
        evidence=("required lookup key not present", "customer_id=missing-entity"),
    ),
)


def main() -> None:
    lucidlog.install(config=config)
    lucidlog.register_diagnostic_rule(custom_rule)
    install_threading_hook(config=config)

    show("1) Exception chain, rendering, and redaction")
    try:
        broken_lookup()
    except Exception as exc:
        diagnostic = lucidlog.diagnose(exc, config=config)
        print(lucidlog.render_diagnostic(diagnostic, full=True, color=True))
        print("\nJSON payload:")
        print(diagnostic.to_json(indent=2))

    show("2) Custom rule override")
    try:
        raise LookupError("customer_id=customer-00042")
    except LookupError as exc:
        diagnostic = lucidlog.diagnose(exc, config=config)
        print(lucidlog.render_diagnostic(diagnostic, full=False, color=True))

    show("3) Structured logging payload")
    logger = logging.getLogger("lucidlog.demo")
    logger.setLevel(logging.ERROR)
    logger.handlers.clear()
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(handler)
    try:
        raise RuntimeError("password=super-secret-logger-value")
    except RuntimeError:
        diagnostic = lucidlog.log_exception(logger, "database write failed", config=config)
        print("\nLogger record includes a sanitized `lucidlog` payload:")
        print(diagnostic.to_json(indent=2))

    show("4) Git responsibility metadata")
    try:
        raise RuntimeError("responsibility metadata demo")
    except RuntimeError as exc:
        diagnostic = lucidlog.diagnose(exc, config=config)
        frame = diagnostic.primary_frame
        responsibility = frame.responsibility if frame else None
        print("Git responsibility metadata for the failing line:")
        if responsibility is None:
            print("  Not available: file is not under Git or blame metadata could not be resolved.")
        else:
            print(f"  author: {responsibility.author}")
            print(f"  email: {responsibility.email}")
            print(f"  commit: {responsibility.commit_id}")
            print(f"  summary: {responsibility.summary}")
            print(f"  date: {responsibility.date}")
        print(diagnostic.to_json(indent=2))

    show("5) Thread hook integration")

    def worker() -> None:
        raise RuntimeError("token=threaded-worker-secret")

    thread = threading.Thread(target=worker)
    thread.start()
    thread.join()

    show("6) Asyncio hook integration")

    async def runner() -> None:
        install_asyncio_exception_handler(config=config)
        try:
            await async_failure()
        except Exception:
            pass
        finally:
            uninstall_asyncio_exception_handler()

    asyncio.run(runner())

    lucidlog.uninstall()
    uninstall_threading_hook()


if __name__ == "__main__":
    main()
