"""Command classifier — deterministic risk-level mapping for shell commands."""

from __future__ import annotations

import re
from enum import IntEnum
from typing import Dict, List, Tuple


class RiskLevel(IntEnum):
    READ_ONLY = 1
    LOW_RISK_WRITE = 2
    REPO_MUTATION = 3
    SYSTEM_MUTATION = 4
    DESTRUCTIVE = 5


# Canonical mapping: command base -> risk level.
# Subcommand variants are handled by _SUBCOMMAND_OVERRIDES.
_BASE_COMMAND_RISK: Dict[str, RiskLevel] = {
    # READ_ONLY
    "ls": RiskLevel.READ_ONLY,
    "cat": RiskLevel.READ_ONLY,
    "head": RiskLevel.READ_ONLY,
    "tail": RiskLevel.READ_ONLY,
    "wc": RiskLevel.READ_ONLY,
    "echo": RiskLevel.READ_ONLY,
    "which": RiskLevel.READ_ONLY,
    "whoami": RiskLevel.READ_ONLY,
    "hostname": RiskLevel.READ_ONLY,
    "uname": RiskLevel.READ_ONLY,
    "date": RiskLevel.READ_ONLY,
    "env": RiskLevel.READ_ONLY,
    "printenv": RiskLevel.READ_ONLY,
    "pwd": RiskLevel.READ_ONLY,
    "file": RiskLevel.READ_ONLY,
    "stat": RiskLevel.READ_ONLY,
    "du": RiskLevel.READ_ONLY,
    "df": RiskLevel.READ_ONLY,
    "free": RiskLevel.READ_ONLY,
    "uptime": RiskLevel.READ_ONLY,
    "id": RiskLevel.READ_ONLY,
    "ps": RiskLevel.READ_ONLY,
    "top": RiskLevel.READ_ONLY,
    "grep": RiskLevel.READ_ONLY,
    "rg": RiskLevel.READ_ONLY,
    "find": RiskLevel.READ_ONLY,
    "diff": RiskLevel.READ_ONLY,
    "sha256sum": RiskLevel.READ_ONLY,
    "md5sum": RiskLevel.READ_ONLY,
    "base64": RiskLevel.READ_ONLY,
    "curl": RiskLevel.READ_ONLY,
    "wget": RiskLevel.READ_ONLY,
    "python3": RiskLevel.READ_ONLY,
    "python": RiskLevel.READ_ONLY,
    "pip": RiskLevel.READ_ONLY,
    "node": RiskLevel.READ_ONLY,
    "jq": RiskLevel.READ_ONLY,
    "tee": RiskLevel.READ_ONLY,

    # LOW_RISK_WRITE
    "touch": RiskLevel.LOW_RISK_WRITE,
    "mkdir": RiskLevel.LOW_RISK_WRITE,
    "cp": RiskLevel.LOW_RISK_WRITE,
    "mv": RiskLevel.LOW_RISK_WRITE,
    "ln": RiskLevel.LOW_RISK_WRITE,
    "install": RiskLevel.LOW_RISK_WRITE,
    "sed": RiskLevel.LOW_RISK_WRITE,
    "awk": RiskLevel.LOW_RISK_WRITE,
    "sort": RiskLevel.LOW_RISK_WRITE,
    "uniq": RiskLevel.LOW_RISK_WRITE,
    "tr": RiskLevel.LOW_RISK_WRITE,
    "xargs": RiskLevel.LOW_RISK_WRITE,
    "tar": RiskLevel.LOW_RISK_WRITE,
    "zip": RiskLevel.LOW_RISK_WRITE,
    "unzip": RiskLevel.LOW_RISK_WRITE,
    "gzip": RiskLevel.LOW_RISK_WRITE,
    "gunzip": RiskLevel.LOW_RISK_WRITE,

    # REPO_MUTATION (git commands; subcommand overrides below)
    "git": RiskLevel.REPO_MUTATION,

    # SYSTEM_MUTATION
    "apt": RiskLevel.SYSTEM_MUTATION,
    "apt-get": RiskLevel.SYSTEM_MUTATION,
    "yum": RiskLevel.SYSTEM_MUTATION,
    "dnf": RiskLevel.SYSTEM_MUTATION,
    "pacman": RiskLevel.SYSTEM_MUTATION,
    "brew": RiskLevel.SYSTEM_MUTATION,
    "pip3": RiskLevel.SYSTEM_MUTATION,
    "npm": RiskLevel.SYSTEM_MUTATION,
    "yarn": RiskLevel.SYSTEM_MUTATION,
    "pnpm": RiskLevel.SYSTEM_MUTATION,
    "cargo": RiskLevel.SYSTEM_MUTATION,
    "systemctl": RiskLevel.SYSTEM_MUTATION,
    "journalctl": RiskLevel.READ_ONLY,
    "useradd": RiskLevel.SYSTEM_MUTATION,
    "usermod": RiskLevel.SYSTEM_MUTATION,
    "userdel": RiskLevel.SYSTEM_MUTATION,
    "groupadd": RiskLevel.SYSTEM_MUTATION,
    "groupdel": RiskLevel.SYSTEM_MUTATION,
    "chown": RiskLevel.SYSTEM_MUTATION,
    "chmod": RiskLevel.SYSTEM_MUTATION,
    "chgrp": RiskLevel.SYSTEM_MUTATION,
    "mount": RiskLevel.SYSTEM_MUTATION,
    "umount": RiskLevel.SYSTEM_MUTATION,
    "modprobe": RiskLevel.SYSTEM_MUTATION,
    "iptables": RiskLevel.SYSTEM_MUTATION,
    "nft": RiskLevel.SYSTEM_MUTATION,
    "sysctl": RiskLevel.SYSTEM_MUTATION,
    "swapoff": RiskLevel.SYSTEM_MUTATION,
    "swapon": RiskLevel.SYSTEM_MUTATION,
    "losetup": RiskLevel.SYSTEM_MUTATION,

    # DESTRUCTIVE
    "rm": RiskLevel.DESTRUCTIVE,
    "rmdir": RiskLevel.DESTRUCTIVE,
    "shred": RiskLevel.DESTRUCTIVE,
    "dd": RiskLevel.DESTRUCTIVE,
    "mkfs": RiskLevel.DESTRUCTIVE,
    "wipefs": RiskLevel.DESTRUCTIVE,
    "fdisk": RiskLevel.DESTRUCTIVE,
    "parted": RiskLevel.DESTRUCTIVE,
    "kill": RiskLevel.DESTRUCTIVE,
    "killall": RiskLevel.DESTRUCTIVE,
    "pkill": RiskLevel.DESTRUCTIVE,
    "reboot": RiskLevel.DESTRUCTIVE,
    "shutdown": RiskLevel.DESTRUCTIVE,
    "poweroff": RiskLevel.DESTRUCTIVE,
    "init": RiskLevel.DESTRUCTIVE,
    "halt": RiskLevel.DESTRUCTIVE,
    "dropdb": RiskLevel.DESTRUCTIVE,
    "dropuser": RiskLevel.DESTRUCTIVE,
}

