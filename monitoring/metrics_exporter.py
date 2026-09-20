#!/usr/bin/env python3
"""Prometheus metrics exporter for agent governance system.

Reads receipt JSON files and laws.yaml, exposes metrics on :9199/metrics.
"""

import glob
import json
import os
import re
import threading
import time
from http.server import HTTPServer, BaseHTTPRequestHandler

import yaml
from prometheus_client import (
    REGISTRY,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
    CONTENT_TYPE_LATEST,
)

RECEIPTS_DIR = os.environ.get(
    "GOVERNANCE_RECEIPTS_DIR",
    "/home/scott/ai-lab/agent-governance/receipts",
)
LAWS_FILE = os.environ.get(
    "GOVERNANCE_LAWS_FILE",
    "/home/scott/ai-lab/agent-governance/laws.yaml",
)
NODES_FILE = os.environ.get(
    "GOVERNANCE_NODES_FILE",
    "/home/scott/ai-lab/agent-governance/nodes.json",
)
LISTEN_PORT = int(os.environ.get("GOVERNANCE_EXPORTER_PORT", "9199"))
SCRAPE_INTERVAL = int(os.environ.get("GOVERNANCE_SCRAPE_INTERVAL", "15"))

# --- Prometheus metrics ---
EVALUATIONS = Counter(
    "agent_law_evaluations_total",
    "Total agent law evaluations",
    ["result"],
)
VIOLATIONS = Counter(
    "agent_law_violations_total",
    "Total agent law violations",
    ["law_id", "severity"],
)
RECEIPTS_TOTAL = Counter(
    "action_receipts_total",
    "Total action receipts processed",
    ["action_type", "status"],
)
ACTION_DURATION = Histogram(
    "action_duration_seconds",
    "Action execution duration in seconds",
    buckets=[0.01, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0],
)
ACTIVE_NODES = Gauge(
    "active_nodes",
    "Number of registered governance nodes",
    ["health"],
)
POLICY_DENIALS = Counter(
    "policy_denials_total",
    "Total policy denials by category",
    ["category"],
)

# Track which receipts have already been counted
_seen_receipts: set[str] = set()
_lock = threading.Lock()


def _load_laws():
    """Load laws.yaml and return parsed dict."""
    try:
        with open(LAWS_FILE) as f:
            return yaml.safe_load(f) or {}
    except Exception as e:
        print(f"[warn] failed to load laws: {e}")
        return {}


def _load_nodes():
    """Load nodes.json and return parsed dict."""
    try:
        with open(NODES_FILE) as f:
            return json.load(f)
    except Exception as e:
        print(f"[warn] failed to load nodes: {e}")
        return {"nodes": []}


def _classify_policy_denial(policy_decisions: list[dict]) -> dict[str, int]:
    """Classify policy decisions into categories and count denials."""
    categories: dict[str, int] = {}
    laws = _load_laws()
    policy_names = {p["name"]: p for p in laws.get("policies", [])}

    for dec in policy_decisions:
        if dec.get("result") == "denied":
            law_id = dec.get("law_id", "unknown")
            # Determine category from the policy's severity or name
            policy = policy_names.get(law_id, {})
            severity = policy.get("severity", "UNKNOWN")
            cat = severity if severity != "UNKNOWN" else "other"
            categories[cat] = categories.get(cat, 0) + 1
    return categories


def _process_receipt(filepath: str):
    """Parse a single receipt file and update metrics."""
    try:
        with open(filepath) as f:
            data = json.load(f)
    except Exception:
        return

    # Use filepath as dedup key
    basename = os.path.basename(filepath)
    with _lock:
        if basename in _seen_receipts:
            return
        _seen_receipts.add(basename)

    action_type = data.get("action_type", "unknown")
    status = data.get("status", "unknown")
    duration = data.get("duration", 0)
    policy_decisions = data.get("policy_decisions", [])

    # action_type/status counters
    RECEIPTS_TOTAL.labels(action_type=action_type, status=status).inc()
    ACTION_DURATION.observe(duration)

    # Process policy decisions
    for dec in policy_decisions:
        result = dec.get("result", "unknown")
        EVALUATIONS.labels(result=result).inc()

        if result == "denied":
            law_id = dec.get("law_id", "unknown")
            # Look up severity from laws
            laws = _load_laws()
            severity = "UNKNOWN"
            for p in laws.get("policies", []):
                if p["name"] == law_id:
                    severity = p.get("severity", "UNKNOWN")
                    break
            VIOLATIONS.labels(law_id=law_id, severity=severity).inc()

    # Policy denial categories
    for cat, count in _classify_policy_denial(policy_decisions).items():
        for _ in range(count):
            POLICY_DENIALS.labels(category=cat).inc()


def _process_nodes():
    """Update active_nodes gauge from nodes.json."""
    data = _load_nodes()
    # Count by health status
    health_counts: dict[str, int] = {}
    for node in data.get("nodes", []):
        health = node.get("health_status", "unknown")
        health_counts[health] = health_counts.get(health, 0) + 1

    # Clear and reset gauge
    ACTIVE_NODES.clear()
    for health, count in health_counts.items():
        ACTIVE_NODES.labels(health=health).set(count)


def _scrape_loop():
    """Periodically scan receipts and update gauges."""
    # Initial laws parse to seed evaluation counter at 0
    EVALUATIONS.labels(result="allowed")
    EVALUATIONS.labels(result="denied")
    POLICY_DENIALS.labels(category="CRITICAL")
    POLICY_DENIALS.labels(category="HIGH")
    POLICY_DENIALS.labels(category="MEDIUM")
    POLICY_DENIALS.labels(category="other")

    while True:
        try:
            # Scan all receipt files
            pattern = os.path.join(RECEIPTS_DIR, "*.json")
            for fpath in sorted(glob.glob(pattern)):
                _process_receipt(fpath)

            # Update node gauge
            _process_nodes()
        except Exception as e:
            print(f"[error] scrape loop: {e}")

        time.sleep(SCRAPE_INTERVAL)


class MetricsHandler(BaseHTTPRequestHandler):
    """HTTP handler that serves /metrics for Prometheus."""

    def do_GET(self):
        if self.path == "/metrics":
            output = generate_latest(REGISTRY)
            self.send_response(200)
            self.send_header("Content-Type", CONTENT_TYPE_LATEST)
            self.send_header("Content-Length", str(len(output)))
            self.end_headers()
            self.wfile.write(output)
        elif self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}')
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        # Suppress default request logging
        pass


def main():
    # Start background scrape thread
    t = threading.Thread(target=_scrape_loop, daemon=True)
    t.start()

    server = HTTPServer(("0.0.0.0", LISTEN_PORT), MetricsHandler)
    print(f"[governance-exporter] listening on :{LISTEN_PORT}/metrics")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("[governance-exporter] shutting down")
        server.shutdown()


if __name__ == "__main__":
    main()
