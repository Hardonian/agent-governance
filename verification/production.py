"""Production-Aware Checks — verify deployment readiness across Vercel, Supabase, Stripe, Docker, CI."""
import json
import os
import re
import subprocess


def _run(cmd: str, cwd: str = None, timeout: int = 30) -> tuple:
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout, cwd=cwd)
        return r.returncode, r.stdout + r.stderr
    except Exception as e:
        return -1, str(e)


def _read_text(path: str, max_bytes: int = 100_000) -> str:
    try:
        with open(path, "r", errors="replace") as f:
            return f.read(max_bytes)
    except OSError:
        return ""


def _file_exists(path: str, *parts: str) -> bool:
    return os.path.isfile(os.path.join(path, *parts))


def _dir_exists(path: str, *parts: str) -> bool:
    return os.path.isdir(os.path.join(path, *parts))


def check_production_readiness(repo_path: str, repo_id: str) -> dict:
    """Run production-readiness checks on a repository.

    Checks:
    - Vercel: vercel.json exists, env vars configured
    - Supabase: RLS policies, service-role key not in client code
    - Stripe: webhook verification, no test keys in prod
    - Docker: health check, no hardcoded secrets
    - CI: workflows exist, required checks configured

    Returns: {checks: [{name, status, evidence, severity}]}
    """
    checks = []

    # --- Vercel ---
    checks.append(_check_vercel(repo_path))

    # --- Supabase ---
    checks.append(_check_supabase(repo_path))

    # --- Stripe ---
    checks.append(_check_stripe(repo_path))

    # --- Docker ---
    checks.append(_check_docker(repo_path))

    # --- CI ---
    checks.append(_check_ci(repo_path))

    return {"checks": checks}


def _check_vercel(repo_path: str) -> dict:
    """Check Vercel deployment config."""
    has_vercel_json = _file_exists(repo_path, "vercel.json")

    # Check for env var references
    has_env_config = False
    if has_vercel_json:
        content = _read_text(os.path.join(repo_path, "vercel.json"))
        try:
            vj = json.loads(content)
            has_env_config = bool(vj.get("env") or vj.get("build", {}).get("env"))
        except json.JSONDecodeError:
            pass

    # Check for .env.example
    has_env_example = any(
        _file_exists(repo_path, f)
        for f in (".env.example", ".env.sample", ".env.template", "env.example")
    )

    if has_vercel_json:
        evidence = {"vercel.json": True, "env_config": has_env_config, "env_example": has_env_example}
        if has_env_config or has_env_example:
            return {"name": "vercel", "status": "pass", "evidence": evidence, "severity": "info"}
        else:
            return {"name": "vercel", "status": "warn", "evidence": evidence, "severity": "warning"}
    else:
        return {"name": "vercel", "status": "skip", "evidence": {"vercel.json": False}, "severity": "info"}


def _check_supabase(repo_path: str) -> dict:
    """Check Supabase RLS and key exposure."""
    # Look for supabase references
    has_supabase = False
    for f in ("supabase/config.toml", "supabase.ts", "supabaseClient.ts"):
        if _file_exists(repo_path, f):
            has_supabase = True
            break

    if not has_supabase:
        # Grep for supabase in source
        code, out = _run("grep -rl 'supabase' --include='*.ts' --include='*.js' --include='*.py' . 2>/dev/null | head -3", cwd=repo_path)
        has_supabase = bool(out.strip())

    if not has_supabase:
        return {"name": "supabase", "status": "skip", "evidence": {"supabase_found": False}, "severity": "info"}

    # Check for service-role key in client code
    key_exposed = False
    evidence_files = []
    code, out = _run(
        "grep -rl 'service_role\\|SERVICE_ROLE\\|serviceRole' --include='*.ts' --include='*.js' --include='*.tsx' --include='*.jsx' . 2>/dev/null | grep -v node_modules | head -5",
        cwd=repo_path,
    )
    if out.strip():
        key_exposed = True
        evidence_files = [f.strip() for f in out.strip().split("\n") if f.strip()]

    # Check for RLS policy files
    has_rls = _dir_exists(repo_path, "supabase", "migrations")
    if not has_rls:
        code, out = _run("grep -rl 'ENABLE ROW LEVEL SECURITY\\|ALTER TABLE.*ENABLE ROW LEVEL' --include='*.sql' . 2>/dev/null | head -3", cwd=repo_path)
        has_rls = bool(out.strip())

    evidence = {
        "supabase_found": True,
        "rls_policies": has_rls,
        "service_key_exposed": key_exposed,
        "exposed_files": evidence_files,
    }

    if key_exposed:
        return {"name": "supabase", "status": "fail", "evidence": evidence, "severity": "critical"}
    if has_rls:
        return {"name": "supabase", "status": "pass", "evidence": evidence, "severity": "info"}
    return {"name": "supabase", "status": "warn", "evidence": evidence, "severity": "warning"}


