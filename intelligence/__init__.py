"""Repo Intelligence - indexing and repo mapping for Agent Governance Phase 2."""
import os
import json
import hashlib
import subprocess
from pathlib import Path
from typing import Optional


def _run(cmd: str, cwd: str = None, timeout: int = 10) -> str:
    """Run shell command and return stdout."""
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout, cwd=cwd)
        return r.stdout.strip()
    except Exception:
        return ""


def _detect_entry_points(repo_path: str) -> list[dict]:
    """Detect entry points in a repository."""
    entries = []
    candidates = [
        ("main.py", "python"), ("app.py", "python"), ("manage.py", "python-django"),
        ("server.py", "python"), ("__main__.py", "python"), ("run.py", "python"),
        ("index.js", "node"), ("index.ts", "node"), ("server.js", "node"),
        ("src/main.rs", "rust"), ("src/main.go", "go"),
        ("cmd/main.go", "go"), ("cmd/server/main.go", "go"),
        ("main.go", "go"), ("App.tsx", "react"), ("App.jsx", "react"),
        ("next.config.js", "next"), ("next.config.ts", "next"), ("next.config.mjs", "next"),
        ("manage.py", "django"),
    ]
    for fname, lang in candidates:
        fpath = os.path.join(repo_path, fname)
        if os.path.isfile(fpath):
            entries.append({"file": fname, "language": lang, "type": "entry_point"})
    return entries


def _detect_modules(repo_path: str) -> list[dict]:
    """Detect major modules/packages."""
    modules = []
    src_dirs = ["src", "app", "lib", "packages", "pkg", "cmd", "internal", "api"]
    for d in src_dirs:
        dpath = os.path.join(repo_path, d)
        if os.path.isdir(dpath):
            subdirs = [e for e in os.listdir(dpath) if os.path.isdir(os.path.join(dpath, e)) and not e.startswith('.')]
            if subdirs:
                modules.append({"path": d, "submodules": subdirs[:20], "type": "package"})
            else:
                files = [e for e in os.listdir(dpath) if os.path.isfile(os.path.join(dpath, e))]
                if files:
                    modules.append({"path": d, "files": len(files), "type": "directory"})
    # Check for monorepo
    if os.path.isfile(os.path.join(repo_path, "turbo.json")):
        modules.append({"path": "turbo.json", "type": "monorepo", "tool": "turborepo"})
    if os.path.isfile(os.path.join(repo_path, "pnpm-workspace.yaml")):
        modules.append({"path": "pnpm-workspace.yaml", "type": "monorepo", "tool": "pnpm"})
    return modules


def _detect_services(repo_path: str) -> list[dict]:
    """Detect services (docker, systemd, etc)."""
    services = []
    if os.path.isfile(os.path.join(repo_path, "Dockerfile")):
        services.append({"type": "docker", "file": "Dockerfile"})
    if os.path.isfile(os.path.join(repo_path, "docker-compose.yml")):
        services.append({"type": "docker-compose", "file": "docker-compose.yml"})
    if os.path.isfile(os.path.join(repo_path, "docker-compose.yaml")):
        services.append({"type": "docker-compose", "file": "docker-compose.yaml"})
    return services


def _detect_api_endpoints(repo_path: str) -> list[dict]:
    """Detect API endpoints from code (quick grep, not full AST)."""
    endpoints = []
    # Python FastAPI/Flask routes
    routes_raw = _run(f"grep -rn '@app\\.@router\\.\\|@app\\.get\\|@app\\.post\\|@app\\.put\\|@app\\.delete\\|@router\\.get\\|@router\\.post\\|@router\\.put\\|@router\\.delete' --include='*.py' {repo_path} 2>/dev/null | head -30")
    if routes_raw:
        for line in routes_raw.split('\n')[:20]:
            parts = line.split(':', 2)
            if len(parts) >= 2:
                endpoints.append({"file": parts[0].replace(repo_path + '/', ''), "line": parts[1], "content": parts[2][:100] if len(parts) > 2 else ""})
    # Node.js Express routes
    routes_raw = _run(f"grep -rn 'app\\.get\\|app\\.post\\|app\\.put\\|app\\.delete\\|router\\.get\\|router\\.post\\|router\\.put\\|router\\.delete' --include='*.ts' --include='*.js' {repo_path} 2>/dev/null | head -30")
    if routes_raw:
        for line in routes_raw.split('\n')[:20]:
            parts = line.split(':', 2)
            if len(parts) >= 2:
                endpoints.append({"file": parts[0].replace(repo_path + '/', ''), "line": parts[1], "content": parts[2][:100] if len(parts) > 2 else "", "framework": "express"})
    return endpoints


