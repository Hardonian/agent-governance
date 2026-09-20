#!/usr/bin/env python3
"""CLI entry point for repository quick actions.

Usage:
    python -m actions.quick_actions <command> <repo_path> [--goal GOAL] [--query QUERY] [--message MSG] [--files F [F ...]] [--commit HASH]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

# Allow running from the agent-governance directory
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from providers.local_git import LocalGitProvider
from providers.github_api import GitHubProvider, AuthError
from actions.action_receipt import ActionReceipt, receipt_store


def _json_out(obj: object) -> None:
    """Pretty-print a Pydantic model or dict as JSON."""
    if hasattr(obj, "model_dump"):
        print(json.dumps(obj.model_dump(), indent=2, default=str))
    else:
        print(json.dumps(obj, indent=2, default=str))


def _start_receipt(action_type: str, repo_path: str, provider: LocalGitProvider) -> ActionReceipt:
    """Build a receipt skeleton with the starting commit."""
    starting = ""
    try:
        info = provider.inspect(repo_path)
        if info.last_commit:
            starting = info.last_commit.hash
    except Exception:
        pass
    return ActionReceipt(action_type=action_type, repo_path=repo_path, starting_commit=starting)


def _finish_receipt(receipt: ActionReceipt, status: str, duration: float, *, commands: list[str] | None = None, files: list[str] | None = None, ending: str = "", extra: dict | None = None) -> None:
    """Finalize and persist a receipt."""
    receipt.status = status
    receipt.duration = round(duration, 3)
    if commands:
        receipt.commands_run = commands
    if files:
        receipt.files_changed = files
    if ending:
        receipt.ending_commit = ending
    if extra:
        receipt.extra = extra
    path = receipt_store.save(receipt)
    print(f"\n[receipt] {path}", file=sys.stderr)


def _get_ending_commit(repo_path: str, provider: LocalGitProvider) -> str:
    try:
        info = provider.inspect(repo_path)
        return info.last_commit.hash if info.last_commit else ""
    except Exception:
        return ""


# ------------------------------------------------------------------
# Command handlers
# ------------------------------------------------------------------

def cmd_inspect(args: argparse.Namespace, provider: LocalGitProvider) -> None:
    receipt = _start_receipt("inspect", args.repo, provider)
    t0 = time.monotonic()
    try:
        result = provider.inspect(args.repo)
        _json_out(result)
        _finish_receipt(receipt, "success", time.monotonic() - t0)
    except Exception as exc:
        _finish_receipt(receipt, "error", time.monotonic() - t0, extra={"error": str(exc)})
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


def cmd_status(args: argparse.Namespace, provider: LocalGitProvider) -> None:
    receipt = _start_receipt("status", args.repo, provider)
    t0 = time.monotonic()
    try:
        result = provider.status(args.repo)
        _json_out(result)
        _finish_receipt(receipt, "success", time.monotonic() - t0)
    except Exception as exc:
        _finish_receipt(receipt, "error", time.monotonic() - t0, extra={"error": str(exc)})
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


def cmd_plan(args: argparse.Namespace, provider: LocalGitProvider) -> None:
    goal = args.goal or "review"
    receipt = _start_receipt("plan", args.repo, provider)
    t0 = time.monotonic()
    try:
        result = provider.plan(args.repo, goal)
        _json_out(result)
        _finish_receipt(receipt, "success", time.monotonic() - t0, extra={"goal": goal})
    except Exception as exc:
        _finish_receipt(receipt, "error", time.monotonic() - t0, extra={"error": str(exc)})
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


def cmd_search(args: argparse.Namespace, provider: LocalGitProvider) -> None:
    query = args.query or args.goal or ""
    if not query:
        print("Error: --query or --goal is required for search", file=sys.stderr)
        sys.exit(1)
    receipt = _start_receipt("search", args.repo, provider)
    t0 = time.monotonic()
    try:
        results = provider.search(args.repo, query)
        _json_out(results)
        _finish_receipt(receipt, "success", time.monotonic() - t0, extra={"query": query, "hits": len(results)})
    except Exception as exc:
        _finish_receipt(receipt, "error", time.monotonic() - t0, extra={"error": str(exc)})
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


def cmd_test(args: argparse.Namespace, provider: LocalGitProvider) -> None:
    receipt = _start_receipt("test", args.repo, provider)
    t0 = time.monotonic()
    try:
        result = provider.test(args.repo)
        _json_out(result)
        _finish_receipt(receipt, "success" if result.failed == 0 and result.errors == 0 else "failure", time.monotonic() - t0, commands=[result.command_run])
    except Exception as exc:
        _finish_receipt(receipt, "error", time.monotonic() - t0, extra={"error": str(exc)})
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


def cmd_lint(args: argparse.Namespace, provider: LocalGitProvider) -> None:
    receipt = _start_receipt("lint", args.repo, provider)
    t0 = time.monotonic()
    try:
        result = provider.lint(args.repo)
        _json_out(result)
        _finish_receipt(receipt, "success" if result.errors == 0 else "failure", time.monotonic() - t0, commands=[result.command_run])
    except Exception as exc:
        _finish_receipt(receipt, "error", time.monotonic() - t0, extra={"error": str(exc)})
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


def cmd_diff(args: argparse.Namespace, provider: LocalGitProvider) -> None:
    receipt = _start_receipt("diff", args.repo, provider)
    t0 = time.monotonic()
    try:
        ref_a = args.ref_a if hasattr(args, "ref_a") else None
        ref_b = args.ref_b if hasattr(args, "ref_b") else None
        result = provider.diff(args.repo, ref_a=ref_a, ref_b=ref_b)
        _json_out(result)
        _finish_receipt(receipt, "success", time.monotonic() - t0, files=result.files_changed)
    except Exception as exc:
        _finish_receipt(receipt, "error", time.monotonic() - t0, extra={"error": str(exc)})
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


def cmd_commit(args: argparse.Namespace, provider: LocalGitProvider) -> None:
    message = args.message or args.goal or ""
    if not message:
        print("Error: --message or --goal is required for commit", file=sys.stderr)
        sys.exit(1)
    files = args.files if hasattr(args, "files") and args.files else None
    receipt = _start_receipt("commit", args.repo, provider)
    t0 = time.monotonic()
    try:
        result = provider.commit(args.repo, message, files)
        _json_out(result)
        ending = result.commit_hash or ""
        _finish_receipt(receipt, "success" if result.success else "failure", time.monotonic() - t0, files=result.files_changed, ending=ending, commands=["git add", f"git commit -m {message!r}"])
    except Exception as exc:
        _finish_receipt(receipt, "error", time.monotonic() - t0, extra={"error": str(exc)})
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


def cmd_rollback(args: argparse.Namespace, provider: LocalGitProvider) -> None:
    to_commit = args.commit or args.goal or ""
    if not to_commit:
        print("Error: --commit or --goal is required for rollback", file=sys.stderr)
        sys.exit(1)
    receipt = _start_receipt("rollback", args.repo, provider)
    receipt.policy_decisions.append(f"User requested rollback to {to_commit}")
    t0 = time.monotonic()
    try:
        result = provider.rollback(args.repo, to_commit)
        _json_out(result)
        _finish_receipt(receipt, "success" if result.success else "failure", time.monotonic() - t0, ending=result.to_commit, commands=[f"git reset --hard {to_commit}"])
    except Exception as exc:
        _finish_receipt(receipt, "error", time.monotonic() - t0, extra={"error": str(exc)})
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


def cmd_fix(args: argparse.Namespace, provider: LocalGitProvider) -> None:
    """Run lint, then test. Report combined results."""
    receipt = _start_receipt("fix", args.repo, provider)
    t0 = time.monotonic()
    try:
        lint = provider.lint(args.repo)
        test = provider.test(args.repo)
        result = {"lint": lint.model_dump(), "test": test.model_dump()}
        _json_out(result)
        ok = lint.errors == 0 and test.failed == 0 and test.errors == 0
        _finish_receipt(receipt, "success" if ok else "failure", time.monotonic() - t0, commands=[lint.command_run, test.command_run])
    except Exception as exc:
        _finish_receipt(receipt, "error", time.monotonic() - t0, extra={"error": str(exc)})
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


def cmd_review(args: argparse.Namespace, provider: LocalGitProvider) -> None:
    """Comprehensive review: inspect + status + diff + recent log."""
    receipt = _start_receipt("review", args.repo, provider)
    t0 = time.monotonic()
    try:
        info = provider.inspect(args.repo)
        st = provider.status(args.repo)
        d = provider.diff(args.repo)
        result = {
            "info": info.model_dump(),
            "status": st.model_dump(),
            "diff_summary": {
                "files_changed": d.files_changed,
                "total_added": d.total_added,
                "total_removed": d.total_removed,
            },
        }
        _json_out(result)
        _finish_receipt(receipt, "success", time.monotonic() - t0, files=d.files_changed)
    except Exception as exc:
        _finish_receipt(receipt, "error", time.monotonic() - t0, extra={"error": str(exc)})
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


def cmd_pr_prepare(args: argparse.Namespace, provider: LocalGitProvider) -> None:
    """Create a PR from the current branch via the GitHub API provider."""
    gh = GitHubProvider()
    receipt = _start_receipt("pr_prepare", args.repo, gh)
    t0 = time.monotonic()
    title = args.message or args.goal or f"PR from {gh._current_branch(os.path.abspath(args.repo))}"
    body = getattr(args, "body", "") or ""
    base = getattr(args, "base", "main") or "main"
    draft = getattr(args, "draft", False)
    try:
        result = gh.create_pr(args.repo, title=title, body=body, base=base, draft=draft)
        _json_out(result)
        status = "success" if result.get("url") and not result.get("error") else "failure"
        _finish_receipt(receipt, status, time.monotonic() - t0, extra=result)
        if result.get("error"):
            print(f"Error: {result['error']}", file=sys.stderr)
            sys.exit(1)
    except AuthError as exc:
        _finish_receipt(receipt, "error", time.monotonic() - t0, extra={"error": f"auth: {exc}"})
        print(f"GitHub auth error: {exc}", file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        _finish_receipt(receipt, "error", time.monotonic() - t0, extra={"error": str(exc)})
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


def cmd_security_scan(args: argparse.Namespace, provider: LocalGitProvider) -> None:
    """Check code-scanning and Dependabot alerts via the GitHub API."""
    gh = GitHubProvider()
    receipt = _start_receipt("security_scan", args.repo, gh)
    t0 = time.monotonic()
    try:
        result = gh.security_scan(args.repo)
        _json_out(result)
        has_issues = (
            result.get("summary", {}).get("code_scanning_open", 0) > 0
            or result.get("summary", {}).get("dependabot_open", 0) > 0
        )
        _finish_receipt(receipt, "success", time.monotonic() - t0, extra=result.get("summary", {}))
        if result.get("error"):
            print(f"Warning: {result['error']}", file=sys.stderr)
    except AuthError as exc:
        _finish_receipt(receipt, "error", time.monotonic() - t0, extra={"error": f"auth: {exc}"})
        print(f"GitHub auth error: {exc}", file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        _finish_receipt(receipt, "error", time.monotonic() - t0, extra={"error": str(exc)})
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


def cmd_review_pr(args: argparse.Namespace, provider: LocalGitProvider) -> None:
    """Review a PR: metadata, diff, and CI checks via the GitHub API."""
    gh = GitHubProvider()
    receipt = _start_receipt("review_pr", args.repo, gh)
    t0 = time.monotonic()
    pr_number = None
    if hasattr(args, "pr_number") and args.pr_number:
        try:
            pr_number = int(args.pr_number)
        except ValueError:
            print("Error: --pr-number must be an integer", file=sys.stderr)
            sys.exit(1)
    try:
        result = gh.review_pr(args.repo, pr_number=pr_number)
        _json_out(result)
        status = "success" if not result.get("error") else "failure"
        _finish_receipt(receipt, status, time.monotonic() - t0, extra={"pr_info": result.get("pr_info", {})})
        if result.get("error"):
            print(f"Error: {result['error']}", file=sys.stderr)
            sys.exit(1)
    except AuthError as exc:
        _finish_receipt(receipt, "error", time.monotonic() - t0, extra={"error": f"auth: {exc}"})
        print(f"GitHub auth error: {exc}", file=sys.stderr)
        sys.exit(1)
    except Exception as exc:
        _finish_receipt(receipt, "error", time.monotonic() - t0, extra={"error": str(exc)})
        print(f"Error: {exc}", file=sys.stderr)
        sys.exit(1)


# ------------------------------------------------------------------
# CLI
# ------------------------------------------------------------------

_COMMANDS = {
    "inspect": cmd_inspect,
    "status": cmd_status,
    "plan": cmd_plan,
    "search": cmd_search,
    "fix": cmd_fix,
    "test": cmd_test,
    "lint": cmd_lint,
    "diff": cmd_diff,
    "commit": cmd_commit,
    "review": cmd_review,
    "rollback": cmd_rollback,
    "pr_prepare": cmd_pr_prepare,
    "security_scan": cmd_security_scan,
    "review_pr": cmd_review_pr,
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="quick_actions",
        description="Repository quick actions CLI — inspect, status, plan, search, fix, test, lint, diff, commit, review, rollback, pr_prepare, security_scan, review_pr.",
    )
    parser.add_argument("command", choices=list(_COMMANDS.keys()), help="Action to perform")
    parser.add_argument("repo", help="Path to the git repository")
    parser.add_argument("--goal", help="Goal string for plan/commit/rollback/search")
    parser.add_argument("--query", help="Search query (for search command)")
    parser.add_argument("--message", "-m", help="Commit message / PR title")
    parser.add_argument("--files", nargs="+", help="Files to stage (for commit command)")
    parser.add_argument("--commit", help="Target commit hash (for rollback command)")
    parser.add_argument("--ref-a", help="First ref for diff")
    parser.add_argument("--ref-b", help="Second ref for diff")
    parser.add_argument("--pr-number", help="PR number (for review_pr command)")
    parser.add_argument("--body", help="PR body text (for pr_prepare command)")
    parser.add_argument("--base", help="Base branch for PR (default: main)")
    parser.add_argument("--draft", action="store_true", help="Create PR as draft")
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    provider = LocalGitProvider()
    handler = _COMMANDS[args.command]
    handler(args, provider)


if __name__ == "__main__":
    main()
