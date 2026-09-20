"""CLI for the Agent Governance job engine.

Usage:
    python3 -m jobs list [--status S] [--repo R] [--limit N]
    python3 -m jobs create --title T --type TYPE [--repo R] [--priority P] [--description D]
    python3 -m jobs get JOB_ID
    python3 -m jobs claim WORKER_ID
    python3 -m jobs complete JOB_ID [--status S] [--result JSON]
    python3 -m jobs fail JOB_ID --error MSG [--no-retry]
    python3 -m jobs cancel JOB_ID
    python3 -m jobs recover
    python3 -m jobs depth
"""
import argparse
import json
import sys
import os

# Ensure parent package is importable when run as `python3 -m jobs`
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jobs import JobEngine


def _pp(obj):
    """Pretty-print a dict or list of dicts."""
    def _serialize(row):
        out = {}
        for k, v in row.items():
            if hasattr(v, "isoformat"):
                out[k] = v.isoformat()
            else:
                out[k] = v
        return out

    if isinstance(obj, list):
        print(json.dumps([_serialize(r) for r in obj], indent=2))
    elif obj is not None:
        print(json.dumps(_serialize(obj), indent=2))
    else:
        print("null")


def main():
    p = argparse.ArgumentParser(prog="jobs", description="Agent Governance job engine CLI")
    sub = p.add_subparsers(dest="cmd")

    # list
    ls = sub.add_parser("list")
    ls.add_argument("--status")
    ls.add_argument("--repo")
    ls.add_argument("--limit", type=int, default=50)

    # create
    cr = sub.add_parser("create")
    cr.add_argument("--title", required=True)
    cr.add_argument("--type", dest="job_type", required=True)
    cr.add_argument("--repo")
    cr.add_argument("--priority", type=int, default=5)
    cr.add_argument("--description")

    # get
    g = sub.add_parser("get")
    g.add_argument("job_id")

    # claim
    cl = sub.add_parser("claim")
    cl.add_argument("worker_id")

    # complete
    co = sub.add_parser("complete")
    co.add_argument("job_id")
    co.add_argument("--status", default="SUCCEEDED")
    co.add_argument("--result")

    # fail
    fl = sub.add_parser("fail")
    fl.add_argument("job_id")
    fl.add_argument("--error", required=True)
    fl.add_argument("--no-retry", action="store_true")

    # cancel
    ca = sub.add_parser("cancel")
    ca.add_argument("job_id")

    # recover
    sub.add_parser("recover")

    # depth
    sub.add_parser("depth")

    args = p.parse_args()
    if not args.cmd:
        p.print_help()
        sys.exit(1)

    if args.cmd == "list":
        _pp(JobEngine.list_jobs(status=args.status, repo_id=args.repo,
                                limit=args.limit))

    elif args.cmd == "create":
        jid = JobEngine.create(
            title=args.title, job_type=args.job_type,
            repo_id=args.repo, priority=args.priority,
            description=args.description,
        )
        print(jid)

    elif args.cmd == "get":
        _pp(JobEngine.get(args.job_id))

    elif args.cmd == "claim":
        job = JobEngine.claim(args.worker_id)
        _pp(job)

    elif args.cmd == "complete":
        result = json.loads(args.result) if args.result else None
        JobEngine.complete(args.job_id, status=args.status, result=result)
        print(f"Job {args.job_id} -> {args.status}")

    elif args.cmd == "fail":
        requeued = JobEngine.fail(args.job_id, error=args.error,
                                  can_retry=not args.no_retry)
        state = "re-queued" if requeued else "FAILED permanently"
        print(f"Job {args.job_id}: {state}")

    elif args.cmd == "cancel":
        ok = JobEngine.cancel(args.job_id)
        print(f"Job {args.job_id}: {'cancelled' if ok else 'was already terminal'}")

    elif args.cmd == "recover":
        n = JobEngine.recover_on_startup()
        print(f"Recovered {n} stuck jobs")

    elif args.cmd == "depth":
        _pp(JobEngine.queue_depth())


if __name__ == "__main__":
    main()