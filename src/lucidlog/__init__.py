"""LucidLog public API."""

from .api import (
    clear_diagnostic_rules,
    diagnose,
    exception,
    get_registered_rules,
    install,
    log_exception,
    register_diagnostic_rule,
    render_diagnostic,
    render_exception,
    uninstall,
)
from .config import Config, default_config
from .rules import DiagnosticRule, clear_rules, list_rules, register_rule

__all__ = [
    "Config",
    "DiagnosticRule",
    "clear_diagnostic_rules",
    "clear_rules",
    "default_config",
    "diagnose",
    "exception",
    "get_registered_rules",
    "install",
    "list_rules",
    "log_exception",
    "register_diagnostic_rule",
    "register_rule",
    "render_diagnostic",
    "render_exception",
    "uninstall",
]

__version__ = "0.1.2"
