"""Daily Sweep — lightweight automated registry refresh, change detection, health checks, backlog update."""

import json
import subprocess
import os
from datetime import datetime, timezone

from db import execute, fetchall, fetchone


def _git_head(repo_path: str) -> str:
    try:
        r = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_path, capture_output=True, text=True, timeout=10,
        )
        return r.stdout.strip()
    except Exception:
        return ""


def run_sweep() -> dict:
    """Run a full governance sweep: refresh registry, detect changes,
    run health checks on changed repos, identify new failures, update backlog.
    
    Returns:
        {repos_scanned, repos_changed, new_findings, backlog_delta}
    """
    from registry import RepoRegistry
    from health import HealthEngine

    registry = RepoRegistry()
    health = HealthEngine()

    # 1. Refresh registry (re-discover and re-register all repos)
    registered = registry.scan_and_register()
    repos_scanned = len(registered)

    # 2. Detect changes: compare current HEAD vs stored HEAD
    repos_changed = []
    all_repos = fetchall("SELECT repo_id, canonical_path, head_hash FROM repos")

    for r in all_repos:
        current_head = _git_head(r["canonical_path"])
        stored_head = r.get("head_hash", "")
        if current_head and current_head != stored_head:
            repos_changed.append(r["repo_id"])

    # 3. Run lightweight health checks on changed repos
    new_findings = 0
    for repo_id in repos_changed:
        repo = fetchone("SELECT canonical_path FROM repos WHERE repo_id = :rid", {"rid": repo_id})
        if not repo:
            continue
        path = repo["canonical_path"]

        # Run targeted checks only (git state, build) — skip expensive ones
        try:
            findings = health.analyze(path, repo_id)
            new_findings += len(findings)
        except Exception:
            pass

    # 4. Count current backlog state
    backlog_before = fetchone("SELECT COUNT(*) as cnt FROM backlog_items WHERE status = 'open'")
    backlog_before_count = backlog_before["cnt"] if backlog_before else 0

    # 5. Update backlog for all repos
    try:
        health.generate_backlog()
    except Exception:
        pass

    backlog_after = fetchone("SELECT COUNT(*) as cnt FROM backlog_items WHERE status = 'open'")
    backlog_after_count = backlog_after["cnt"] if backlog_after else 0
    backlog_delta = backlog_after_count - backlog_before_count

    # 6. Log the sweep
    _log_sweep(repos_scanned, len(repos_changed), new_findings, backlog_delta)

    return {
        "repos_scanned": repos_scanned,
        "repos_changed": len(repos_changed),
        "new_findings": new_findings,
        "backlog_delta": backlog_delta,
    }


def _log_sweep(scanned: int, changed: int, findings: int, backlog_delta: int) -> None:
    """Log sweep results to a simple file for audit trail."""
    log_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, "sweep.log")
    timestamp = datetime.now(timezone.utc).isoformat()
    entry = json.dumps({
        "timestamp": timestamp,
        "repos_scanned": scanned,
        "repos_changed": changed,
        "new_findings": findings,
        "backlog_delta": backlog_delta,
    })
    try:
        with open(log_file, "a") as f:
            f.write(entry + "\n")
    except Exception:
        pass