"""Safe Auto-Fix Loop — deterministic, verified, rollback-capable fixes for health findings."""
import os
import subprocess
import json


def _run(cmd: str, cwd: str = None, timeout: int = 120) -> tuple:
    """Run shell command, return (exit_code, output)."""
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout, cwd=cwd)
        return r.returncode, r.stdout + r.stderr
    except subprocess.TimeoutExpired:
        return -1, "TIMEOUT"
    except Exception as e:
        return -1, str(e)


class AutoFixer:
    """Applies safe, verified fixes to health findings with rollback on failure."""

    def fix(self, repo_path: str, finding: dict, max_retries: int = 3) -> dict:
        """Fix a single health finding.

        Args:
            repo_path: absolute path to the repo
            finding: dict with keys dimension, description, file_path, recommended_action, can_fix_safely
            max_retries: max attempts (unused for most ops, reserved for iterative fixes)

        Returns:
            {status, steps_taken, verified, rolled_back}
        """
        steps = []
        verified = False
        rolled_back = False

        # Gate: only fix safe findings
        if not finding.get("can_fix_safely"):
            return {
                "status": "skipped",
                "reason": "not safe to auto-fix",
                "steps_taken": steps,
                "verified": False,
                "rolled_back": False,
            }

        dimension = finding.get("dimension", "")
        description = finding.get("description", "")
        file_path = finding.get("file_path")
        recommended_action = finding.get("recommended_action", "")
        desc_lower = description.lower()

        try:
            # --- dead_code / FIXME ---
            if dimension == "dead_code" and "FIXME" in desc_upper(finding):
                steps.append("Identified FIXME comments — reported for manual review")
                return _result("reported", steps, verified=False, rolled_back=False)

            # --- dead_code / TODO ---
            if dimension == "dead_code" and "TODO" in desc_upper(finding):
                steps.append("Identified TODO comments — reported for manual review")
                return _result("reported", steps, verified=False, rolled_back=False)

            # --- dead_code / HACK ---
            if dimension == "dead_code" and "HACK" in desc_upper(finding):
                steps.append("Identified HACK comments — reported for manual review")
                return _result("reported", steps, verified=False, rolled_back=False)

            # --- dead_code (generic) ---
            if dimension == "dead_code":
                steps.append("Dead code identified — reported for manual review")
                return _result("reported", steps, verified=False, rolled_back=False)

            # --- git / dirty tree ---
            if dimension == "git" and "dirty" in desc_lower:
                steps.append("Dirty tree detected — reported for manual commit/stash")
                return _result("reported", steps, verified=False, rolled_back=False)

            # --- lint ---
            if dimension == "lint":
                return self._fix_lint(repo_path, finding, steps, max_retries)

            # --- security / env tracked ---
            if dimension == "security" and ("env" in desc_lower or "tracked" in desc_lower or ".gitignore" in desc_lower):
                return self._fix_gitignore(repo_path, finding, steps)

            # --- security / .gitignore missing ---
            if dimension == "security" and "gitignore" in desc_lower:
                return self._fix_gitignore(repo_path, finding, steps)

            # --- tests / no test runner ---
            if dimension == "tests" and "runner" in desc_lower:
                steps.append("No test runner config — reported for manual setup")
                return _result("reported", steps, verified=False, rolled_back=False)

            # --- build / no build ---
            if dimension == "build" and "no build" in desc_lower:
                steps.append("No build config — reported for manual setup")
                return _result("reported", steps, verified=False, rolled_back=False)

            # --- deps / lockfile ---
            if dimension == "deps" and "lockfile" in desc_lower:
                return self._fix_lockfile(repo_path, finding, steps)

            # --- typecheck ---
            if dimension == "typecheck":
                steps.append("Typecheck config missing — reported for manual setup")
                return _result("reported", steps, verified=False, rolled_back=False)

            # --- ci ---
            if dimension == "ci":
                steps.append("CI config missing — reported for manual setup")
                return _result("reported", steps, verified=False, rolled_back=False)

            # --- deploy ---
            if dimension == "deploy":
                steps.append("Deploy config missing — reported for manual setup")
                return _result("reported", steps, verified=False, rolled_back=False)

            # Fallback: report
            steps.append(f"No auto-fix strategy for dimension={dimension}")
            return _result("reported", steps, verified=False, rolled_back=False)

        except Exception as e:
            steps.append(f"Exception during fix: {e}")
            return _result("error", steps, verified=False, rolled_back=False)

    def auto_fix_batch(self, repo_path: str, findings: list) -> list:
        """Fix all can_fix_safely findings in order.

        Returns list of fix results, one per finding.
        """
        results = []
        for finding in findings:
            if finding.get("can_fix_safely"):
                result = self.fix(repo_path, finding)
                results.append(result)
        return results

    # --- Private fix strategies ---

    def _fix_lint(self, repo_path: str, finding: dict, steps: list, max_retries: int) -> dict:
        """Auto-fix lint issues using ruff --fix or prettier."""
        has_python = any(
            os.path.isfile(os.path.join(repo_path, f))
            for f in ("pyproject.toml", "setup.py", "requirements.txt")
        )
        has_js = os.path.isfile(os.path.join(repo_path, "package.json"))

        fixed = False
        if has_python:
            code, out = _run("python3 -m ruff check --fix . 2>&1 | tail -20", cwd=repo_path)
            steps.append(f"ruff --fix: exit={code}")
            if code == 0:
                fixed = True

        if has_js:
            code, out = _run("npx prettier --write . 2>&1 | tail -20", cwd=repo_path)
            steps.append(f"prettier --write: exit={code}")
            if code == 0:
                fixed = True

        if not fixed:
            steps.append("No linter available to auto-fix")
            return _result("reported", steps, verified=False, rolled_back=False)

        # Verify fix didn't break anything
        verified = self._verify(repo_path, steps)
        if not verified:
            rolled_back = self._rollback(repo_path, steps)
            return _result("failed", steps, verified=False, rolled_back=rolled_back)

        return _result("fixed", steps, verified=True, rolled_back=False)

    def _fix_gitignore(self, repo_path: str, finding: dict, steps: list) -> dict:
        """Add .gitignore with standard entries."""
        gitignore_path = os.path.join(repo_path, ".gitignore")

        standard_entries = [
            "# Environment / secrets",
            ".env",
            ".env.local",
            ".env.*.local",
            "*.pem",
            "*.key",
            "",
            "# Python",
            "__pycache__/",
            "*.pyc",
            ".venv/",
            "venv/",
            "",
            "# Node",
            "node_modules/",
            "",
            "# Build",
            "dist/",
            "build/",
            "*.egg-info/",
            "",
            "# IDE",
            ".vscode/",
            ".idea/",
            "",
            "# OS",
            ".DS_Store",
            "Thumbs.db",
        ]

        existing = ""
        if os.path.isfile(gitignore_path):
            with open(gitignore_path, "r") as f:
                existing = f.read()

        # Add missing entries
        new_entries = []
        for entry in standard_entries:
            if entry and entry not in existing:
                new_entries.append(entry)

        if not new_entries:
            steps.append(".gitignore already has all standard entries")
            return _result("already_done", steps, verified=True, rolled_back=False)

        with open(gitignore_path, "a") as f:
            f.write("\n" + "\n".join(new_entries) + "\n")

        steps.append(f"Added {len(new_entries)} entries to .gitignore")
        return _result("fixed", steps, verified=True, rolled_back=False)

    def _fix_lockfile(self, repo_path: str, finding: dict, steps: list) -> dict:
        """Generate a lockfile if possible."""
        has_python = os.path.isfile(os.path.join(repo_path, "requirements.txt"))
        has_js = os.path.isfile(os.path.join(repo_path, "package.json"))

        if has_python:
            code, out = _run("pip install pip-tools && pip-compile requirements.txt 2>&1 | tail -10", cwd=repo_path)
            steps.append(f"pip-compile: exit={code}")
            if code == 0:
                verified = self._verify(repo_path, steps)
                return _result("fixed" if verified else "failed", steps, verified=verified, rolled_back=False)

        if has_js:
            code, out = _run("npm install --package-lock-only 2>&1 | tail -10", cwd=repo_path)
            steps.append(f"npm lockfile: exit={code}")
            if code == 0:
                verified = self._verify(repo_path, steps)
                return _result("fixed" if verified else "failed", steps, verified=verified, rolled_back=False)

        steps.append("Could not generate lockfile automatically")
        return _result("reported", steps, verified=False, rolled_back=False)

    def _verify(self, repo_path: str, steps: list) -> bool:
        """Run verification.profile to check the fix didn't break anything."""
        try:
            from verification import detect_profile, verify
            result = verify(repo_path, install=False)
            passed = result.get("passed", False)
            steps.append(f"Verification ({result.get('profile', 'unknown')}): {'passed' if passed else 'FAILED'}")
            return passed
        except Exception as e:
            steps.append(f"Verification error: {e}")
            return False

    def _rollback(self, repo_path: str, steps: list) -> bool:
        """Rollback changes via git checkout."""
        code, out = _run("git checkout -- . 2>&1", cwd=repo_path)
        rolled_back = code == 0
        steps.append(f"Rollback (git checkout): {'success' if rolled_back else 'FAILED'}")
        return rolled_back


def desc_upper(finding: dict) -> str:
    """Uppercase description for tag matching."""
    return (finding.get("description") or "").upper()


def _result(status: str, steps: list, verified: bool, rolled_back: bool) -> dict:
    return {
        "status": status,
        "steps_taken": steps,
        "verified": verified,
        "rolled_back": rolled_back,
    }