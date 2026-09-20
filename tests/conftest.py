"""Shared fixtures for agent-governance tests."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

# Add the agent-governance root to sys.path so imports work
GOV_ROOT = Path(__file__).resolve().parent.parent
if str(GOV_ROOT) not in sys.path:
    sys.path.insert(0, str(GOV_ROOT))


@pytest.fixture
def gov_root() -> Path:
    """Return the agent-governance root directory."""
    return GOV_ROOT


@pytest.fixture
def default_laws_yaml(gov_root: Path) -> Path:
    """Return the path to default_laws.yaml."""
    return gov_root / "gateway" / "default_laws.yaml"


@pytest.fixture
def tmp_git_repo(tmp_path: Path) -> Path:
    """Create a temporary git repo with one commit."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=str(repo), capture_output=True, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=str(repo), capture_output=True, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=str(repo), capture_output=True, check=True)
    readme = repo / "README.md"
    readme.write_text("# Test Repo\n")
    subprocess.run(["git", "add", "README.md"], cwd=str(repo), capture_output=True, check=True)
    subprocess.run(["git", "commit", "-m", "initial commit"], cwd=str(repo), capture_output=True, check=True)
    return repo


@pytest.fixture
def dirty_git_repo(tmp_git_repo: Path) -> Path:
    """A git repo with uncommitted changes (dirty state)."""
    new_file = tmp_git_repo / "dirty.txt"
    new_file.write_text("uncommitted content\n")
    # Also modify the existing file
    readme = tmp_git_repo / "README.md"
    readme.write_text("# Modified Repo\n")
    return tmp_git_repo


@pytest.fixture
def policy_engine(default_laws_yaml: Path):
    """Return a PolicyEngine loaded with default laws."""
    from gateway.policy_engine import PolicyEngine
    engine = PolicyEngine()
    engine.load_laws_from_yaml(default_laws_yaml)
    return engine


@pytest.fixture
def classifier():
    """Return a CommandClassifier."""
    from gateway.classifier import CommandClassifier
    return CommandClassifier()


@pytest.fixture
def path_guard():
    """Return a PathGuard with default settings."""
    from gateway.path_guard import PathGuard
    return PathGuard()


@pytest.fixture
def secret_detector():
    """Return a SecretDetector."""
    from gateway.secret_detector import SecretDetector
    return SecretDetector()


@pytest.fixture
def local_git_provider():
    """Return a LocalGitProvider."""
    from providers.local_git import LocalGitProvider
    return LocalGitProvider()


@pytest.fixture
def receipt_store(tmp_path: Path):
    """Return a ReceiptStore writing to a temp directory."""
    from actions.action_receipt import ReceiptStore
    return ReceiptStore(directory=tmp_path / "receipts")
