"""Benchmark — measures performance of core Agent Governance operations."""
import json
import os
import sys
import time
import uuid

# Ensure the project root is on sys.path
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)


def _timeit(fn, runs: int = 3) -> dict:
    """Run fn() `runs` times, return avg/min/max in ms."""
    times = []
    for _ in range(runs):
        t0 = time.perf_counter()
        fn()
        elapsed = (time.perf_counter() - t0) * 1000
        times.append(elapsed)
    return {
        "avg_ms": round(sum(times) / len(times), 2),
        "min_ms": round(min(times), 2),
        "max_ms": round(max(times), 2),
    }


def run_benchmarks() -> dict:
    """Run all benchmarks 3 times each, report avg/min/max ms."""
    benchmarks = []

    # 1. registry_scan: time to scan all repos
    def bench_registry_scan():
        from registry import RepoRegistry
        reg = RepoRegistry()
        reg.discover()

    benchmarks.append({"name": "registry_scan", **_timeit(bench_registry_scan)})

    # 2. health_analysis: time to analyze one repo
    def bench_health_analysis():
        from health import HealthEngine
        engine = HealthEngine()
        # Use the agent-governance repo itself as the target
        repo_path = _project_root
        repo_id = "bench_health_001"
        try:
            engine.analyze(repo_path, repo_id)
        except Exception:
            pass  # DB may not be available

    benchmarks.append({"name": "health_analysis", **_timeit(bench_health_analysis)})

    # 3. model_discovery: time to discover all models
    def bench_model_discovery():
        from models import ModelRegistry
        reg = ModelRegistry()
        try:
            reg.discover()
        except Exception:
            pass  # DB may not be available

    benchmarks.append({"name": "model_discovery", **_timeit(bench_model_discovery)})

    # 4. repo_index: time to index one repo
    def bench_repo_index():
        from registry import RepoRegistry
        reg = RepoRegistry()
        try:
            reg.register(_project_root)
        except Exception:
            pass  # DB may not be available

    benchmarks.append({"name": "repo_index", **_timeit(bench_repo_index)})

    # 5. gateway_eval: time to evaluate one law
    def bench_gateway_eval():
        from gateway.policy_engine import PolicyEngine
        from pathlib import Path
        engine = PolicyEngine()
        laws_path = os.path.join(_project_root, "laws.yaml")
        if os.path.isfile(laws_path):
            engine.load_laws_from_yaml(laws_path)
        engine.evaluate("git status", ["/home/scott/ai-lab/agent-governance"])

    benchmarks.append({"name": "gateway_eval", **_timeit(bench_gateway_eval)})

    # 6. job_lifecycle: time for create+claim+complete cycle
    def bench_job_lifecycle():
        from jobs import JobEngine
        try:
            job_id = JobEngine.create(
                title="benchmark-job",
                job_type="benchmark",
                description="Automated benchmark job",
            )
            # Try to claim (will fail if no workers, that's fine)
            claimed = JobEngine.claim("bench-worker")
            JobEngine.complete(job_id, status="SUCCEEDED")
        except Exception:
            pass  # DB may not be available

    benchmarks.append({"name": "job_lifecycle", **_timeit(bench_job_lifecycle)})

    # 7. action_search: time for grep search
    def bench_action_search():
        import subprocess
        subprocess.run(
            ["grep", "-r", "TODO", "--include=*.py", _project_root],
            capture_output=True, timeout=10,
        )

    benchmarks.append({"name": "action_search", **_timeit(bench_action_search)})

    # 8. db_write: time to insert one receipt
    def bench_db_write():
        import db
        receipt_id = str(uuid.uuid4())
        try:
            db.execute(
                """INSERT INTO action_receipts (receipt_id, action_type, status, created_at)
                   VALUES (:rid, :atype, :status, NOW())""",
                {"rid": receipt_id, "atype": "benchmark", "status": "success"},
            )
        except Exception:
            pass  # Table may not exist

    benchmarks.append({"name": "db_write", **_timeit(bench_db_write)})

    # 9. db_read: time to query 100 receipts
    def bench_db_read():
        import db
        try:
            db.fetchall("SELECT * FROM action_receipts LIMIT 100")
        except Exception:
            pass  # Table may not exist

    benchmarks.append({"name": "db_read", **_timeit(bench_db_read)})

    return {"benchmarks": benchmarks}