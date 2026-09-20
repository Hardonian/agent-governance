"""Abstract base class and Pydantic models for repository action providers."""

from __future__ import annotations

import abc
from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class CommitInfo(BaseModel):
    """A single commit entry."""
    hash: str
    short_hash: str = ""
    author: str = ""
    email: str = ""
    date: str = ""
    message: str = ""

    def model_post_init(self, __context: object) -> None:
        if not self.short_hash:
            self.short_hash = self.hash[:8]


class WorktreeInfo(BaseModel):
    """A git worktree."""
    path: str
    head: str
    branch: Optional[str] = None


class RepoInfo(BaseModel):
    """Static information about a repository."""
    path: str
    name: str
    branch: str = ""
    remote_url: Optional[str] = None
    dirty: bool = False
    ahead_behind: tuple[int, int] = (0, 0)  # (ahead, behind) of upstream
    worktrees: list[WorktreeInfo] = Field(default_factory=list)
    last_commit: Optional[CommitInfo] = None


class ChangeSummary(BaseModel):
    """Counts of changed files by type."""
    staged: int = 0
    unstaged: int = 0
    untracked: int = 0
    conflicted: int = 0


class RepoStatus(BaseModel):
    """Working-tree status of a repository."""
    branch: str = ""
    upstream: Optional[str] = None
    changes: ChangeSummary = Field(default_factory=ChangeSummary)
    ahead: int = 0
    behind: int = 0
    clean: bool = True


class RiskLevel(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class PlanStep(BaseModel):
    """A single step in an action plan."""
    order: int
    command: str
    description: str
    risk: RiskLevel = RiskLevel.LOW
    reversible: bool = True


class ActionPlan(BaseModel):
    """A sequence of steps to accomplish a goal."""
    goal: str
    steps: list[PlanStep] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    estimated_duration: str = ""  # human-readable, e.g. "~5s"


class SearchResult(BaseModel):
    """A grep/search hit inside a repository."""
    file: str
    line_number: int
    line: str
    context: str = ""


class TestResult(BaseModel):
    """Outcome of running tests."""
    passed: int = 0
    failed: int = 0
    errors: int = 0
    skipped: int = 0
    duration: float = 0.0  # seconds
    output: str = ""
    command_run: str = ""


class LintResult(BaseModel):
    """Outcome of running a linter."""
    issues: int = 0
    errors: int = 0
    warnings: int = 0
    output: str = ""
    command_run: str = ""
    duration: float = 0.0


class DiffHunk(BaseModel):
    """A single diff hunk."""
    header: str = ""
    added: int = 0
    removed: int = 0
    content: str = ""


class DiffResult(BaseModel):
    """Diff between working tree (or two refs)."""
    files_changed: list[str] = Field(default_factory=list)
    total_added: int = 0
    total_removed: int = 0
    hunks: list[DiffHunk] = Field(default_factory=list)
    raw: str = ""


class CommitResult(BaseModel):
    """Outcome of a commit operation."""
    success: bool = False
    commit_hash: Optional[str] = None
    message: str = ""
    files_changed: list[str] = Field(default_factory=list)
    error: Optional[str] = None


class RollbackResult(BaseModel):
    """Outcome of a rollback operation."""
    success: bool = False
    from_commit: str = ""
    to_commit: str = ""
    error: Optional[str] = None


class ProviderHealth(BaseModel):
    """Health-check response from a provider."""
    healthy: bool = True
    provider: str = ""
    version: str = ""
    git_available: bool = False
    git_version: str = ""
    details: str = ""


# ---------------------------------------------------------------------------
# Abstract base
# ---------------------------------------------------------------------------

class RepoActionProvider(abc.ABC):
    """Abstract provider for repository-aware actions."""

    @abc.abstractmethod
    def inspect(self, repo_path: str) -> RepoInfo:
        """Return static information about the repository."""

    @abc.abstractmethod
    def status(self, repo_path: str) -> RepoStatus:
        """Return working-tree status."""

    @abc.abstractmethod
    def plan(self, repo_path: str, goal: str) -> ActionPlan:
        """Return an action plan to accomplish *goal* in the repository."""

    @abc.abstractmethod
    def search(self, repo_path: str, query: str) -> list[SearchResult]:
        """Search repository contents for *query*."""

    @abc.abstractmethod
    def test(self, repo_path: str) -> TestResult:
        """Run the repository's test suite."""

    @abc.abstractmethod
    def lint(self, repo_path: str) -> LintResult:
        """Run the repository's linter."""

    @abc.abstractmethod
    def diff(self, repo_path: str, ref_a: Optional[str] = None, ref_b: Optional[str] = None) -> DiffResult:
        """Return the diff of working tree or between two refs."""

    @abc.abstractmethod
    def commit(self, repo_path: str, message: str, files: Optional[list[str]] = None) -> CommitResult:
        """Stage *files* (or all) and commit with *message*."""

    @abc.abstractmethod
    def rollback(self, repo_path: str, to_commit: str) -> RollbackResult:
        """Roll back to a specific commit (reset --hard)."""

    @abc.abstractmethod
    def health(self) -> ProviderHealth:
        """Return provider health status."""

    @abc.abstractmethod
    def capabilities(self) -> list[str]:
        """Return list of supported capability names."""
