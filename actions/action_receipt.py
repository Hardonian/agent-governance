"""Action receipts — JSON audit trail for every action performed."""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, Field

_RECEIPT_DIR = Path("/home/scott/ai-lab/agent-governance/receipts")


class ActionReceipt(BaseModel):
    """Immutable receipt for a single action invocation."""

    action_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    action_type: str  # inspect, status, plan, search, test, lint, diff, commit, rollback, fix, review
    repo_path: str
    starting_commit: str = ""
    ending_commit: str = ""
    files_changed: list[str] = Field(default_factory=list)
    commands_run: list[str] = Field(default_factory=list)
    duration: float = 0.0  # seconds
    status: str = "pending"  # pending, success, failure, error
    policy_decisions: list[str] = Field(default_factory=list)
    extra: dict[str, Any] = Field(default_factory=dict)


class ReceiptStore:
    """Manages persistence of action receipts."""

    def __init__(self, directory: Path | str = _RECEIPT_DIR) -> None:
        self._dir = Path(directory)
        self._dir.mkdir(parents=True, exist_ok=True)

    @property
    def directory(self) -> Path:
        return self._dir

    def save(self, receipt: ActionReceipt) -> Path:
        """Persist a receipt and return the file path."""
        filename = f"{receipt.timestamp[:19].replace(':', '-')}_{receipt.action_id[:8]}.json"
        path = self._dir / filename
        path.write_text(receipt.model_dump_json(indent=2) + "\n")
        return path

    def load(self, path: Path | str) -> ActionReceipt:
        """Load a receipt from a JSON file."""
        return ActionReceipt.model_validate_json(Path(path).read_text())

    def list_recent(self, limit: int = 20) -> list[ActionReceipt]:
        """Return the most recent receipts, newest first."""
        files = sorted(self._dir.glob("*.json"), reverse=True)
        receipts: list[ActionReceipt] = []
        for f in files[:limit]:
            try:
                receipts.append(self.load(f))
            except Exception:
                continue
        return receipts

    def find_by_repo(self, repo_path: str, limit: int = 20) -> list[ActionReceipt]:
        """Return recent receipts for a specific repo."""
        return [r for r in self.list_recent(limit=limit * 3) if r.repo_path == repo_path][:limit]

    def find_by_action(self, action_type: str, limit: int = 20) -> list[ActionReceipt]:
        """Return recent receipts for a specific action type."""
        return [r for r in self.list_recent(limit=limit * 3) if r.action_type == action_type][:limit]


# Module-level singleton
receipt_store = ReceiptStore()
