"""Grafana Dashboard Wire-up — adds Phase 2 panels to agent-governance dashboard."""
import json
import os
import urllib.request
import urllib.error


def _get_grafana_creds() -> tuple[str, str, str]:
    """Get Grafana credentials: (base_url, user, password)."""
    base_url = "http://localhost:3005"
    user = "admin"
    password = ""

    # Try env file first
    env_path = "/home/scott/.config/secrets/grafana.env"
    if os.path.isfile(env_path):
        with open(env_path) as f:
            for line in f:
                line = line.strip()
                if line.startswith("GF_SECURITY_ADMIN_PASSWORD="):
                    password = line.split("=", 1)[1].strip()
                if line.startswith("GF_SECURITY_ADMIN_USER="):
                    user = line.split("=", 1)[1].strip()

    return base_url, user, password


def _grafana_request(base_url: str, user: str, password: str,
                     method: str, path: str, data: dict = None) -> dict | None:
    """Make an authenticated request to Grafana API."""
    url = f"{base_url}{path}"
    body = json.dumps(data).encode() if data else None
    req = urllib.request.Request(url, data=body, method=method)
    req.add_header("Content-Type", "application/json")

    # Basic auth
    import base64
    cred = base64.b64encode(f"{user}:{password}".encode()).decode()
    req.add_header("Authorization", f"Basic {cred}")

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except (urllib.error.URLError, OSError, json.JSONDecodeError, TimeoutError) as e:
        return None


def update_governance_dashboard() -> dict:
    """Add Phase 2 metric panels to the agent-governance Grafana dashboard."""
    base_url, user, password = _get_grafana_creds()

    # Get existing dashboard
    dash = _grafana_request(base_url, user, password, "GET",
                            "/api/dashboards/uid/agent-governance-001")
    if not dash:
        return {"status": "error", "reason": "Could not fetch dashboard", "panels_added": 0}

    dashboard = dash.get("dashboard", {})
    existing_panels = dashboard.get("panels", [])
    existing_titles = {p.get("title", "") for p in existing_panels}

    # Phase 2 panels to add
    phase2_panels = [
        {
            "title": "Registered Repos",
            "type": "stat",
            "targets": [{"expr": "repo_count", "legendFormat": "repos"}],
            "gridPos": {"h": 4, "w": 6, "x": 0, "y": 0},
        },
        {
            "title": "Job Queue Depth",
            "type": "gauge",
            "targets": [{"expr": "job_queue_depth", "legendFormat": "queued"}],
            "gridPos": {"h": 4, "w": 6, "x": 6, "y": 0},
        },
        {
            "title": "Health Findings by Severity",
            "type": "barchart",
            "targets": [
                {"expr": 'health_findings{severity="critical"}', "legendFormat": "critical"},
                {"expr": 'health_findings{severity="error"}', "legendFormat": "error"},
                {"expr": 'health_findings{severity="warning"}', "legendFormat": "warning"},
                {"expr": 'health_findings{severity="info"}', "legendFormat": "info"},
            ],
            "gridPos": {"h": 6, "w": 12, "x": 0, "y": 4},
        },
        {
            "title": "Model Routing Distribution",
            "type": "piechart",
            "targets": [{"expr": "model_routing_distribution", "legendFormat": "{{model_id}}"}],
            "gridPos": {"h": 6, "w": 6, "x": 12, "y": 0},
        },
        {
            "title": "Action Receipt Count",
            "type": "stat",
            "targets": [{"expr": "action_receipts_total", "legendFormat": "receipts"}],
            "gridPos": {"h": 4, "w": 6, "x": 18, "y": 0},
        },
        {
            "title": "Active Repo Locks",
            "type": "stat",
            "targets": [{"expr": "repo_lock_count", "legendFormat": "locks"}],
            "gridPos": {"h": 4, "w": 6, "x": 18, "y": 4},
        },
        {
            "title": "Active Workers",
            "type": "stat",
            "targets": [{"expr": "active_workers", "legendFormat": "workers"}],
            "gridPos": {"h": 4, "w": 6, "x": 12, "y": 6},
        },
    ]

    # Filter out panels that already exist
    new_panels = [p for p in phase2_panels if p["title"] not in existing_titles]
    if not new_panels:
        return {"status": "ok", "reason": "All panels already exist", "panels_added": 0}

    # Assign IDs and datasource
    next_id = max((p.get("id", 0) for p in existing_panels), default=0) + 1
    for panel in new_panels:
        panel["id"] = next_id
        next_id += 1
        for target in panel.get("targets", []):
            target["datasource"] = {"type": "prometheus", "uid": "prometheus"}
        # Adjust y positions to not overlap
        max_y = max((p.get("gridPos", {}).get("y", 0) + p.get("gridPos", {}).get("h", 0)
                      for p in existing_panels), default=0)
        panel["gridPos"]["y"] = max_y + panel["gridPos"]["y"]

    # Add panels to dashboard
    dashboard["panels"] = existing_panels + new_panels

    # Save updated dashboard
    payload = {
        "dashboard": dashboard,
        "overwrite": True,
    }
    result = _grafana_request(base_url, user, password, "POST",
                              "/api/dashboards/db", payload)
    if result and result.get("status") == "success":
        return {"status": "ok", "panels_added": len(new_panels),
                "panel_titles": [p["title"] for p in new_panels]}
    elif result:
        return {"status": "ok", "panels_added": len(new_panels),
                "panel_titles": [p["title"] for p in new_panels]}
    else:
        return {"status": "error", "reason": "Failed to save dashboard",
                "panels_added": 0}