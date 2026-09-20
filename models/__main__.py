"""CLI for Model Registry and Resource Scheduler.

Usage:
    python3 -m models discover     Scan all endpoints and register models
    python3 -m models list         List all registered models
    python3 -m models route TASK   Pick best model for task class
    python3 -m models resources    Show current resource snapshot
    python3 -m models stats MODEL  Show routing stats for a model
"""
import sys
import json

from models import ModelRegistry
from models.scheduler import ResourceScheduler


def print_json(obj):
    """Pretty-print JSON, handling datetime and other non-serializable types."""
    def default(o):
        if hasattr(o, "isoformat"):
            return o.isoformat()
        return str(o)
    print(json.dumps(obj, indent=2, default=default))


def cmd_discover():
    reg = ModelRegistry()
    discovered = reg.discover()
    print(f"Discovered {len(discovered)} models across all endpoints:")
    for m in discovered:
        print(f"  {m['model_id']:40s}  {m['provider']:8s}  vram={m['vram_required_mb']:>6d}MB  "
              f"tools={'Y' if m['supports_tools'] else 'N'}  vision={'Y' if m['supports_vision'] else 'N'}  "
              f"coding={m['coding_capability']:8s}  reasoning={m['reasoning_capability']:8s}")
    return len(discovered)


def cmd_list():
    reg = ModelRegistry()
    models = reg.list_models()
    if not models:
        print("No models registered. Run 'discover' first.")
        return
    print(f"{'model_id':40s} {'provider':8s} {'ctx':>7s} {'vram':>7s} {'tools':5s} {'vis':4s} "
          f"{'coding':8s} {'reason':8s} {'routes':>6s} {'success':>7s}")
    print("-" * 110)
    for m in models:
        rc = m.get("routing_classes", [])
        if isinstance(rc, str):
            rc = json.loads(rc)
        print(f"{m['model_id']:40s} {m['provider']:8s} {m.get('context_capacity',0) or 0:>7d} "
              f"{m.get('vram_required_mb',0) or 0:>5d}MB "
              f"{'Y' if m.get('supports_tools') else 'N':5s} "
              f"{'Y' if m.get('supports_vision') else 'N':4s} "
              f"{m.get('coding_capability','unknown'):8s} "
              f"{m.get('reasoning_capability','unknown'):8s} "
              f"{m.get('task_total_count',0) or 0:>6d} "
              f"{m.get('task_success_count',0) or 0:>7d}")


def cmd_route(task_class):
    reg = ModelRegistry()
    model_id = reg.route(task_class)
    if model_id:
        print(f"Best model for '{task_class}': {model_id}")
    else:
        print(f"No suitable model found for task class '{task_class}'")
    return model_id


def cmd_resources():
    sched = ResourceScheduler()
    resources = sched.get_resources()
    print_json(resources)


def cmd_stats(model_id):
    reg = ModelRegistry()
    stats = reg.get_routing_stats(model_id)
    print_json(stats)


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    command = sys.argv[1].lower()

    if command == "discover":
        cmd_discover()
    elif command == "list":
        cmd_list()
    elif command == "route":
        if len(sys.argv) < 3:
            print("Usage: python3 -m models route TASK_CLASS")
            print(f"Available: {', '.join(['coding','reasoning','analysis','vision','general','lightweight'])}")
            sys.exit(1)
        cmd_route(sys.argv[2])
    elif command == "resources":
        cmd_resources()
    elif command == "stats":
        if len(sys.argv) < 3:
            print("Usage: python3 -m models stats MODEL_ID")
            sys.exit(1)
        cmd_stats(sys.argv[2])
    else:
        print(f"Unknown command: {command}")
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()