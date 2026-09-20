"""Multi-Repo Query — answer cross-repo questions by querying the governance DB and grepping."""

import json
import os
import re
import subprocess

from db import fetchall, fetchone


# ── Pre-built query handlers ──────────────────────────────────────────

def _uses_nextjs(repos: list[dict]) -> list[dict]:
    results = []
    for r in repos:
        frameworks = r.get("frameworks") or []
        if isinstance(frameworks, str):
            frameworks = json.loads(frameworks)
        if any("next" in f.lower() for f in frameworks):
            results.append({"repo_id": r["repo_id"], "name": r["name"],
                            "evidence": {"frameworks": frameworks}})
    return results


def _uses_stripe(repos: list[dict]) -> list[dict]:
    results = []
    for r in repos:
        path = r["canonical_path"]
        try:
            out = subprocess.run(
                ["grep", "-rl", "stripe", "--include=*.ts", "--include=*.js",
                 "--include=*.py", path],
                capture_output=True, text=True, timeout=10,
            )
            if out.stdout.strip():
                files = [f.replace(path + "/", "") for f in out.stdout.strip().split("\n")[:5]]
                results.append({"repo_id": r["repo_id"], "name": r["name"],
                                "evidence": {"files": files}})
        except Exception:
            pass
    return results


def _uses_supabase(repos: list[dict]) -> list[dict]:
    results = []
    for r in repos:
        path = r["canonical_path"]
        try:
            out = subprocess.run(
                ["grep", "-rl", "supabase", "--include=*.ts", "--include=*.js",
                 "--include=*.py", "--include=*.env*", path],
                capture_output=True, text=True, timeout=10,
            )
            if out.stdout.strip():
                files = [f.replace(path + "/", "") for f in out.stdout.strip().split("\n")[:5]]
                results.append({"repo_id": r["repo_id"], "name": r["name"],
                                "evidence": {"files": files}})
        except Exception:
            pass
    return results


def _fails_build(repos: list[dict]) -> list[dict]:
    """Check health_findings for build errors."""
    findings = fetchall(
        "SELECT DISTINCT repo_id FROM health_findings WHERE dimension = 'build' "
        "AND severity IN ('error', 'critical') AND resolved = FALSE"
    )
    repo_ids_with_build_errors = {f["repo_id"] for f in findings}
    results = []
    for r in repos:
        if r["repo_id"] in repo_ids_with_build_errors:
            repo_findings = fetchall(
                "SELECT description FROM health_findings WHERE repo_id = :rid "
                "AND dimension = 'build' AND severity IN ('error', 'critical') AND resolved = FALSE LIMIT 3",
                {"rid": r["repo_id"]},
            )
            results.append({"repo_id": r["repo_id"], "name": r["name"],
                            "evidence": {"findings": [f["description"] for f in repo_findings]}})
    return results


def _has_no_tests(repos: list[dict]) -> list[dict]:
    results = []
    for r in repos:
        test_cmds = r.get("test_commands") or []
        if isinstance(test_cmds, str):
            test_cmds = json.loads(test_cmds)
        if not test_cmds:
            results.append({"repo_id": r["repo_id"], "name": r["name"],
                            "evidence": {"test_commands": []}})
    return results


def _uses_docker(repos: list[dict]) -> list[dict]:
    results = []
    for r in repos:
        containerized = r.get("containerized", False)
        build_system = r.get("build_system") or []
        if isinstance(build_system, str):
            build_system = json.loads(build_system)
        if containerized or "docker" in build_system:
            results.append({"repo_id": r["repo_id"], "name": r["name"],
                            "evidence": {"containerized": containerized, "build_system": build_system}})
    return results


def _deploy_vercel(repos: list[dict]) -> list[dict]:
    results = []
    for r in repos:
        platform = r.get("deployment_platform", "")
        if platform and "vercel" in str(platform).lower():
            results.append({"repo_id": r["repo_id"], "name": r["name"],
                            "evidence": {"deployment_platform": platform}})
    return results


def _uses_auth(repos: list[dict]) -> list[dict]:
    """Check repos for auth providers via intelligence data or grep."""
    results = []
    for r in repos:
        path = r["canonical_path"]
        providers = []
        for pat, name in [
            ("supabase", "supabase"), ("next-auth", "next-auth"),
            ("@auth/", "auth.js"), ("@clerk", "clerk"),
            ("passport", "passport"), ("firebase", "firebase"),
        ]:
            try:
                out = subprocess.run(
                    ["grep", "-rl", pat, "--include=*.ts", "--include=*.js",
                     "--include=*.py", path],
                    capture_output=True, text=True, timeout=8,
                )
                if out.stdout.strip():
                    providers.append(name)
            except Exception:
                pass
        if providers:
            results.append({"repo_id": r["repo_id"], "name": r["name"],
                            "evidence": {"auth_providers": providers}})
    return results


def _generic_grep(repos: list[dict], question: str) -> list[dict]:
    """Fallback: grep for question terms across repo files."""
    # Extract search terms from the question
    stop_words = {"what", "which", "repos", "repo", "uses", "use", "has", "have",
                  "does", "do", "the", "a", "an", "is", "are", "that", "with",
                  "in", "on", "for", "to", "of", "and", "or", "it"}
    terms = [w.lower() for w in re.findall(r'\w+', question) if w.lower() not in stop_words]
    if not terms:
        terms = [question.strip().lower()]

    results = []
    for r in repos:
        path = r["canonical_path"]
        matched_terms = []
        evidence_files = []
        for term in terms:
            try:
                out = subprocess.run(
                    ["grep", "-rl", "--include=*.py", "--include=*.ts",
                     "--include=*.js", "--include=*.tsx", "--include=*.json",
                     "--include=*.toml", "--include=*.yaml", "--include=*.yml",
                     term, path],
                    capture_output=True, text=True, timeout=8,
                )
                if out.stdout.strip():
                    matched_terms.append(term)
                    files = out.stdout.strip().split("\n")[:3]
                    evidence_files.extend([f.replace(path + "/", "") for f in files])
            except Exception:
                pass
        if matched_terms:
            results.append({"repo_id": r["repo_id"], "name": r["name"],
                            "evidence": {"matched_terms": matched_terms,
                                         "files": evidence_files[:5]}})
    return results


# ── Query router ──────────────────────────────────────────────────────

_QUERY_MAP = [
    (lambda q: "nextjs" in q or "next.js" in q or "next js" in q, _uses_nextjs),
    (lambda q: "stripe" in q, _uses_stripe),
    (lambda q: "supabase" in q, _uses_supabase),
    (lambda q: "fail" in q and "build" in q, _fails_build),
    (lambda q: "no test" in q or "has no test" in q or "without test" in q, _has_no_tests),
    (lambda q: "docker" in q, _uses_docker),
    (lambda q: "vercel" in q and ("deploy" in q or "uses" in q), _deploy_vercel),
    (lambda q: "auth" in q, _uses_auth),
]


def query_repos(question: str, repos: list = None) -> list[dict]:
    """Answer cross-repo questions.
    
    Args:
        question: Natural language question (e.g. 'uses stripe', 'fails build').
        repos: Optional list of repo dicts. If None, loads all from DB.
    
    Returns:
        List of {repo_id, name, evidence} dicts.
    """
    if repos is None:
        repos = fetchall("SELECT * FROM repos ORDER BY name")
    if not repos:
        return []

    q = question.lower().strip()

    # Route to specific handler
    for matcher, handler in _QUERY_MAP:
        if matcher(q):
            return handler(repos)

    # Fallback: generic grep
    return _generic_grep(repos, question)