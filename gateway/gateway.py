"""Agent Law Gateway - command classification, path guarding, secret detection, policy enforcement."""

import re
import shlex
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Optional

import yaml


class CommandClass(Enum):
    READ_ONLY = "READ_ONLY"
    DESTRUCTIVE = "DESTRUCTIVE"
    NETWORK = "NETWORK"
    GIT_WRITE = "GIT_WRITE"
    COMPILE = "COMPILE"
    UNKNOWN = "UNKNOWN"


class PolicyAction(Enum):
    ALLOW = "ALLOW"
    DENY = "DENY"


class Severity(Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


@dataclass
class ClassificationResult:
    command: str
    command_class: CommandClass
    matched_prefix: Optional[str] = None


@dataclass
class PathGuardResult:
    allowed: bool
    path: str
    reason: Optional[str] = None


@dataclass
class SecretDetection:
    found: bool
    secrets: list = field(default_factory=list)  # list of {name, matched_text}


@dataclass
class PolicyDecision:
    allowed: bool
    policy_name: Optional[str] = None
    reason: Optional[str] = None
    severity: Optional[Severity] = None


class AgentLawGateway:
    """Gateway that enforces agent governance laws on commands."""

    def __init__(self, laws_path: Optional[str] = None):
        if laws_path is None:
            laws_path = str(Path(__file__).parent.parent / "laws.yaml")
        self.laws = self._load_laws(laws_path)

    def _load_laws(self, path: str) -> dict:
        with open(path, "r") as f:
            return yaml.safe_load(f)

    def reload_laws(self, path: Optional[str] = None):
        if path is None:
            path = str(Path(__file__).parent.parent / "laws.yaml")
        self.laws = self._load_laws(path)

    def classify_command(self, command: str) -> ClassificationResult:
        """Classify a command into a CommandClass based on its prefix."""
        stripped = command.strip()
        if not stripped:
            return ClassificationResult(command=command, command_class=CommandClass.UNKNOWN)

        # Extract the base command (first token or first two tokens for compound commands)
        try:
            tokens = shlex.split(stripped)
        except ValueError:
            # Fallback for malformed commands
            tokens = stripped.split()

        if not tokens:
            return ClassificationResult(command=command, command_class=CommandClass.UNKNOWN)

        # Check compound commands like "git push", "git commit"
        check_prefixes = []
        if len(tokens) >= 2:
            check_prefixes.append(f"{tokens[0]} {tokens[1]}")
        check_prefixes.append(tokens[0])

        command_classes = self.laws.get("command_classes", {})
        for prefix in check_prefixes:
            for class_name, patterns in command_classes.items():
                for pattern in patterns:
                    if prefix == pattern or prefix.startswith(pattern):
                        return ClassificationResult(
                            command=command,
                            command_class=CommandClass(class_name),
                            matched_prefix=pattern,
                        )

        return ClassificationResult(command=command, command_class=CommandClass.UNKNOWN)

    def check_path_guard(self, command: str) -> PathGuardResult:
        """Check if a command attempts to access blocked paths."""
        path_guards = self.laws.get("path_guards", {})
        blocked_paths = path_guards.get("blocked", [])

        # Expand ~ to home directory
        home = str(Path.home())

        for blocked in blocked_paths:
            expanded = blocked.replace("~", home)
            # Check if the blocked path appears in the command
            # Also check the raw pattern (with ~) since the command might literally contain it
            if expanded in command or blocked in command:
                return PathGuardResult(
                    allowed=False,
                    path=blocked,
                    reason=f"Access to blocked path: {blocked}",
                )

            # Also check via regex for path-like patterns
            escaped = re.escape(expanded)
            if re.search(escaped, command):
                return PathGuardResult(
                    allowed=False,
                    path=blocked,
                    reason=f"Access to blocked path: {blocked}",
                )

        return PathGuardResult(allowed=True, path="", reason=None)

    def detect_secrets(self, command: str) -> SecretDetection:
        """Detect potential secrets/credentials in a command string."""
        secret_patterns = self.laws.get("secret_patterns", [])
        found_secrets = []

        for pattern_def in secret_patterns:
            name = pattern_def["name"]
            pattern = pattern_def["pattern"]
            matches = re.findall(pattern, command)
            for match in matches:
                if isinstance(match, tuple):
                    match = match[0]
                found_secrets.append({"name": name, "matched_text": match})

        return SecretDetection(found=len(found_secrets) > 0, secrets=found_secrets)

    def evaluate_policy(self, command: str, current_branch: Optional[str] = None) -> PolicyDecision:
        """Evaluate a command against all policies."""
        policies = self.laws.get("policies", [])

        for policy in policies:
            name = policy.get("name", "unnamed")
            rule = policy.get("rule", "")
            action = policy.get("action", "DENY")
            severity_str = policy.get("severity", "LOW")
            branches = policy.get("branches")

            # If policy is branch-restricted, check the branch
            if branches and current_branch:
                branch_match = False
                for branch_pattern in branches:
                    if branch_pattern.endswith("*"):
                        if current_branch.startswith(branch_pattern[:-1]):
                            branch_match = True
                            break
                    elif current_branch == branch_pattern:
                        branch_match = True
                        break
                if not branch_match:
                    continue

            # Check if the command matches the policy rule
            try:
                if re.search(rule, command):
                    severity = Severity(severity_str) if severity_str else Severity.LOW
                    if action == "DENY":
                        return PolicyDecision(
                            allowed=False,
                            policy_name=name,
                            reason=policy.get("description", f"Blocked by policy: {name}"),
                            severity=severity,
                        )
                    elif action == "ALLOW":
                        return PolicyDecision(
                            allowed=True,
                            policy_name=name,
                            reason=f"Allowed by policy: {name}",
                        )
            except re.error:
                continue

        # Default: allow if no policy matches
        return PolicyDecision(allowed=True, policy_name=None, reason="No policy matched, defaulting to allow")

    def full_evaluation(self, command: str, current_branch: Optional[str] = None) -> dict:
        """Run all checks and return a comprehensive evaluation."""
        classification = self.classify_command(command)
        path_guard = self.check_path_guard(command)
        secret_detection = self.detect_secrets(command)
        policy_decision = self.evaluate_policy(command, current_branch)

        allowed = path_guard.allowed and (not secret_detection.found) and policy_decision.allowed

        return {
            "command": command,
            "classification": classification.command_class.value,
            "path_guard": {
                "allowed": path_guard.allowed,
                "path": path_guard.path,
                "reason": path_guard.reason,
            },
            "secret_detection": {
                "found": secret_detection.found,
                "secrets": secret_detection.secrets,
            },
            "policy_decision": {
                "allowed": policy_decision.allowed,
                "policy_name": policy_decision.policy_name,
                "reason": policy_decision.reason,
                "severity": policy_decision.severity.value if policy_decision.severity else None,
            },
            "overall_allowed": allowed,
        }
