"""GitHub API provider — repository actions via the ``gh`` CLI.

Implements the same ``RepoActionProvider`` interface as ``LocalGitProvider``
but delegates to ``gh`` (GitHub CLI) for remote operations.  Also exposes
GitHub-specific helpers: ``create_pr``, ``review_pr``, ``security_scan``.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from typing import Any, Optional

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
_GH_TIMEOUT = 45  # gh CLI can be slower over the network


class AuthError(RuntimeError):
    """Raised when ``gh`` reports an authentication failure."""


class GitHubProvider(RepoActionProvider):
    """Repository action provider backed by the GitHub CLI (``gh``).

    Falls back to local ``git`` for operations that are inherently local
    (commit, rollback, test, lint).  Uses ``gh`` for everything that
    benefits from the GitHub API (inspect remote, PRs, search, security).
    """

    def __init__(self, timeout: int = _DEFAULT_TIMEOUT) -> None:
        self._timeout = timeout

    # ------------------------------------------------------------------
    # Low-level helpers
    # ------------------------------------------------------------------

    def _run(
        self,
        args: list[str],
        cwd: str = "/tmp",
        *,
        check: bool = False,
        timeout: int | None = None,
    ) -> subprocess.CompletedProcess[str]:
        """Run a command, raising ``AuthError`` on gh auth failures."""
        try:
            r = subprocess.run(
                args,
                cwd=cwd,
                capture_output=True,
                text=True,
                check=check,
                timeout=timeout or self._timeout,
            )
        except subprocess.TimeoutExpired:
            raise
        # Detect gh auth failures
        combined = (r.stdout + r.stderr).lower()
        if r.returncode != 0 and ("not logged in" in combined or "auth" in combined and "required" in combined):
            raise AuthError(f"GitHub authentication failed: {r.stderr.strip()}")
        return r

    def _gh(self, *args: str, cwd: str = "/tmp", timeout: int | None = None) -> str:
        """Run ``gh <args>`` and return stripped stdout."""
        r = self._run(["gh", *args], cwd=cwd, timeout=timeout or _GH_TIMEOUT)
        if r.returncode != 0:
            raise RuntimeError(f"gh {' '.join(args)} failed: {r.stderr.strip()}")
        return r.stdout.strip()

    def _gh_json(self, *args: str, cwd: str = "/tmp", timeout: int | None = None) -> Any:
        """Run ``gh <args>`` and parse JSON output."""
        raw = self._gh(*args, cwd=cwd, timeout=timeout)
        return json.loads(raw) if raw else {}

    def _git(self, *args: str, cwd: str, timeout: int | None = None) -> str:
        r = self._run(["git", *args], cwd=cwd, timeout=timeout or self._timeout)
        if r.returncode != 0:
            raise RuntimeError(f"git {' '.join(args)} failed: {r.stderr.strip()}")
        return r.stdout.strip()

    def _git_rc(self, *args: str, cwd: str, timeout: int | None = None) -> int:
        r = self._run(["git", *args], cwd=cwd, timeout=timeout or self._timeout)
        return r.returncode

    @staticmethod
    def _is_git_repo(path: str) -> bool:
        return os.path.isdir(os.path.join(path, ".git")) or os.path.isfile(os.path.join(path, ".git"))

    def _ensure_repo(self, path: str) -> str:
        path = os.path.abspath(path)
        if not self._is_git_repo(path):
            raise ValueError(f"Not a git repository: {path}")
        return path

    def _owner_repo(self, cwd: str) -> tuple[str, str]:
        """Derive (owner, repo) from the git remote origin URL."""
        try:
            remote = self._git("remote", "get-url", "origin", cwd=cwd)
        except Exception:
            raise ValueError("No remote 'origin' configured")
        # SSH: git@github.com:owner/repo.git  |  HTTPS: https://github.com/owner/repo.git
        m = re.search(r"github\.com[:/](.+?)/(.+?)(?:\.git)?$", remote)
        if not m:
            raise ValueError(f"Cannot parse GitHub owner/repo from remote: {remote}")
        return m.group(1), m.group(2)

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

    def _last_commit(self, cwd: str) -> Optional[CommitInfo]:
        fmt = "%H%x00%an%x00%ae%x00%aI%x00%s"
        try:
            raw = self._git("log", "-1", f"--format={fmt}", cwd=cwd)
        except Exception:
            return None
        parts = raw.split("\x00")
        if len(parts) < 5:
            return None
        return CommitInfo(
            hash=parts[0], author=parts[1], email=parts[2],
            date=parts[3], message=parts[4],
        )

    def _ahead_behind(self, cwd: str) -> tuple[int, int]:
        try:
            raw = self._git("rev-list", "--left-right", "--count", "HEAD...@{upstream}", cwd=cwd)
            parts = raw.split()
            return int(parts[0]), int(parts[1])
        except Exception:
            return (0, 0)

    # ------------------------------------------------------------------
    # RepoActionProvider interface
    # ------------------------------------------------------------------

    def inspect(self, repo_path: str) -> RepoInfo:
        """Inspect via ``gh repo view`` enriched with local git state."""
        path = self._ensure_repo(repo_path)
        name = os.path.basename(path)
        branch = self._current_branch(path)
        remote = self._remote_url(path)
        ab = self._ahead_behind(path)
        lc = self._last_commit(path)

        dirty = self._git_rc("diff", "--quiet", cwd=path) != 0
        if not dirty:
            dirty = self._git_rc("diff", "--cached", "--quiet", cwd=path) != 0

        worktrees: list[WorktreeInfo] = []
        try:
            owner, repo = self._owner_repo(path)
            # Try to enrich with gh data (non-fatal)
            info = self._gh_json("repo", "view", f"{owner}/{repo}", "--json", "name,defaultBranchRef,isEmpty")
        except Exception:
            info = {}

        return RepoInfo(
            path=path,
            name=name,
            branch=branch,
            remote_url=remote,
            dirty=dirty,
            ahead_behind=ab,
            worktrees=worktrees,
            last_commit=lc,
        )

    def status(self, repo_path: str) -> RepoStatus:
        """Local git status plus open PR count from ``gh pr list``."""
        path = self._ensure_repo(repo_path)
        raw = self._git("status", "--porcelain=v2", "--branch", cwd=path)
        return self._parse_porcelain_status(raw)

    def _parse_porcelain_status(self, raw: str) -> RepoStatus:
        branch = upstream = ""
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
            elif line.startswith("1 "):
                xy = line.split()[1]
                if xy[0] != ".":
                    staged += 1
                if xy[1] != ".":
                    unstaged += 1
            elif line.startswith("2 "):
                staged += 1
            elif line.startswith("u "):
                conflicted += 1
            elif line.startswith("? "):
                untracked += 1
        changes = ChangeSummary(staged=staged, unstaged=unstaged, untracked=untracked, conflicted=conflicted)
        clean = (staged + unstaged + untracked + conflicted) == 0
        return RepoStatus(branch=branch, upstream=upstream, changes=changes, ahead=ahead, behind=behind, clean=clean)

    def plan(self, repo_path: str, goal: str) -> ActionPlan:
        path = self._ensure_repo(repo_path)
        goal_lower = goal.lower()
        steps: list[PlanStep] = []
        risks: list[str] = []
        duration = "~5s"

        if "pr" in goal_lower:
            steps = [
                PlanStep(order=1, command="git status", description="Check working tree"),
                PlanStep(order=2, command="git push -u origin HEAD", description="Push branch to remote", risk=RiskLevel.LOW),
                PlanStep(order=3, command="gh pr create", description="Open pull request", risk=RiskLevel.LOW),
            ]
            risks = ["Ensure branch is up-to-date with base"]
            duration = "~15s"
        elif "security" in goal_lower:
            steps = [
                PlanStep(order=1, command="gh api repos/{owner}/{repo}/code-scanning/alerts", description="Check code scanning alerts"),
                PlanStep(order=2, command="gh api repos/{owner}/{repo}/dependabot/alerts", description="Check Dependabot alerts"),
            ]
            risks = ["Alerts may include sensitive vulnerability details"]
            duration = "~10s"
        elif "review" in goal_lower:
            steps = [
                PlanStep(order=1, command="gh pr view", description="View PR metadata"),
                PlanStep(order=2, command="gh pr diff", description="Get PR diff"),
                PlanStep(order=3, command="gh pr checks", description="Check CI status"),
            ]
            risks = []
            duration = "~10s"
        elif "commit" in goal_lower:
            steps = [
                PlanStep(order=1, command="git add -A", description="Stage all changes", risk=RiskLevel.LOW),
                PlanStep(order=2, command="git diff --cached --stat", description="Review staged", risk=RiskLevel.LOW),
                PlanStep(order=3, command="git commit -m <message>", description="Create commit", risk=RiskLevel.LOW),
            ]
            risks = ["Ensure no secrets are staged"]
            duration = "~3s"
        else:
            steps = [
                PlanStep(order=1, command="git status", description="Check current state"),
                PlanStep(order=2, command="gh pr list", description="List open PRs"),
                PlanStep(order=3, command="git log --oneline -5", description="Recent history"),
            ]
            risks = ["No specific plan — manual review recommended"]
            duration = "~3s"
        return ActionPlan(goal=goal, steps=steps, risks=risks, estimated_duration=duration)

    def search(self, repo_path: str, query: str) -> list[SearchResult]:
        """Search via ``gh search code`` for the repo."""
        path = self._ensure_repo(repo_path)
        try:
            owner, repo = self._owner_repo(path)
        except Exception:
            return []
        try:
            raw = self._gh(
                "search", "code", query,
                "--repo", f"{owner}/{repo}",
                "--limit", "30",
                "--json", "path,lineNumber,textMatches",
                cwd=path,
                timeout=60,
            )
        except (RuntimeError, subprocess.TimeoutExpired):
            return []
        if not raw:
            return []
        try:
            items = json.loads(raw)
        except json.JSONDecodeError:
            return []
        results: list[SearchResult] = []
        for item in items:
            f = item.get("path", "")
            ln = item.get("lineNumber", 0)
            matches = item.get("textMatches", [])
            line_text = matches[0]["fragment"] if matches else ""
            results.append(SearchResult(file=f, line_number=ln, line=line_text))
        return results

    def test(self, repo_path: str) -> TestResult:
        """Run tests locally (same logic as LocalGitProvider)."""
        path = self._ensure_repo(repo_path)
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
        import time
        t0 = time.monotonic()
        try:
            r = self._run(cmd, cwd=path, timeout=120)
            elapsed = time.monotonic() - t0
            output = r.stdout + r.stderr
            passed = failed = errors = skipped = 0
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
            if passed == failed == errors == 0:
                passed = 1 if r.returncode == 0 else 0
                failed = 0 if r.returncode == 0 else 1
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
        """Run linter locally (same logic as LocalGitProvider)."""
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
        import time
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
        """Get diff — tries ``gh pr diff`` if a PR exists for the current branch, else falls back to git."""
        path = self._ensure_repo(repo_path)
        # Try gh pr diff for the current branch first
        try:
            owner, repo = self._owner_repo(path)
            branch = self._current_branch(path)
            raw = self._gh("pr", "diff", "--repo", f"{owner}/{repo}", cwd=path, timeout=30)
            if raw.strip():
                return self._parse_diff_raw(raw)
        except Exception:
            pass
        # Fallback to local git diff
        if ref_a and ref_b:
            cmd = ["git", "diff", f"{ref_a}..{ref_b}"]
        elif ref_a:
            cmd = ["git", "diff", ref_a]
        else:
            cmd = ["git", "diff"]
        try:
            r = self._run(cmd, cwd=path, timeout=30)
            return self._parse_diff_raw(r.stdout)
        except Exception:
            return DiffResult()

    def _parse_diff_raw(self, raw: str) -> DiffResult:
        """Parse unified diff output into a DiffResult."""
        files: list[str] = []
        total_add = total_rem = 0
        hunks: list[DiffHunk] = []
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
        """Stage and commit via local git."""
        path = self._ensure_repo(repo_path)
        try:
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
            try:
                changed_raw = self._git("diff-tree", "--no-commit-id", "--name-only", "-r", after, cwd=path)
                changed = [f for f in changed_raw.splitlines() if f]
            except Exception:
                changed = files or []
            return CommitResult(success=True, commit_hash=after, message=message, files_changed=changed)
        except subprocess.TimeoutExpired:
            return CommitResult(success=False, error="Commit timed out")
        except Exception as exc:
            return CommitResult(success=False, error=str(exc))

    def rollback(self, repo_path: str, to_commit: str) -> RollbackResult:
        """Roll back via local git reset --hard."""
        path = self._ensure_repo(repo_path)
        try:
            current = self._git("rev-parse", "HEAD", cwd=path)
        except Exception:
            current = ""
        try:
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
        """Check that both ``git`` and ``gh`` are available and authenticated."""
        git_ok = False
        git_ver = ""
        gh_ok = False
        gh_ver = ""
        details_parts: list[str] = []

        try:
            r = self._run(["git", "--version"], cwd="/tmp")
            git_ver = r.stdout.strip()
            git_ok = r.returncode == 0
        except Exception:
            details_parts.append("git not found")

        try:
            r = self._run(["gh", "--version"], cwd="/tmp")
            gh_ver = r.stdout.strip().split("\n")[0]
            gh_ok = r.returncode == 0
        except FileNotFoundError:
            details_parts.append("gh CLI not found")
        except Exception as exc:
            details_parts.append(f"gh error: {exc}")

        if gh_ok:
            try:
                r = self._run(["gh", "auth", "status"], cwd="/tmp")
                if r.returncode != 0:
                    details_parts.append("gh not authenticated")
                    gh_ok = False
            except Exception:
                details_parts.append("gh auth check failed")
                gh_ok = False

        return ProviderHealth(
            healthy=git_ok and gh_ok,
            provider="github",
            version="1.0.0",
            git_available=git_ok,
            git_version=git_ver,
            details="; ".join(details_parts) if details_parts else f"gh: {gh_ver}",
        )

    def capabilities(self) -> list[str]:
        return [
            "inspect", "status", "plan", "search",
            "test", "lint", "diff", "commit",
            "rollback", "health",
            "create_pr", "review_pr", "security_scan",
        ]

    # ------------------------------------------------------------------
    # GitHub-specific operations (not in the base interface)
    # ------------------------------------------------------------------

    def create_pr(
        self,
        repo_path: str,
        title: str,
        body: str = "",
        base: str = "main",
        draft: bool = False,
    ) -> dict[str, Any]:
        """Create a pull request via ``gh pr create``.

        Returns a dict with keys: ``url``, ``number``, ``title``, ``error``.
        """
        path = self._ensure_repo(repo_path)
        try:
            owner, repo = self._owner_repo(path)
        except Exception as exc:
            return {"url": "", "number": 0, "title": title, "error": str(exc)}

        # Ensure branch is pushed
        branch = self._current_branch(path)
        try:
            self._run(["git", "push", "-u", "origin", branch], cwd=path, timeout=30)
        except Exception:
            pass  # may already be tracked

        cmd = [
            "gh", "pr", "create",
            "--repo", f"{owner}/{repo}",
            "--title", title,
            "--base", base,
        ]
        if body:
            cmd += ["--body", body]
        if draft:
            cmd.append("--draft")

        try:
            raw = self._gh(*cmd, cwd=path, timeout=30)
            # gh pr create outputs the URL
            url = raw.strip()
            # Try to extract PR number from URL
            m = re.search(r"/pull/(\d+)", url)
            number = int(m.group(1)) if m else 0
            return {"url": url, "number": number, "title": title, "error": ""}
        except AuthError as exc:
            return {"url": "", "number": 0, "title": title, "error": f"auth: {exc}"}
        except RuntimeError as exc:
            return {"url": "", "number": 0, "title": title, "error": str(exc)}
        except subprocess.TimeoutExpired:
            return {"url": "", "number": 0, "title": title, "error": "timed out"}

    def review_pr(self, repo_path: str, pr_number: int | None = None) -> dict[str, Any]:
        """Review a PR: fetch metadata, diff, and check status.

        If *pr_number* is ``None``, uses the PR for the current branch.
        Returns a dict with: ``pr_info``, ``diff``, ``checks``, ``error``.
        """
        path = self._ensure_repo(repo_path)
        result: dict[str, Any] = {"pr_info": {}, "diff": {}, "checks": {}, "error": ""}

        try:
            owner, repo = self._owner_repo(path)
        except Exception as exc:
            result["error"] = str(exc)
            return result

        repo_slug = f"{owner}/{repo}"

        # PR metadata
        try:
            pr_args = ["pr", "view", "--repo", repo_slug, "--json",
                        "number,title,state,author,baseRefName,headRefName,additions,deletions,changedFiles,reviewDecision,url"]
            if pr_number is not None:
                pr_args.insert(2, str(pr_number))
            else:
                # gh pr view <branch> — branch is a positional argument
                branch = self._current_branch(path)
                pr_args.insert(2, branch)
            result["pr_info"] = self._gh_json(*pr_args, cwd=path)
        except Exception as exc:
            result["error"] = f"pr view failed: {exc}"
            return result

        # Diff
        try:
            diff_args = ["pr", "diff", "--repo", repo_slug]
            if pr_number is not None:
                diff_args.insert(2, str(pr_number))
            raw_diff = self._gh(*diff_args, cwd=path, timeout=30)
            diff_parsed = self._parse_diff_raw(raw_diff)
            result["diff"] = {
                "files_changed": diff_parsed.files_changed,
                "total_added": diff_parsed.total_added,
                "total_removed": diff_parsed.total_removed,
                "hunks_count": len(diff_parsed.hunks),
            }
        except Exception as exc:
            result["diff"] = {"error": str(exc)}

        # Checks
        try:
            checks_args = ["pr", "checks", "--repo", repo_slug, "--json", "name,state,description"]
            if pr_number is not None:
                checks_args.insert(2, str(pr_number))
            result["checks"] = self._gh_json(*checks_args, cwd=path)
        except Exception:
            result["checks"] = {"status": "unavailable"}

        return result

    def security_scan(self, repo_path: str) -> dict[str, Any]:
        """Fetch code-scanning and Dependabot alerts via the GitHub API.

        Returns a dict with: ``code_scanning``, ``dependabot``, ``summary``, ``error``.
        """
        path = self._ensure_repo(repo_path)
        result: dict[str, Any] = {"code_scanning": [], "dependabot": [], "summary": {}, "error": ""}

        try:
            owner, repo = self._owner_repo(path)
        except Exception as exc:
            result["error"] = str(exc)
            return result

        # Code-scanning alerts
        try:
            raw = self._gh(
                "api", f"repos/{owner}/{repo}/code-scanning/alerts",
                "--paginate",
                "-q", "[.[] | {state, rule_id: .rule.id, severity: .rule.security_severity_level, url: .html_url}]",
                cwd=path,
                timeout=30,
            )
            result["code_scanning"] = json.loads(raw) if raw.strip() else []
        except json.JSONDecodeError:
            result["code_scanning"] = []
        except (RuntimeError, subprocess.TimeoutExpired) as exc:
            result["code_scanning"] = {"error": str(exc)}

        # Dependabot alerts
        try:
            raw = self._gh(
                "api", f"repos/{owner}/{repo}/dependabot/alerts",
                "--paginate",
                "-q", "[.[] | {state, package: .dependency.package.name, severity: .security_advisory.severity, url: .html_url}]",
                cwd=path,
                timeout=30,
            )
            result["dependabot"] = json.loads(raw) if raw.strip() else []
        except json.JSONDecodeError:
            result["dependabot"] = []
        except (RuntimeError, subprocess.TimeoutExpired) as exc:
            result["dependabot"] = {"error": str(exc)}

        # Summary
        cs = result["code_scanning"]
        da = result["dependabot"]
        cs_open = sum(1 for a in cs if isinstance(a, dict) and a.get("state") == "open") if isinstance(cs, list) else 0
        da_open = sum(1 for a in da if isinstance(a, dict) and a.get("state") == "open") if isinstance(da, list) else 0
        result["summary"] = {
            "code_scanning_open": cs_open,
            "code_scanning_total": len(cs) if isinstance(cs, list) else 0,
            "dependabot_open": da_open,
            "dependabot_total": len(da) if isinstance(da, list) else 0,
        }

        return result
