import asyncio
import io
import logging
import os
import re
import runpy
import subprocess
import sys
import threading
from pathlib import Path

import pytest

import lucidlog
import lucidlog.api as api
from lucidlog.config import Config
from lucidlog.integrations.asyncio import (
    install_asyncio_exception_handler,
    uninstall_asyncio_exception_handler,
)
from lucidlog.integrations.threading import install_threading_hook, uninstall_threading_hook
from lucidlog.models import Diagnosis
from lucidlog.rules import DiagnosticRule


def test_explicit_and_implicit_exception_chains_are_distinguished():
    try:
        try:
            raise KeyError("source")
        except KeyError as original:
            raise ValueError("outer") from original
    except ValueError as error:
        diagnostic = lucidlog.diagnose(error)

    assert diagnostic.causes[0].relation == "cause"
    assert diagnostic.causes[0].exception_type == "KeyError"
    assert diagnostic.causes[0].frames
    assert "Caused by" in diagnostic.formatted_traceback

    try:
        try:
            raise KeyError("implicit")
        except KeyError:
            raise ValueError("outer")
    except ValueError as error:
        diagnostic = lucidlog.diagnose(error)

    assert diagnostic.causes[0].relation == "context"


def test_exception_cycles_and_chain_limits_are_bounded():
    first = ValueError("first")
    second = RuntimeError("second")
    first.__cause__ = second
    second.__cause__ = first

    diagnostic = lucidlog.diagnose(first, config=Config(max_chain_depth=1))
    assert len(diagnostic.causes) == 1
    assert diagnostic.causes_truncated


def test_exception_groups_capture_bounded_members():
    group_type = getattr(__import__("builtins"), "ExceptionGroup", None)
    if group_type is None:
        pytest.skip("ExceptionGroup requires Python 3.11 or newer")

    try:
        raise group_type("group", [ValueError("one"), TypeError("two")])
    except BaseException as error:
        diagnostic = lucidlog.diagnose(error, config=Config(max_group_exceptions=1))

    assert diagnostic.causes[0].relation == "group_member"
    assert len(diagnostic.causes) == 1
    assert diagnostic.causes_truncated
    assert diagnostic.to_dict()["causes"][0]["exception_type"] == "ValueError"
    assert diagnostic.summary == "Multiple exceptions were raised together."


def test_builtin_rules_match_exception_subclasses():
    diagnostic = lucidlog.diagnose(ConnectionRefusedError("service unavailable"))
    assert diagnostic.summary == "A connection failure occurred."


def test_redaction_applies_to_json_compact_and_full_output():
    secret = "customer-48291"
    try:
        raise RuntimeError(f"request failed: {secret}")
    except RuntimeError as error:
        diagnostic = lucidlog.diagnose(
            error,
            config=Config(redact_patterns=(r"customer-\d+",)),
        )

    stream = io.StringIO()
    lucidlog.render_diagnostic(diagnostic, full=True, stream=stream, color=False)
    assert secret not in diagnostic.to_json()
    assert secret not in stream.getvalue()
    assert "[REDACTED]" in stream.getvalue()


def test_default_secret_redaction_and_message_limits():
    diagnostic = None
    try:
        raise ValueError('password="super secret value" ' + ("x" * 100))
    except ValueError as error:
        diagnostic = lucidlog.diagnose(error, config=Config(max_message_length=32))

    assert "super secret value" not in diagnostic.message
    assert len(diagnostic.message) < 64
    assert "super secret value" not in diagnostic.formatted_traceback


def test_source_context_is_redacted_before_serialization(tmp_path):
    source = tmp_path / "source_secret.py"
    secret = "source-secret-123"
    source.write_text(f'raise RuntimeError("failure")  # api_key={secret}\n', encoding="utf-8")
    try:
        runpy.run_path(str(source))
    except RuntimeError as error:
        diagnostic = lucidlog.diagnose(error)

    assert secret not in diagnostic.to_json()
    assert "[REDACTED]" in diagnostic.to_json()


def test_source_context_omits_large_and_missing_files(tmp_path):
    large_source = tmp_path / "large.py"
    large_source.write_text("x = 1\n" * 100, encoding="utf-8")
    missing_source = tmp_path / "missing.py"
    for filename in (str(large_source), str(missing_source)):
        code = compile("raise ValueError('source unavailable')", filename, "exec")
        try:
            exec(code, {})
        except ValueError as error:
            diagnostic = lucidlog.diagnose(
                error,
                config=Config(max_source_file_bytes=128),
            )
        assert diagnostic.primary_frame is not None
        assert diagnostic.primary_frame.source_line is None


def test_config_rejects_invalid_limits_and_regexes():
    with pytest.raises(ValueError):
        Config(max_frames=0)
    with pytest.raises(re.error):
        Config(redact_patterns=("[",))
    with pytest.raises(ValueError):
        Config(include_local_variables=True)
    with pytest.raises(ValueError):
        Config(allow_network=True)


def test_default_path_policy_shortens_home_directory():
    try:
        raise ValueError("path policy")
    except ValueError as error:
        diagnostic = lucidlog.diagnose(error)

    assert str(Path.home()) not in diagnostic.to_json()


def test_colors_are_opt_in_for_non_tty_streams(monkeypatch):
    try:
        raise ValueError("colored")
    except ValueError as error:
        diagnostic = lucidlog.diagnose(error)

    stream = io.StringIO()
    lucidlog.render_diagnostic(diagnostic, stream=stream)
    assert "\x1b[" not in stream.getvalue()

    colored_stream = io.StringIO()
    text = lucidlog.render_diagnostic(diagnostic, stream=colored_stream, color=True)
    assert "\x1b[33m" in text

    monkeypatch.setenv("NO_COLOR", "")
    no_color_stream = io.StringIO()
    lucidlog.render_diagnostic(diagnostic, stream=no_color_stream, color=None)
    assert "\x1b[" not in no_color_stream.getvalue()


