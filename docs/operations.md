# Agent Governance - Quick Reference

## Quick Actions

```bash
# Inspect a repository
python3 /home/scott/ai-lab/agent-governance/actions/quick_actions.py inspect /path/to/repo

# Check status
python3 /home/scott/ai-lab/agent-governance/actions/quick_actions.py status /path/to/repo

# Run tests
python3 /home/scott/ai-lab/agent-governance/actions/quick_actions.py test /path/to/repo

# Lint
python3 /home/scott/ai-lab/agent-governance/actions/quick_actions.py lint /path/to/repo

# View diff
python3 /home/scott/ai-lab/agent-governance/actions/quick_actions.py diff /path/to/repo

# Safe commit
python3 /home/scott/ai-lab/agent-governance/actions/quick_actions.py commit /path/to/repo -m "message"

# Rollback
python3 /home/scott/ai-lab/agent-governance/actions/quick_actions.py rollback /path/to/repo <commit>

# Security review
python3 /home/scott/ai-lab/agent-governance/actions/quick_actions.py review /path/to/repo
```

## Agent Laws

Edit: `/home/scott/ai-lab/agent-governance/gateway/default_laws.yaml`
Version: 1.0.0

### Law Categories
- FILESYSTEM: Path protection, symlink detection, boundary enforcement
- GIT: Branch protection, force-push prevention, state capture
- SECRETS: API key detection, token scanning, commit screening
- SHELL: Command classification, risk gating, destructive operation blocking
- INFRASTRUCTURE: Service change approval, Docker/systemctl gating
- DATABASE: Destructive SQL blocking, migration awareness
- NETWORK: Listener restrictions, port range enforcement
- VERIFICATION: Mutation verification requirements

### Enforcement Levels
- BLOCK: Hard stop, requires explicit override
- WARN: Proceed with logged warning
- LOG: Record only, no intervention

## Action Receipts

Every action produces a JSON receipt at:
`/home/scott/ai-lab/agent-governance/receipts/`

Each receipt contains:
- action_id (UUID)
- timestamp
- action_type
- repository path
- starting/ending commit
- files changed
- commands executed
- duration
- policy decisions
- verification results

## Node Registry

EPYC is registered as `epyc-primary`.
HX370 can register via: `node_registry.register(node)`

## Rollback

To disable governance:
```bash
# Remove laws (caution!)
mv /home/scott/ai-lab/agent-governance/gateway/default_laws.yaml \
   /home/scott/ai-lab/agent-governance/gateway/default_laws.yaml.disabled
```

To restore a receipted action:
```bash
python3 /home/scott/ai-lab/agent-governance/actions/quick_actions.py rollback \
  /path/to/repo <commit_from_receipt>
```