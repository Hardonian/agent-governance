# Agent Governance System - Architecture
# EPYC AI Lab Control Plane

## Overview

This system provides enforceable agent governance for the Hermes AI lab.
It sits between model intent and execution, ensuring deterministic policy
enforcement that cannot be bypassed by prompt manipulation.

## Architecture

```
USER / HERMES / CLI
        |
        v
  AGENT LAW GATEWAY (gateway/)
    - laws.py: Policy definitions (YAML-loaded)
    - classifier.py: Command risk classification
    - policy_engine.py: Decision engine
    - secret_detector.py: Secret pattern matching
    - path_guard.py: Path traversal protection
        |
        v
  REPO ACTION PROVIDER (providers/)
    - base.py: Abstract provider interface
    - local_git.py: Git/subprocess implementation
        |
        v
  QUICK ACTIONS (actions/)
    - quick_actions.py: CLI commands
    - action_receipt.py: Audit trail
        |
        v
  NODE REGISTRY (node_registry.py)
    - EPYC self-registration
    - HX370 future registration
    - Capability-based routing
```

## Components

### Agent Law Gateway
Deterministic policy layer. Laws are version-controlled YAML.
Fail-closed: if in doubt, block.

### Repo Action Provider
Abstract interface for repository operations. Currently backed
by local Git/subprocess. Future providers: Jev semantic,
remote execution, Codex, Gemini, Claude.

### Quick Actions
CLI commands: inspect, status, plan, search, fix, test, lint,
diff, commit, review, rollback. Every action produces a receipt.

### Node Registry
Capability-based execution node management. EPYC registers itself.
HX370 will register when integrated. Authentication required.

## Jev Integration
The policy engine implements Jev-compatible decision patterns:
- Deterministic layer (hard-deny, safe patterns) runs first
- Semantic layer (optional, via local model) handles ambiguous cases
- Fail-closed: unavailability = block

## Bend Assessment
Bend (HigherOrderCO/Bend) is a parallel programming language, not
a repository governance tool. It may be useful for GPU-parallel
compute tasks but is not integrated into this governance system.

## HX370 Integration Prerequisites
1. Network connectivity between EPYC and HX370
2. SSH key authentication
3. Node registry API endpoint (future)
4. Shared repository access (Git remote or NFS)
5. Authentication token exchange