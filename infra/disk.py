"""Disk Management — scan mounts, caches, worktrees; safe cleanup."""
import os
import subprocess
import time
from pathlib import Path


def _run(cmd: list[str], timeout: int = 15) -> str:
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip()
    except Exception:
        return ""


def _du_mb(path: str) -> int:
    """Return directory size in MB via du, 0 on error."""
    out = _run(["du", "-sm", path], timeout=30)
    if out:
        try:
            return int(out.split()[0])
        except (ValueError, IndexError):
            pass
    return 0


def _is_under_protected(path: str) -> bool:
    """True if path is under a protected directory that cleanup must never touch."""
    protected = [
        "/home/scott/ai-lab",
        "/home/scott/Settler",
        "/home/scott/MissionLedger",
        "/home/scott/ThermalOS",
        "/mnt/ai-storage",
    ]
    real = os.path.realpath(path)
    for p in protected:
        if real.startswith(os.path.realpath(p)):
            return True
    return False


class DiskManager:
    """Scans disk usage and performs safe cleanup."""

    def scan(self) -> dict:
        """Full disk scan: mounts, temps, worktrees, models, cleanup."""
        mounts = self._scan_mounts()
        temps = self.get_temp_usage()
        worktrees = self.get_worktree_usage()
        models = self.get_model_cache()
        cleaned_bytes, warnings = self.cleanup_safe()
        return {
            "mounts": mounts,
            "temps": temps,
            "worktrees": worktrees,
            "models": models,
            "cleaned_bytes": cleaned_bytes,
            "warnings": warnings,
        }

    def _scan_mounts(self) -> list[dict]:
        """Check disk usage on all mount points via df."""
        out = _run(["df", "-h", "--output=source,fstype,size,used,avail,pcent,target"])
        mounts = []
        for line in out.splitlines()[1:]:
            parts = line.split()
            if len(parts) < 7:
                continue
            source, fstype, size, used, avail, pct, target = (
                parts[0], parts[1], parts[2], parts[3], parts[4], parts[5],
                " ".join(parts[6:]),
            )
            # Skip pseudo-filesystems
            if fstype in ("tmpfs", "devtmpfs", "squashfs", "overlay"):
                continue
            if source.startswith("/dev/loop"):
                continue
            usage_pct = int(pct.rstrip("%")) if pct.endswith("%") else 0
            mounts.append({
                "source": source,
                "fstype": fstype,
                "size": size,
                "used": used,
                "avail": avail,
                "usage_pct": usage_pct,
                "mount_point": target,
            })
        return mounts

    def get_temp_usage(self) -> list[dict]:
        """Check /home/scott/.hermes/cache/ sizes."""
        cache_root = Path("/home/scott/.hermes/cache")
        if not cache_root.is_dir():
            return []
        results = []
        for entry in sorted(cache_root.iterdir()):
            if entry.is_dir():
                mb = _du_mb(str(entry))
                results.append({"path": str(entry), "size_mb": mb})
        return results

    def get_worktree_usage(self) -> list[dict]:
        """Check for git worktrees."""
        out = _run(["git", "worktree", "list"])
        if not out:
            return []
        worktrees = []
        for line in out.splitlines():
            parts = line.split()
            if len(parts) >= 2:
                path = parts[0]
                branch = parts[1] if len(parts) > 1 else ""
                mb = _du_mb(path) if os.path.isdir(path) else 0
                worktrees.append({"path": path, "branch": branch, "size_mb": mb})
        return worktrees

    def get_model_cache(self) -> dict:
        """Check Ollama model storage and HuggingFace cache sizes."""
        ollama_path = os.path.expanduser("~/.ollama/models")
        hf_path = os.path.expanduser("~/.cache/huggingface")
        return {
            "ollama_models_mb": _du_mb(ollama_path) if os.path.isdir(ollama_path) else 0,
            "huggingface_cache_mb": _du_mb(hf_path) if os.path.isdir(hf_path) else 0,
        }

    def cleanup_safe(self) -> tuple[int, list[str]]:
        """Remove old cache entries (>72h), old scratch files, empty worktrees.
        NEVER removes: source repos, databases, model data, config, action receipts.
        Returns (cleaned_bytes, warnings).
        """
        cleaned_bytes = 0
        warnings: list[str] = []
        now = time.time()
        max_age = 72 * 3600  # 72 hours

        # 1. Clean old scratch files
        scratch = Path("/home/scott/.hermes/cache/scratch")
        if scratch.is_dir():
            for f in scratch.rglob("*"):
                if f.is_file():
                    try:
                        age = now - f.stat().st_mtime
                        if age > max_age:
                            size = f.stat().st_size
                            if not _is_under_protected(str(f)):
                                f.unlink()
                                cleaned_bytes += size
                    except OSError as e:
                        warnings.append(f"Could not remove scratch {f}: {e}")

        # 2. Clean old cache entries (>72h)
        cache_root = Path("/home/scott/.hermes/cache")
        if cache_root.is_dir():
            for entry in cache_root.iterdir():
                if entry.is_dir() and entry.name != "scratch":
                    try:
                        age = now - entry.stat().st_mtime
                        if age > max_age:
                            if not _is_under_protected(str(entry)):
                                size = _du_mb(str(entry)) * 1024 * 1024
                                import shutil
                                shutil.rmtree(entry)
                                cleaned_bytes += size
                    except OSError as e:
                        warnings.append(f"Could not remove cache {entry}: {e}")

        # 3. Clean empty worktrees
        out = _run(["git", "worktree", "list", "--porcelain"])
        if out:
            worktree_paths: list[str] = []
            for line in out.splitlines():
                if line.startswith("worktree "):
                    worktree_paths.append(line.split(" ", 1)[1])
            for wtp in worktree_paths:
                if os.path.isdir(wtp):
                    # Check if empty (no tracked files)
                    entries = [e for e in os.listdir(wtp) if e != ".git"]
                    if not entries and not _is_under_protected(wtp):
                        try:
                            size = _du_mb(wtp) * 1024 * 1024
                            _run(["git", "worktree", "remove", "--force", wtp])
                            cleaned_bytes += size
                        except Exception as e:
                            warnings.append(f"Could not remove worktree {wtp}: {e}")

        return cleaned_bytes, warnings