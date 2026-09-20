"""Autonomy Enforcement — checks if a job's autonomy level permits a given action."""
import db
from actions.autonomy import AutonomyLevel, check_permission


def get_job_autonomy(job_id: str) -> int:
    """Read the autonomy_level for a job from the jobs table.

    Returns the integer autonomy level (0-4). Defaults to 0 if job not found.
    """
    row = db.fetchone(
        "SELECT autonomy_level FROM jobs WHERE job_id = :jid",
        {"jid": job_id},
    )
    if row is None:
        return 0
    return row.get("autonomy_level", 0)


def enforce_autonomy(job_id: str, action: str) -> dict:
    """Check if a job's autonomy level permits the given action.

    Args:
        job_id: the job ID to check
        action: the action string (must be in ACTION_PERMISSIONS)

    Returns:
        {allowed: bool, reason: str, required_level: int|None}
    """
    level_int = get_job_autonomy(job_id)
    try:
        level = AutonomyLevel(level_int)
    except ValueError:
        level = AutonomyLevel.INSPECT_ONLY

    allowed, reason = check_permission(level, action)

    # Find the minimum level that can do this action
    from actions.autonomy import get_max_action_level
    required_level = get_max_action_level(action)
    required_int = required_level.value if required_level is not None else None

    return {
        "allowed": allowed,
        "reason": reason,
        "required_level": required_int,
    }