#!/usr/bin/env python3
"""Hermes shell hook: Agent Law Gateway pre-tool-call guard.

Registered via config.yaml hooks.pre_tool_call. Receives JSON on stdin
with {tool_name, tool_input, session_id, cwd, extra}. Evaluates the
proposed action against Agent Laws. Returns JSON with decision to
allow or block.

Exit 0 + {} = allow
Exit 0 + {"decision":"block","reason":"..."} = block
Exit 2 = block (hard signal, no JSON needed)
"""

import json
import os
import sys

# Add governance module to path
GOV_ROOT = "/home/scott/ai-lab/agent-governance"
sys.path.insert(0, GOV_ROOT)

from gateway.gateway import AgentLawGateway

# Lazy singleton — loaded once per hook invocation
_gw = None

def get_gateway():
    global _gw
    if _gw is None:
        os.chdir("/")  # ensure we don't hold cwd locks
        _gw = AgentLawGateway(
            laws_path=os.path.join(GOV_ROOT, "laws.yaml")
        )
    return _gw


def main():
    try:
        payload = json.loads(sys.stdin.read())
    except (json.JSONDecodeError, EOFError):
        # Malformed input — fail open (let Hermes handle it)
        print("{}")
        return

    tool_name = payload.get("tool_name", "")
    tool_input = payload.get("tool_input") or {}

    # Only gate terminal/shell commands
    if tool_name not in ("terminal", "execute_code", "computer_use"):
        print("{}")
        return

    # Extract the command from tool_input
    command = ""
    if isinstance(tool_input, dict):
        command = tool_input.get("command", "")
    elif isinstance(tool_input, str):
        command = tool_input

    if not command or not command.strip():
        print("{}")
        return

    # Evaluate against Agent Laws
    gw = get_gateway()
    try:
        result = gw.full_evaluation(command.strip())
    except Exception as e:
        # Gateway error — fail open (don't block legitimate work on a bug)
        print(json.dumps({"context": f"gov_gateway_error: {e}"}))
        return

    if not result.get("overall_allowed", True):
        # Build block reason
        reasons = []
        if result.get("policy_decision"):
            pd = result["policy_decision"]
            reasons.append(f"policy:{pd.get('policy_name', 'unknown')}")
        if result.get("secret_detection", {}).get("found"):
            reasons.append("secrets_detected")
        if result.get("path_guard", {}).get("violations"):
            vg = result["path_guard"]["violations"]
            reasons.append(f"path:{vg[0].get('rule', 'violation')}")

        reason = " | ".join(reasons) if reasons else "agent_law_violation"

        # Log the block
        _log_block(command, reason, payload)

        print(json.dumps({
            "decision": "block",
            "reason": f"Agent Law Gateway: {reason}",
        }))
        return

    # Allowed — pass through with optional context
    classification = result.get("classification", "UNKNOWN")
    if classification in ("REPO_MUTATION", "SYSTEM_MUTATION"):
        print(json.dumps({
            "context": f"gov_classification={classification}",
        }))
    else:
        print("{}")


def _log_block(command: str, reason: str, payload: dict):
    """Append block event to governance log."""
    import datetime
    log_path = os.path.join(GOV_ROOT, "mesh", "governance.log")
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    try:
        with open(log_path, "a") as f:
            entry = {
                "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "event": "blocked",
                "command": command[:200],
                "reason": reason,
                "session": payload.get("session_id", "")[:20],
                "tool": payload.get("tool_name", ""),
            }
            f.write(json.dumps(entry) + "\n")
    except OSError:
        pass  # Don't fail the hook on logging errors


if __name__ == "__main__":
    main()