# Subcommand overrides for commands that vary by subcommand.
# Key: (base_command, subcommand) -> RiskLevel
_SUBCOMMAND_OVERRIDES: Dict[Tuple[str, str], RiskLevel] = {
    # Git subcommands
    ("git", "status"): RiskLevel.READ_ONLY,
    ("git", "log"): RiskLevel.READ_ONLY,
    ("git", "show"): RiskLevel.READ_ONLY,
    ("git", "diff"): RiskLevel.READ_ONLY,
    ("git", "branch"): RiskLevel.READ_ONLY,
    ("git", "tag"): RiskLevel.READ_ONLY,
    ("git", "remote"): RiskLevel.READ_ONLY,
    ("git", "describe"): RiskLevel.READ_ONLY,
    ("git", "rev-parse"): RiskLevel.READ_ONLY,
    ("git", "ls-files"): RiskLevel.READ_ONLY,
    ("git", "ls-remote"): RiskLevel.READ_ONLY,
    ("git", "blame"): RiskLevel.READ_ONLY,
    ("git", "stash"): RiskLevel.LOW_RISK_WRITE,
    ("git", "checkout"): RiskLevel.LOW_RISK_WRITE,
    ("git", "switch"): RiskLevel.LOW_RISK_WRITE,
    ("git", "pull"): RiskLevel.LOW_RISK_WRITE,
    ("git", "fetch"): RiskLevel.LOW_RISK_WRITE,
    ("git", "clone"): RiskLevel.LOW_RISK_WRITE,
    ("git", "add"): RiskLevel.REPO_MUTATION,
    ("git", "commit"): RiskLevel.REPO_MUTATION,
    ("git", "push"): RiskLevel.REPO_MUTATION,
    ("git", "merge"): RiskLevel.REPO_MUTATION,
    ("git", "rebase"): RiskLevel.REPO_MUTATION,
    ("git", "cherry-pick"): RiskLevel.REPO_MUTATION,
    ("git", "reset"): RiskLevel.DESTRUCTIVE,
    ("git", "clean"): RiskLevel.DESTRUCTIVE,
    ("git", "push --force"): RiskLevel.DESTRUCTIVE,
    ("git", "push -f"): RiskLevel.DESTRUCTIVE,
    ("git", "branch -D"): RiskLevel.DESTRUCTIVE,
    ("git", "checkout --detach"): RiskLevel.DESTRUCTIVE,
    ("git", "filter-branch"): RiskLevel.DESTRUCTIVE,
    ("git", "filter-repo"): RiskLevel.DESTRUCTIVE,
}


