"""Local git provider — all operations via subprocess.run (no GitPython)."""

from __future__ import annotations

import os
import re
import subprocess
import time
from typing import Optional

from .base import (
    ActionPlan,
    ChangeSummary,
    CommitInfo,
    CommitResult,
    DiffHunk,
    DiffResult,
    LintResult,
    PlanStep,
    ProviderHealth,
    RepoActionProvider,
    RepoInfo,
    RepoStatus,
    RiskLevel,
    RollbackResult,
    SearchResult,
    TestResult,
    WorktreeInfo,
)

_DEFAULT_TIMEOUT = 30  # seconds


class LocalGitProvider(RepoActionProvider):
    """Repository action provider backed by local ``git`` via subprocess."""

    def __init__(self, timeout: int = _DEFAULT_TIMEOUT) -> None:
        self._timeout = timeout

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _run(
        self,
        args: list[str],
        cwd: str,
        *,
        check: bool = False,
        timeout: int | None = None,
        capture: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            args,
            cwd=cwd,
            capture_output=capture,
            text=True,
            check=check,
            timeout=timeout or self._timeout,
        )

    def _git(self, *args: str, cwd: str, timeout: int | None = None) -> str:
        """Run ``git <args>`` in *cwd* and return stripped stdout."""
        r = self._run(["git", *args], cwd=cwd, timeout=timeout)
        return r.stdout.strip()

    def _git_rc(self, *args: str, cwd: str, timeout: int | None = None) -> int:
        """Run ``git <args>`` and return the exit code."""
        r = self._run(["git", *args], cwd=cwd, timeout=timeout)
        return r.returncode

    @staticmethod
    def _is_git_repo(path: str) -> bool:
        return os.path.isdir(os.path.join(path, ".git")) or os.path.isfile(os.path.join(path, ".git"))

    def _ensure_repo(self, path: str) -> str:
        path = os.path.abspath(path)
        if not self._is_git_repo(path):
            raise ValueError(f"Not a git repository: {path}")
        return path

    def _current_branch(self, cwd: str) -> str:
        try:
            return self._git("rev-parse", "--abbrev-ref", "HEAD", cwd=cwd)
        except Exception:
            return "unknown"

    def _remote_url(self, cwd: str) -> Optional[str]:
        try:
            return self._git("remote", "get-url", "origin", cwd=cwd) or None
        except Exception:
            return None

    def _parse_commit_log(self, raw: str) -> Optional[CommitInfo]:
        """Parse ``git log --format`` output into a CommitInfo."""
        if not raw:
            return None
        parts = raw.split("\x00")
        if len(parts) < 5:
            return None
        return CommitInfo(
            hash=parts[0],
            author=parts[1],
            email=parts[2],
            date=parts[3],
            message=parts[4],
        )

    def _last_commit(self, cwd: str) -> Optional[CommitInfo]:
        fmt = "%H%x00%an%x00%ae%x00%aI%x00%s"
        try:
            raw = self._git("log", "-1", f"--format={fmt}", cwd=cwd)
            return self._parse_commit_log(raw)
        except Exception:
            return None

    def _ahead_behind(self, cwd: str) -> tuple[int, int]:
        try:
            raw = self._git("rev-list", "--left-right", "--count", "HEAD...@{upstream}", cwd=cwd)
            parts = raw.split()
            return int(parts[0]), int(parts[1])
        except Exception:
            return (0, 0)

    def _worktrees(self, cwd: str) -> list[WorktreeInfo]:
        try:
            raw = self._git("worktree", "list", "--porcelain", cwd=cwd)
        except Exception:
            return []
        trees: list[WorktreeInfo] = []
        current: dict[str, str] = {}
        for line in raw.splitlines():
            if not line:
                if current.get("worktree"):
                    trees.append(WorktreeInfo(
                        path=current["worktree"],
                        head=current.get("HEAD", ""),
                        branch=current.get("branch"),
                    ))
                current = {}
                continue
            key, _, val = line.partition(" ")
            current[key] = val
        if current.get("worktree"):
            trees.append(WorktreeInfo(
                path=current["worktree"],
                head=current.get("HEAD", ""),
                branch=current.get("branch"),
            ))
        return trees

    def _parse_porcelain_status(self, raw: str) -> RepoStatus:
        """Parse ``git status --porcelain=v2 --branch`` output."""
        branch = ""
        upstream = None
        ahead = behind = 0
        staged = unstaged = untracked = conflicted = 0

        for line in raw.splitlines():
            if line.startswith("# branch.head"):
                branch = line.split()[-1]
            elif line.startswith("# branch.upstream"):
                upstream = line.split()[-1]
            elif line.startswith("# branch.ab"):
                parts = line.split()
                ahead = int(parts[-2].lstrip("+"))
                behind = int(parts[-1].lstrip("-"))
            elif line.startswith("1 "):  # ordinary changed
                xy = line.split()[1]
                if xy[0] != ".":
                    staged += 1
                if xy[1] != ".":
                    unstaged += 1
            elif line.startswith("2 "):  # renamed/copied
                staged += 1
            elif line.startswith("u "):  # unmerged
                conflicted += 1
            elif line.startswith("? "):  # untracked
                untracked += 1

        changes = ChangeSummary(staged=staged, unstaged=unstaged, untracked=untracked, conflicted=conflicted)
        clean = (staged + unstaged + untracked + conflicted) == 0
        return RepoStatus(branch=branch, upstream=upstream, changes=changes, ahead=ahead, behind=behind, clean=clean)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def inspect(self, repo_path: str) -> RepoInfo:
        path = self._ensure_repo(repo_path)
        name = os.path.basename(path)
        branch = self._current_branch(path)
        remote = self._remote_url(path)
        ab = self._ahead_behind(path)
        wts = self._worktrees(path)
        lc = self._last_commit(path)

        # dirty check
        dirty = self._git_rc("diff", "--quiet", cwd=path) != 0
        if not dirty:
            dirty = self._git_rc("diff", "--cached", "--quiet", cwd=path) != 0

        return RepoInfo(
            path=path,
            name=name,
            branch=branch,
            remote_url=remote,
            dirty=dirty,
            ahead_behind=ab,
            worktrees=wts,
            last_commit=lc,
        )

    def status(self, repo_path: str) -> RepoStatus:
        path = self._ensure_repo(repo_path)
        raw = self._git("status", "--porcelain=v2", "--branch", cwd=path)
        return self._parse_porcelain_status(raw)

    def plan(self, repo_path: str, goal: str) -> ActionPlan:
        path = self._ensure_repo(repo_path)
        goal_lower = goal.lower()
        steps: list[PlanStep] = []
        risks: list[str] = []
        duration = "~5s"

        if "commit" in goal_lower:
            steps = [
                PlanStep(order=1, command="git add -A", description="Stage all changes", risk=RiskLevel.LOW),
                PlanStep(order=2, command="git diff --cached --stat", description="Review staged changes", risk=RiskLevel.LOW),
                PlanStep(order=3, command="git commit -m <message>", description="Create commit", risk=RiskLevel.LOW, reversible=True),
            ]
            risks = ["Ensure no secrets are staged"]
            duration = "~3s"
        elif "push" in goal_lower:
            steps = [
                PlanStep(order=1, command="git status", description="Check working tree state"),
                PlanStep(order=2, command="git push", description="Push to remote", risk=RiskLevel.MEDIUM, reversible=False),
            ]
            risks = ["Pushing unreviewed commits to shared branches"]
            duration = "~10s"
        elif "rollback" in goal_lower or "revert" in goal_lower:
            steps = [
                PlanStep(order=1, command="git log --oneline -10", description="Review recent commits"),
                PlanStep(order=2, command="git reset --hard <commit>", description="Reset to target commit", risk=RiskLevel.CRITICAL, reversible=False),
            ]
            risks = ["DESTROYS uncommitted changes", "Hard to undo if pushed"]
            duration = "~2s"
        elif "clean" in goal_lower:
            steps = [
                PlanStep(order=1, command="git clean -fd --dry-run", description="Preview removal of untracked files"),
                PlanStep(order=2, command="git clean -fd", description="Remove untracked files", risk=RiskLevel.HIGH, reversible=False),
            ]
            risks = ["Untracked files are permanently deleted"]
            duration = "~2s"
        elif "test" in goal_lower:
            steps = [PlanStep(order=1, command="pytest / make test / npm test", description="Run test suite", risk=RiskLevel.LOW)]
            duration = "~60s"
        elif "lint" in goal_lower:
            steps = [PlanStep(order=1, command="ruff / eslint / flake8", description="Run linter", risk=RiskLevel.LOW)]
            duration = "~10s"
        else:
            steps = [
                PlanStep(order=1, command="git status", description="Check current state"),
                PlanStep(order=2, command="git diff --stat", description="Review changes"),
                PlanStep(order=3, command="git log --oneline -5", description="Review recent history"),
            ]
            risks = ["No specific plan for this goal — manual review recommended"]
            duration = "~3s"

        return ActionPlan(goal=goal, steps=steps, risks=risks, estimated_duration=duration)

    def search(self, repo_path: str, query: str) -> list[SearchResult]:
        path = self._ensure_repo(repo_path)
        results: list[SearchResult] = []
        try:
            raw = self._git(
                "grep", "-n", "-I", "--no-color", "-e", query, cwd=path, timeout=60,
            )
        except subprocess.CalledProcessError:
            return []  # no matches
        except Exception:
            return []
        for line in raw.splitlines():
            parts = line.split(":", 2)
            if len(parts) >= 3:
                f, ln, content = parts
                try:
                    line_no = int(ln)
                except ValueError:
                    continue
                results.append(SearchResult(file=f, line_number=line_no, line=content))
        return results

    def test(self, repo_path: str) -> TestResult:
        path = self._ensure_repo(repo_path)
        # Detect test runner
        runners: list[tuple[list[str], str]] = []
        if os.path.isfile(os.path.join(path, "Makefile")):
            runners.append((["make", "test"], "make test"))
        if os.path.isfile(os.path.join(path, "package.json")):
            runners.append((["npm", "test"], "npm test"))
        if os.path.isfile(os.path.join(path, "pyproject.toml")) or os.path.isfile(os.path.join(path, "setup.py")):
            runners.append((["python3", "-m", "pytest", "-q", "--tb=short"], "pytest"))
        if os.path.isfile(os.path.join(path, "Cargo.toml")):
            runners.append((["cargo", "test", "--quiet"], "cargo test"))

        if not runners:
            return TestResult(output="No recognized test runner found.", command_run="(none)")

        cmd, cmd_str = runners[0]
        t0 = time.monotonic()
        try:
            r = self._run(cmd, cwd=path, timeout=120)
            elapsed = time.monotonic() - t0
            output = r.stdout + r.stderr
            passed = failed = errors = skipped = 0
            # Try to parse pytest output
            m = re.search(r"(\d+) passed", output)
            if m:
                passed = int(m.group(1))
            m = re.search(r"(\d+) failed", output)
            if m:
                failed = int(m.group(1))
            m = re.search(r"(\d+) error", output)
            if m:
                errors = int(m.group(1))
            m = re.search(r"(\d+) skipped", output)
            if m:
                skipped = int(m.group(1))
            # If nothing parsed, infer from exit code
            if passed == failed == errors == 0:
                if r.returncode == 0:
                    passed = 1
                else:
                    failed = 1
            return TestResult(
                passed=passed, failed=failed, errors=errors, skipped=skipped,
                duration=round(elapsed, 2), output=output[-4000:], command_run=cmd_str,
            )
        except subprocess.TimeoutExpired:
            return TestResult(output="Test timed out after 120s", command_run=cmd_str, duration=120.0)
        except FileNotFoundError:
            return TestResult(output=f"Command not found: {cmd[0]}", command_run=cmd_str)
        except Exception as exc:
            return TestResult(output=str(exc), command_run=cmd_str)

    def lint(self, repo_path: str) -> LintResult:
        path = self._ensure_repo(repo_path)
        runners: list[tuple[list[str], str]] = []
        if os.path.isfile(os.path.join(path, "pyproject.toml")):
            runners.append((["python3", "-m", "ruff", "check", "."], "ruff check"))
            runners.append((["python3", "-m", "flake8", "."], "flake8"))
        if os.path.isfile(os.path.join(path, "package.json")):
            runners.append((["npx", "eslint", "."], "eslint"))
        if os.path.isfile(os.path.join(path, "Cargo.toml")):
            runners.append((["cargo", "clippy", "--quiet"], "cargo clippy"))

        if not runners:
            return LintResult(output="No recognized linter found.", command_run="(none)")

        cmd, cmd_str = runners[0]
        t0 = time.monotonic()
        try:
            r = self._run(cmd, cwd=path, timeout=60)
            elapsed = time.monotonic() - t0
            output = r.stdout + r.stderr
            issues = output.count("\n")
            errors = len(re.findall(r"\berror\b", output, re.IGNORECASE))
            warnings = len(re.findall(r"\bwarn", output, re.IGNORECASE))
            return LintResult(
                issues=issues, errors=errors, warnings=warnings,
                output=output[-4000:], command_run=cmd_str, duration=round(elapsed, 2),
            )
        except subprocess.TimeoutExpired:
            return LintResult(output="Lint timed out after 60s", command_run=cmd_str)
        except FileNotFoundError:
            return LintResult(output=f"Command not found: {cmd[0]}", command_run=cmd_str)
        except Exception as exc:
            return LintResult(output=str(exc), command_run=cmd_str)

    def diff(self, repo_path: str, ref_a: Optional[str] = None, ref_b: Optional[str] = None) -> DiffResult:
        path = self._ensure_repo(repo_path)
        if ref_a and ref_b:
            cmd = ["git", "diff", f"{ref_a}..{ref_b}"]
        elif ref_a:
            cmd = ["git", "diff", ref_a]
        else:
            cmd = ["git", "diff"]
        try:
            r = self._run(cmd, cwd=path, timeout=30)
            raw = r.stdout
        except Exception:
            return DiffResult()

        files: list[str] = []
        total_add = total_rem = 0
        hunks: list[DiffHunk] = []
        current_file = ""
        hunk_lines: list[str] = []
        hunk_add = hunk_rem = 0
        hunk_header = ""

        for line in raw.splitlines():
            if line.startswith("diff --git"):
                if hunk_lines:
                    hunks.append(DiffHunk(header=hunk_header, added=hunk_add, removed=hunk_rem, content="\n".join(hunk_lines)))
                    hunk_lines = []
                    hunk_add = hunk_rem = 0
                m = re.search(r" b/(.+)$", line)
                current_file = m.group(1) if m else ""
                if current_file and current_file not in files:
                    files.append(current_file)
                hunk_header = line
            elif line.startswith("@@"):
                if hunk_lines:
                    hunks.append(DiffHunk(header=hunk_header, added=hunk_add, removed=hunk_rem, content="\n".join(hunk_lines)))
                    hunk_lines = []
                    hunk_add = hunk_rem = 0
                hunk_header = line
            elif line.startswith("+") and not line.startswith("+++"):
                hunk_add += 1
                total_add += 1
                hunk_lines.append(line)
            elif line.startswith("-") and not line.startswith("---"):
                hunk_rem += 1
                total_rem += 1
                hunk_lines.append(line)
            else:
                hunk_lines.append(line)

        if hunk_lines:
            hunks.append(DiffHunk(header=hunk_header, added=hunk_add, removed=hunk_rem, content="\n".join(hunk_lines)))

        return DiffResult(
            files_changed=files, total_added=total_add, total_removed=total_rem,
            hunks=hunks, raw=raw[-8000:],
        )

    def commit(self, repo_path: str, message: str, files: Optional[list[str]] = None) -> CommitResult:
        path = self._ensure_repo(repo_path)
        try:
            # Get current commit before
            before = self._git("rev-parse", "HEAD", cwd=path)
        except Exception:
            before = ""

        try:
            if files:
                for f in files:
                    self._run(["git", "add", f], cwd=path, check=True, timeout=10)
            else:
                self._run(["git", "add", "-A"], cwd=path, check=True, timeout=10)

            r = self._run(["git", "commit", "-m", message], cwd=path, timeout=15)
            if r.returncode != 0:
                return CommitResult(success=False, error=r.stderr.strip())

            after = self._git("rev-parse", "HEAD", cwd=path)
            # Files changed
            try:
                changed_raw = self._git("diff-tree", "--no-commit-id", "--name-only", "-r", after, cwd=path)
                changed = [f for f in changed_raw.splitlines() if f]
            except Exception:
                changed = files or []

            return CommitResult(
                success=True, commit_hash=after, message=message,
                files_changed=changed,
            )
        except subprocess.TimeoutExpired:
            return CommitResult(success=False, error="Commit timed out")
        except Exception as exc:
            return CommitResult(success=False, error=str(exc))

    def rollback(self, repo_path: str, to_commit: str) -> RollbackResult:
        path = self._ensure_repo(repo_path)
        try:
            current = self._git("rev-parse", "HEAD", cwd=path)
        except Exception:
            current = ""

        try:
            # Validate target exists
            self._run(["git", "rev-parse", "--verify", to_commit], cwd=path, check=True, timeout=10)
            r = self._run(["git", "reset", "--hard", to_commit], cwd=path, timeout=15)
            if r.returncode != 0:
                return RollbackResult(success=False, from_commit=current, to_commit=to_commit, error=r.stderr.strip())
            return RollbackResult(success=True, from_commit=current, to_commit=to_commit)
        except subprocess.CalledProcessError:
            return RollbackResult(success=False, from_commit=current, to_commit=to_commit, error=f"Invalid commit: {to_commit}")
        except subprocess.TimeoutExpired:
            return RollbackResult(success=False, from_commit=current, to_commit=to_commit, error="Rollback timed out")
        except Exception as exc:
            return RollbackResult(success=False, from_commit=current, to_commit=to_commit, error=str(exc))

    def health(self) -> ProviderHealth:
        try:
            r = self._run(["git", "--version"], cwd="/tmp")
            git_ver = r.stdout.strip()
        except Exception:
            return ProviderHealth(healthy=False, provider="local_git", git_available=False, details="git not found")

        return ProviderHealth(
            healthy=True,
            provider="local_git",
            version="1.0.0",
            git_available=True,
            git_version=git_ver,
        )

    def capabilities(self) -> list[str]:
        return [
            "inspect", "status", "plan", "search",
            "test", "lint", "diff", "commit",
            "rollback", "health",
        ]