def _check_stripe(repo_path: str) -> dict:
    """Check Stripe webhook verification and test vs prod keys."""
    has_stripe = False
    code, out = _run(
        "grep -rl 'stripe\\|Stripe' --include='*.ts' --include='*.js' --include='*.py' . 2>/dev/null | grep -v node_modules | head -3",
        cwd=repo_path,
    )
    has_stripe = bool(out.strip())

    if not has_stripe:
        return {"name": "stripe", "status": "skip", "evidence": {"stripe_found": False}, "severity": "info"}

    # Check for webhook verification
    code, out = _run(
        "grep -rl 'webhook.*construct\\|verifyHeader\\|Webhook.*signature' --include='*.ts' --include='*.js' --include='*.py' . 2>/dev/null | grep -v node_modules | head -3",
        cwd=repo_path,
    )
    has_webhook_verify = bool(out.strip())

    # Check for test keys in production-looking code
    code, out = _run(
        "grep -rn 'sk_test_\\|pk_test_' --include='*.ts' --include='*.js' --include='*.py' . 2>/dev/null | grep -v node_modules | grep -v '.env' | grep -v 'test' | head -5",
        cwd=repo_path,
    )
    test_keys_in_prod = bool(out.strip())

    evidence = {
        "stripe_found": True,
        "webhook_verification": has_webhook_verify,
        "test_keys_exposed": test_keys_in_prod,
    }

    if test_keys_in_prod:
        return {"name": "stripe", "status": "fail", "evidence": evidence, "severity": "critical"}
    if has_webhook_verify:
        return {"name": "stripe", "status": "pass", "evidence": evidence, "severity": "info"}
    return {"name": "stripe", "status": "warn", "evidence": evidence, "severity": "warning"}


def _check_docker(repo_path: str) -> dict:
    """Check Docker health check and hardcoded secrets."""
    has_docker = _file_exists(repo_path, "Dockerfile") or _file_exists(repo_path, "docker-compose.yml") or _file_exists(repo_path, "docker-compose.yaml")

    if not has_docker:
        return {"name": "docker", "status": "skip", "evidence": {"dockerfile_found": False}, "severity": "info"}

    # Check for HEALTHCHECK in Dockerfile
    has_healthcheck = False
    if _file_exists(repo_path, "Dockerfile"):
        content = _read_text(os.path.join(repo_path, "Dockerfile"))
        has_healthcheck = "HEALTHCHECK" in content

    # Check for hardcoded secrets in Dockerfile
    hardcoded_secrets = []
    if _file_exists(repo_path, "Dockerfile"):
        content = _read_text(os.path.join(repo_path, "Dockerfile"))
        for line_num, line in enumerate(content.splitlines(), 1):
            if re.search(r'(?i)(password|secret|api_key|token)\s*=\s*["\'][^"\']{8,}', line):
                hardcoded_secrets.append(f"Dockerfile:{line_num}")

    # Check docker-compose for hardcoded secrets
    for dc_file in ("docker-compose.yml", "docker-compose.yaml"):
        if _file_exists(repo_path, dc_file):
            content = _read_text(os.path.join(repo_path, dc_file))
            for line_num, line in enumerate(content.splitlines(), 1):
                if re.search(r'(?i)(password|secret|api_key|token):\s*["\']?(?!{|\$)[A-Za-z0-9+/=_\-]{8,}', line):
                    hardcoded_secrets.append(f"{dc_file}:{line_num}")

    evidence = {
        "dockerfile_found": True,
        "healthcheck": has_healthcheck,
        "hardcoded_secrets": hardcoded_secrets,
    }

    if hardcoded_secrets:
        return {"name": "docker", "status": "fail", "evidence": evidence, "severity": "critical"}
    if has_healthcheck:
        return {"name": "docker", "status": "pass", "evidence": evidence, "severity": "info"}
    return {"name": "docker", "status": "warn", "evidence": evidence, "severity": "warning"}


def _check_ci(repo_path: str) -> dict:
    """Check CI workflow existence and required checks."""
    has_github_workflows = _dir_exists(repo_path, ".github", "workflows")

    if not has_github_workflows:
        # Check other CI systems
        for name in (".travis.yml", ".circleci", "Jenkinsfile", ".gitlab-ci.yml"):
            if _file_exists(repo_path, name) or _dir_exists(repo_path, name):
                return {"name": "ci", "status": "pass", "evidence": {"ci_system": name}, "severity": "info"}
        return {"name": "ci", "status": "skip", "evidence": {"ci_found": False}, "severity": "warning"}

    # Count workflow files
    workflow_dir = os.path.join(repo_path, ".github", "workflows")
    try:
        workflows = [f for f in os.listdir(workflow_dir) if f.endswith((".yml", ".yaml"))]
    except OSError:
        workflows = []

    # Check for required checks in branch protection (can't check GitHub API here)
    # Just verify workflows exist with test/build steps
    has_test_workflow = False
    has_build_workflow = False
    for wf in workflows:
        content = _read_text(os.path.join(workflow_dir, wf))
        if "test" in content.lower() or "pytest" in content.lower() or "jest" in content.lower():
            has_test_workflow = True
        if "build" in content.lower() or "compile" in content.lower():
            has_build_workflow = True

    evidence = {
        "workflows": workflows,
        "has_test": has_test_workflow,
        "has_build": has_build_workflow,
    }

    if has_test_workflow and has_build_workflow:
        return {"name": "ci", "status": "pass", "evidence": evidence, "severity": "info"}
    if workflows:
        return {"name": "ci", "status": "warn", "evidence": evidence, "severity": "warning"}
    return {"name": "ci", "status": "fail", "evidence": evidence, "severity": "error"}