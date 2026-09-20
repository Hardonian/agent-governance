"""Tests for repository actions: inspect, status, diff, and action receipts."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

# Ensure imports work
GOV_ROOT = Path(__file__).resolve().parent.parent
if str(GOV_ROOT) not in sys.path:
    sys.path.insert(0, str(GOV_ROOT))

from providers.local_git import LocalGitProvider
from providers.base import RepoInfo, RepoStatus, DiffResult
from actions.action_receipt import ActionReceipt, ReceiptStore


# ═══════════════════════════════════════════════
# Inspect Tests
# ═══════════════════════════════════════════════

class TestInspect:
    """Test repo inspection on real git repos."""

    def test_inspect_returns_repo_info(self, local_git_provider: LocalGitProvider, tmp_git_repo: Path):
        info = local_git_provider.inspect(str(tmp_git_repo))
        assert isinstance(info, RepoInfo)
        assert info.path == str(tmp_git_repo)
        assert info.name == "repo"

    def test_inspect_detects_branch(self, local_git_provider: LocalGitProvider, tmp_git_repo: Path):
        info = local_git_provider.inspect(str(tmp_git_repo))
        # Default branch is usually 'master' or 'main'
        assert info.branch in ("master", "main")

    def test_inspect_has_last_commit(self, local_git_provider: LocalGitProvider, tmp_git_repo: Path):
        info = local_git_provider.inspect(str(tmp_git_repo))
        assert info.last_commit is not None
        assert len(info.last_commit.hash) == 40  # full SHA
        assert info.last_commit.message == "initial commit"
        assert info.last_commit.author == "Test"

    def test_inspect_clean_repo_not_dirty(self, local_git_provider: LocalGitProvider, tmp_git_repo: Path):
        info = local_git_provider.inspect(str(tmp_git_repo))
        assert info.dirty is False

    def test_inspect_dirty_repo(self, local_git_provider: LocalGitProvider, dirty_git_repo: Path):
        info = local_git_provider.inspect(str(dirty_git_repo))
        assert info.dirty is True

    def test_inspect_invalid_path_raises(self, local_git_provider: LocalGitProvider, tmp_path: Path):
        with pytest.raises(ValueError, match="Not a git repository"):
            local_git_provider.inspect(str(tmp_path / "nonexistent"))


# ═══════════════════════════════════════════════
# Status Tests
# ═══════════════════════════════════════════════

class TestStatus:
    """Test working-tree status detection."""

    def test_status_clean_repo(self, local_git_provider: LocalGitProvider, tmp_git_repo: Path):
        status = local_git_provider.status(str(tmp_git_repo))
        assert isinstance(status, RepoStatus)
        assert status.clean is True
        assert status.changes.staged == 0
        assert status.changes.unstaged == 0
        assert status.changes.untracked == 0

    def test_status_detects_dirty_state(self, local_git_provider: LocalGitProvider, dirty_git_repo: Path):
        status = local_git_provider.status(str(dirty_git_repo))
        assert status.clean is False
        # dirty.txt is untracked, README.md is modified
        assert status.changes.unstaged >= 1 or status.changes.untracked >= 1

    def test_status_detects_untracked_files(self, local_git_provider: LocalGitProvider, tmp_git_repo: Path):
        # Create an untracked file
        (tmp_git_repo / "untracked.txt").write_text("new file\n")
        status = local_git_provider.status(str(tmp_git_repo))
        assert status.clean is False
        assert status.changes.untracked >= 1

    def test_status_detects_staged_changes(self, local_git_provider: LocalGitProvider, tmp_git_repo: Path):
        # Create and stage a new file
        (tmp_git_repo / "staged.txt").write_text("staged content\n")
        subprocess.run(["git", "add", "staged.txt"], cwd=str(tmp_git_repo), capture_output=True)
        status = local_git_provider.status(str(tmp_git_repo))
        assert status.clean is False
        assert status.changes.staged >= 1

    def test_status_shows_branch(self, local_git_provider: LocalGitProvider, tmp_git_repo: Path):
        status = local_git_provider.status(str(tmp_git_repo))
        assert status.branch in ("master", "main")


# ═══════════════════════════════════════════════
# Diff Tests
# ═══════════════════════════════════════════════

class TestDiff:
    """Test diff generation on real git repos."""

    def test_diff_clean_repo_empty(self, local_git_provider: LocalGitProvider, tmp_git_repo: Path):
        result = local_git_provider.diff(str(tmp_git_repo))
        assert isinstance(result, DiffResult)
        assert len(result.files_changed) == 0
        assert result.total_added == 0
        assert result.total_removed == 0

    def test_diff_shows_changes(self, local_git_provider: LocalGitProvider, dirty_git_repo: Path):
        result = local_git_provider.diff(str(dirty_git_repo))
        assert isinstance(result, DiffResult)
        # README.md was modified
        assert len(result.files_changed) >= 1
        assert result.total_added >= 1
        assert result.total_removed >= 1

    def test_diff_raw_not_empty(self, local_git_provider: LocalGitProvider, dirty_git_repo: Path):
        result = local_git_provider.diff(str(dirty_git_repo))
        assert len(result.raw) > 0

    def test_diff_between_refs(self, local_git_provider: LocalGitProvider, tmp_git_repo: Path):
        # Make a second commit
        (tmp_git_repo / "second.txt").write_text("second file\n")
        subprocess.run(["git", "add", "second.txt"], cwd=str(tmp_git_repo), capture_output=True)
        subprocess.run(["git", "commit", "-m", "second commit"], cwd=str(tmp_git_repo), capture_output=True)

        result = local_git_provider.diff(str(tmp_git_repo), ref_a="HEAD~1", ref_b="HEAD")
        assert isinstance(result, DiffResult)
        assert "second.txt" in result.files_changed

    def test_diff_hunks_parsed(self, local_git_provider: LocalGitProvider, dirty_git_repo: Path):
        result = local_git_provider.diff(str(dirty_git_repo))
        if result.hunks:
            hunk = result.hunks[0]
            assert hunk.added >= 0
            assert hunk.removed >= 0


# ═══════════════════════════════════════════════
# Action Receipt Tests
# ═══════════════════════════════════════════════

class TestActionReceipt:
    """Test action receipt creation and persistence."""

    def test_receipt_creation(self):
        receipt = ActionReceipt(action_type="inspect", repo_path="/tmp/test")
        assert receipt.action_type == "inspect"
        assert receipt.repo_path == "/tmp/test"
        assert receipt.status == "pending"
        assert len(receipt.action_id) > 0

    def test_receipt_has_timestamp(self):
        receipt = ActionReceipt(action_type="status", repo_path="/tmp/test")
        assert receipt.timestamp != ""
        assert "T" in receipt.timestamp  # ISO format

    def test_receipt_store_save_and_load(self, receipt_store: ReceiptStore, tmp_path: Path):
        receipt = ActionReceipt(
            action_type="diff",
            repo_path="/tmp/test",
            status="success",
            duration=1.23,
        )
        saved_path = receipt_store.save(receipt)
        assert saved_path.exists()

        loaded = receipt_store.load(saved_path)
        assert loaded.action_type == "diff"
        assert loaded.status == "success"
        assert loaded.duration == 1.23
        assert loaded.action_id == receipt.action_id

    def test_receipt_store_list_recent(self, receipt_store: ReceiptStore):
        for i in range(3):
            receipt = ActionReceipt(action_type=f"action_{i}", repo_path="/tmp/test")
            receipt_store.save(receipt)

        recent = receipt_store.list_recent(limit=10)
        assert len(recent) >= 3

    def test_receipt_store_find_by_repo(self, receipt_store: ReceiptStore):
        r1 = ActionReceipt(action_type="inspect", repo_path="/tmp/repo_a")
        r2 = ActionReceipt(action_type="status", repo_path="/tmp/repo_b")
        r3 = ActionReceipt(action_type="diff", repo_path="/tmp/repo_a")
        receipt_store.save(r1)
        receipt_store.save(r2)
        receipt_store.save(r3)

        found = receipt_store.find_by_repo("/tmp/repo_a")
        assert len(found) >= 2
        assert all(r.repo_path == "/tmp/repo_a" for r in found)

    def test_receipt_model_dump(self):
        receipt = ActionReceipt(
            action_type="commit",
            repo_path="/tmp/test",
            files_changed=["a.py", "b.py"],
            commands_run=["git add -A", "git commit -m test"],
        )
        d = receipt.model_dump()
        assert d["action_type"] == "commit"
        assert "a.py" in d["files_changed"]

    def test_receipt_with_extra_data(self):
        receipt = ActionReceipt(
            action_type="test",
            repo_path="/tmp/test",
            extra={"tests_passed": 42, "tests_failed": 0},
        )
        assert receipt.extra["tests_passed"] == 42
