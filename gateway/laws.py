"""Agent Law models — Pydantic v2 definitions for governance rules."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class Severity(str, Enum):
    CRITICAL = "CRITICAL"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class Category(str, Enum):
    FILESYSTEM = "FILESYSTEM"
    GIT = "GIT"
    SHELL = "SHELL"
    SECRETS = "SECRETS"
    INFRASTRUCTURE = "INFRASTRUCTURE"
    DATABASE = "DATABASE"
    NETWORK = "NETWORK"


class Enforcement(str, Enum):
    BLOCK = "BLOCK"
    WARN = "WARN"
    LOG = "LOG"


class Condition(BaseModel):
    """A single predicate evaluated against a proposed action.

    Supported predicate types:
      - command_matches: regex against the raw command string
      - risk_level_at_least: minimum RiskLevel the command must reach
      - path_in: list of directory prefixes that trigger this condition
      - path_outside_allowed: true if any path is outside allowed roots
      - secret_detected: true if secrets are found in command/context
      - category: matches the command's Category classification
      - context_key_present: a key that must exist in the action context
      - context_value_equals: {key: expected_value} pair
    """

    command_matches: str | None = None
    risk_level_at_least: str | None = None
    path_in: list[str] | None = None
    path_outside_allowed: bool | None = None
    secret_detected: bool | None = None
    category: str | None = None
    context_key_present: str | None = None
    context_value_equals: dict[str, Any] | None = None


class AgentLaw(BaseModel):
    """A single governance law evaluated against proposed tool calls."""

    law_id: str = Field(..., pattern=r"^[A-Z][A-Z0-9_]{2,63}$")
    description: str
    severity: Severity
    category: Category
    conditions: list[Condition] = Field(..., min_length=1)
    enforcement: Enforcement

    # All conditions must match (AND semantics) for the law to fire.
    # If a law fires, its enforcement action is applied.
