"""Resource Scheduler — tracks local hardware capacity and concurrency limits."""
import os
import subprocess
import json
import urllib.request

from db import fetchall

# Concurrency limits
MAX_REPO_WORKERS = 3
MAX_INFERENCE_PER_GPU = 1
MAX_TOTAL_CONCURRENT = 6

# GPU lane endpoints for live VRAM queries
GPU_LANES = {
    "v100": {"port": 11434, "vram_total_mb": 16384, "gpu_name": "Tesla V100-SXM2-16GB"},
    "p40":  {"port": 11435, "vram_total_mb": 23040, "gpu_name": "Tesla P40"},
    "3060": {"port": 11436, "vram_total_mb": 12288, "gpu_name": "NVIDIA GeForce RTX 3060"},
}


def _get_nvidia_smi() -> list[dict]:
    """Parse nvidia-smi for live GPU memory."""
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=index,name,memory.total,memory.used,memory.free",
             "--format=csv,noheader,nounits"],
            timeout=5, text=True
        )
        gpus = []
        for line in out.strip().split("\n"):
            parts = [p.strip() for p in line.split(",")]
            if len(parts) >= 5:
                gpus.append({
                    "index": int(parts[0]),
                    "name": parts[1],
                    "vram_total_mb": int(parts[2]),
                    "vram_used_mb": int(parts[3]),
                    "vram_free_mb": int(parts[4]),
                })
        return gpus
    except (subprocess.CalledProcessError, FileNotFoundError, ValueError):
        return []


def _get_ollama_running(port: int) -> list[str]:
    """Check which models are currently loaded on an Ollama port."""
    try:
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/ps")
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read())
            return [m.get("name", "") for m in data.get("models", [])]
    except Exception:
        return []


class ResourceScheduler:
    """Tracks local hardware capacity and enforces concurrency limits."""

    def get_resources(self) -> dict:
        """Return current CPU, RAM, GPU, VRAM resource snapshot."""
        # CPU/RAM from /proc
        cpu_count = os.cpu_count() or 1
        load_1m, load_5m, load_15m = os.getloadavg()

        mem_info = {}
        try:
            with open("/proc/meminfo") as f:
                for line in f:
                    parts = line.split(":")
                    if len(parts) == 2:
                        key = parts[0].strip()
                        val = parts[1].strip().split()[0]
                        mem_info[key] = int(val)  # kB
        except FileNotFoundError:
            pass

        mem_total_mb = mem_info.get("MemTotal", 0) // 1024
        mem_avail_mb = mem_info.get("MemAvailable", 0) // 1024
        mem_used_mb = mem_total_mb - mem_avail_mb

        # GPU info
        gpus = _get_nvidia_smi()
        gpu_info = []
        for g in gpus:
            # Match GPU to lane by closest VRAM total
            best_lane = None
            best_diff = float("inf")
            for lane_name, lane_cfg in GPU_LANES.items():
                diff = abs(g["vram_total_mb"] - lane_cfg["vram_total_mb"])
                if diff < best_diff:
                    best_diff = diff
                    best_lane = lane_name
            running = _get_ollama_running(GPU_LANES[best_lane]["port"]) if best_lane else []
            gpu_info.append({
                "index": g["index"],
                "name": g["name"],
                "lane": best_lane or "unknown",
                "vram_total_mb": g["vram_total_mb"],
                "vram_used_mb": g["vram_used_mb"],
                "vram_free_mb": g["vram_free_mb"],
                "running_models": running,
            })

        # Current concurrency from DB
        try:
            active_jobs = fetchall("""
                SELECT COUNT(*) as cnt FROM jobs
                WHERE status IN ('RUNNING', 'PLANNING', 'POLICY_CHECK', 'VERIFYING')
            """)
            active_count = active_jobs[0]["cnt"] if active_jobs else 0

            repo_workers = fetchall("""
                SELECT COUNT(*) as cnt FROM workers
                WHERE status = 'busy'
            """)
            repo_worker_count = repo_workers[0]["cnt"] if repo_workers else 0
        except Exception:
            active_count = 0
            repo_worker_count = 0

        return {
            "cpu": {
                "cores": cpu_count,
                "load_1m": round(load_1m, 2),
                "load_5m": round(load_5m, 2),
                "load_15m": round(load_15m, 2),
            },
            "ram": {
                "total_mb": mem_total_mb,
                "used_mb": mem_used_mb,
                "available_mb": mem_avail_mb,
                "usage_pct": round(mem_used_mb / mem_total_mb * 100, 1) if mem_total_mb else 0,
            },
            "gpus": gpu_info,
            "concurrency": {
                "active_jobs": active_count,
                "busy_repo_workers": repo_worker_count,
                "max_repo_workers": MAX_REPO_WORKERS,
                "max_inference_per_gpu": MAX_INFERENCE_PER_GPU,
                "max_total_concurrent": MAX_TOTAL_CONCURRENT,
                "repo_workers_available": max(0, MAX_REPO_WORKERS - repo_worker_count),
                "total_slots_available": max(0, MAX_TOTAL_CONCURRENT - active_count),
            },
        }

    def can_run(self, task_type: str) -> tuple[bool, str]:
        """Check if a task of the given type can be scheduled now.

        Returns (allowed, reason).
        """
        resources = self.get_resources()
        conc = resources["concurrency"]

        # Global limit
        if conc["active_jobs"] >= MAX_TOTAL_CONCURRENT:
            return False, f"at capacity: {conc['active_jobs']}/{MAX_TOTAL_CONCURRENT} active jobs"

        # Task-specific checks
        if task_type in ("repo_mutation", "repo_audit", "repo_inspect"):
            if conc["busy_repo_workers"] >= MAX_REPO_WORKERS:
                return False, f"repo worker limit reached: {conc['busy_repo_workers']}/{MAX_REPO_WORKERS}"
            return True, "ok"

        if task_type in ("inference", "coding", "reasoning", "analysis", "vision", "general"):
            # Check if any GPU lane has free capacity
            for gpu in resources["gpus"]:
                lane = gpu.get("lane", "unknown")
                running = gpu.get("running_models", [])
                if len(running) < MAX_INFERENCE_PER_GPU and gpu["vram_free_mb"] > 1024:
                    return True, f"lane {lane} available: {gpu['vram_free_mb']}MB free, {len(running)} models loaded"
            return False, "no GPU lane with free inference slot"

        # Unknown task type — allow if global capacity exists
        return True, "ok (generic task, global capacity available)"