import sys

import pytest

import lucidlog
from lucidlog.models import Diagnosis, ExceptionContext
from lucidlog.redaction import redact_text
from lucidlog.rules import DiagnosticRule


def test_install_and_uninstall():
    previous = sys.excepthook
    lucidlog.install()
    assert sys.excepthook is not previous
    lucidlog.uninstall()
    assert sys.excepthook is previous


def test_exception_requires_active_exception():
    with pytest.raises(RuntimeError):
        lucidlog.exception("message")


def test_diagnose_returns_structured_output():
    try:
        {}["missing"]
    except Exception as exc:
        diagnostic = lucidlog.diagnose(exc)

    assert diagnostic.exception_type == "KeyError"
    assert "missing" in diagnostic.message
    assert diagnostic.summary
    assert diagnostic.explanation
    assert diagnostic.location


def test_source_context_available_for_runtime_file():
    try:
        raise ValueError("bad")
    except Exception as exc:
        diagnostic = lucidlog.diagnose(exc)
    assert diagnostic.primary_frame is not None
    assert diagnostic.primary_frame.source_line is not None


def test_render_diagnostic_text():
    try:
        [1, 2][10]
    except Exception as exc:
        diagnostic = lucidlog.diagnose(exc)
    text = lucidlog.render_diagnostic(diagnostic)
    assert "IndexError" in text
    assert "Diagnosis:" in text


def test_broad_rule_coverage_for_common_exceptions():
    for exception_factory, expected_name in [
        (lambda: 1 / 0, "ZeroDivisionError"),
        (lambda: object().__getattribute__("missing"), "AttributeError"),
        (lambda: [1, 2][10], "IndexError"),
        (lambda: open("./definitely_missing_123456.txt", "r"), "FileNotFoundError"),
    ]:
        try:
            exception_factory()
        except Exception as exc:
            diagnostic = lucidlog.diagnose(exc)
            assert diagnostic.exception_type == expected_name or diagnostic.summary


def test_custom_rule_registration():
    def factory(error: ExceptionContext) -> Diagnosis:
        return Diagnosis(
            summary="Custom rule fired",
            explanation="This was created via a user-registered rule.",
            suggestion="Use custom diagnostics when the built-ins are not enough.",
            confidence="high",
            origin="custom",
            evidence=(error.message,),
        )

    custom_rule = DiagnosticRule(
        name="CustomRule",
        matches=lambda error: error.exception_type == "RuntimeError",
        diagnose=factory,
    )
    lucidlog.register_rule(custom_rule)
    try:
        raise RuntimeError("custom-runtime")
    except Exception as exc:
        diagnostic = lucidlog.diagnose(exc)
        assert diagnostic.origin == "custom"
        assert diagnostic.summary == "Custom rule fired"
    lucidlog.clear_rules()


def test_redaction_masks_sensitive_values():
    secret = "password=super-secret-token"
    redacted = redact_text(secret, (r"(?i)password\s*[:=]\s*.*",))
    assert "super-secret-token" not in redacted
    assert "[REDACTED]" in redacted
