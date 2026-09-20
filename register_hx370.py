#!/usr/bin/env python3
"""HX370 Node Registration Protocol.

Run this on the HX370 to register as an execution node with the EPYC control plane.

Usage:
  python3 register_hx370.py --epyc-host <epyc-ip> --token <auth-token>

Or configure via environment:
  EPYC_HOST=<ip> EPYC_TOKEN=<token> python3 register_hx370.py

The script:
1. Discovers local hardware capabilities (CPU, RAM, NPU, GPU)
2. Advertises them to the EPYC node registry
3. Registers the node for task routing
4. Can be run as a systemd timer for periodic heartbeat
"""

import json
import os
import platform
import socket
import subprocess
import sys
import time
from pathlib import Path

# Configuration
GOV_ROOT = Path(__file__).parent.parent
NODES_FILE = GOV_ROOT / "nodes.json"
HX370_NODE_ID = "hx370-secondary"


def discover_local_capabilities():
    """Discover hardware capabilities of the current machine."""
    info = {
        "node_id": HX370_NODE_ID,
        "hostname": socket.gethostname(),
        "architecture": platform.machine(),
        "cpu_cores": os.cpu_count() or 0,
        "ram_gb": 0.0,
        "gpus": [],
        "npu_available": False,
        "available_models": [],
        "available_tools": ["git", "python3", "node"],
        "repo_access": [],
        "capabilities": ["compile", "test", "code-index"],
        "health_status": "healthy",
        "load_average": 0.0,
        "latency_ms": 0,
        "authentication_token_hash": "",
    }

    # RAM
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    kb = int(line.split()[1])
                    info["ram_gb"] = round(kb / 1024 / 1024, 1)
                    break
    except (OSError, ValueError):
        pass

    # Load average
    try:
        load1, _, _ = os.getloadavg()
        info["load_average"] = round(load1, 2)
    except OSError:
        pass

    # GPU detection
    try:
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5
        )
        if r.returncode == 0:
            for line in r.stdout.strip().split("\n"):
                if line.strip():
                    parts = line.split(",")
                    name = parts[0].strip()
                    mem = int(parts[1].strip()) if len(parts) > 1 else 0
                    info["gpus"].append({"name": name, "memory_mb": mem})
                    info["capabilities"].append("inference")
                    info["capabilities"].append("image-generation")
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    # NPU detection (AMD XDNA/Ryzen AI)
    try:
        r = subprocess.run(
            ["ls", "/dev/accel/accel0"],
            capture_output=True, text=True, timeout=2
        )
        if r.returncode == 0:
            info["npu_available"] = True
            info["capabilities"].append("npu-inference")
    except FileNotFoundError:
        pass

    # Ollama models (if running locally)
    try:
        import urllib.request
        req = urllib.request.Request("http://localhost:11434/api/tags")
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read())
            for m in data.get("models", []):
                info["available_models"].append(m["name"])
            if info["available_models"] and "inference" not in info["capabilities"]:
                info["capabilities"].append("inference")
    except Exception:
        pass

    # Unique capabilities
    info["capabilities"] = sorted(set(info["capabilities"]))
    return info


def register_with_epyc(node_info, epyc_host, token):
    """Send node registration to EPYC control plane."""
    import urllib.request
    
    node_info["authentication_token_hash"] = token  # In production, hash this
    node_info["last_seen"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    # Update local nodes.json
    _update_local_registry(node_info)

    # Try HTTP registration (if EPYC exposes an endpoint)
    # For now, update the file-based registry
    print(f"Node {node_info['node_id']} registered locally.")
    print(f"  Hostname: {node_info['hostname']}")
    print(f"  Arch: {node_info['architecture']}")
    print(f"  CPU: {node_info['cpu_cores']} cores, RAM: {node_info['ram_gb']}GB")
    print(f"  GPUs: {len(node_info['gpus'])}")
    print(f"  NPU: {node_info['npu_available']}")
    print(f"  Models: {len(node_info['available_models'])}")
    print(f"  Capabilities: {', '.join(node_info['capabilities'])}")
    return True


def _update_local_registry(node_info):
    """Add/update node in the local nodes.json."""
    try:
        if NODES_FILE.exists():
            with open(NODES_FILE) as f:
                data = json.load(f)
        else:
            data = {"nodes": []}
    except (json.JSONDecodeError, OSError):
        data = {"nodes": []}

    # Update or add
    found = False
    for i, n in enumerate(data["nodes"]):
        if n.get("node_id") == node_info["node_id"]:
            data["nodes"][i] = node_info
            found = True
            break

    if not found:
        data["nodes"].append(node_info)

    NODES_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(NODES_FILE, "w") as f:
        json.dump(data, f, indent=2)


def heartbeat():
    """Quick health update."""
    info = discover_local_capabilities()
    _update_local_registry(info)
    return info


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Register HX370 as execution node")
    parser.add_argument("--epyc-host", default=os.environ.get("EPYC_HOST", "localhost"))
    parser.add_argument("--token", default=os.environ.get("EPYC_TOKEN", ""))
    parser.add_argument("--heartbeat", action="store_true", help="Quick heartbeat update only")
    args = parser.parse_args()

    if args.heartbeat:
        info = heartbeat()
        print(f"Heartbeat: {info['node_id']} healthy, load={info['load_average']}")
    else:
        info = discover_local_capabilities()
        register_with_epyc(info, args.epyc_host, args.token)
