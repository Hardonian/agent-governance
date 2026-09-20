"""Repo Health Engine — deterministic health checks + priority backlog for Agent Governance."""

from __future__ import annotations

import json
import os
import re
import subprocess
from datetime import datetime, timezone
from typing import Optional

from db import execute, fetchall, fetchone
from providers.local_git import LocalGitProvider

_DEFAULT_TIMEOUT = 15  # seconds for shell commands

_SEVERITY_WEIGHT = {"critical": 100, "error": 70, "warning": 40, "info": 10}


def _run(cmd: list[str], cwd: str, timeout: int = _DEFAULT_TIMEOUT) -> subprocess.CompletedProcess[str]:
    """Run a shell command with timeout; return CompletedProcess."""
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)


def _file_exists(path: str, *parts: str) -> bool:
    return os.path.isfile(os.path.join(path, *parts))


def _dir_exists(path: str, *parts: str) -> bool:
    return os.path.isdir(os.path.join(path, *parts))


def _read_text(path: str, max_bytes: int = 200_000) -> str:
    try:
        with open(path, "r", errors="replace") as f:
            return f.read(max_bytes)
    except OSError:
        return ""


# ────────────────────────────────────────────────────────────────────
# Secret patterns (conservative — real keys only, not variable names)
# ────────────────────────────────────────────────────────────────────
_SECRET_PATTERNS: list[tuple[str, str]] = [
    (r'(?i)(?:api[_-]?key|secret[_-]?key|password|token|auth)\s*[:=]\s*["\'][A-Za-z0-9+/=_\-]{16,}["\']', "hardcoded credential"),
    (r'(?i)-----BEGIN\s+(?:RSA\s+)?PRIVATE\s+KEY-----', "embedded private key"),
    (r'(?i)(?:AKIA[0-9A-Z]{16})', "AWS access key"),
    (r'(?i)(?:sk-[A-Za-z0-9]{20,})', "possible OpenAI/Stripe secret key"),
    (r'(?i)(?:ghp_[A-Za-z0-9]{36})', "GitHub personal access token"),
]

# Files to skip when scanning for secrets
_SKIP_SECRET_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".tox", ".mypy_cache", ".ruff_cache"}
_SKIP_SECRET_EXTS = {".pyc", ".pyo", ".so", ".o", ".a", ".png", ".jpg", ".jpeg", ".gif", ".ico", ".woff", ".woff2", ".ttf", ".eot", ".lock"}


