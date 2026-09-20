"""Verification Profiles - per-repo verification strategies."""
import os
import json
import subprocess
from typing import Optional


def _run(cmd: str, cwd: str = None, timeout: int = 120) -> tuple[int, str]:
    """Run command, return (exit_code, combined_output)."""
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout, cwd=cwd)
        return r.returncode, r.stdout + r.stderr
    except subprocess.TimeoutExpired:
        return -1, "TIMEOUT"
    except Exception as e:
        return -1, str(e)


# Built-in verification profiles
PROFILES = {
    "nextjs": {
        "name": "Next.js",
        "detect": lambda p: any(os.path.isfile(os.path.join(p, f)) for f in ["next.config.js", "next.config.ts", "next.config.mjs"]),
        "install": "pnpm install --no-frozen-lockfile 2>&1 | tail -5",
        "steps": [
            {"name": "lint", "cmd": "pnpm run lint 2>&1 | tail -20", "required": False},
            {"name": "typecheck", "cmd": "npx tsc --noEmit 2>&1 | tail -20", "required": False},
            {"name": "build", "cmd": "pnpm run build 2>&1 | tail -30", "required": True},
        ],
        "install_policy": "always",
    },
    "python-fastapi": {
        "name": "Python FastAPI",
        "detect": lambda p: _has_import(p, "fastapi"),
        "install": "pip install -r requirements.txt 2>&1 | tail -5 || true",
        "steps": [
            {"name": "lint", "cmd": "python3 -m ruff check . 2>&1 | tail -20 || python3 -m flake8 . 2>&1 | tail -20 || true", "required": False},
            {"name": "typecheck", "cmd": "python3 -m mypy . 2>&1 | tail -20 || true", "required": False},
            {"name": "test", "cmd": "python3 -m pytest -x --tb=short 2>&1 | tail -30", "required": True},
        ],
        "install_policy": "if_requirements_changed",
    },
    "python": {
        "name": "Python",
        "detect": lambda p: os.path.isfile(os.path.join(p, "setup.py")) or os.path.isfile(os.path.join(p, "pyproject.toml")),
        "install": "pip install -e . 2>&1 | tail -5 || true",
        "steps": [
            {"name": "lint", "cmd": "python3 -m ruff check . 2>&1 | tail -20 || true", "required": False},
            {"name": "test", "cmd": "python3 -m pytest -x --tb=short 2>&1 | tail -30", "required": True},
        ],
        "install_policy": "if_requirements_changed",
    },
    "typescript-pnpm": {
        "name": "TypeScript pnpm monorepo",
        "detect": lambda p: os.path.isfile(os.path.join(p, "pnpm-workspace.yaml")),
        "install": "pnpm install --no-frozen-lockfile 2>&1 | tail -5",
        "steps": [
            {"name": "lint", "cmd": "pnpm run lint 2>&1 | tail -20 || true", "required": False},
            {"name": "typecheck", "cmd": "pnpm run typecheck 2>&1 | tail -20 || npx tsc --noEmit 2>&1 | tail -20 || true", "required": False},
            {"name": "build", "cmd": "pnpm run build 2>&1 | tail -30", "required": True},
            {"name": "test", "cmd": "pnpm run test 2>&1 | tail -30", "required": True},
        ],
        "install_policy": "always",
    },
    "rust": {
        "name": "Rust",
        "detect": lambda p: os.path.isfile(os.path.join(p, "Cargo.toml")),
        "install": None,
        "steps": [
            {"name": "check", "cmd": "cargo check 2>&1 | tail -20", "required": True},
            {"name": "test", "cmd": "cargo test 2>&1 | tail -30", "required": True},
            {"name": "clippy", "cmd": "cargo clippy 2>&1 | tail -20 || true", "required": False},
        ],
        "install_policy": "none",
    },
    "go": {
        "name": "Go",
        "detect": lambda p: os.path.isfile(os.path.join(p, "go.mod")),
        "install": None,
        "steps": [
            {"name": "vet", "cmd": "go vet ./... 2>&1 | tail -20", "required": True},
            {"name": "test", "cmd": "go test ./... 2>&1 | tail -30", "required": True},
        ],
        "install_policy": "none",
    },
    "generic": {
        "name": "Generic",
        "detect": lambda p: True,
        "install": None,
        "steps": [],
        "install_policy": "none",
    },
}


def _has_import(repo_path: str, module: str) -> bool:
    """Check if a Python file imports a module."""
    for req_file in ["requirements.txt", "pyproject.toml"]:
        fpath = os.path.join(repo_path, req_file)
        if os.path.isfile(fpath):
            try:
                with open(fpath) as f:
                    content = f.read(10000)
                if module.lower() in content.lower():
                    return True
            except Exception:
                pass
    return False


def detect_profile(repo_path: str) -> str:
    """Detect the verification profile for a repo."""
    for profile_id, profile in PROFILES.items():
        if profile_id == "generic":
            continue
        try:
            if profile["detect"](repo_path):
                return profile_id
        except Exception:
            continue
    return "generic"


def get_profile(profile_id: str) -> dict:
    """Get a verification profile by ID."""
    return PROFILES.get(profile_id, PROFILES["generic"])


def verify(repo_path: str, profile_id: str = None, install: bool = True) -> dict:
    """Run verification for a repo.
    
    Returns: {profile, install_result, steps: [{name, exit_code, output, passed}]}
    """
    if profile_id is None:
        profile_id = detect_profile(repo_path)
    profile = get_profile(profile_id)

    result = {
        "profile": profile_id,
        "profile_name": profile["name"],
        "install": None,
        "steps": [],
        "passed": True,
        "failed_step": None,
    }

    # Install dependencies if needed
    if install and profile.get("install"):
        code, output = _run(profile["install"], cwd=repo_path, timeout=300)
        result["install"] = {"exit_code": code, "output": output[-500:]}

    # Run verification steps
    for step in profile.get("steps", []):
        code, output = _run(step["cmd"], cwd=repo_path, timeout=300)
        passed = code == 0 or not step.get("required", True)
        step_result = {
            "name": step["name"],
            "exit_code": code,
            "output": output[-1000:],
            "passed": passed,
            "required": step.get("required", True),
        }
        result["steps"].append(step_result)
        if not passed:
            result["passed"] = False
            result["failed_step"] = step["name"]
            break  # Stop at first required failure

    return result