def _detect_auth(repo_path: str) -> dict:
    """Detect authentication configuration."""
    auth = {"type": "unknown", "providers": []}
    # Supabase
    supabase = _run(f"grep -rl 'supabase' --include='*.ts' --include='*.js' --include='*.py' --include='*.env*' {repo_path} 2>/dev/null | head -5")
    if supabase:
        auth["providers"].append("supabase")
    # Auth.js / NextAuth
    auth_js = _run(f"grep -rl 'next-auth\\|@auth' --include='*.ts' --include='*.js' {repo_path} 2>/dev/null | head -3")
    if auth_js:
        auth["providers"].append("next-auth")
    # Clerk
    clerk = _run(f"grep -rl '@clerk' --include='*.ts' --include='*.js' {repo_path} 2>/dev/null | head -3")
    if clerk:
        auth["providers"].append("clerk")
    # JWT
    jwt = _run(f"grep -rl 'jsonwebtoken\\|jwt\\|jose' --include='*.ts' --include='*.js' --include='*.py' {repo_path} 2>/dev/null | head -3")
    if jwt:
        auth["providers"].append("jwt")
    if auth["providers"]:
        auth["type"] = "detected"
    return auth


def _detect_db(repo_path: str) -> dict:
    """Detect database configuration."""
    db = {"type": "unknown", "orm": None, "migrations": False}
    # Prisma
    if os.path.isfile(os.path.join(repo_path, "prisma", "schema.prisma")):
        db["type"] = "prisma"
        db["orm"] = "prisma"
        db["migrations"] = os.path.isdir(os.path.join(repo_path, "prisma", "migrations"))
    # Drizzle
    drizzle = _run(f"find {repo_path} -name 'drizzle.config.*' -maxdepth 3 2>/dev/null")
    if drizzle:
        db["type"] = "drizzle"
        db["orm"] = "drizzle"
    # SQLAlchemy
    sa = _run(f"grep -rl 'SQLAlchemy\\|sqlalchemy' --include='*.py' {repo_path} 2>/dev/null | head -3")
    if sa:
        db["type"] = "sqlalchemy"
        db["orm"] = "sqlalchemy"
    # Supabase
    if os.path.isfile(os.path.join(repo_path, "supabase", "config.toml")):
        db["type"] = "supabase"
    return db


def _detect_billing(repo_path: str) -> dict:
    """Detect billing/Stripe configuration."""
    billing = {"type": "unknown", "provider": None}
    stripe = _run(f"grep -rl 'stripe' --include='*.ts' --include='*.js' --include='*.py' {repo_path} 2>/dev/null | head -5")
    if stripe:
        billing["type"] = "stripe"
        billing["provider"] = "stripe"
    return billing


def _detect_deployment(repo_path: str) -> dict:
    """Detect deployment configuration."""
    deploy = {"platform": "unknown", "config_files": []}
    if os.path.isfile(os.path.join(repo_path, "vercel.json")):
        deploy["platform"] = "vercel"
        deploy["config_files"].append("vercel.json")
    if os.path.isfile(os.path.join(repo_path, ".github", "workflows")):
        deploy["config_files"].append(".github/workflows")
        if deploy["platform"] == "unknown":
            deploy["platform"] = "github-actions"
    if os.path.isfile(os.path.join(repo_path, "Dockerfile")):
        deploy["config_files"].append("Dockerfile")
        if deploy["platform"] == "unknown":
            deploy["platform"] = "docker"
    return deploy


def _detect_ci(repo_path: str) -> dict:
    """Detect CI configuration."""
    ci = {"provider": "unknown", "workflows": []}
    gh_workflows = os.path.join(repo_path, ".github", "workflows")
    if os.path.isdir(gh_workflows):
        ci["provider"] = "github-actions"
        for f in os.listdir(gh_workflows):
            if f.endswith(('.yml', '.yaml')):
                ci["workflows"].append(f)
    return ci


def _build_repo_map(repo_path: str) -> dict:
    """Build a structured repo map."""
    return {
        "entry_points": _detect_entry_points(repo_path),
        "modules": _detect_modules(repo_path),
        "services": _detect_services(repo_path),
        "api_endpoints": _detect_api_endpoints(repo_path),
        "auth": _detect_auth(repo_path),
        "database": _detect_db(repo_path),
        "billing": _detect_billing(repo_path),
        "deployment": _detect_deployment(repo_path),
        "ci": _detect_ci(repo_path),
    }


