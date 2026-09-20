"""Agent Law Gateway — CLI entry point.

Usage:
    python -m gateway evaluate "rm -rf /tmp/junk"
    python -m gateway evaluate "git push --force" --paths /home/scott/repo
    python -m gateway classify "git push --force"
    python -m gateway scan-secrets "sk-abc123456789012345678901234567890"
    python -m gateway check-path /etc/passwd
    python -m gateway list-laws
"""

from __future__ import annotations

import argparse
import json
import sys

from gateway import load_default_engine
from gateway.classifier import CommandClassifier, RiskLevel
from gateway.secret_detector import SecretDetector
from gateway.path_guard import PathGuard


def cmd_evaluate(args: argparse.Namespace) -> int:
    engine = load_default_engine()
    paths = args.paths or []
    context = {}
    if args.context:
        for kv in args.context:
            if "=" in kv:
                k, v = kv.split("=", 1)
                context[k] = v

    decision = engine.evaluate(command=args.command, paths=paths, context=context)

    result = {
        "allowed": decision.allowed,
        "law_id": decision.law_id,
        "reason": decision.reason,
        "severity": decision.severity.value if decision.severity else None,
        "enforcement": decision.enforcement.value if decision.enforcement else None,
        "evidence": decision.evidence,
        "warnings": decision.warnings,
    }
    print(json.dumps(result, indent=2))
    return 0 if decision.allowed else 1


def cmd_classify(args: argparse.Namespace) -> int:
    classifier = CommandClassifier()
    level = classifier.classify(args.command)
    print(json.dumps({
        "command": args.command,
        "risk_level": level.name,
        "risk_value": level.value,
    }, indent=2))
    return 0


def cmd_scan_secrets(args: argparse.Namespace) -> int:
    detector = SecretDetector()
    matches = detector.scan(args.text)
    result = {
        "has_secrets": len(matches) > 0,
        "count": len(matches),
        "matches": [
            {"kind": m.kind, "redacted": m.redacted, "start": m.start, "end": m.end}
            for m in matches
        ],
    }
    print(json.dumps(result, indent=2))
    return 0 if not matches else 1


def cmd_check_path(args: argparse.Namespace) -> int:
    guard = PathGuard(allowed_roots=args.roots or ["/home", "/tmp", "/opt"])
    violations = guard.validate(args.path)
    result = {
        "path": args.path,
        "safe": len(violations) == 0,
        "violations": [
            {"rule": v.rule, "detail": v.detail} for v in violations
        ],
    }
    print(json.dumps(result, indent=2))
    return 0 if not violations else 1


def cmd_list_laws(args: argparse.Namespace) -> int:
    engine = load_default_engine()
    laws = []
    for law in engine._laws:
        laws.append({
            "law_id": law.law_id,
            "description": law.description,
            "severity": law.severity.value,
            "category": law.category.value,
            "enforcement": law.enforcement.value,
            "conditions": len(law.conditions),
        })
    print(json.dumps(laws, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="gateway",
        description="Agent Law Gateway — deterministic policy layer for Hermes",
    )
    sub = parser.add_subparsers(dest="command_name", required=True)

    # evaluate
    p_eval = sub.add_parser("evaluate", help="Evaluate a proposed command against all laws")
    p_eval.add_argument("command", help="The shell command to evaluate")
    p_eval.add_argument("--paths", nargs="*", default=[], help="File paths referenced")
    p_eval.add_argument("--context", nargs="*", default=[], help="Context key=value pairs")

    # classify
    p_class = sub.add_parser("classify", help="Classify a command's risk level")
    p_class.add_argument("command", help="The shell command to classify")

    # scan-secrets
    p_secret = sub.add_parser("scan-secrets", help="Scan text for secrets/credentials")
    p_secret.add_argument("text", help="Text to scan")

    # check-path
    p_path = sub.add_parser("check-path", help="Validate a path against guard rules")
    p_path.add_argument("path", help="Path to validate")
    p_path.add_argument("--roots", nargs="*", help="Allowed root directories")

    # list-laws
    sub.add_parser("list-laws", help="List all loaded laws")

    args = parser.parse_args()
    handlers = {
        "evaluate": cmd_evaluate,
        "classify": cmd_classify,
        "scan-secrets": cmd_scan_secrets,
        "check-path": cmd_check_path,
        "list-laws": cmd_list_laws,
    }
    return handlers[args.command_name](args)


if __name__ == "__main__":
    sys.exit(main())
