"""Repo Registry — discovers, registers, and tracks repositories."""

import hashlib
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from db import execute, fetchall, fetchone

DEFAULT_ROOTS = [
    "/home/scott/ai-lab",
    "/home/scott/Settler",
    "/home/scott/MissionLedger",
    "/home/scott/ThermalOS",
]

# ── helpers ────────────────────────────────────────────────────────────────

def _git(repo_path: str, args: list[str], timeout: int = 10) -> str:
    """Run a git command in repo_path, return stdout stripped. Empty on error."""
    try:
        r = subprocess.run(
            ["git"] + args,
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return r.stdout.strip()
    except Exception:
        return ""


def _repo_id(path: str) -> str:
    return hashlib.sha256(os.path.realpath(path).encode()).hexdigest()[:16]


def _detect_languages(path: str) -> list[str]:
    ext_map = {
        ".py": "python", ".ts": "typescript", ".tsx": "typescript",
        ".js": "javascript", ".jsx": "javascript", ".rs": "rust",
        ".go": "go", ".java": "java", ".rb": "ruby", ".c": "c",
        ".cpp": "cpp", ".h": "c-header", ".css": "css", ".html": "html",
        ".sql": "sql", ".sh": "shell", ".yaml": "yaml", ".yml": "yaml",
        ".toml": "toml", ".json": "json", ".md": "markdown",
        ".vue": "vue", ".svelte": "svelte",
    }
    seen: set[str] = set()
    root = Path(path)
    for f in root.rglob("*"):
        if f.is_file() and not any(p.startswith(".") for p in f.relative_to(root).parts):
            lang = ext_map.get(f.suffix.lower())
            if lang:
                seen.add(lang)
        if len(seen) >= 20:
            break
    return sorted(seen)


def _detect_frameworks(path: str) -> list[str]:
    p = Path(path)
    fw: list[str] = []
    if (p / "next.config.js").exists() or (p / "next.config.mjs").exists() or (p / "next.config.ts").exists():
        fw.append("next.js")
    if (p / "nuxt.config.ts").exists() or (p / "nuxt.config.js").exists():
        fw.append("nuxt")
    req = p / "requirements.txt"
    if req.exists():
        text = req.read_text(errors="ignore").lower()
        if "fastapi" in text:
            fw.append("fastapi")
        if "django" in text:
            fw.append("django")
        if "flask" in text:
            fw.append("flask")
    pyproject = p / "pyproject.toml"
    if pyproject.exists():
        txt = pyproject.read_text(errors="ignore").lower()
        if "fastapi" in txt:
            fw.append("fastapi")
        if "django" in txt:
            fw.append("django")
        if "flask" in txt:
            fw.append("flask")
    pkg = p / "package.json"
    if pkg.exists():
        txt = pkg.read_text(errors="ignore")
        if "express" in txt:
            fw.append("express")
        if '"react"' in txt or "'react'" in txt:
            fw.append("react")
        if '"vue"' in txt:
            fw.append("vue")
    if (p / "Cargo.toml").exists():
        fw.append("rust")
    return sorted(set(fw))


def _detect_package_managers(path: str) -> list[str]:
    p = Path(path)
    pm: list[str] = []
    if (p / "package-lock.json").exists():
        pm.append("npm")
    if (p / "pnpm-lock.yaml").exists():
        pm.append("pnpm")
    if (p / "yarn.lock").exists():
        pm.append("yarn")
    if (p / "requirements.txt").exists() or (p / "pyproject.toml").exists():
        pm.append("pip")
    if (p / "Cargo.lock").exists() or (p / "Cargo.toml").exists():
        pm.append("cargo")
    if (p / "go.sum").exists():
        pm.append("go")
    return sorted(set(pm))


def _detect_build_system(path: str) -> list[str]:
    p = Path(path)
    bs: list[str] = []
    if (p / "Makefile").exists():
        bs.append("make")
    if (p / "Dockerfile").exists():
        bs.append("docker")
    if (p / "docker-compose.yml").exists() or (p / "docker-compose.yaml").exists():
        bs.append("docker-compose")
    if (p / "Cargo.toml").exists():
        bs.append("cargo")
    if (p / "package.json").exists():
        bs.append("npm-scripts")
    if (p / "pyproject.toml").exists():
        bs.append("pyproject")
    if (p / "turbo.json").exists():
        bs.append("turborepo")
    if (p / "nx.json").exists():
        bs.append("nx")
    return sorted(set(bs))


def _detect_test_commands(path: str) -> list[str]:
    p = Path(path)
    cmds: list[str] = []
    if (p / "pytest.ini").exists() or (p / "conftest.py").exists() or (p / "pyproject.toml").exists():
        cmds.append("pytest")
    pkg = p / "package.json"
    if pkg.exists():
        try:
            data = json.loads(pkg.read_text(errors="ignore"))
            scripts = data.get("scripts", {})
            if "test" in scripts:
                cmds.append("npm test")
        except Exception:
            pass
    if (p / "Cargo.toml").exists():
        cmds.append("cargo test")
    return sorted(set(cmds))


def _detect_lint_commands(path: str) -> list[str]:
    p = Path(path)
    cmds: list[str] = []
    if (p / ".eslintrc.json").exists() or (p / ".eslintrc.js").exists() or (p / "eslint.config.mjs").exists() or (p / "eslint.config.js").exists():
        cmds.append("eslint")
    if (p / ".flake8").exists() or (p / "setup.cfg").exists():
        cmds.append("flake8")
    if (p / "pyproject.toml").exists():
        txt = (p / "pyproject.toml").read_text(errors="ignore").lower()
        if "ruff" in txt:
            cmds.append("ruff")
        elif "flake8" in txt:
            cmds.append("flake8")
    if (p / "biome.json").exists():
        cmds.append("biome")
    if (p / "rustfmt.toml").exists():
        cmds.append("rustfmt")
    return sorted(set(cmds))


def _extract_meta(path: str) -> dict:
    """Extract all metadata for a repo at *path*."""
    remote_url = _git(path, ["remote", "get-url", "origin"])
    default_branch = _git(path, ["symbolic-ref", "refs/remotes/origin/HEAD"]) or ""
    if default_branch:
        default_branch = default_branch.split("/")[-1]  # e.g. origin/main -> main
    else:
        default_branch = "main"
    current_branch = _git(path, ["rev-parse", "--abbrev-ref", "HEAD"])
    head_hash = _git(path, ["rev-parse", "HEAD"])
    head_message = _git(path, ["log", "-1", "--format=%s"])
    dirty_out = _git(path, ["status", "--porcelain"])
    dirty = bool(dirty_out)

    return {
        "repo_id": _repo_id(path),
        "name": os.path.basename(os.path.realpath(path)),
        "canonical_path": os.path.realpath(path),
        "remote_url": remote_url or None,
        "default_branch": default_branch,
        "current_branch": current_branch or None,
        "head_hash": head_hash or None,
        "head_message": head_message or None,
        "dirty": dirty,
        "languages": _detect_languages(path),
        "frameworks": _detect_frameworks(path),
        "package_managers": _detect_package_managers(path),
        "build_system": _detect_build_system(path),
        "test_commands": _detect_test_commands(path),
        "lint_commands": _detect_lint_commands(path),
        "typecheck_commands": [],
        "deployment_platform": None,
        "database_deps": [],
        "containerized": (Path(path) / "Dockerfile").exists(),
        "ci_provider": None,
        "risk_classification": "unknown",
        "importance_classification": "normal",
        "health_state": {},
    }


# ── RepoRegistry class ────────────────────────────────────────────────────

class RepoRegistry:
    """Discovers and tracks repositories in the agent_governance DB."""

    # -- discovery ----------------------------------------------------------

    def discover(self, roots: list[str] | None = None) -> list[dict]:
        """Scan approved roots for git repos up to 2 levels deep."""
        roots = roots or DEFAULT_ROOTS
        repos: list[dict] = []
        seen: set[str] = set()
        for root in roots:
            root = os.path.expanduser(root)
            if not os.path.isdir(root):
                continue
            # Check if the root itself is a git repo
            if os.path.isdir(os.path.join(root, ".git")):
                rp = os.path.realpath(root)
                if rp not in seen:
                    seen.add(rp)
                    repos.append(_extract_meta(root))
            for depth1 in sorted(os.listdir(root)):
                p1 = os.path.join(root, depth1)
                if not os.path.isdir(p1):
                    continue
                if os.path.isdir(os.path.join(p1, ".git")):
                    rp = os.path.realpath(p1)
                    if rp not in seen:
                        seen.add(rp)
                        repos.append(_extract_meta(p1))
                # depth 2
                for depth2 in sorted(os.listdir(p1)):
                    p2 = os.path.join(p1, depth2)
                    if not os.path.isdir(p2):
                        continue
                    if os.path.isdir(os.path.join(p2, ".git")):
                        rp = os.path.realpath(p2)
                        if rp not in seen:
                            seen.add(rp)
                            repos.append(_extract_meta(p2))
        return repos

    # -- register -----------------------------------------------------------

    def register(self, path: str) -> dict:
        """Register (upsert) a repo into the DB. Returns the repo dict."""
        meta = _extract_meta(path)
        execute(
            """
            INSERT INTO repos (
                repo_id, name, canonical_path, remote_url, default_branch,
                current_branch, head_hash, head_message, dirty,
                languages, frameworks, package_managers, build_system,
                test_commands, lint_commands, typecheck_commands,
                deployment_platform, database_deps, containerized, ci_provider,
                risk_classification, importance_classification, health_state,
                last_inspection_at, created_at, updated_at
            ) VALUES (
                :repo_id, :name, :canonical_path, :remote_url, :default_branch,
                :current_branch, :head_hash, :head_message, :dirty,
                :languages, :frameworks, :package_managers, :build_system,
                :test_commands, :lint_commands, :typecheck_commands,
                :deployment_platform, :database_deps, :containerized, :ci_provider,
                :risk_classification, :importance_classification, :health_state,
                NOW(), NOW(), NOW()
            )
            ON CONFLICT (canonical_path) DO UPDATE SET
                repo_id = EXCLUDED.repo_id,
                name = EXCLUDED.name,
                remote_url = EXCLUDED.remote_url,
                default_branch = EXCLUDED.default_branch,
                current_branch = EXCLUDED.current_branch,
                head_hash = EXCLUDED.head_hash,
                head_message = EXCLUDED.head_message,
                dirty = EXCLUDED.dirty,
                languages = EXCLUDED.languages,
                frameworks = EXCLUDED.frameworks,
                package_managers = EXCLUDED.package_managers,
                build_system = EXCLUDED.build_system,
                test_commands = EXCLUDED.test_commands,
                lint_commands = EXCLUDED.lint_commands,
                containerized = EXCLUDED.containerized,
                risk_classification = EXCLUDED.risk_classification,
                importance_classification = EXCLUDED.importance_classification,
                health_state = EXCLUDED.health_state,
                last_inspection_at = NOW(),
                updated_at = NOW()
            """,
            {
                **meta,
                "languages": json.dumps(meta["languages"]),
                "frameworks": json.dumps(meta["frameworks"]),
                "package_managers": json.dumps(meta["package_managers"]),
                "build_system": json.dumps(meta["build_system"]),
                "test_commands": json.dumps(meta["test_commands"]),
                "lint_commands": json.dumps(meta["lint_commands"]),
                "typecheck_commands": json.dumps([]),
                "database_deps": json.dumps(meta["database_deps"]),
                "health_state": json.dumps(meta["health_state"]),
            },
        )
        return meta

    # -- update -------------------------------------------------------------

    def update(self, repo_id: str) -> dict | None:
        """Refresh metadata for a registered repo."""
        row = fetchone("SELECT canonical_path FROM repos WHERE repo_id = :rid", {"rid": repo_id})
        if not row:
            return None
        return self.register(row["canonical_path"])

    # -- get ----------------------------------------------------------------

    def get(self, repo_id: str) -> dict | None:
        """Return full state of a single repo."""
        return fetchone("SELECT * FROM repos WHERE repo_id = :rid", {"rid": repo_id})

    # -- list ---------------------------------------------------------------

    def list_repos(self) -> list[dict]:
        """Return all registered repos."""
        return fetchall("SELECT * FROM repos ORDER BY name")

    # -- scan_and_register --------------------------------------------------

    def scan_and_register(self, roots: list[str] | None = None) -> list[dict]:
        """Discover all repos from approved roots and register them."""
        discovered = self.discover(roots)
        registered: list[dict] = []
        for meta in discovered:
            self.register(meta["canonical_path"])
            registered.append(meta)
        return registered

    # -- remove -------------------------------------------------------------

    def remove(self, repo_id: str) -> None:
        """Remove a repo from the registry (cascades)."""
        execute("DELETE FROM repos WHERE repo_id = :rid", {"rid": repo_id})