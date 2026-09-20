"""Hermes Control CLI - unified interface for Agent Governance Phase 2."""
import sys
import os
import json

# Ensure the agent-governance directory is on the path
GOV_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if GOV_DIR not in sys.path:
    sys.path.insert(0, GOV_DIR)


def cmd_health(args):
    """Show overall system health."""
    from models.scheduler import ResourceScheduler
    rs = ResourceScheduler()
    resources = rs.get_resources()
    cpu = resources.get("cpu", {})
    ram = resources.get("ram", {})
    print("=== HERMES HEALTH ===")
    print(f"  CPU: {cpu.get('cores', '?')} cores, load {cpu.get('load_1m', '?')}/{cpu.get('load_5m', '?')}/{cpu.get('load_15m', '?')}")
    print(f"  RAM: {ram.get('used_mb', 0)//1024}/{ram.get('total_mb', 0)//1024} GB ({ram.get('usage_pct', '?')}%)")
    code, out = __import__('subprocess').run('df -h / --output=used,size,pcent | tail -1', shell=True, capture_output=True, text=True).stdout.strip(), ''
    import subprocess as _sp
    _r = _sp.run('df -h / --output=used,size,pcent | tail -1', shell=True, capture_output=True, text=True)
    print(f"  Disk: {_r.stdout.strip()}")
    for gpu in resources.get("gpus", []):
        print(f"  GPU {gpu.get('name', '?')}: {gpu.get('vram_used_mb', '?')}/{gpu.get('vram_total_mb', '?')} MB, lane={gpu.get('lane', '?')}")
    # Services
    print("\n=== SERVICES ===")
    import subprocess
    services = ["postgresql"]
    # Docker containers
    import subprocess as _sp2
    for container in ["grafana"]:
        r = _sp2.run(f"docker ps --filter name={container} --format '{{.Names}}: {{.Status}}'", shell=True, capture_output=True, text=True)
        if r.stdout.strip():
            print(f"  ✓ Docker/{container}: {r.stdout.strip()}")
        else:
            print(f"  ✗ Docker/{container}: not running")
    for svc in services:
        r = subprocess.run(f"systemctl is-active {svc}", shell=True, capture_output=True, text=True)
        status = r.stdout.strip()
        icon = "✓" if status == "active" else "✗"
        print(f"  {icon} {svc}: {status}")
    # Ollama
    for port, name in [(11434, "V100"), (11435, "P40"), (11436, "3060"), (11437, "router")]:
        r = subprocess.run(f"curl -s --max-time 2 http://localhost:{port}/api/tags", shell=True, capture_output=True, text=True)
        if r.returncode == 0:
            try:
                models = json.loads(r.stdout).get("models", [])
                print(f"  ✓ Ollama:{port} ({name}): {len(models)} models")
            except:
                print(f"  ✓ Ollama:{port} ({name}): running")
        else:
            print(f"  ✗ Ollama:{port} ({name}): not responding")
    # LiteLLM
    r = subprocess.run("curl -s --max-time 2 http://localhost:4000/health", shell=True, capture_output=True, text=True)
    print(f"  {'✓' if r.returncode == 0 else '✗'} LiteLLM:4000: {'running' if r.returncode == 0 else 'not responding'}")


def cmd_nodes(args):
    """Show registered nodes."""
    from db import fetchall
    nodes = fetchall("SELECT * FROM workers ORDER BY node")
    if not nodes:
        print("No workers registered. EPYC should be self-registered.")
    for n in nodes:
        print(f"  {n['worker_id']}: node={n['node']} status={n['status']} last_heartbeat={n.get('last_heartbeat', '?')}")


def cmd_models(args):
    """Show model registry."""
    from db import fetchall
    models = fetchall("SELECT * FROM models ORDER BY provider, model_id")
    if not models:
        print("No models registered. Run: python3 -m models discover")
        return
    print(f"{'MODEL':<35} {'PROVIDER':<12} {'TOOLS':<6} {'CODING':<10} {'ROUTING':<25}")
    print("-" * 90)
    for m in models:
        classes = ", ".join(json.loads(m.get("routing_classes", "[]")) if isinstance(m.get("routing_classes"), str) else m.get("routing_classes", []))
        print(f"  {m['model_id']:<33} {m['provider']:<12} {str(m.get('supports_tools', '')):<6} {m.get('coding_capability', '?'):<10} {classes:<25}")


