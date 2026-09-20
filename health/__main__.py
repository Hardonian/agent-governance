"""CLI entry point for the health engine: python3 -m health <command>."""

from __future__ import annotations

import json
import sys
import os

# Ensure project root is on sys.path so `db` and `providers` are importable
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from health import HealthEngine


def _usage() -> None:
    print("Usage:")
    print("  python3 -m health analyze <REPO_PATH> [REPO_ID]")
    print("  python3 -m health backlog [--limit N]")
    print("  python3 -m health findings <REPO_ID>")
    sys.exit(1)


def main() -> None:
    args = sys.argv[1:]
    if not args:
        _usage()

    cmd = args[0]
    engine = HealthEngine()

    if cmd == "analyze":
        if len(args) < 2:
            _usage()
        repo_path = os.path.abspath(args[1])
        repo_id = args[2] if len(args) > 2 else os.path.basename(repo_path)
        print(f"Analyzing {repo_path} (repo_id={repo_id}) …")
        findings = engine.analyze(repo_path, repo_id)
        print(f"\n{'='*60}")
        print(f"Found {len(findings)} issue(s):")
        print(f"{'='*60}")
        for f in findings:
            sev = f.get("severity", "?").upper()
            dim = f.get("dimension", "?")
            desc = f.get("description", "")
            safe = " [auto-fixable]" if f.get("can_fix_safely") else ""
            print(f"  [{sev:>8}] [{dim:<10}] {desc}{safe}")
        print()

    elif cmd == "backlog":
        limit = 20
        if "--limit" in args:
            idx = args.index("--limit")
            if idx + 1 < len(args):
                limit = int(args[idx + 1])
        items = engine.get_backlog(limit=limit)
        if not items:
            print("Backlog is empty — run 'analyze' first.")
            return
        print(f"{'='*60}")
        print(f"Priority Backlog ({len(items)} items)")
        print(f"{'='*60}")
        for i, item in enumerate(items, 1):
            score = item.get("priority_score", 0)
            sev = (item.get("severity") or "?").upper()
            title = item.get("title", "")
            print(f"  {i:>3}. [{sev:>8}] score={score:>6.1f}  {title}")
        print()

    elif cmd == "findings":
        if len(args) < 2:
            _usage()
        repo_id = args[1]
        findings = engine.get_findings(repo_id)
        if not findings:
            print(f"No open findings for repo '{repo_id}'.")
            return
        print(f"{'='*60}")
        print(f"Open findings for '{repo_id}' ({len(findings)} items)")
        print(f"{'='*60}")
        for f in findings:
            sev = (f.get("severity") or "?").upper()
            dim = f.get("dimension", "?")
            desc = f.get("description", "")
            fid = f.get("id", "?")
            print(f"  #{fid} [{sev:>8}] [{dim:<10}] {desc}")
        print()

    else:
        _usage()


if __name__ == "__main__":
    main()