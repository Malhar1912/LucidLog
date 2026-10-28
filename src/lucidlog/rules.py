from __future__ import annotations

from dataclasses import dataclass
import threading
from typing import Callable, Iterable, Protocol

from .models import Diagnosis, ExceptionContext


class RuleLike(Protocol):
    name: str

    def matches(self, error: ExceptionContext) -> bool:
        ...

    def diagnose(self, error: ExceptionContext) -> Diagnosis:
        ...


@dataclass(frozen=True)
class DiagnosticRule:
    name: str
    matches: Callable[[ExceptionContext], bool]
    diagnose: Callable[[ExceptionContext], Diagnosis]


def _contains_any(text: str, needles: Iterable[str]) -> bool:
    lower = (text or "").lower()
    return any(needle.lower() in lower for needle in needles)


def _is_exception(error: ExceptionContext, *names: str) -> bool:
    return error.exception_type in names or any(name in error.exception_mro for name in names)


def _default_rule(error: ExceptionContext) -> Diagnosis:
    return Diagnosis(
        summary="No built-in diagnosis",
        explanation="No built-in diagnosis is available for this exception type.",
        suggestion="Inspect the traceback and the local code path around the failing call.",
        confidence="low",
        origin="none",
        evidence=(f"Exception type was not matched by a built-in rule: {error.exception_type}.",),
    )


def _key_error_rule(error: ExceptionContext) -> Diagnosis:
    msg = error.message
    if "None" in msg or "null" in msg.lower():
        suggestion = "Check whether a nested object is None before indexing or key lookup."
    else:
        suggestion = "Confirm the expected keys and verify the dictionary-like object matches the data you are accessing."
    return Diagnosis(
        summary="A mapping key is missing.",
        explanation="A dictionary-like lookup did not find the requested key.",
        suggestion=suggestion,
        confidence="medium",
        origin="builtin_rule",
        evidence=(f"Key lookup failed for: {msg}",),
    )


def _attribute_error_rule(error: ExceptionContext) -> Diagnosis:
    msg = error.message
    if _contains_any(msg, ("NoneType", "has no attribute", "object has no attribute")):
        suggestion = "Check for None values, path assumptions, and object construction before attribute access."
    else:
        suggestion = "Verify the object type and inspect the code path that created or mutated it."
    return Diagnosis(
        summary="An attribute is missing.",
        explanation="The object being used does not define the attribute or method that was requested.",
        suggestion=suggestion,
        confidence="medium",
        origin="builtin_rule",
        evidence=(f"Attribute access failed: {msg}",),
    )


def _type_error_rule(error: ExceptionContext) -> Diagnosis:
    msg = error.message
    if _contains_any(msg, ("unsupported operand type", "not supported between instances", "bad operand type")):
        suggestion = "Review the arithmetic or comparison operation and confirm the operand types are compatible."
    elif _contains_any(msg, ("positional argument", "unexpected keyword", "missing required positional argument", "takes")):
        suggestion = "Check the callable signature and verify the call arguments match the function definition."
    else:
        suggestion = "Review the argument types and ensure the values you are passing match the expected contract."
    return Diagnosis(
        summary="A value or callable signature is incompatible.",
        explanation="Python detected a type mismatch or a call contract violation.",
        suggestion=suggestion,
        confidence="medium",
        origin="builtin_rule",
        evidence=(f"Type validation failed: {msg}",),
    )


def _index_error_rule(error: ExceptionContext) -> Diagnosis:
    return Diagnosis(
        summary="An index is out of bounds.",
        explanation="A sequence or container was accessed with a value outside its valid range.",
        suggestion="Check the bounds and validate index generation before addressing elements in the collection.",
        confidence="medium",
        origin="builtin_rule",
        evidence=(f"Sequence index failure: {error.message}",),
    )


def _zero_division_error_rule(error: ExceptionContext) -> Diagnosis:
    return Diagnosis(
        summary="Division by zero occurred.",
        explanation="An arithmetic operation attempted to divide by zero.",
        suggestion="Inspect the denominator and guard the calculation with a valid zero-check or input validation.",
        confidence="high",
        origin="builtin_rule",
        evidence=(f"ZeroDivisionError was raised: {error.message}",),
    )


def _file_system_rule(error: ExceptionContext) -> Diagnosis:
    name = error.exception_type
    if name == "PermissionError":
        summary = "A file permission is blocking access."
        explanation = "The process does not have enough permission to read or write the target path."
        suggestion = "Verify file ownership, ACLs, and the runtime account that is performing the operation."
    elif name in {"IsADirectoryError", "NotADirectoryError"}:
        summary = "The path type is wrong for the requested operation."
        explanation = "The code treated a directory as a file or vice versa."
        suggestion = "Inspect the target path and ensure it matches the operation's expected file system type."
    elif name == "FileNotFoundError":
        summary = "A file or path could not be found."
        explanation = "The code attempted to access a path that did not exist or was not available at runtime."
        suggestion = "Verify the path, working directory, and deployment artifacts before retrying."
    else:
        summary = "An I/O operation failed."
        explanation = "The system could not complete the requested file or path operation."
        suggestion = "Check the path, permissions, and environment-level configuration."
    return Diagnosis(
        summary=summary,
        explanation=explanation,
        suggestion=suggestion,
        confidence="medium",
        origin="builtin_rule",
        evidence=(f"File-system operation failed: {error.message}",),
    )


