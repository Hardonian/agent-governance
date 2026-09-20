"""Policy engine — evaluates proposed actions against loaded laws, returns EnforcementDecision."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

import yaml

from .classifier import CommandClassifier, RiskLevel
from .laws import AgentLaw, Category, Condition, Enforcement, Severity
from .path_guard import PathGuard
from .secret_detector import SecretDetector


@dataclass
class EnforcementDecision:
    """Result of evaluating a proposed action against all loaded laws."""
    allowed: bool
    law_id: Optional[str] = None       # The law that triggered (None if allowed)
    reason: Optional[str] = None
    severity: Optional[Severity] = None
    enforcement: Optional[Enforcement] = None
    evidence: Dict[str, Any] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)  # WARN-only laws that fired


class PolicyEngine:
    """Evaluates proposed actions against version-controlled laws.

    Fail-closed: if anything is uncertain, block.
    """

    def __init__(
        self,
        allowed_roots: Sequence[str] | None = None,
        extra_blocked_paths: Sequence[str] | None = None,
    ):
        self._laws: List[AgentLaw] = []
        self._classifier = CommandClassifier()
        self._secret_detector = SecretDetector()
        self._path_guard = PathGuard(
            allowed_roots=allowed_roots,
            extra_blocked=extra_blocked_paths,
        )

    def load_laws_from_yaml(self, path: Path | str) -> int:
        """Load laws from a YAML file. Returns count of laws loaded."""
        with open(path, "r") as f:
            data = yaml.safe_load(f)
        return self.load_laws_from_dicts(data if isinstance(data, list) else data.get("laws", []))

    def load_laws_from_dicts(self, dicts: List[Dict[str, Any]]) -> int:
        """Load laws from a list of dicts. Returns count loaded."""
        count = 0
        for d in dicts:
            law = AgentLaw.model_validate(d)
            self._laws.append(law)
            count += 1
        return count

    def add_law(self, law: AgentLaw) -> None:
        """Add a single law."""
        self._laws.append(law)

    def _evaluate_condition(
        self,
        cond: Condition,
        command: str,
        paths: List[str],
        context: Dict[str, Any],
        risk_level: RiskLevel,
        category: Category,
    ) -> bool:
        """Evaluate a single condition against the action context. Returns True if matched."""

        # command_matches: regex against the raw command
        if cond.command_matches is not None:
            if not re.search(cond.command_matches, command):
                return False

        # risk_level_at_least: minimum risk level
        if cond.risk_level_at_least is not None:
            try:
                min_level = RiskLevel[cond.risk_level_at_least]
            except KeyError:
                return False
            if risk_level < min_level:
                return False

        # path_in: any path starts with one of the given prefixes
        if cond.path_in is not None:
            found = False
            for p in paths:
                for prefix in cond.path_in:
                    if p.startswith(prefix):
                        found = True
                        break
                if found:
                    break
            if not found:
                return False

        # path_outside_allowed: true if any path has violations
        if cond.path_outside_allowed is not None:
            violations = self._path_guard.validate_all(paths)
            has_outside = any(v.rule in ("OUTSIDE_ROOT", "BLOCKED_PREFIX") for v in violations)
            if cond.path_outside_allowed != has_outside:
                return False

        # secret_detected: true if secrets found in command or context
        if cond.secret_detected is not None:
            has_secret = self._secret_detector.has_secrets(command)
            if not has_secret:
                # Also check context values
                for v in context.values():
                    if isinstance(v, str) and self._secret_detector.has_secrets(v):
                        has_secret = True
                        break
            if cond.secret_detected != has_secret:
                return False

        # category: matches the command's category
        if cond.category is not None:
            if category.value != cond.category:
                return False

        # context_key_present: a key that must exist in context
        if cond.context_key_present is not None:
            if cond.context_key_present not in context:
                return False

        # context_value_equals: {key: expected_value} pair
        if cond.context_value_equals is not None:
            for key, expected in cond.context_value_equals.items():
                if context.get(key) != expected:
                    return False

        return True

    def _classify_category(self, command: str) -> Category:
        """Derive the Category from the command."""
        base = command.strip().split()[0] if command.strip() else ""
        # Simple heuristic mapping
        if base == "git":
            return Category.GIT
        if base in ("rm", "cp", "mv", "mkdir", "touch", "ln", "chmod", "chown", "find", "ls", "cat", "sed", "awk"):
            return Category.FILESYSTEM
        if base in ("systemctl", "journalctl", "apt", "apt-get", "yum", "dnf", "pacman", "brew", "mount", "umount", "iptables", "nft", "sysctl"):
            return Category.INFRASTRUCTURE
        if base in ("psql", "mysql", "mongo", "redis-cli", "sqlite3", "dropdb", "createdb"):
            return Category.DATABASE
        if base in ("curl", "wget", "ssh", "scp", "rsync", "nc", "ncat", "nmap"):
            return Category.NETWORK
        # Default: SHELL
        return Category.SHELL

    def evaluate(
        self,
        command: str,
        paths: List[str],
        context: Dict[str, Any] | None = None,
    ) -> EnforcementDecision:
        """Evaluate a proposed action against all loaded laws.

        Args:
            command: the shell command or tool action string
            paths: file paths referenced by the action
            context: additional context (e.g. {"user": "agent", "session": "..."})

        Returns:
            EnforcementDecision with allowed, reason, severity, evidence
        """
        ctx = context or {}

        # Step 1: Classify the command.
        risk_level = self._classifier.classify(command)
        category = self._classify_category(command)

        # Step 2: Scan for secrets in the command.
        secret_matches = self._secret_detector.scan(command)

        # Step 3: Validate paths.
        path_violations = self._path_guard.validate_all(paths)

        # Step 4: Build evidence for decision.
        evidence: Dict[str, Any] = {
            "risk_level": risk_level.name,
            "category": category.value,
            "secret_count": len(secret_matches),
            "path_violation_count": len(path_violations),
        }
        if path_violations:
            evidence["path_violations"] = [
                {"path": v.path, "rule": v.rule, "detail": v.detail}
                for v in path_violations
            ]
        if secret_matches:
            evidence["secrets_found"] = [
                {"kind": s.kind, "redacted": s.redacted}
                for s in secret_matches
            ]

        # Step 5: Evaluate all laws. Collect BLOCKs and WARNs.
        # Laws are evaluated in order; the most severe BLOCK wins.
        block_decision: Optional[EnforcementDecision] = None
        warnings: List[str] = []

        for law in self._laws:
            # All conditions must match (AND semantics).
            all_match = True
            for cond in law.conditions:
                if not self._evaluate_condition(cond, command, paths, ctx, risk_level, category):
                    all_match = False
                    break

            if all_match:
                if law.enforcement == Enforcement.BLOCK:
                    decision = EnforcementDecision(
                        allowed=False,
                        law_id=law.law_id,
                        reason=law.description,
                        severity=law.severity,
                        enforcement=law.enforcement,
                        evidence=evidence,
                    )
                    # Keep the most severe block (CRITICAL > HIGH > MEDIUM > LOW).
                    if block_decision is None or (
                        decision.severity and block_decision.severity
                        and decision.severity.value < block_decision.severity.value
                    ):
                        block_decision = decision
                elif law.enforcement == Enforcement.WARN:
                    warnings.append(f"[{law.law_id}] {law.description}")
                # LOG: silently noted in evidence.

        # Step 6: Fail-closed — if path violations exist and no explicit BLOCK fired, block anyway.
        if block_decision is None and path_violations:
            block_decision = EnforcementDecision(
                allowed=False,
                law_id="PATH_GUARD_DEFAULT",
                reason="Path validation failed — fail-closed enforcement",
                severity=Severity.HIGH,
                enforcement=Enforcement.BLOCK,
                evidence=evidence,
            )

        # Step 7: If secrets detected and no explicit BLOCK fired, block.
        if block_decision is None and secret_matches:
            block_decision = EnforcementDecision(
                allowed=False,
                law_id="SECRET_DETECTED_DEFAULT",
                reason="Secret/credential detected in command — blocked by default",
                severity=Severity.CRITICAL,
                enforcement=Enforcement.BLOCK,
                evidence=evidence,
            )

        # Return the block decision if any; otherwise allowed with warnings.
        if block_decision:
            block_decision.warnings = warnings
            return block_decision

        return EnforcementDecision(
            allowed=True,
            law_id=None,
            reason=None,
            severity=None,
            enforcement=None,
            evidence=evidence,
            warnings=warnings,
        )
