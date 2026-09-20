"""Service Reliability — health checks for all expected services."""
import json
import subprocess
import urllib.request
import urllib.error


def _run(cmd: list[str], timeout: int = 10) -> tuple[str, int]:
    """Run command, return (stdout, returncode)."""
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        return r.stdout.strip(), r.returncode
    except Exception as e:
        return str(e), -1


def _http_get(url: str, timeout: int = 5) -> tuple[bool, str]:
    """HTTP GET, return (ok, body_or_error). 401/403 count as alive."""
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", errors="replace")[:500]
            return resp.status < 400, body
    except urllib.error.HTTPError as e:
        # 401/403 means the service is alive, just auth-protected
        if e.code in (401, 403):
            body = e.read().decode("utf-8", errors="replace")[:500] if e.fp else ""
            return True, body
        return False, str(e)
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        return False, str(e)


def _systemctl_check(unit: str, user: bool = False) -> dict:
    """Check a systemd unit."""
    cmd = ["systemctl"]
    if user:
        cmd.append("--user")
    cmd.extend(["is-active", unit])
    out, rc = _run(cmd)
    active = out.strip() == "active"

    # Get uptime
    uptime_cmd = ["systemctl"]
    if user:
        uptime_cmd.append("--user")
    uptime_cmd.extend(["show", unit, "--property=ActiveEnterTimestamp,NRestarts"])
    uptime_out, _ = _run(uptime_cmd)
    restarts = 0
    uptime_str = ""
    for line in uptime_out.splitlines():
        if line.startswith("ActiveEnterTimestamp="):
            uptime_str = line.split("=", 1)[1].strip()
        if line.startswith("NRestarts="):
            try:
                restarts = int(line.split("=", 1)[1].strip())
            except ValueError:
                pass

    # Last log line
    log_cmd = ["journalctl"]
    if user:
        log_cmd.append("--user")
    log_cmd.extend(["-u", unit, "-n", "1", "--no-pager", "-o", "short-iso"])
    log_out, _ = _run(log_cmd)

    return {
        "status": "active" if active else "inactive",
        "uptime": uptime_str,
        "restarts": restarts,
        "healthy": active,
        "last_log": log_out.strip()[:200] if log_out else "",
    }


def _docker_check(name_filter: str) -> dict:
    """Check a docker container by name filter."""
    out, rc = _run([
        "docker", "ps", "--filter", f"name={name_filter}",
        "--format", "{{.Names}} {{.Status}} {{.RunningFor}}"
    ])
    if out and rc == 0:
        parts = out.split(None, 2)
        running = "Up" in out
        return {
            "status": "active" if running else "inactive",
            "uptime": parts[2] if len(parts) > 2 else "",
            "restarts": 0,
            "healthy": running,
            "last_log": out[:200],
        }
    return {"status": "inactive", "uptime": "", "restarts": 0, "healthy": False, "last_log": ""}


def _http_check(url: str, name: str) -> dict:
    """Check an HTTP service."""
    ok, body = _http_get(url)
    return {
        "status": "active" if ok else "inactive",
        "uptime": "",
        "restarts": 0,
        "healthy": ok,
        "last_log": body[:200] if body else "",
    }


def _ollama_check() -> dict:
    """Check Ollama across all GPU lanes (11434-11437)."""
    healthy_ports = []
    for port in range(11434, 11438):
        ok, _ = _http_get(f"http://127.0.0.1:{port}/api/tags")
        if ok:
            healthy_ports.append(port)
    all_up = len(healthy_ports) == 4
    some_up = len(healthy_ports) > 0
    return {
        "status": "active" if all_up else ("degraded" if some_up else "inactive"),
        "uptime": "",
        "restarts": 0,
        "healthy": some_up,
        "last_log": f"healthy_ports={healthy_ports}",
    }


def check_services() -> dict:
    """Check all expected services and return health status."""
    services = []

    # PostgreSQL
    pg = _systemctl_check("postgresql")
    pg["name"] = "postgresql"
    services.append(pg)

    # Grafana (Docker)
    grafana = _docker_check("grafana")
    grafana["name"] = "grafana"
    services.append(grafana)

    # Prometheus
    prom = _http_check("http://localhost:9090/-/healthy", "prometheus")
    prom["name"] = "prometheus"
    services.append(prom)

    # Ollama (all lanes)
    ollama = _ollama_check()
    ollama["name"] = "ollama"
    services.append(ollama)

    # LiteLLM
    litellm = _http_check("http://localhost:4000/health", "litellm")
    litellm["name"] = "litellm"
    services.append(litellm)

    # Metrics exporter
    metrics = _http_check("http://localhost:9199/metrics", "metrics-exporter")
    metrics["name"] = "metrics-exporter"
    services.append(metrics)

    # Mesh timer (user service)
    mesh = _systemctl_check("mesh-monitor.timer", user=True)
    mesh["name"] = "mesh-monitor.timer"
    services.append(mesh)

    # Storefront
    sf = _http_check("http://localhost:8020", "storefront")
    sf["name"] = "storefront"
    services.append(sf)

    # Checkout API
    checkout = _http_check("http://localhost:8012/health", "checkout-api")
    checkout["name"] = "checkout-api"
    services.append(checkout)

    return {"services": services}


def get_restart_policies() -> dict:
    """Check systemd restart= directives for each service."""
    units = [
        ("postgresql", False),
        ("prometheus", False),
        ("litellm-host", False),
        ("mesh-monitor.timer", True),
        ("storefront", False),
        ("checkout-api", False),
    ]
    policies = {}
    for unit, is_user in units:
        cmd = ["systemctl"]
        if is_user:
            cmd.append("--user")
        cmd.extend(["show", unit, "--property=Restart,RestartSec"])
        out, rc = _run(cmd)
        restart = "unknown"
        restart_sec = "unknown"
        for line in out.splitlines():
            if line.startswith("Restart="):
                restart = line.split("=", 1)[1].strip()
            if line.startswith("RestartSec="):
                restart_sec = line.split("=", 1)[1].strip()
        policies[unit] = {"restart": restart, "restart_sec": restart_sec}
    return policies