def _import_error_rule(error: ExceptionContext) -> Diagnosis:
    message = error.message
    if error.exception_type == "ModuleNotFoundError":
        summary = "An import failed because the module is unavailable."
        explanation = "Python could not locate a required module or package in the active environment."
        suggestion = "Check the Python environment, installed packages, and import path configuration."
    else:
        summary = "An import failed."
        explanation = "The runtime could not load one of the modules required by the application."
        suggestion = "Verify the package name, environment, and dependency installation before retrying."
    return Diagnosis(
        summary=summary,
        explanation=explanation,
        suggestion=suggestion,
        confidence="medium",
        origin="builtin_rule",
        evidence=(f"Import resolution failed: {message}",),
    )


def _value_error_rule(error: ExceptionContext) -> Diagnosis:
    msg = error.message
    suggestion = "Validate the data entering the failing operation and confirm it satisfies the expected constraints."
    if _contains_any(msg, ("out of range", "range", "must be", "invalid")):
        suggestion = "Inspect the value range and ensure the input stays within the accepted boundaries for the operation."
    return Diagnosis(
        summary="A value does not satisfy the operation requirements.",
        explanation="The code received or produced a value that is invalid for the operation being attempted.",
        suggestion=suggestion,
        confidence="medium",
        origin="builtin_rule",
        evidence=(f"Value validation failed: {msg}",),
    )


def _timeout_rule(error: ExceptionContext) -> Diagnosis:
    return Diagnosis(
        summary="An operation exceeded its time limit.",
        explanation="The program waited longer than the configured or default timeout threshold.",
        suggestion="Review the operation timeout configuration and the dependency or endpoint being contacted.",
        confidence="medium",
        origin="builtin_rule",
        evidence=(f"Timeout triggered: {error.message}",),
    )


def _connection_rule(error: ExceptionContext) -> Diagnosis:
    return Diagnosis(
        summary="A connection failure occurred.",
        explanation="A network or service connection could not be established or maintained.",
        suggestion="Check the endpoint, service availability, credentials, and network configuration.",
        confidence="medium",
        origin="builtin_rule",
        evidence=(f"Connection failure: {error.message}",),
    )


def _runtime_rule(error: ExceptionContext) -> Diagnosis:
    msg = error.message
    if _contains_any(msg, ("maximum recursion", "recursion", "too deep")):
        suggestion = "Look for infinite recursion or a cycle in the call path and add termination checks."
        summary = "The call stack exceeded the recursion limit."
        explanation = "The program recursively entered the same chain too many times."
    elif _contains_any(msg, ("memory", "out of memory")):
        suggestion = "Reduce object retention and avoid unbounded growth in memory-heavy data structures."
        summary = "The process ran out of memory."
        explanation = "The application consumed more memory than the runtime could allocate."
    else:
        suggestion = "Inspect the failing call path and verify that the state and control flow match the expected execution model."
        summary = "A runtime error occurred."
        explanation = "Python raised a runtime exception before the program could complete its execution flow."
    return Diagnosis(
        summary=summary,
        explanation=explanation,
        suggestion=suggestion,
        confidence="medium",
        origin="builtin_rule",
        evidence=(f"Runtime failure: {msg}",),
    )


def _assertion_rule(error: ExceptionContext) -> Diagnosis:
    return Diagnosis(
        summary="An assertion failed.",
        explanation="A defensive invariant or validation check did not hold at runtime.",
        suggestion="Review the failed assertion and the state it was validating to confirm the assumptions are still true.",
        confidence="medium",
        origin="builtin_rule",
        evidence=(f"Assertion failure: {error.message}",),
    )


def _overflow_rule(error: ExceptionContext) -> Diagnosis:
    return Diagnosis(
        summary="An arithmetic overflow or numerical range issue occurred.",
        explanation="A numeric operation produced a value that exceeded the supported range for the operation or type.",
        suggestion="Validate the magnitude of numeric inputs and ensure the operation stays within the relevant range limits.",
        confidence="medium",
        origin="builtin_rule",
        evidence=(f"Numeric overflow: {error.message}",),
    )


def _not_implemented_rule(error: ExceptionContext) -> Diagnosis:
    return Diagnosis(
        summary="An unimplemented path was reached.",
        explanation="The runtime hit a feature or branch that is intentionally not implemented yet.",
        suggestion="Check whether the execution path is missing a required implementation or a compatibility guard.",
        confidence="low",
        origin="builtin_rule",
        evidence=(f"Unimplemented branch triggered: {error.message}",),
    )


