"""Durable job queue engine for Agent Governance Phase 2."""
import uuid
import json
from datetime import datetime, timezone

import db

# ---- constants -----------------------------------------------------------
TERMINAL = frozenset({"SUCCEEDED", "FAILED", "CANCELLED", "ROLLED_BACK"})
NON_TERMINAL = frozenset({"QUEUED", "POLICY_CHECK", "PLANNING", "RUNNING",
                          "VERIFYING", "BLOCKED", "ROLLING_BACK"})


def _uuid() -> str:
    return str(uuid.uuid4())


# ---- engine --------------------------------------------------------------
class JobEngine:
    """CRUD + lifecycle for the jobs table with atomic state transitions."""

    # -- create ------------------------------------------------------------
    @staticmethod
    def create(
        title: str,
        job_type: str,
        repo_id: str | None = None,
        description: str | None = None,
        priority: int = 5,
        autonomy_level: int = 0,
        depends_on: list[str] | None = None,
        idempotency_key: str | None = None,
        timeout_seconds: int = 600,
        max_retries: int = 3,
    ) -> str:
        job_id = _uuid()
        sql = """
            INSERT INTO jobs (
                job_id, repo_id, job_type, title, description,
                status, priority, autonomy_level, depends_on,
                idempotency_key, timeout_seconds, max_retries
            ) VALUES (
                :job_id, :repo_id, :job_type, :title, :description,
                'QUEUED', :priority, :autonomy_level, :depends_on,
                :idempotency_key, :timeout_seconds, :max_retries
            )
        """
        db.execute(sql, {
            "job_id": job_id,
            "repo_id": repo_id,
            "job_type": job_type,
            "title": title,
            "description": description,
            "priority": priority,
            "autonomy_level": autonomy_level,
            "depends_on": depends_on,          # TEXT[] — psycopg passes directly
            "idempotency_key": idempotency_key,
            "timeout_seconds": timeout_seconds,
            "max_retries": max_retries,
        })
        return job_id

    # -- claim (atomic) ----------------------------------------------------
    @staticmethod
    def claim(worker_id: str) -> dict | None:
        """Atomically claim the highest-priority QUEUED job whose deps are met."""
        sql = """
            WITH candidate AS (
                SELECT j.job_id
                FROM jobs j
                WHERE j.status = 'QUEUED'
                  AND (
                        j.depends_on IS NULL
                     OR j.depends_on = '{}'
                     OR NOT EXISTS (
                            SELECT 1 FROM unnest(j.depends_on) dep_id
                            JOIN jobs d ON d.job_id = dep_id
                            WHERE d.status NOT IN ('SUCCEEDED','CANCELLED')
                        )
                  )
                ORDER BY j.priority DESC, j.created_at ASC
                LIMIT 1
                FOR UPDATE OF j SKIP LOCKED
            )
            UPDATE jobs
            SET status      = 'RUNNING',
                started_at  = NOW(),
                updated_at  = NOW(),
                worker_id   = :worker_id
            FROM candidate
            WHERE jobs.job_id = candidate.job_id
            RETURNING jobs.*
        """
        # Must run in a single transaction for SELECT … FOR UPDATE semantics
        with db.get_conn() as conn:
            from sqlalchemy import text as sa_text
            result = conn.execute(sa_text(sql), {"worker_id": worker_id})
            row = result.fetchone()
            if row is None:
                return None
            return dict(row._mapping)

    # -- update fields -----------------------------------------------------
    @staticmethod
    def update(job_id: str, **fields) -> None:
        if not fields:
            return
        # JSONB-ify complex values so they store correctly
        safe = {}
        for k, v in fields.items():
            if isinstance(v, (dict, list)):
                safe[k] = json.dumps(v)
            else:
                safe[k] = v
        sets = ", ".join(f"{k} = :{k}" for k in safe)
        sets += ", updated_at = NOW()"
        safe["job_id"] = job_id
        db.execute(
            f"UPDATE jobs SET {sets} WHERE job_id = :job_id",
            safe,
        )

    # -- complete ----------------------------------------------------------
    @staticmethod
    def complete(job_id: str, status: str = "SUCCEEDED", result=None) -> None:
        if status not in ("SUCCEEDED", "FAILED", "CANCELLED", "ROLLED_BACK"):
            raise ValueError(f"Terminal status required, got {status}")
        sql = """
            UPDATE jobs
            SET status        = :status,
                verification_result = COALESCE(CAST(:result AS jsonb), verification_result),
                completed_at  = NOW(),
                duration_ms   = EXTRACT(EPOCH FROM (NOW() - started_at)) * 1000,
                updated_at    = NOW()
            WHERE job_id = :job_id
              AND status NOT IN ('SUCCEEDED','FAILED','CANCELLED','ROLLED_BACK')
        """
        db.execute(sql, {
            "job_id": job_id,
            "status": status,
            "result": json.dumps(result) if result is not None else None,
        })

    # -- fail --------------------------------------------------------------
    @staticmethod
    def fail(job_id: str, error: str, can_retry: bool = True) -> bool:
        """Fail a job. Returns True if re-queued, False if permanently failed."""
        # Read current retry state
        job = db.fetchone(
            "SELECT retry_count, max_retries FROM jobs WHERE job_id = :jid",
            {"jid": job_id},
        )
        if job is None:
            raise ValueError(f"Job {job_id} not found")

        if can_retry and job["retry_count"] < job["max_retries"]:
            # Increment and re-queue
            db.execute("""
                UPDATE jobs
                SET status        = 'QUEUED',
                    retry_count   = retry_count + 1,
                    error_message = :error,
                    worker_id     = NULL,
                    started_at    = NULL,
                    updated_at    = NOW()
                WHERE job_id = :jid
            """, {"jid": job_id, "error": error})
            return True
        else:
            # Permanent failure
            db.execute("""
                UPDATE jobs
                SET status        = 'FAILED',
                    error_message = :error,
                    completed_at  = NOW(),
                    duration_ms   = EXTRACT(EPOCH FROM (NOW() - started_at)) * 1000,
                    updated_at    = NOW()
                WHERE job_id = :jid
            """, {"jid": job_id, "error": error})
            return False

    # -- cancel ------------------------------------------------------------
    @staticmethod
    def cancel(job_id: str) -> bool:
        """Cancel if not already terminal. Returns True if transitioned."""
        sql = """
            UPDATE jobs
            SET status      = 'CANCELLED',
                completed_at = NOW(),
                updated_at   = NOW()
            WHERE job_id = :jid
              AND status NOT IN ('SUCCEEDED','FAILED','CANCELLED','ROLLED_BACK')
            RETURNING job_id
        """
        with db.get_conn() as conn:
            from sqlalchemy import text as sa_text
            result = conn.execute(sa_text(sql), {"jid": job_id})
            return result.fetchone() is not None

    # -- rollback ----------------------------------------------------------
    @staticmethod
    def rollback_job(job_id: str) -> None:
        sql = """
            UPDATE jobs
            SET status      = 'ROLLING_BACK',
                updated_at  = NOW()
            WHERE job_id = :jid
              AND status NOT IN ('SUCCEEDED','FAILED','CANCELLED','ROLLED_BACK')
        """
        db.execute(sql, {"jid": job_id})

    # -- get ---------------------------------------------------------------
    @staticmethod
    def get(job_id: str) -> dict | None:
        return db.fetchone("SELECT * FROM jobs WHERE job_id = :jid", {"jid": job_id})

    # -- list_jobs ---------------------------------------------------------
    @staticmethod
    def list_jobs(status: str | None = None, repo_id: str | None = None,
                  limit: int = 50) -> list[dict]:
        clauses = []
        params: dict = {"limit": limit}
        if status:
            clauses.append("status = :status")
            params["status"] = status
        if repo_id:
            clauses.append("repo_id = :repo_id")
            params["repo_id"] = repo_id
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        sql = f"SELECT * FROM jobs {where} ORDER BY priority DESC, created_at DESC LIMIT :limit"
        return db.fetchall(sql, params)

    # -- recover_stale -----------------------------------------------------
    @staticmethod
    def recover_stale(timeout_minutes: int = 30) -> int:
        sql = """
            UPDATE jobs
            SET status      = 'QUEUED',
                worker_id   = NULL,
                started_at  = NULL,
                retry_count = retry_count + 1,
                updated_at  = NOW()
            WHERE status = 'RUNNING'
              AND started_at < NOW() - make_interval(mins => :mins)
        """
        result = db.execute(sql, {"mins": timeout_minutes})
        return result.rowcount

    # -- recover_on_startup ------------------------------------------------
    @staticmethod
    def recover_on_startup() -> int:
        sql = """
            UPDATE jobs
            SET status     = 'QUEUED',
                worker_id  = NULL,
                started_at = NULL,
                updated_at = NOW()
            WHERE status IN ('ROLLING_BACK', 'POLICY_CHECK', 'PLANNING',
                             'VERIFYING', 'BLOCKED')
        """
        result = db.execute(sql)
        return result.rowcount

    # -- queue_depth -------------------------------------------------------
    @staticmethod
    def queue_depth() -> dict:
        rows = db.fetchall(
            "SELECT status, COUNT(*) AS cnt FROM jobs GROUP BY status"
        )
        return {r["status"]: r["cnt"] for r in rows}