def cmd_repos(args):
    """Show registered repos."""
    from registry import RepoRegistry
    reg = RepoRegistry()
    repos = reg.list_repos()
    if not repos:
        print("No repos registered. Run: python3 -m registry scan")
        return
    print(f"{'NAME':<30} {'BRANCH':<20} {'DIRTY':<6} {'LANGUAGES':<30} {'LAST INSPECT'}")
    print("-" * 110)
    for r in repos:
        langs = ", ".join(json.loads(r.get("languages", "[]")) if isinstance(r.get("languages"), str) else r.get("languages", []))[:28]
        inspect = str(r.get("last_inspection_at", "never"))[:19]
        print(f"  {r.get('name', '?'):<28} {r.get('current_branch', '?'):<20} {str(r.get('dirty', '')):<6} {langs:<30} {inspect}")


def cmd_repo_inspect(args):
    """Inspect a specific repo."""
    if not args:
        print("Usage: hermes repo inspect REPO_PATH")
        return
    from actions.action_pack import ActionPack
    from registry import RepoRegistry
    path = args[0]
    reg = RepoRegistry()
    repo = None
    for r in reg.list_repos():
        if r.get("canonical_path") == path or r.get("name") == path or r.get("repo_id") == path:
            repo = r
            break
    if not repo:
        print(f"Repo not found: {path}")
        return
    pack = ActionPack(autonomy_level=0)
    result = pack.inspect(repo["canonical_path"], repo.get("repo_id"))
    print(json.dumps(result, indent=2, default=str))


def cmd_repo_health(args):
    """Run health analysis on a repo."""
    if not args:
        print("Usage: hermes repo health REPO_PATH")
        return
    from health import HealthEngine
    from registry import RepoRegistry
    path = args[0]
    reg = RepoRegistry()
    repo = None
    for r in reg.list_repos():
        if r.get("canonical_path") == path or r.get("name") == path or r.get("repo_id") == path:
            repo = r
            break
    if not repo:
        print(f"Repo not found: {path}")
        return
    he = HealthEngine()
    findings = he.analyze(repo["canonical_path"], repo.get("repo_id"))
    if not findings:
        print("No findings - repo is healthy!")
        return
    for f in findings:
        icon = {"critical": "🔴", "error": "🟠", "warning": "🟡", "info": "ℹ️"}.get(f.get("severity", "info"), "?")
        print(f"  {icon} [{f.get('dimension', '?')}] {f.get('description', '?')}")
        if f.get("file_path"):
            print(f"     at {f['file_path']}:{f.get('line_number', '')}")


def cmd_repo_map(args):
    """Show repo map."""
    if not args:
        print("Usage: hermes repo map REPO_PATH")
        return
    from intelligence import index_repo
    from registry import RepoRegistry
    path = args[0]
    reg = RepoRegistry()
    repo = None
    for r in reg.list_repos():
        if r.get("canonical_path") == path or r.get("name") == path or r.get("repo_id") == path:
            repo = r
            break
    if not repo:
        print(f"Repo not found: {path}")
        return
    data = index_repo(repo["canonical_path"], repo.get("repo_id"))
    repo_map = data.get("repo_map", {})
    print(f"=== REPO MAP: {repo.get('name', '?')} ===")
    print(f"Entry points: {len(repo_map.get('entry_points', []))}")
    for ep in repo_map.get("entry_points", []):
        print(f"  {ep['file']} ({ep['language']})")
    print(f"Modules: {len(repo_map.get('modules', []))}")
    for m in repo_map.get("modules", []):
        print(f"  {m['path']} ({m['type']})")
    print(f"API endpoints: {len(repo_map.get('api_endpoints', []))}")
    for ep in repo_map.get("api_endpoints", [])[:5]:
        print(f"  {ep.get('file', '?')}:{ep.get('line', '?')} {ep.get('content', '')[:60]}")
    print(f"Auth: {repo_map.get('auth', {}).get('type', 'unknown')} ({', '.join(repo_map.get('auth', {}).get('providers', []))})")
    print(f"Database: {repo_map.get('database', {}).get('type', 'unknown')} (orm={repo_map.get('database', {}).get('orm', 'none')})")
    print(f"Deployment: {repo_map.get('deployment', {}).get('platform', 'unknown')}")
    print(f"CI: {repo_map.get('ci', {}).get('provider', 'unknown')} ({', '.join(repo_map.get('ci', {}).get('workflows', []))})")
    print(f"Files indexed: {len(data.get('files', []))}")