_BUILTIN_RULES: tuple[DiagnosticRule, ...] = (
    DiagnosticRule("KeyError", lambda error: error.exception_type == "KeyError", _key_error_rule),
    DiagnosticRule("AttributeError", lambda error: error.exception_type == "AttributeError", _attribute_error_rule),
    DiagnosticRule("TypeError", lambda error: error.exception_type == "TypeError", _type_error_rule),
    DiagnosticRule("IndexError", lambda error: error.exception_type == "IndexError", _index_error_rule),
    DiagnosticRule("ZeroDivisionError", lambda error: error.exception_type == "ZeroDivisionError", _zero_division_error_rule),
    DiagnosticRule("PermissionError", lambda error: error.exception_type == "PermissionError", _file_system_rule),
    DiagnosticRule("FileNotFoundError", lambda error: error.exception_type == "FileNotFoundError", _file_system_rule),
    DiagnosticRule("IsADirectoryError", lambda error: error.exception_type == "IsADirectoryError", _file_system_rule),
    DiagnosticRule("NotADirectoryError", lambda error: error.exception_type == "NotADirectoryError", _file_system_rule),
    DiagnosticRule("ImportError", lambda error: _is_exception(error, "ImportError"), _import_error_rule),
    DiagnosticRule("ModuleNotFoundError", lambda error: _is_exception(error, "ModuleNotFoundError"), _import_error_rule),
    DiagnosticRule("ValueError", lambda error: _is_exception(error, "ValueError"), _value_error_rule),
    DiagnosticRule("TimeoutError", lambda error: _is_exception(error, "TimeoutError"), _timeout_rule),
    DiagnosticRule("ConnectionError", lambda error: _is_exception(error, "ConnectionError"), _connection_rule),
    DiagnosticRule("AssertionError", lambda error: error.exception_type == "AssertionError", _assertion_rule),
    DiagnosticRule("RuntimeError", lambda error: error.exception_type == "RuntimeError", _runtime_rule),
    DiagnosticRule("OverflowError", lambda error: error.exception_type == "OverflowError", _overflow_rule),
    DiagnosticRule("NotImplementedError", lambda error: error.exception_type == "NotImplementedError", _not_implemented_rule),
    DiagnosticRule("OSError", lambda error: _is_exception(error, "OSError"), _file_system_rule),
    DiagnosticRule(
        "ExceptionGroup",
        lambda error: error.exception_type in {"ExceptionGroup", "BaseExceptionGroup"},
        lambda error: Diagnosis(
            summary="Multiple exceptions were raised together.",
            explanation="An exception group contains one or more failures that should be examined individually.",
            suggestion="Inspect each group member and its nested cause/context to identify the failing branches.",
            confidence="high",
            origin="builtin_rule",
            evidence=(f"Captured {len(error.causes)} top-level exception group members.",),
        ),
    ),
    DiagnosticRule("EOFError", lambda error: error.exception_type == "EOFError", lambda e: Diagnosis(
        summary="Unexpected end of input.",
        explanation="The program reached the end of an input stream before it received the expected data.",
        suggestion="Check whether the upstream source or file is truncated or whether control flow is exiting early.",
        confidence="medium",
        origin="builtin_rule",
        evidence=(f"Unexpected EOF: {e.message}",),
    )),
    DiagnosticRule("RecursionError", lambda error: error.exception_type == "RecursionError", lambda e: Diagnosis(
        summary="The call stack exceeded its recursion limit.",
        explanation="Python detected excessive recursive nesting before the call stack could unwind.",
        suggestion="Review recursive calls for missing termination conditions and cycle-prone execution paths.",
        confidence="medium",
        origin="builtin_rule",
        evidence=(f"Recursion limit reached: {e.message}",),
    )),
    DiagnosticRule("MemoryError", lambda error: error.exception_type == "MemoryError", lambda e: Diagnosis(
        summary="The process ran out of memory.",
        explanation="The application attempted to allocate more memory than the runtime could provide.",
        suggestion="Reduce object retention, stream large payloads, and avoid unbounded growth in collections.",
        confidence="medium",
        origin="builtin_rule",
        evidence=(f"Memory limit reached: {e.message}",),
    )),
)

_CUSTOM_RULES: list[DiagnosticRule] = []
_RULE_LOCK = threading.RLock()


def register_rule(rule: DiagnosticRule) -> None:
    if not rule.name.strip():
        raise ValueError("A diagnostic rule must have a non-empty name.")
    if not callable(rule.matches) or not callable(rule.diagnose):
        raise TypeError("A diagnostic rule must provide callable matches and diagnose functions.")
    with _RULE_LOCK:
        for index, existing in enumerate(_CUSTOM_RULES):
            if existing.name == rule.name:
                _CUSTOM_RULES[index] = rule
                break
        else:
            _CUSTOM_RULES.append(rule)


def list_rules() -> tuple[DiagnosticRule, ...]:
    with _RULE_LOCK:
        return tuple(_CUSTOM_RULES) + _BUILTIN_RULES


def clear_rules() -> None:
    with _RULE_LOCK:
        _CUSTOM_RULES.clear()


def default_diagnosis(error: ExceptionContext) -> Diagnosis:
    return _default_rule(error)


def diagnose_with_rules(error: ExceptionContext) -> Diagnosis:
    for rule in list_rules():
        if rule.matches(error):
            return rule.diagnose(error)
    return default_diagnosis(error)
