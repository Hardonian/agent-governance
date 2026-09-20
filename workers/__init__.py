"""Worker Registration — register, heartbeat, claim, and report for governance workers."""
import json

def _resolve_repo_id(repo_id_or_name: str) -> str:
    """Resolve repo name to actual repo_id if needed."""
    from db import fetchone
    row = fetchone("SELECT repo_id FROM repos WHERE repo_id = :id OR name = :id", {"id": repo_id_or_name})
    return row["repo_id"] if row else repo_id_or_name

import platform
from datetime import datetime, timezone

import db


class WorkerManager:
    """Manages worker registration and lifecycle in the governance system."""

    # --- Registration ---

    def register_epyc(self) -> str:
        """Register the EPYC machine as a worker with standard capabilities.

        Returns the worker_id.
        """
        worker_id = f"epyc-{platform.node()}"
        capabilities = [
            "git", "python", "node", "rust", "cargo", "docker", "nvidia-smi",
        ]
        now = datetime.now(timezone.utc).isoformat()

        db.execute(
            """INSERT INTO workers (worker_id, node, capabilities, status, last_heartbeat)
               VALUES (:wid, :node, :caps, 'idle', :hb)
               ON CONFLICT (worker_id) DO UPDATE SET
                   capabilities = EXCLUDED.capabilities,
                   status = 'idle',
                   last_heartbeat = EXCLUDED.last_heartbeat""",
            {
                "wid": worker_id,
                "node": platform.node(),
                "caps": json.dumps(capabilities),
                "hb": now,
            },
        )
        return worker_id

    # --- Heartbeat ---

    def heartbeat(self, worker_id: str) -> None:
        """Update the last_heartbeat timestamp for a worker."""
        db.execute(
            "UPDATE workers SET last_heartbeat = NOW() WHERE worker_id = :wid",
            {"wid": worker_id},
        )

    # --- Listing ---

    def list_workers(self) -> list:
        """List all registered workers."""
        return db.fetchall("SELECT * FROM workers ORDER BY worker_id")

    # --- Job Operations ---

    def claim_job(self, worker_id: str) -> dict | None:
        """Claim the highest-priority queued job for this worker.

        Delegates to jobs.claim() with atomic SELECT FOR UPDATE.
        Returns the claimed job dict, or None if nothing available.
        """
        from jobs import JobEngine

        # Mark worker as busy
        db.execute(
            "UPDATE workers SET status = 'busy' WHERE worker_id = :wid",
            {"wid": worker_id},
        )

        job = JobEngine.claim(worker_id)
        if job is None:
            db.execute(
                "UPDATE workers SET status = 'idle' WHERE worker_id = :wid",
                {"wid": worker_id},
            )
            return None

        # Link job to worker
        db.execute(
            "UPDATE workers SET current_job_id = :jid WHERE worker_id = :wid",
            {"jid": job["job_id"], "wid": worker_id},
        )

        return job

    def report_result(self, worker_id: str, job_id: str, status: str, result: dict = None) -> None:
        """Report a job completion/failure and update worker state.

        Args:
            worker_id: the worker reporting
            job_id: the completed job
            status: terminal status (SUCCEEDED, FAILED, CANCELLED, ROLLED_BACK)
            result: optional result payload
        """
        from jobs import JobEngine

        # Update the job
        if status == "FAILED":
            JobEngine.fail(job_id, error=json.dumps(result) if result else "Unknown error", can_retry=False)
        else:
            JobEngine.complete(job_id, status=status, result=result)

        # Release worker
        db.execute(
            """UPDATE workers
               SET status = 'idle', current_job_id = NULL
               WHERE worker_id = :wid""",
            {"wid": worker_id},
        )