def cmd_repo_fix(args):
    """Fix a repo issue."""
    if len(args) < 2:
        print("Usage: hermes repo fix REPO_PATH GOAL")
        return
    from actions.action_pack import ActionPack
    path, goal = args[0], " ".join(args[1:])
    print(f"Attempting to fix: {goal}")
    print(f"Path: {path}")
    pack = ActionPack(autonomy_level=1)
    # For now, try ruff auto-fix for lint issues
    if "lint" in goal.lower() or "format" in goal.lower():
        result = pack.fix_lint(path) if hasattr(pack, 'fix_lint') else {"status": "not implemented"}
        print(json.dumps(result, indent=2, default=str))
    else:
        print("Automated fix for this goal not yet implemented. Available fixes: lint, format")


def cmd_repo_verify(args):
    """Verify a repo."""
    if not args:
        print("Usage: hermes repo verify REPO_PATH")
        return
    from verification import verify, detect_profile
    path = args[0]
    profile = detect_profile(path)
    print(f"Profile: {profile}")
    result = verify(path, profile)
    for step in result.get("steps", []):
        icon = "✓" if step["passed"] else "✗"
        print(f"  {icon} {step['name']}: exit={step['exit_code']}")
        if not step["passed"]:
            print(f"    {step['output'][:200]}")
    print(f"\nOverall: {'PASSED' if result['passed'] else 'FAILED'}")


def cmd_jobs(args):
    """Show job queue."""
    from jobs import JobEngine
    je = JobEngine()
    depth = je.queue_depth()
    print("=== JOB QUEUE ===")
    for status, count in depth.items():
        print(f"  {status}: {count}")
    jobs = je.list_jobs(limit=10)
    if jobs:
        print(f"\n=== RECENT JOBS ===")
        for j in jobs:
            print(f"  {j.get('job_id', '?')[:8]} [{j.get('status', '?')}] {j.get('title', '?')} (repo={j.get('repo_id', '?')})")


def cmd_job_detail(args):
    """Show job details."""
    if not args:
        print("Usage: hermes job JOB_ID")
        return
    from jobs import JobEngine
    je = JobEngine()
    job = je.get(args[0])
    if not job:
        print(f"Job not found: {args[0]}")
        return
    print(json.dumps(job, indent=2, default=str))


def cmd_action_history(args):
    """Show action history."""
    from db import fetchall
    repo_id = args[0] if args else None
    if repo_id:
        rows = fetchall("SELECT * FROM action_receipts WHERE repo_id = :r ORDER BY created_at DESC LIMIT 20", {"r": repo_id})
    else:
        rows = fetchall("SELECT * FROM action_receipts ORDER BY created_at DESC LIMIT 20")
    if not rows:
        print("No action history.")
        return
    for r in rows:
        print(f"  {r.get('receipt_id', '?')[:8]} [{r.get('final_status', '?')}] {r.get('interpreted_action', '?')} repo={r.get('repo_id', '?')} at={str(r.get('created_at', '?'))[:19]}")


def cmd_rollback(args):
    """Rollback an action."""
    if not args:
        print("Usage: hermes rollback ACTION_ID or REPO_PATH COMMIT_HASH")
        return
    if len(args) >= 2:
        from actions.action_pack import ActionPack
        pack = ActionPack(autonomy_level=1)
        result = pack.rollback(args[0], args[1])
        print(json.dumps(result, indent=2, default=str))
    else:
        print("Need: REPO_PATH COMMIT_HASH")


