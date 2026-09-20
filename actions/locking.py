"""Repo Locking - prevents conflicting mutations."""
import time
import uuid
from db import execute, fetchone, fetchall


def acquire_lock(repo_id: str, lock_type: str, worker_id: str = None, timeout_seconds: int = 600) -> dict:
    """Acquire a lock on a repo. Returns lock info or raises."""
    worker_id = worker_id or f"hermes-{uuid.uuid4().hex[:8]}"
    # Clean expired locks
    execute("DELETE FROM repo_locks WHERE expires_at < NOW()")
    # Try to acquire
    try:
        execute("""
            INSERT INTO repo_locks (repo_id, lock_type, worker_id, acquired_at, expires_at)
            VALUES (:repo, :lt, :wid, NOW(), NOW() + make_interval(secs => :timeout))
        """, {"repo": repo_id, "lt": lock_type, "wid": worker_id, "timeout": timeout_seconds})
        return {"repo_id": repo_id, "lock_type": lock_type, "worker_id": worker_id, "acquired": True}
    except Exception:
        # Lock already held
        existing = fetchone(
            "SELECT * FROM repo_locks WHERE repo_id = :repo AND lock_type = :lt",
            {"repo": repo_id, "lt": lock_type}
        )
        return {"repo_id": repo_id, "lock_type": lock_type, "acquired": False, "held_by": existing.get("worker_id") if existing else None}


def release_lock(repo_id: str, lock_type: str, worker_id: str = None) -> bool:
    """Release a lock."""
    if worker_id:
        execute(
            "DELETE FROM repo_locks WHERE repo_id = :repo AND lock_type = :lt AND worker_id = :wid",
            {"repo": repo_id, "lt": lock_type, "wid": worker_id}
        )
    else:
        execute(
            "DELETE FROM repo_locks WHERE repo_id = :repo AND lock_type = :lt",
            {"repo": repo_id, "lt": lock_type}
        )
    return True


def is_locked(repo_id: str, lock_type: str = "write") -> bool:
    """Check if a repo is locked."""
    execute("DELETE FROM repo_locks WHERE expires_at < NOW()")
    lock = fetchone(
        "SELECT * FROM repo_locks WHERE repo_id = :repo AND lock_type = :lt",
        {"repo": repo_id, "lt": lock_type}
    )
    return lock is not None


def get_locks(repo_id: str = None) -> list:
    """Get all active locks."""
    execute("DELETE FROM repo_locks WHERE expires_at < NOW()")
    if repo_id:
        return fetchall("SELECT * FROM repo_locks WHERE repo_id = :repo", {"repo": repo_id})
    return fetchall("SELECT * FROM repo_locks ORDER BY acquired_at DESC")


def cleanup_stale(max_age_seconds: int = 3600) -> int:
    """Remove locks older than max_age."""
    execute("DELETE FROM repo_locks WHERE acquired_at < NOW() - make_interval(secs => :age)", {"age": max_age_seconds})
    return 0