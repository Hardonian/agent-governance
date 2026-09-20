"""Agent Law Gateway — deterministic policy layer for Hermes tool-call governance.

Evaluates proposed tool calls against version-controlled laws before execution.
Fail-closed: if in doubt, block.
"""

from __future__ import annotations

import pathlib
from typing import Any

from .laws import (
    AgentLaw,
    Category,
    Condition,
    Enforcement,
    Severity,
)
from .policy_engine import EnforcementDecision, PolicyEngine
from .classifier import CommandClassifier, RiskLevel
from .secret_detector import SecretDetector
from .path_guard import PathGuard

__all__ = [
    # Models
    "AgentLaw",
    "Category",
    "Condition",
    "Enforcement",
    "Severity",
    "EnforcementDecision",
    "RiskLevel",
    # Components
    "PolicyEngine",
    "CommandClassifier",
    "SecretDetector",
    "PathGuard",
    # Convenience
    "load_default_engine",
    "evaluate",
]

_DEFAULT_LAWS_YAML = pathlib.Path(__file__).parent / "default_laws.yaml"


def load_default_engine(extra_laws: list[dict[str, Any]] | None = None) -> PolicyEngine:
    """Create a PolicyEngine pre-loaded with default_laws.yaml."""
    engine = PolicyEngine()
    engine.load_laws_from_yaml(_DEFAULT_LAWS_YAML)
    if extra_laws:
        engine.load_laws_from_dicts(extra_laws)
    return engine


def evaluate(
    command: str,
    paths: list[str] | None = None,
    context: dict[str, Any] | None = None,
    *,
    engine: PolicyEngine | None = None,
) -> EnforcementDecision:
    """One-shot convenience: evaluate a proposed action against default laws."""
    eng = engine or load_default_engine()
    return eng.evaluate(command=command, paths=paths or [], context=context or {})
