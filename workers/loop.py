"""Worker loop - continuously claims and executes jobs."""
import time
import signal
import sys
from workers import WorkerManager
from jobs import JobEngine
from actions.autonomy import AutonomyLevel, check_permission

WORKER_ID = "epyc-epyc"
POLL_INTERVAL = 10  # seconds

_running = True

def _signal_handler(sig, frame):
    global _running
    _running = False
    print(f"\nWorker {WORKER_ID} shutting down...")

signal.signal(signal.SIGINT, _signal_handler)
signal.signal(signal.SIGTERM, _signal_handler)


def execute_job(job: dict) -> dict:
    """Execute a claimed job based on its type."""
    job_type = job.get("job_type", "unknown")
    autonomy = AutonomyLevel(job.get("autonomy_level", 0))
    
    if job_type == "inspection":
        from actions.action_pack import ActionPack
        pack = ActionPack(autonomy_level=int(autonomy))
        repo_path = job.get("worktree_path") or "."
        result = pack.inspect(repo_path, job.get("repo_id"))
        return {"status": "SUCCEEDED", "result": result}
    
    elif job_type == "health_check":
        from health import HealthEngine
        he = HealthEngine()
        findings = he.analyze(job.get("worktree_path", "."), job.get("repo_id", ""))
        return {"status": "SUCCEEDED", "result": {"findings": len(findings)}}
    
    elif job_type == "test":
        allowed, reason = check_permission(autonomy, "test")
        if not allowed:
            return {"status": "BLOCKED", "reason": reason}
        from actions.action_pack import ActionPack
        pack = ActionPack(autonomy_level=int(autonomy))
        result = pack.test(job.get("worktree_path", "."), job.get("repo_id"))
        return {"status": "SUCCEEDED", "result": result}
    
    else:
        return {"status": "FAILED", "reason": f"Unknown job type: {job_type}"}


def run_worker_loop():
    """Main worker loop."""
    wm = WorkerManager()
    je = JobEngine()
    
    wm.register_epyc()
    print(f"Worker {WORKER_ID} started. Polling every {POLL_INTERVAL}s...")
    
    while _running:
        wm.heartbeat(WORKER_ID)
        job = je.claim(WORKER_ID)
        
        if job:
            print(f"  Claimed: {job['job_id'][:8]} [{job['job_type']}] {job['title']}")
            try:
                result = execute_job(job)
                status = result.get("status", "SUCCEEDED")
                je.complete(job["job_id"], status)
                print(f"  Completed: {status}")
            except Exception as e:
                je.fail(job["job_id"], str(e), can_retry=True)
                print(f"  Failed: {e}")
        else:
            time.sleep(POLL_INTERVAL)
    
    print(f"Worker {WORKER_ID} stopped.")


if __name__ == "__main__":
    run_worker_loop()