class HealthEngine:
    """Deterministic repo health analyser backed by the Agent Governance DB."""

    def __init__(self, provider: Optional[LocalGitProvider] = None) -> None:
        self._provider = provider or LocalGitProvider()

    # ── public API ─────────────────────────────────────────────────

    def analyze(self, repo_path: str, repo_id: str) -> list[dict]:
        """Run all health checks on *repo_path*, store findings, return them."""
        repo_path = os.path.abspath(repo_path)
        self._ensure_repo(repo_id, repo_path)
        findings: list[dict] = []

        findings.extend(self._check_git_state(repo_path, repo_id))
        findings.extend(self._check_build(repo_path, repo_id))
        findings.extend(self._check_tests(repo_path, repo_id))
        findings.extend(self._check_lint(repo_path, repo_id))
        findings.extend(self._check_typecheck(repo_path, repo_id))
        findings.extend(self._check_dependencies(repo_path, repo_id))
        findings.extend(self._check_security(repo_path, repo_id))
        findings.extend(self._check_ci(repo_path, repo_id))
        findings.extend(self._check_deploy(repo_path, repo_id))
        findings.extend(self._check_dead_code(repo_path, repo_id))

        # Persist findings
        for f in findings:
            self._store_finding(f)

        # Auto-generate backlog for this repo
        self.generate_backlog(repo_id)

        return findings

    def get_findings(self, repo_id: str) -> list[dict]:
        """Return all unresolved findings for a repo."""
        return fetchall(
            "SELECT * FROM health_findings WHERE repo_id = :rid AND resolved = FALSE ORDER BY created_at DESC",
            {"rid": repo_id},
        )

    def resolve_finding(self, finding_id: int) -> None:
        """Mark a finding as resolved."""
        execute(
            "UPDATE health_findings SET resolved = TRUE, resolved_at = NOW() WHERE id = :id",
            {"id": finding_id},
        )

    def generate_backlog(self, repo_id: str = None) -> list[dict]:
        """Create prioritised backlog items from unresolved findings."""
        if repo_id:
            findings = fetchall(
                "SELECT * FROM health_findings WHERE repo_id = :rid AND resolved = FALSE ORDER BY severity DESC",
                {"rid": repo_id},
            )
        else:
            findings = fetchall(
                "SELECT * FROM health_findings WHERE resolved = FALSE ORDER BY severity DESC",
            )

        # Remove old open backlog items for these findings (re-generate cleanly)
        finding_ids = [f["id"] for f in findings]
        if finding_ids:
            placeholders = ",".join(str(i) for i in finding_ids)
            execute(
                f"DELETE FROM backlog_items WHERE finding_id IN ({placeholders}) AND status = 'open'",
            )

        items: list[dict] = []
        for f in findings:
            score = self._priority_score(f)
            title = f"[{f['dimension'].upper()}] {f['description'][:120]}"
            execute(
                """INSERT INTO backlog_items
                   (repo_id, finding_id, title, description, severity, confidence,
                    blast_radius, effort_estimate, verification_available, reversible,
                    priority_score, status)
                   VALUES (:repo_id, :finding_id, :title, :desc, :sev, :conf,
                           :blast, :effort, :verif, :rev, :score, 'open')""",
                {
                    "repo_id": f["repo_id"],
                    "finding_id": f["id"],
                    "title": title,
                    "desc": f.get("description", ""),
                    "sev": f["severity"],
                    "conf": f.get("confidence", 1.0),
                    "blast": "small",
                    "effort": "unknown",
                    "verif": False,
                    "rev": f.get("can_fix_safely", False),
                    "score": score,
                },
            )
            items.append({"title": title, "priority_score": score, "severity": f["severity"], "finding_id": f["id"]})

        return items

    def get_backlog(self, limit: int = 20) -> list[dict]:
        """Return the top-priority open backlog items."""
        return fetchall(
            "SELECT * FROM backlog_items WHERE status = 'open' ORDER BY priority_score DESC LIMIT :lim",
            {"lim": limit},
        )

    # ── priority scoring ───────────────────────────────────────────

    @staticmethod
    def _priority_score(finding: dict) -> float:
        sev = _SEVERITY_WEIGHT.get(finding.get("severity", "info"), 10)
        conf = finding.get("confidence", 1.0)
        safe_bonus = 10 if finding.get("can_fix_safely") else 0
        return round(sev * conf + safe_bonus, 2)

    # ── DB persistence ─────────────────────────────────────────────

    @staticmethod
    def _ensure_repo(repo_id: str, repo_path: str) -> None:
        """Upsert into repos so FK constraints pass."""
        try:
            execute(
                """INSERT INTO repos (repo_id, name, canonical_path)
                   VALUES (:rid, :name, :path)
                   ON CONFLICT (repo_id) DO UPDATE SET updated_at = NOW()""",
                {"rid": repo_id, "name": os.path.basename(repo_path), "path": repo_path},
            )
        except Exception:
            # canonical_path unique constraint hit — update existing record
            execute(
                "UPDATE repos SET repo_id = :rid, updated_at = NOW() WHERE canonical_path = :path",
                {"rid": repo_id, "path": repo_path},
            )

    @staticmethod
    def _store_finding(f: dict) -> None:
        execute(
            """INSERT INTO health_findings
               (repo_id, dimension, severity, confidence, description,
                evidence, file_path, line_number, recommended_action, can_fix_safely)
               VALUES (:repo_id, :dimension, :severity, :confidence, :description,
                       :evidence, :file_path, :line_number, :recommended_action, :can_fix_safely)""",
            {
                "repo_id": f["repo_id"],
                "dimension": f["dimension"],
                "severity": f["severity"],
                "confidence": f.get("confidence", 1.0),
                "description": f["description"],
                "evidence": json.dumps(f.get("evidence", {})),
                "file_path": f.get("file_path"),
                "line_number": f.get("line_number"),
                "recommended_action": f.get("recommended_action", ""),
                "can_fix_safely": f.get("can_fix_safely", False),
            },
        )

    # ── helpers to build findings ──────────────────────────────────

    @staticmethod
    def _finding(
        repo_id: str,
        dimension: str,
        severity: str,
        description: str,
        *,
        confidence: float = 1.0,
        evidence: dict | None = None,
        file_path: str | None = None,
        line_number: int | None = None,
        recommended_action: str = "",
        can_fix_safely: bool = False,
    ) -> dict:
        return {
            "repo_id": repo_id,
            "dimension": dimension,
            "severity": severity,
            "confidence": confidence,
            "description": description,
            "evidence": evidence or {},
            "file_path": file_path,
            "line_number": line_number,
            "recommended_action": recommended_action,
            "can_fix_safely": can_fix_safely,
        }

    # ── Individual checks ──────────────────────────────────────────

    def _check_git_state(self, path: str, repo_id: str) -> list[dict]:
        findings: list[dict] = []
        try:
            info = self._provider.inspect(path)
        except Exception as exc:
            findings.append(self._finding(repo_id, "git", "error", f"Cannot inspect repo: {exc}"))
            return findings

        # Dirty tree
        if info.dirty:
            findings.append(self._finding(
                repo_id, "git", "warning", "Working tree has uncommitted changes",
                evidence={"staged": info.dirty},
                recommended_action="Commit or stash changes before automated work",
                can_fix_safely=True,
            ))

        # Detached HEAD
        if info.branch and info.branch.startswith("HEAD"):
            findings.append(self._finding(
                repo_id, "git", "warning", "Repository is in detached HEAD state",
                evidence={"branch": info.branch},
                recommended_action="Checkout a named branch",
            ))

        # Unpushed commits
        ahead, behind = info.ahead_behind
        if ahead > 0:
            findings.append(self._finding(
                repo_id, "git", "warning", f"{ahead} unpushed commit(s) on branch '{info.branch}'",
                evidence={"ahead": ahead, "behind": behind},
                recommended_action="Push commits to remote or squash before push",
                can_fix_safely=True,
            ))

        # Stale branch (check last commit age)
        if info.last_commit and info.last_commit.date:
            try:
                last = datetime.fromisoformat(info.last_commit.date.replace("Z", "+00:00"))
                age_days = (datetime.now(timezone.utc) - last).days
                if age_days > 90:
                    findings.append(self._finding(
                        repo_id, "git", "info", f"Branch '{info.branch}' last commit was {age_days} days ago",
                        evidence={"age_days": age_days, "last_commit": info.last_commit.date},
                        recommended_action="Consider archiving or merging stale branch",
                    ))
            except Exception:
                pass

        return findings

    def _check_build(self, path: str, repo_id: str) -> list[dict]:
        findings: list[dict] = []
        build_scripts: list[str] = []
        for name in ("Makefile", "Justfile", "Taskfile.yml", "Taskfile.yaml"):
            if _file_exists(path, name):
                build_scripts.append(name)
        if _file_exists(path, "package.json"):
            try:
                pkg = json.loads(_read_text(os.path.join(path, "package.json")))
                scripts = pkg.get("scripts", {})
                if scripts.get("build"):
                    build_scripts.append("package.json:scripts.build")
            except Exception:
                pass
        if _file_exists(path, "pyproject.toml"):
            content = _read_text(os.path.join(path, "pyproject.toml"))
            if "build-backend" in content or "[build-system]" in content:
                build_scripts.append("pyproject.toml")

        if not build_scripts:
            findings.append(self._finding(
                repo_id, "build", "info", "No build configuration found",
                recommended_action="Add a Makefile, Justfile, or package.json build script",
            ))
        return findings

    def _check_tests(self, path: str, repo_id: str) -> list[dict]:
        findings: list[dict] = []
        has_tests = False
        test_files: list[str] = []

        # Look for test directories/files
        for root, dirs, files in os.walk(path):
            # Skip hidden / vendor dirs
            dirs[:] = [d for d in dirs if not d.startswith(".") and d not in ("node_modules", "__pycache__", "venv", ".venv")]
            rel = os.path.relpath(root, path)
            for fname in files:
                if fname.startswith("test_") or fname.endswith("_test.py") or fname.endswith(".test.ts") or fname.endswith(".test.js") or fname.endswith(".spec.ts") or fname.endswith(".spec.js"):
                    test_files.append(os.path.join(rel, fname))
                    has_tests = True
                if len(test_files) >= 5:
                    break
            if has_tests:
                break

        # Check for test runner config
        has_runner = False
        if _file_exists(path, "pytest.ini") or _file_exists(path, "setup.cfg"):
            has_runner = True
        if _file_exists(path, "pyproject.toml"):
            content = _read_text(os.path.join(path, "pyproject.toml"))
            if "[tool.pytest" in content:
                has_runner = True
        if _file_exists(path, "jest.config.js") or _file_exists(path, "jest.config.ts") or _file_exists(path, "vitest.config.ts"):
            has_runner = True

        if not has_tests:
            findings.append(self._finding(
                repo_id, "tests", "warning", "No test files found in repository",
                recommended_action="Add test files (test_*.py, *.test.ts, etc.)",
            ))
        if has_tests and not has_runner:
            findings.append(self._finding(
                repo_id, "tests", "info", "Test files exist but no test runner config found",
                evidence={"test_files_sample": test_files[:3]},
                recommended_action="Add pytest.ini, jest.config, or equivalent test configuration",
                can_fix_safely=True,
            ))

        return findings

    def _check_lint(self, path: str, repo_id: str) -> list[dict]:
        findings: list[dict] = []
        lint_configs = [
            ".flake8", ".pylintrc", "pylintrc", ".eslintrc.js", ".eslintrc.json",
            ".eslintrc.yml", "eslint.config.js", "eslint.config.mjs", ".ruff.toml",
            "ruff.toml", ".clang-format", ".stylelintrc",
        ]
        has_lint = any(_file_exists(path, c) for c in lint_configs)
        if not has_lint and _file_exists(path, "pyproject.toml"):
            content = _read_text(os.path.join(path, "pyproject.toml"))
            if "[tool.ruff" in content or "[tool.flake8]" in content or "[tool.pylint" in content:
                has_lint = True
        if not has_lint and _file_exists(path, "package.json"):
            try:
                pkg = json.loads(_read_text(os.path.join(path, "package.json")))
                if pkg.get("devDependencies", {}).get("eslint") or pkg.get("dependencies", {}).get("eslint"):
                    has_lint = True
            except Exception:
                pass

        if not has_lint:
            findings.append(self._finding(
                repo_id, "lint", "info", "No linting configuration found",
                recommended_action="Add ruff.toml, .eslintrc, or equivalent linter config",
                can_fix_safely=True,
            ))
        return findings

    def _check_typecheck(self, path: str, repo_id: str) -> list[dict]:
        findings: list[dict] = []
        has_tsconfig = _file_exists(path, "tsconfig.json")
        has_mypy = _file_exists(path, "mypy.ini") or _file_exists(path, ".mypy.ini")
        if _file_exists(path, "pyproject.toml"):
            content = _read_text(os.path.join(path, "pyproject.toml"))
            if "[tool.mypy" in content:
                has_mypy = True

        # Only flag if the project looks like it should have type checking
        is_python = _file_exists(path, "pyproject.toml") or _file_exists(path, "setup.py") or _file_exists(path, "requirements.txt")
        is_typescript = has_tsconfig or _file_exists(path, "package.json")

        if is_python and not has_mypy:
            findings.append(self._finding(
                repo_id, "typecheck", "info", "No mypy/type-checker configuration for Python project",
                recommended_action="Add mypy.ini or [tool.mypy] to pyproject.toml",
                can_fix_safely=True,
            ))
        if is_typescript and not has_tsconfig and _file_exists(path, "package.json"):
            findings.append(self._finding(
                repo_id, "typecheck", "info", "TypeScript/JS project without tsconfig.json",
                recommended_action="Add tsconfig.json for type checking",
                can_fix_safely=True,
            ))
        return findings

    def _check_dependencies(self, path: str, repo_id: str) -> list[dict]:
        findings: list[dict] = []

        # Python lockfiles
        if _file_exists(path, "requirements.txt") and not any(
            _file_exists(path, n) for n in ("requirements.lock", "poetry.lock", "Pipfile.lock", "uv.lock")
        ):
            findings.append(self._finding(
                repo_id, "deps", "warning", "requirements.txt exists without a lockfile",
                recommended_action="Generate a lockfile (pip-compile, poetry lock, uv lock) for reproducible builds",
                can_fix_safely=True,
            ))

        # JS lockfiles
        if _file_exists(path, "package.json") and not any(
            _file_exists(path, n) for n in ("package-lock.json", "yarn.lock", "pnpm-lock.yaml")
        ):
            findings.append(self._finding(
                repo_id, "deps", "warning", "package.json exists without a lockfile",
                recommended_action="Run npm install / yarn / pnpm install to generate a lockfile",
                can_fix_safely=True,
            ))

        return findings

    def _check_security(self, path: str, repo_id: str) -> list[dict]:
        findings: list[dict] = []

        # .gitignore check
        if not _file_exists(path, ".gitignore"):
            findings.append(self._finding(
                repo_id, "security", "warning", "No .gitignore file found",
                recommended_action="Add a .gitignore to prevent committing secrets and build artifacts",
                can_fix_safely=True,
            ))

        # Scan for hardcoded secrets (limited, conservative)
        secret_hits: list[dict] = []
        for root, dirs, files in os.walk(path):
            dirs[:] = [d for d in dirs if d not in _SKIP_SECRET_DIRS and not d.startswith(".")]
            for fname in files:
                ext = os.path.splitext(fname)[1].lower()
                if ext in _SKIP_SECRET_EXTS:
                    continue
                fpath = os.path.join(root, fname)
                relpath = os.path.relpath(fpath, path)
                try:
                    content = _read_text(fpath, max_bytes=100_000)
                    for lineno, line in enumerate(content.splitlines(), 1):
                        for pattern, label in _SECRET_PATTERNS:
                            if re.search(pattern, line):
                                secret_hits.append({
                                    "file": relpath,
                                    "line": lineno,
                                    "type": label,
                                    "snippet": line.strip()[:80],
                                })
                                break
                        if len(secret_hits) >= 10:
                            break
                except Exception:
                    continue
                if len(secret_hits) >= 10:
                    break
            if len(secret_hits) >= 10:
                break

        for hit in secret_hits:
            findings.append(self._finding(
                repo_id, "security", "critical",
                f"Possible {hit['type']} in {hit['file']}:{hit['line']}",
                confidence=0.7,
                evidence={"snippet": hit["snippet"]},
                file_path=hit["file"],
                line_number=hit["line"],
                recommended_action="Move secret to environment variable or secret manager",
                can_fix_safely=False,
            ))

        return findings

    def _check_ci(self, path: str, repo_id: str) -> list[dict]:
        findings: list[dict] = []
        has_ci = _dir_exists(path, ".github", "workflows")
        if not has_ci:
            # Check other CI systems
            for name in (".travis.yml", ".circleci", "Jenkinsfile", ".gitlab-ci.yml", "azure-pipelines.yml", "bitbucket-pipelines.yml"):
                if _file_exists(path, name) or _dir_exists(path, name):
                    has_ci = True
                    break
        if not has_ci:
            findings.append(self._finding(
                repo_id, "ci", "warning", "No CI/CD configuration found",
                recommended_action="Add .github/workflows or equivalent CI configuration",
            ))
        return findings

    def _check_deploy(self, path: str, repo_id: str) -> list[dict]:
        findings: list[dict] = []
        deploy_configs: list[str] = []
        for name in ("Dockerfile", "docker-compose.yml", "docker-compose.yaml", "vercel.json", "fly.toml", "render.yaml", "Procfile", "railway.json"):
            if _file_exists(path, name):
                deploy_configs.append(name)

        if not deploy_configs:
            findings.append(self._finding(
                repo_id, "deploy", "info", "No deployment configuration found",
                recommended_action="Add Dockerfile, vercel.json, or equivalent deploy config if this service is deployed",
            ))
        return findings

    def _check_dead_code(self, path: str, repo_id: str) -> list[dict]:
        findings: list[dict] = []
        todo_hits: list[dict] = []

        for root, dirs, files in os.walk(path):
            dirs[:] = [d for d in dirs if d not in _SKIP_SECRET_DIRS and not d.startswith(".")]
            for fname in files:
                ext = os.path.splitext(fname)[1].lower()
                if ext in _SKIP_SECRET_EXTS:
                    continue
                fpath = os.path.join(root, fname)
                relpath = os.path.relpath(fpath, path)
                try:
                    content = _read_text(fpath, max_bytes=100_000)
                    for lineno, line in enumerate(content.splitlines(), 1):
                        upper = line.upper()
                        if "TODO" in upper or "FIXME" in upper or "HACK" in upper:
                            tag = "TODO" if "TODO" in upper else ("FIXME" if "FIXME" in upper else "HACK")
                            todo_hits.append({
                                "file": relpath,
                                "line": lineno,
                                "tag": tag,
                                "snippet": line.strip()[:100],
                            })
                        if len(todo_hits) >= 30:
                            break
                except Exception:
                    continue
                if len(todo_hits) >= 30:
                    break
            if len(todo_hits) >= 30:
                break

        # Summarise: one finding per tag type
        for tag in ("FIXME", "HACK", "TODO"):
            hits = [h for h in todo_hits if h["tag"] == tag]
            if hits:
                sev = "warning" if tag == "FIXME" else ("warning" if tag == "HACK" else "info")
                findings.append(self._finding(
                    repo_id, "dead_code", sev,
                    f"{len(hits)} {tag} comment(s) found in source",
                    evidence={"count": len(hits), "samples": hits[:3]},
                    recommended_action=f"Address or remove {tag} comments",
                    can_fix_safely=True,
                ))

        return findings