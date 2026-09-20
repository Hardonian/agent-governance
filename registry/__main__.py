"""CLI for the Repo Registry.

Usage:
    python -m registry scan      Discover and register all repos from approved roots
    python -m registry list      List all registered repos
    python -m registry inspect REPO_ID   Show full repo state
    python -m registry update REPO_ID    Refresh a repo's metadata
    python -m registry remove REPO_ID    Remove a repo from registry
"""

import json
import sys

from registry import RepoRegistry


def _json(obj):
    """Pretty-print JSON, handling datetime and other non-serialisable types."""
    def _default(o):
        if hasattr(o, "isoformat"):
            return o.isoformat()
        return str(o)
    print(json.dumps(obj, indent=2, default=_default))


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(1)

    cmd = args[0]
    reg = RepoRegistry()

    if cmd == "scan":
        repos = reg.scan_and_register()
        print(f"Discovered and registered {len(repos)} repo(s):")
        for r in repos:
            flags = []
            if r["dirty"]:
                flags.append("DIRTY")
            lang = ", ".join(r["languages"][:5]) if r["languages"] else "unknown"
            fw = ", ".join(r["frameworks"]) if r["frameworks"] else "none"
            print(f"  {r['name']:30s}  {lang:20s}  fw={fw:15s}  {' '.join(flags)}")
        print(f"\nTotal registered: {len(repos)}")

    elif cmd == "list":
        repos = reg.list_repos()
        if not repos:
            print("No repos registered. Run: python -m registry scan")
            sys.exit(0)
        print(f"{'Name':30s} {'Branch':15s} {'Head':10s} {'Dirty':5s} {'Languages':25s} {'Frameworks'}")
        print("-" * 120)
        for r in repos:
            langs = ", ".join(json.loads(r["languages"])[:4]) if isinstance(r["languages"], str) else ", ".join((r["languages"] or [])[:4])
            fws = ", ".join(json.loads(r["frameworks"])) if isinstance(r["frameworks"], str) else ", ".join(r["frameworks"] or [])
            dirty = "yes" if r["dirty"] else "no"
            head = (r["head_hash"] or "")[:8]
            branch = r["current_branch"] or "?"
            print(f"{r['name']:30s} {branch:15s} {head:10s} {dirty:5s} {langs:25s} {fws}")
        print(f"\nTotal: {len(repos)} repo(s)")

    elif cmd == "inspect":
        if len(args) < 2:
            print("Usage: python -m registry inspect REPO_ID")
            sys.exit(1)
        repo = reg.get(args[1])
        if not repo:
            print(f"Repo not found: {args[1]}")
            sys.exit(1)
        _json(repo)

    elif cmd == "update":
        if len(args) < 2:
            print("Usage: python -m registry update REPO_ID")
            sys.exit(1)
        repo = reg.update(args[1])
        if not repo:
            print(f"Repo not found: {args[1]}")
            sys.exit(1)
        print(f"Updated: {repo['name']}")

    elif cmd == "remove":
        if len(args) < 2:
            print("Usage: python -m registry remove REPO_ID")
            sys.exit(1)
        reg.remove(args[1])
        print(f"Removed repo {args[1]}")

    else:
        print(f"Unknown command: {cmd}")
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()