def cmd_resources(args):
    """Show resource utilization."""
    from models.scheduler import ResourceScheduler
    rs = ResourceScheduler()
    r = rs.get_resources()
    cpu = r.get("cpu", {})
    ram = r.get("ram", {})
    print(f"CPU: {cpu.get('cores', '?')} cores, load {cpu.get('load_1m', '?')}/{cpu.get('load_5m', '?')}/{cpu.get('load_15m', '?')}")
    print(f"RAM: {ram.get('used_mb', 0)//1024}/{ram.get('total_mb', 0)//1024} GB ({ram.get('usage_pct', '?')}%)")
    print(f"Concurrency: {r.get('concurrency', {})}")
    for gpu in r.get("gpus", []):
        print(f"GPU {gpu.get('name', '?')}: {gpu.get('vram_used_mb', '?')}/{gpu.get('vram_total_mb', '?')} MB, lane={gpu.get('lane', '?')}, models={[m[0] if isinstance(m, tuple) else m for m in gpu.get('running_models', [])]}")


def cmd_backlog(args):
    """Show priority backlog."""
    from health import HealthEngine
    he = HealthEngine()
    items = he.get_backlog(limit=20)
    if not items:
        print("Backlog is empty. Run health analysis first: hermes repo health REPO")
        return
    print(f"{'#':<4} {'SEV':<10} {'SCORE':<8} {'REPO':<20} {'TITLE'}")
    print("-" * 80)
    for i, item in enumerate(items[:20], 1):
        print(f"  {i:<3} {item.get('severity', '?'):<10} {item.get('priority_score', 0):<8.1f} {item.get('repo_id', '?')[:18]:<20} {item.get('title', '?')[:50]}")


def cmd_repos_graph(args):
    """Show cross-repo relationships."""
    from db import fetchall
    rels = fetchall("""
        SELECT r1.name as source, r2.name as target, rel.relationship_type, rel.confidence
        FROM repo_relationships rel
        JOIN repos r1 ON rel.source_repo = r1.repo_id
        JOIN repos r2 ON rel.target_repo = r2.repo_id
        ORDER BY rel.confidence DESC
        LIMIT 30
    """)
    if not rels:
        print("No cross-repo relationships detected yet. Run repo scan first.")
        return
    for r in rels:
        print(f"  {r['source']} --[{r['relationship_type']}]--> {r['target']} (conf={r['confidence']:.2f})")


# Main dispatcher
def main():
    if len(sys.argv) < 2:
        print("Hermes Agent Governance CLI")
        print("Usage: python3 -m control <command> [args]")
        print()
        print("Commands:")
        print("  health              System health overview")
        print("  nodes               Registered nodes/workers")
        print("  models              Model registry")
        print("  repos               Registered repositories")
        print("  repo inspect PATH   Inspect a repository")
        print("  repo health PATH    Run health analysis")
        print("  repo map PATH       Show repo map")
        print("  repo fix PATH GOAL  Fix repo issues")
        print("  repo verify PATH    Run verification")
        print("  jobs                Job queue")
        print("  job ID              Job details")
        print("  history [REPO_ID]   Action history")
        print("  rollback PATH HASH  Rollback to commit")
        print("  resources           Resource utilization")
        print("  backlog             Priority backlog")
        print("  graph               Cross-repo relationships")
        return

    cmd = sys.argv[1]
    args = sys.argv[2:]

    dispatch = {
        "health": cmd_health,
        "nodes": cmd_nodes,
        "models": cmd_models,
        "repos": cmd_repos,
        "jobs": cmd_jobs,
        "job": cmd_job_detail,
        "history": cmd_action_history,
        "rollback": cmd_rollback,
        "resources": cmd_resources,
        "backlog": cmd_backlog,
        "graph": cmd_repos_graph,
    }

    # Handle subcommands
    if cmd == "repo" and args:
        subcmd = args[0]
        subargs = args[1:]
        sub_dispatch = {
            "inspect": cmd_repo_inspect,
            "health": cmd_repo_health,
            "map": cmd_repo_map,
            "fix": cmd_repo_fix,
            "verify": cmd_repo_verify,
        }
        if subcmd in sub_dispatch:
            sub_dispatch[subcmd](subargs)
        else:
            print(f"Unknown repo subcommand: {subcmd}")
    elif cmd in dispatch:
        dispatch[cmd](args)
    else:
        print(f"Unknown command: {cmd}")


if __name__ == "__main__":
    main()