def _symbols_from_file(filepath: str, language: str) -> dict:
    """Extract symbols from a file (lightweight grep-based)."""
    symbols = {"functions": [], "classes": [], "imports": [], "exports": []}
    try:
        with open(filepath, 'r', errors='ignore') as f:
            content = f.read(100000)  # cap at 100KB
    except Exception:
        return symbols

    import re
    if language in ('python',):
        symbols["functions"] = re.findall(r'(?:^|\n)\s*(?:async\s+)?def\s+(\w+)', content)
        symbols["classes"] = re.findall(r'(?:^|\n)\s*class\s+(\w+)', content)
        symbols["imports"] = re.findall(r'(?:^|\n)\s*(?:from\s+\S+\s+)?import\s+(\w+)', content)
    elif language in ('typescript', 'javascript'):
        symbols["functions"] = re.findall(r'(?:export\s+)?(?:async\s+)?function\s+(\w+)', content)
        symbols["classes"] = re.findall(r'(?:export\s+)?class\s+(\w+)', content)
        symbols["exports"] = re.findall(r'export\s+(?:default\s+)?(?:function|class|const|let|var)\s+(\w+)', content)
    elif language in ('rust',):
        symbols["functions"] = re.findall(r'(?:pub\s+)?(?:async\s+)?fn\s+(\w+)', content)
        symbols["classes"] = re.findall(r'(?:pub\s+)?struct\s+(\w+)', content)
    return symbols


def _language_from_ext(ext: str) -> str:
    """Map file extension to language."""
    return {
        '.py': 'python', '.js': 'javascript', '.ts': 'typescript', '.tsx': 'typescript',
        '.jsx': 'javascript', '.rs': 'rust', '.go': 'go', '.rb': 'ruby',
        '.java': 'java', '.kt': 'kotlin', '.swift': 'swift', '.c': 'c',
        '.cpp': 'cpp', '.h': 'c-header', '.hpp': 'cpp-header',
    }.get(ext.lower(), 'unknown')


EXCLUDE_DIRS = {'node_modules', '.git', 'vendor', '.next', 'dist', 'build',
                'coverage', '__pycache__', '.venv', 'venv', '.turbo', '.cache',
                'target', 'out', '.output', 'tmp', '.hermes'}


def index_repo(repo_path: str, repo_id: str, full: bool = True) -> dict:
    """Index a repository - build intelligence layer.
    
    Returns indexed data suitable for storing in repo_intelligence and repo_files tables.
    """
    result = {
        "repo_id": repo_id,
        "repo_map": {},
        "files": [],
        "index_version": 1,
    }

    if full:
        result["repo_map"] = _build_repo_map(repo_path)

    # Index files (limited depth, exclude heavy dirs)
    file_count = 0
    max_files = 500
    for root, dirs, files in os.walk(repo_path):
        # Prune excluded dirs
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
        # Limit depth
        depth = root.replace(repo_path, '').count(os.sep)
        if depth > 5:
            dirs.clear()
            continue

        for fname in files:
            if file_count >= max_files:
                break
            fpath = os.path.join(root, fname)
            rel_path = os.path.relpath(fpath, repo_path)
            ext = os.path.splitext(fname)[1].lower()
            if not ext or ext in ('.lock', '.min.js', '.min.css', '.map', '.pyc', '.pyo',
                                   '.so', '.dylib', '.dll', '.exe', '.bin', '.dat',
                                   '.png', '.jpg', '.jpeg', '.gif', '.svg', '.ico',
                                   '.woff', '.woff2', '.ttf', '.eot', '.pdf'):
                continue

            language = _language_from_ext(ext)
            if language == 'unknown':
                continue

            try:
                size = os.path.getsize(fpath)
                if size > 500000:  # Skip files > 500KB
                    continue
                with open(fpath, 'rb') as f:
                    file_hash = hashlib.md5(f.read(50000)).hexdigest()
            except Exception:
                size = 0
                file_hash = ""

            symbols = {}
            if full and size < 100000:
                symbols = _symbols_from_file(fpath, language)

            result["files"].append({
                "file_path": rel_path,
                "file_hash": file_hash,
                "language": language,
                "size_bytes": size,
                "symbols": symbols,
            })
            file_count += 1

    return result


def detect_stale(repo_path: str, indexed_head: str) -> bool:
    """Check if index is stale compared to current HEAD."""
    current_head = _run("git rev-parse HEAD", cwd=repo_path)
    return current_head != indexed_head


def incremental_update(repo_path: str, repo_id: str, old_index: dict) -> dict:
    """Update only changed files in the index."""
    changed = _run("git diff --name-only HEAD~1", cwd=repo_path)
    if not changed:
        return old_index

    changed_files = set(changed.strip().split('\n'))
    new_files = []
    for f in old_index.get("files", []):
        if f["file_path"] in changed_files:
            # Re-index this file
            fpath = os.path.join(repo_path, f["file_path"])
            ext = os.path.splitext(f["file_path"])[1].lower()
            language = _language_from_ext(ext)
            try:
                size = os.path.getsize(fpath)
                with open(fpath, 'rb') as fh:
                    file_hash = hashlib.md5(fh.read(50000)).hexdigest()
            except Exception:
                size = 0
                file_hash = ""
            symbols = _symbols_from_file(fpath, language) if size < 100000 else {}
            new_files.append({
                "file_path": f["file_path"],
                "file_hash": file_hash,
                "language": language,
                "size_bytes": size,
                "symbols": symbols,
            })
        else:
            new_files.append(f)
    old_index["files"] = new_files
    return old_index