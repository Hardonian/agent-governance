"""Autonomy Levels - controls what operations are permitted."""
from enum import IntEnum
from typing import Optional


class AutonomyLevel(IntEnum):
    """Autonomy levels for agent operations.
    
    Level 0: INSPECT ONLY - No mutation allowed
    Level 1: SAFE REPO FIX - May modify isolated worktree, must verify, no merge/deploy
    Level 2: PREPARE CHANGE - May create commit/branch/PR, no production deploy
    Level 3: APPROVED AUTOMATION - May perform repeatable configured operations
    Level 4: INFRASTRUCTURE - Requires explicit stronger authorization
    """
    INSPECT_ONLY = 0
    SAFE_REPO_FIX = 1
    PREPARE_CHANGE = 2
    APPROVED_AUTOMATION = 3
    INFRASTRUCTURE = 4


# What each level can do
LEVEL_PERMISSIONS = {
    AutonomyLevel.INSPECT_ONLY: {
        "read_files": True,
        "run_tests": True,
        "run_lint": True,
        "run_typecheck": True,
        "run_build": False,
        "modify_files": False,
        "create_branch": False,
        "create_commit": False,
        "create_pr": False,
        "merge_pr": False,
        "deploy": False,
        "install_deps": False,
        "run_shell": True,  # read-only shell (git status, ls, grep)
        "delete_files": False,
        "modify_config": False,
    },
    AutonomyLevel.SAFE_REPO_FIX: {
        "read_files": True,
        "run_tests": True,
        "run_lint": True,
        "run_typecheck": True,
        "run_build": True,
        "modify_files": True,  # only in worktree
        "create_branch": True,
        "create_commit": True,  # only in worktree branch
        "create_pr": False,
        "merge_pr": False,
        "deploy": False,
        "install_deps": True,
        "run_shell": True,
        "delete_files": False,
        "modify_config": False,
    },
    AutonomyLevel.PREPARE_CHANGE: {
        "read_files": True,
        "run_tests": True,
        "run_lint": True,
        "run_typecheck": True,
        "run_build": True,
        "modify_files": True,
        "create_branch": True,
        "create_commit": True,
        "create_pr": True,
        "merge_pr": False,
        "deploy": False,
        "install_deps": True,
        "run_shell": True,
        "delete_files": False,
        "modify_config": False,
    },
    AutonomyLevel.APPROVED_AUTOMATION: {
        "read_files": True,
        "run_tests": True,
        "run_lint": True,
        "run_typecheck": True,
        "run_build": True,
        "modify_files": True,
        "create_branch": True,
        "create_commit": True,
        "create_pr": True,
        "merge_pr": True,  # only approved branches
        "deploy": False,
        "install_deps": True,
        "run_shell": True,
        "delete_files": True,  # only in worktree
        "modify_config": True,  # only in worktree
    },
    AutonomyLevel.INFRASTRUCTURE: {
        "read_files": True,
        "run_tests": True,
        "run_lint": True,
        "run_typecheck": True,
        "run_build": True,
        "modify_files": True,
        "create_branch": True,
        "create_commit": True,
        "create_pr": True,
        "merge_pr": True,
        "deploy": True,
        "install_deps": True,
        "run_shell": True,
        "delete_files": True,
        "modify_config": True,
    },
}




# Map action types to required permissions
ACTION_PERMISSIONS = {
    "inspect": "read_files",
    "map": "read_files",
    "search": "read_files",
    "explain": "read_files",
    "status": "read_files",
    "diff": "read_files",
    "test": "run_tests",
    "lint": "run_lint",
    "typecheck": "run_typecheck",
    "build": "run_build",
    "review": "read_files",
    "dead-code": "read_files",
    "dependency-check": "read_files",
    "fix-build": "modify_files",
    "fix-tests": "modify_files",
    "fix-types": "modify_files",
    "fix-lint": "modify_files",
    "fix-dependency": "install_deps",
    "safe-refactor": "modify_files",
    "secret-scan": "read_files",
    "dependency-security": "read_files",
    "auth-review": "read_files",
    "permission-review": "read_files",
    "injection-review": "read_files",
    "configuration-review": "read_files",
    "release-check": "read_files",
    "migration-check": "read_files",
    "deployment-check": "read_files",
    "changelog": "read_files",
    "release-notes": "read_files",
    "release-prepare": "create_commit",
    "branch": "create_branch",
    "commit-prepare": "create_commit",
    "PR-prepare": "create_pr",
    "rollback": "modify_files",
    "action-history": "read_files",
}


def check_permission(level: AutonomyLevel, action: str) -> tuple[bool, str]:
    """Check if an action is permitted at the given autonomy level.
    
    Returns (allowed, reason).
    """
    permission = ACTION_PERMISSIONS.get(action)
    if permission is None:
        return False, f"Unknown action: {action}"

    level_perms = LEVEL_PERMISSIONS.get(level, LEVEL_PERMISSIONS[AutonomyLevel.INSPECT_ONLY])
    allowed = level_perms.get(permission, False)

    if allowed:
        return True, f"Action '{action}' permitted at level {level.name}"
    else:
        return False, f"Action '{action}' requires '{permission}' which is not available at level {level.name}"


def get_max_action_level(action: str) -> Optional[AutonomyLevel]:
    """Get the minimum autonomy level required for an action."""
    permission = ACTION_PERMISSIONS.get(action)
    if permission is None:
        return None
    for level in sorted(AutonomyLevel):
        level_perms = LEVEL_PERMISSIONS.get(level, {})
        if level_perms.get(permission, False):
            return level
    return None