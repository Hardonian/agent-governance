"""Path guard — validates paths against allowed roots, detects symlink traversal, protects system dirs."""

from __future__ import annotations

import os
import pathlib
from dataclasses import dataclass
from typing import List, Optional, Sequence


@dataclass(frozen=True)
class PathViolation:
    """A path that violates guard rules."""
    path: str
    rule: str        # e.g. "OUTSIDE_ROOT", "SYMLINK_ESCAPE", "SYSTEM_DIR"
    detail: str


# System directories that should never be mutated by agent actions.
_SYSTEM_DIRS: frozenset[str] = frozenset({
    "/",
    "/bin",
    "/boot",
    "/dev",
    "/etc",
    "/lib",
    "/lib32",
    "/lib64",
    "/libx32",
    "/proc",
    "/root",
    "/run",
    "/sbin",
    "/sys",
    "/usr",
    "/usr/bin",
    "/usr/sbin",
    "/usr/lib",
    "/usr/local/bin",
    "/usr/local/sbin",
    "/usr/local/lib",
    "/var",
    "/var/lib",
    "/var/log",
    "/var/run",
    "/snap",
})

# Paths that are always off-limits regardless of allowed roots.
_BLOCKED_PREFIXES: frozenset[str] = frozenset({
    "/dev",
    "/proc",
    "/sys",
    "/run",
})


class PathGuard:
    """Validates paths are within allowed roots, detects symlink traversal, protects system dirs."""

    def __init__(
        self,
        allowed_roots: Sequence[str] | None = None,
        extra_blocked: Sequence[str] | None = None,
    ):
        """Create guard.

        Args:
            allowed_roots: list of absolute directory paths that are permitted.
                           Defaults to [/home, /tmp, /opt] if not given.
            extra_blocked: additional path prefixes to block beyond system defaults.
        """
        self._allowed_roots: List[pathlib.Path] = [
            pathlib.Path(r).resolve() for r in (allowed_roots or ["/home", "/tmp", "/opt"])
        ]
        self._blocked = _BLOCKED_PREFIXES | frozenset(extra_blocked or [])

    def _is_blocked(self, resolved: pathlib.Path) -> bool:
        """Check if resolved path falls under a blocked prefix."""
        resolved_str = str(resolved)
        for blocked in self._blocked:
            if resolved_str == blocked or resolved_str.startswith(blocked + "/"):
                return True
        return False

    def _is_system_dir(self, resolved: pathlib.Path) -> bool:
        """Check if resolved path is a protected system directory."""
        return str(resolved) in _SYSTEM_DIRS

    def _is_within_allowed(self, resolved: pathlib.Path) -> bool:
        """Check if resolved path is under any allowed root."""
        for root in self._allowed_roots:
            try:
                resolved.relative_to(root)
                return True
            except ValueError:
                continue
        return False

    def _check_symlink(self, raw_path: str, resolved: pathlib.Path) -> Optional[PathViolation]:
        """Detect if resolving symlinks escaped the expected parent."""
        raw = pathlib.Path(raw_path).expanduser()
        # Walk each component to detect symlink escapes.
        current = pathlib.Path("/")
        for part in raw.parts:
            if part == "/":
                continue
            current = current / part
            try:
                target = current.resolve()
            except (OSError, RuntimeError):
                continue
            # If the symlink target is outside all allowed roots, flag it.
            try:
                if current.is_symlink() and not self._is_within_allowed(target):
                    return PathViolation(
                        path=raw_path,
                        rule="SYMLINK_ESCAPE",
                        detail=f"Symlink {current} -> {target} escapes allowed roots",
                    )
            except (OSError, PermissionError):
                # If we can't stat the path, treat it as inaccessible (block it)
                return PathViolation(
                    path=raw_path,
                    rule="INACCESSIBLE_PATH",
                    detail=f"Path {current} is inaccessible (permission denied), treating as blocked",
                )
        return None

    def validate(self, path: str) -> List[PathViolation]:
        """Validate a single path. Returns list of violations (empty = allowed)."""
        violations: List[PathViolation] = []
        expanded = os.path.expanduser(path)
        try:
            resolved = pathlib.Path(expanded).resolve(strict=False)
        except (OSError, RuntimeError) as e:
            violations.append(PathViolation(path=path, rule="RESOLVE_ERROR", detail=str(e)))
            return violations

        # Check blocked prefixes (devices, proc, sys).
        if self._is_blocked(resolved):
            violations.append(PathViolation(
                path=path,
                rule="BLOCKED_PREFIX",
                detail=f"Path {resolved} is under a blocked prefix",
            ))

        # Check protected system directories.
        if self._is_system_dir(resolved):
            violations.append(PathViolation(
                path=path,
                rule="SYSTEM_DIR",
                detail=f"Path {resolved} is a protected system directory",
            ))

        # Check allowed roots.
        if not self._is_within_allowed(resolved):
            violations.append(PathViolation(
                path=path,
                rule="OUTSIDE_ROOT",
                detail=f"Path {resolved} is outside allowed roots {[str(r) for r in self._allowed_roots]}",
            ))

        # Check symlink traversal.
        symlink_violation = self._check_symlink(path, resolved)
        if symlink_violation:
            violations.append(symlink_violation)

        return violations

    def validate_all(self, paths: Sequence[str]) -> List[PathViolation]:
        """Validate multiple paths. Returns all violations across all paths."""
        all_violations: List[PathViolation] = []
        for p in paths:
            all_violations.extend(self.validate(p))
        return all_violations

    def is_safe(self, path: str) -> bool:
        """Return True if path has no violations."""
        return len(self.validate(path)) == 0
