from __future__ import annotations

from .models import Diagnostic


def diagnostic_to_dict(diagnostic: Diagnostic) -> dict:
    return diagnostic.to_dict()


def diagnostic_to_json(diagnostic: Diagnostic, **kwargs) -> str:
    return diagnostic.to_json(**kwargs)
