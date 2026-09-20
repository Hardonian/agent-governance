"""Quick Action Packs - reusable repo operations with governance."""
import os
import json
import subprocess
import time
import uuid
from typing import Optional


def _run(cmd: str, cwd: str = None, timeout: int = 60) -> tuple:
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout, cwd=cwd)
        return r.returncode, (r.stdout + r.stderr)[-2000:]
    except subprocess.TimeoutExpired:
        return -1, "TIMEOUT"
    except Exception as e:
        return -1, str(e)


class ActionPack:
    """Executes repository actions with governance and receipt support."""

    def __init__(self, autonomy_level: int = 1, gateway=None):
        self.autonomy_level = autonomy_level
        self.gateway = gateway

    def _record(self, repo_id, action, result):
        try:
            from db import execute
            execute("""
                INSERT INTO action_receipts (receipt_id, repo_id, interpreted_action, autonomy_level, final_status, verification, created_at)
                VALUES (:rid, :repo, :action, :level, :status, :result, NOW())
            """, {"rid": str(uuid.uuid4()), "repo": repo_id, "action": action,
                  "level": self.autonomy_level, "status": result.get("status", "?"),
                  "result": json.dumps(result)})
        except Exception:
            pass

    def inspect(self, repo_path: str, repo_id: str = None) -> dict:
        result = {"action": "inspect", "path": repo_path, "items": {}}
        code, out = _run("git log --oneline -5", cwd=repo_path)
        result["items"]["recent_commits"] = out.strip().split('\n') if code == 0 and out.strip() else []
        code, out = _run("git branch -a", cwd=repo_path)
        result["items"]["branches"] = [b.strip() for b in out.strip().split('\n') if b.strip()] if code == 0 else []
        code, out = _run("git status --porcelain", cwd=repo_path)
        result["items"]["dirty_files"] = len([l for l in out.strip().split('\n') if l.strip()]) if code == 0 else 0
        code, out = _run("git remote -v", cwd=repo_path)
        result["items"]["remotes"] = out.strip() if code == 0 else ""
        code, out = _run("find . -maxdepth 3 -type f -not -path './.git/*' -not -path '*/node_modules/*' -not -path '*/.next/*' -not -path '*/dist/*' -not -path '*/__pycache__/*' -not -path '*/target/*' -not -path '*/.venv/*' -not -path '*/build/*' | wc -l", cwd=repo_path)
        result["items"]["file_count"] = int(out.strip()) if code == 0 else 0
        code, out = _run("find . -maxdepth 4 -type f -not -path './.git/*' -not -path '*/node_modules/*' -not -path '*/.next/*' -not -path '*/dist/*' -not -path '*/__pycache__/*' -not -path '*/target/*' | sed 's/.*\\.//' | sort | uniq -c | sort -rn | head -10", cwd=repo_path)
        result["items"]["languages"] = out.strip() if code == 0 else ""
        result["status"] = "ok"
        if repo_id:
            self._record(repo_id, "inspect", result)
        return result

    def status(self, repo_path: str) -> dict:
        result = {"action": "status", "path": repo_path}
        code, out = _run("git status --porcelain", cwd=repo_path)
        result["dirty"] = bool(out.strip()) if code == 0 else True
        code, out = _run("git branch --show-current", cwd=repo_path)
        result["branch"] = out.strip() if code == 0 else "unknown"
        code, out = _run("git log --oneline -1", cwd=repo_path)
        result["head"] = out.strip() if code == 0 else "unknown"
        result["status"] = "ok"
        return result

    def search(self, repo_path: str, query: str) -> dict:
        code, out = _run(
            f"grep -rn '{query}' --include='*.py' --include='*.ts' --include='*.js' --include='*.rs' "
            f"--include='*.go' --include='*.yaml' --include='*.yml' --include='*.json' "
            f"--exclude-dir=node_modules --exclude-dir=.git --exclude-dir=dist "
            f"--exclude-dir=.next --exclude-dir=target --exclude-dir=__pycache__ "
            f"--exclude-dir=.venv --exclude-dir=build . 2>/dev/null | head -30",
            cwd=repo_path, timeout=15
        )
        matches = []
        if code == 0 and out.strip():
            for line in out.strip().split('\n'):
                parts = line.split(':', 2)
                if len(parts) >= 3:
                    matches.append({"file": parts[0], "line": parts[1], "content": parts[2][:200]})
        return {"action": "search", "query": query, "matches": matches, "count": len(matches), "status": "ok"}

    def diff(self, repo_path: str) -> dict:
        code, out = _run("git diff --stat", cwd=repo_path)
        stat = out.strip() if code == 0 else ""
        code, out = _run("git diff", cwd=repo_path)
        return {"action": "diff", "stat": stat, "diff": out[:5000] if code == 0 else "", "status": "ok"}

    def test(self, repo_path: str, repo_id: str = None) -> dict:
        test_cmd = None
        # Try package.json
        code, out = _run("python3 -c \"import json; d=json.load(open('package.json')); print(d.get('scripts',{}).get('test',''))\"", cwd=repo_path)
        if out.strip() and out.strip() != "None":
            test_cmd = out.strip() + " 2>&1 | tail -50"
        elif os.path.isfile(os.path.join(repo_path, "pyproject.toml")) or os.path.isfile(os.path.join(repo_path, "pytest.ini")):
            test_cmd = "python3 -m pytest -x --tb=short 2>&1 | tail -50"
        elif os.path.isfile(os.path.join(repo_path, "Cargo.toml")):
            test_cmd = "cargo test 2>&1 | tail -50"
        elif os.path.isfile(os.path.join(repo_path, "go.mod")):
            test_cmd = "go test ./... 2>&1 | tail -50"
        if not test_cmd:
            return {"action": "test", "output": "No test command detected", "status": "skipped"}
        start = time.time()
        code, out = _run(test_cmd, cwd=repo_path, timeout=300)
        result = {"action": "test", "exit_code": code, "passed": code == 0, "output": out[-2000:], "duration_s": round(time.time() - start, 1), "status": "ok" if code == 0 else "failed"}
        if repo_id:
            self._record(repo_id, "test", result)
        return result

    def lint(self, repo_path: str) -> dict:
        cmds = []
        if os.path.isfile(os.path.join(repo_path, "pyproject.toml")):
            cmds.append("python3 -m ruff check . 2>&1 | tail -30")
        if any(os.path.isfile(os.path.join(repo_path, f)) for f in [".eslintrc.js", ".eslintrc.json", ".eslintrc"]):
            cmds.append("npx eslint . --max-warnings=100 2>&1 | tail -30")
        if os.path.isfile(os.path.join(repo_path, "Cargo.toml")):
            cmds.append("cargo clippy 2>&1 | tail -30")
        if not cmds:
            return {"action": "lint", "output": "No lint config detected", "status": "skipped"}
        results = []
        for cmd in cmds:
            code, out = _run(cmd, cwd=repo_path, timeout=120)
            results.append({"cmd": cmd, "exit_code": code, "output": out[-500:]})
        return {"action": "lint", "results": results, "status": "ok"}

    def secret_scan(self, repo_path: str, repo_id: str = None) -> dict:
        patterns = [
            ("AWS key", r"AKIA[0-9A-Z]{16}"),
            ("Private key", r"-----BEGIN.*PRIVATE KEY-----"),
            ("Generic API key", r"['\"]?[A-Za-z0-9_-]*(?:api|key|token|secret|password)[A-Za-z0-9_-]*['\"]?\s*[:=]\s*['\"][A-Za-z0-9+/=_-]{20,}"),
        ]
        findings = []
        for name, pat in patterns:
            code, out = _run(f"grep -rEn '{pat}' --include='*.py' --include='*.ts' --include='*.js' --include='*.env*' --include='*.yaml' --include='*.yml' --include='*.toml' --exclude-dir=node_modules --exclude-dir=.git --exclude-dir=.venv --exclude-dir=dist . 2>/dev/null | head -5", cwd=repo_path, timeout=15)
            if code == 0 and out.strip():
                for line in out.strip().split('\n')[:5]:
                    parts = line.split(':', 2)
                    findings.append({"type": name, "file": parts[0] if parts else "", "line": parts[1] if len(parts) > 1 else ""})
        code, out = _run("git ls-files '*.env' '*.env.*' 2>/dev/null", cwd=repo_path)
        env_tracked = [f for f in out.strip().split('\n') if f.strip()] if code == 0 else []
        result = {"action": "secret_scan", "findings": findings, "env_tracked": env_tracked, "count": len(findings), "status": "ok"}
        if repo_id:
            self._record(repo_id, "secret_scan", result)
        return result

    def rollback(self, repo_path: str, commit_hash: str, repo_id: str = None) -> dict:
        if self.autonomy_level < 1:
            return {"action": "rollback", "status": "blocked", "reason": "Requires autonomy level >= 1"}
        code, out = _run(f"git cat-file -t {commit_hash}", cwd=repo_path)
        if code != 0:
            return {"action": "rollback", "status": "failed", "reason": f"Commit {commit_hash} not found"}
        code, out = _run(f"git reset --hard {commit_hash}", cwd=repo_path)
        if code != 0:
            return {"action": "rollback", "status": "failed", "reason": out[-500:]}
        _, head = _run("git log --oneline -1", cwd=repo_path)
        result = {"action": "rollback", "target": commit_hash, "head": head.strip(), "status": "ok"}
        if repo_id:
            self._record(repo_id, "rollback", result)
        return result

    def action_history(self, repo_id: str, limit: int = 20) -> list:
        try:
            from db import fetchall
            return fetchall("""
                SELECT receipt_id, interpreted_action, final_status, autonomy_level, created_at
                FROM action_receipts WHERE repo_id = :repo ORDER BY created_at DESC LIMIT :limit
            """, {"repo": repo_id, "limit": limit})
        except Exception:
            return []


PACKS = {
    "core": ["inspect", "status", "search", "diff", "map"],
    "quality": ["test", "lint", "typecheck", "build", "review"],
    "fix": ["fix-build", "fix-tests", "fix-lint", "fix-dependency", "safe-refactor"],
    "security": ["secret-scan", "dependency-security", "auth-review", "permission-review"],
    "release": ["release-check", "migration-check", "deployment-check", "changelog"],
    "git": ["branch", "commit-prepare", "PR-prepare", "rollback", "action-history"],
}