# Patterns that upgrade risk regardless of base command.
_UPGRADE_PATTERNS: List[Tuple[str, RiskLevel]] = [
    (r"-\S*r\S*f\S*", RiskLevel.DESTRUCTIVE),         # rm -rf style
    (r"--force", RiskLevel.DESTRUCTIVE),               # forced operations
    (r"--no-preserve-root", RiskLevel.DESTRUCTIVE),    # rm --no-preserve-root
    (r"/dev/sd[a-z]", RiskLevel.DESTRUCTIVE),          # block device access
    (r"/dev/nvme", RiskLevel.DESTRUCTIVE),             # nvme device access
    (r">\s*/dev/", RiskLevel.DESTRUCTIVE),             # writing to devices
]


def _extract_base_command(command: str) -> str:
    """Return the first token of a shell command."""
    return command.strip().split()[0] if command.strip() else ""


def _extract_subcommand(command: str) -> str:
    """Return the second token (subcommand) if present."""
    parts = command.strip().split()
    if len(parts) >= 2:
        return parts[1]
    return ""


class CommandClassifier:
    """Deterministic shell-command risk classifier."""

    def classify(self, command: str) -> RiskLevel:
        """Return the RiskLevel for *command* (fail-closed: unknown = SYSTEM_MUTATION)."""
        base = _extract_base_command(command)
        sub = _extract_subcommand(command)

        # Check subcommand overrides first (most specific).
        if sub:
            # Try compound subcommand flags (e.g., "push --force").
            compound = f"{sub}" if not any(
                c in sub for c in " \t"
            ) else sub
            key = (base, compound)
            if key in _SUBCOMMAND_OVERRIDES:
                return _SUBCOMMAND_OVERRIDES[key]

        # Check base command.
        base_risk = _BASE_COMMAND_RISK.get(base)

        # Apply upgrade patterns to raw command string.
        max_risk = base_risk or RiskLevel.SYSTEM_MUTATION
        for pattern, upgrade_level in _UPGRADE_PATTERNS:
            if re.search(pattern, command):
                if upgrade_level > max_risk:
                    max_risk = upgrade_level

        # Fail-closed: unknown commands default to SYSTEM_MUTATION.
        return max_risk
