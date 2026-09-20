"""Node Registry — manages execution nodes with capability advertisement for HX370 integration."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class GPUInfo:
    """Information about a single GPU."""
    name: str
    vram_gb: float
    compute_capability: str = ""
    role: str = ""  # e.g. "interactive-inference", "large-model-capacity"


@dataclass
class Node:
    """An execution node with hardware specs, capabilities, and health info."""
    node_id: str
    hostname: str
    architecture: str  # e.g. "x86_64", "aarch64"
    cpu_cores: int
    ram_gb: float
    gpus: List[GPUInfo] = field(default_factory=list)
    npu_available: bool = False
    available_models: List[str] = field(default_factory=list)
    available_tools: List[str] = field(default_factory=list)
    repo_access: List[str] = field(default_factory=list)
    capabilities: List[str] = field(default_factory=list)  # e.g. inference, compile, test, benchmark
    health_status: str = "unknown"  # healthy, degraded, offline, unknown
    load_average: float = 0.0
    latency_ms: float = 0.0
    authentication_token_hash: str = ""
    registered_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        return d

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Node:
        gpus = [GPUInfo(**g) for g in data.get("gpus", [])]
        return cls(
            node_id=data["node_id"],
            hostname=data.get("hostname", ""),
            architecture=data.get("architecture", "unknown"),
            cpu_cores=data.get("cpu_cores", 0),
            ram_gb=data.get("ram_gb", 0.0),
            gpus=gpus,
            npu_available=data.get("npu_available", False),
            available_models=data.get("available_models", []),
            available_tools=data.get("available_tools", []),
            repo_access=data.get("repo_access", []),
            capabilities=data.get("capabilities", []),
            health_status=data.get("health_status", "unknown"),
            load_average=data.get("load_average", 0.0),
            latency_ms=data.get("latency_ms", 0.0),
            authentication_token_hash=data.get("authentication_token_hash", ""),
            registered_at=data.get("registered_at", ""),
        )

    @staticmethod
    def hash_token(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()


class NodeRegistry:
    """Manages execution nodes with JSON persistence."""

    def __init__(self, persistence_path: str | Path = "/home/scott/ai-lab/agent-governance/nodes.json"):
        self._path = Path(persistence_path)
        self._nodes: Dict[str, Node] = {}
        self._last_updated: str = ""
        self._load()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _load(self) -> None:
        if not self._path.exists():
            return
        try:
            data = json.loads(self._path.read_text())
            for nd in data.get("nodes", []):
                node = Node.from_dict(nd)
                self._nodes[node.node_id] = node
            self._last_updated = data.get("last_updated", "")
        except (json.JSONDecodeError, KeyError):
            pass

    def _save(self) -> None:
        self._last_updated = datetime.now(timezone.utc).isoformat()
        payload = {
            "nodes": [n.to_dict() for n in self._nodes.values()],
            "last_updated": self._last_updated,
        }
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(payload, indent=2) + "\n")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def register(self, node: Node) -> None:
        """Register or update a node."""
        if not node.registered_at:
            node.registered_at = datetime.now(timezone.utc).isoformat()
        self._nodes[node.node_id] = node
        self._save()

    def deregister(self, node_id: str) -> bool:
        """Remove a node. Returns True if it existed."""
        if node_id in self._nodes:
            del self._nodes[node_id]
            self._save()
            return True
        return False

    def get_node(self, node_id: str) -> Optional[Node]:
        return self._nodes.get(node_id)

    def list_nodes(self, capability: Optional[str] = None, healthy_only: bool = False) -> List[Node]:
        """List nodes, optionally filtered by capability and health."""
        nodes = list(self._nodes.values())
        if capability:
            nodes = [n for n in nodes if capability in n.capabilities]
        if healthy_only:
            nodes = [n for n in nodes if n.health_status == "healthy"]
        return nodes

    def best_node_for_capability(self, capability: str) -> Optional[Node]:
        """Select the best node for a given capability based on health and load."""
        candidates = [
            n for n in self._nodes.values()
            if capability in n.capabilities and n.health_status in ("healthy", "degraded")
        ]
        if not candidates:
            return None
        # Prefer healthy over degraded, then lowest load
        candidates.sort(key=lambda n: (0 if n.health_status == "healthy" else 1, n.load_average))
        return candidates[0]

    def health_check(self) -> Dict[str, Any]:
        """Return health summary of all registered nodes."""
        healthy = sum(1 for n in self._nodes.values() if n.health_status == "healthy")
        degraded = sum(1 for n in self._nodes.values() if n.health_status == "degraded")
        offline = sum(1 for n in self._nodes.values() if n.health_status == "offline")
        unknown = sum(1 for n in self._nodes.values() if n.health_status not in ("healthy", "degraded", "offline"))
        return {
            "total_nodes": len(self._nodes),
            "healthy": healthy,
            "degraded": degraded,
            "offline": offline,
            "unknown": unknown,
            "nodes": {
                nid: {"status": n.health_status, "load": n.load_average, "latency_ms": n.latency_ms}
                for nid, n in self._nodes.items()
            },
            "last_updated": self._last_updated,
        }

    @property
    def node_count(self) -> int:
        return len(self._nodes)