def test_severity_is_included_in_json_and_rendering():
    try:
        raise MemoryError("allocation failed")
    except MemoryError as error:
        diagnostic = lucidlog.diagnose(error)

    assert diagnostic.severity == "critical"
    assert diagnostic.to_dict()["severity"] == "critical"
    assert "[CRITICAL]" in lucidlog.render_diagnostic(diagnostic, stream=io.StringIO(), color=False)


def test_diagnostic_rule_does_not_retain_live_traceback_frames():
    try:
        raise ValueError("frame check")
    except ValueError as error:
        diagnostic = lucidlog.diagnose(error)

    assert all(not hasattr(frame, "tb_frame") for frame in diagnostic.frames)


def test_logger_emits_one_sanitized_structured_record(caplog):
    logger = logging.getLogger("lucidlog-enterprise-test")
    logger.setLevel(logging.ERROR)
    with caplog.at_level(logging.ERROR, logger=logger.name):
        try:
            raise ValueError("token=log-secret")
        except ValueError:
            diagnostic = lucidlog.log_exception(logger, "operation failed")

    assert len(caplog.records) == 1
    assert caplog.records[0].exc_info is None
    assert caplog.records[0].lucidlog["diagnostic_id"] == diagnostic.diagnostic_id
    assert "log-secret" not in repr(caplog.records[0].lucidlog)


def test_custom_rule_output_is_redacted():
    rule = DiagnosticRule(
        name="SensitiveOutput",
        matches=lambda context: context.exception_type == "LookupError",
        diagnose=lambda context: Diagnosis(
            summary="token=plugin-output-secret",
            explanation="Custom explanation",
            suggestion=None,
            confidence="medium",
            origin="plugin",
            evidence=("api_key=plugin-output-secret", context.exception_type, "third item"),
        ),
    )
    lucidlog.register_rule(rule)
    try:
        try:
            raise LookupError("safe message")
        except LookupError as error:
            diagnostic = lucidlog.diagnose(
                error,
                config=Config(
                    redact_patterns=(r"plugin-output-secret",),
                    max_diagnosis_evidence=2,
                ),
            )
        assert "plugin-output-secret" not in diagnostic.to_json()
        assert "[REDACTED]" in diagnostic.to_json()
        assert len(diagnostic.diagnosis.evidence) == 2
        assert diagnostic.diagnosis.evidence[-1].startswith("Additional rule evidence")
    finally:
        lucidlog.clear_rules()


def test_threading_hook_renders_and_restores(monkeypatch):
    stream = io.StringIO()
    default_hook = threading.__excepthook__
    monkeypatch.setattr(threading, "excepthook", default_hook)
    previous = threading.excepthook
    install_threading_hook(config=Config(output_stream=stream, color=False))
    install_threading_hook(config=Config(output_stream=stream, color=False))
    try:
        def fail():
            raise ValueError("thread failure")

        worker = threading.Thread(target=fail)
        worker.start()
        worker.join()
        assert not worker.is_alive()
        assert "thread failure" in stream.getvalue()
    finally:
        uninstall_threading_hook()
    assert threading.excepthook is previous


def test_asyncio_handler_renders_and_restores():
    loop = asyncio.new_event_loop()
    stream = io.StringIO()
    try:
        install_asyncio_exception_handler(
            loop,
            config=Config(output_stream=stream, color=False),
        )
        loop.call_exception_handler(
            {"message": "unhandled test error", "exception": ValueError("async failure")}
        )
        assert "async failure" in stream.getvalue()
        uninstall_asyncio_exception_handler(loop)
        assert loop.get_exception_handler() is None
    finally:
        uninstall_asyncio_exception_handler(loop)
        loop.close()


def test_main_hook_subprocess_preserves_uncaught_exit_status():
    project_root = Path(__file__).resolve().parents[1]
    source_root = project_root / "src"
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(source_root) + os.pathsep + environment.get("PYTHONPATH", "")
    process = subprocess.run(
        [
            sys.executable,
            "-c",
            "import lucidlog; lucidlog.install(); raise ValueError('subprocess failure')",
        ],
        cwd=project_root,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )

    assert process.returncode == 1
    assert "ValueError" in process.stderr
    assert "subprocess failure" in process.stderr
    assert "\x1b[" not in process.stderr


def test_uninstall_does_not_clobber_a_later_hook():
    previous = sys.excepthook
    lucidlog.install()
    replacement = lambda *args: bool(args)
    sys.excepthook = replacement
    try:
        lucidlog.uninstall()
        assert sys.excepthook is replacement
    finally:
        sys.excepthook = previous


def test_main_hook_falls_back_to_previous_hook_when_diagnosis_fails(monkeypatch):
    observed = []
    error = ValueError("fallback")

    def previous_hook(exc_type, exc_value, exc_traceback):
        observed.append((exc_type, exc_value, exc_traceback))

    def broken_diagnosis(*args, **kwargs):
        raise RuntimeError(f"diagnostic failure {len(args)} {len(kwargs)}")

    monkeypatch.setattr(api, "_previous_excepthook", previous_hook)
    monkeypatch.setattr(api, "build_diagnostic", broken_diagnosis)
    api._handle_uncaught(type(error), error, error.__traceback__)

    assert observed == [(ValueError, error, None)]
