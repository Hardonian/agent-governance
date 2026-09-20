"""Cross-Repo Relationship Graph — detect and persist relationships between repos."""

import json
import os
import re
from urllib.parse import urlparse

from db import execute, fetchall, fetchone


def _parse_remote_base(remote_url: str) -> str:
    """Extract base URL from a git remote (strip .git suffix, normalize)."""
    if not remote_url:
        return ""
    url = remote_url.rstrip("/")
    if url.endswith(".git"):
        url = url[:-4]
    # Normalize SSH form
    if url.startswith("git@"):
        url = url.replace(":", "/").replace("git@", "https://")
    return url.lower()


def _collect_packages(repo_path: str) -> set[str]:
    """Collect package names from requirements.txt, package.json, pyproject.toml."""
    packages: set[str] = set()
    # Python: requirements.txt
    req = os.path.join(repo_path, "requirements.txt")
    if os.path.isfile(req):
        try:
            with open(req) as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and not line.startswith("-"):
                        # Extract package name before version pin
                        name = re.split(r"[>=<!~\[]", line)[0].strip().lower()
                        if name:
                            packages.add(name)
        except Exception:
            pass

    # Node: package.json
    pkg_json = os.path.join(repo_path, "package.json")
    if os.path.isfile(pkg_json):
        try:
            with open(pkg_json) as f:
                data = json.load(f)
            for dep_type in ("dependencies", "devDependencies"):
                for name in data.get(dep_type, {}):
                    packages.add(name.lower())
        except Exception:
            pass

    # Python: pyproject.toml (simple parse)
    pyproject = os.path.join(repo_path, "pyproject.toml")
    if os.path.isfile(pyproject):
        try:
            with open(pyproject) as f:
                content = f.read()
            # Extract dependency names from [project.dependencies] or [tool.poetry.dependencies]
            for m in re.finditer(r'^\s*"?([a-zA-Z0-9_-]+)"?\s*[>=<~![]', content, re.MULTILINE):
                packages.add(m.group(1).lower())
        except Exception:
            pass

    return packages


def _collect_api_routes(repo_path: str) -> set[str]:
    """Extract API route patterns (e.g. /api/checkout)."""
    routes: set[str] = set()
    patterns = [
        r"""(?:app|router)\.(?:get|post|put|delete|patch)\s*\(\s*['"`]([^'"`]+)""",
        r"""@(?:app|router)\.(?:get|post|put|delete|patch)\s*\(\s*['"`]([^'"`]+)""",
    ]
    for root, dirs, files in os.walk(repo_path):
        dirs[:] = [d for d in dirs if d not in {
            "node_modules", ".git", "__pycache__", ".venv", "venv", "dist", "build", ".next"
        }]
        for fname in files:
            if not fname.endswith((".py", ".ts", ".js", ".tsx", ".jsx")):
                continue
            fpath = os.path.join(root, fname)
            try:
                with open(fpath, errors="ignore") as f:
                    content = f.read(100000)
                for pat in patterns:
                    for m in re.finditer(pat, content):
                        route = m.group(1)
                        # Normalize: strip dynamic params for comparison
                        normalized = re.sub(r'\[?\w+\]?', ':param', route)
                        routes.add(normalized)
            except Exception:
                continue
    return routes


def _detect_auth_providers(repo_path: str) -> set[str]:
    """Detect auth providers used."""
    providers: set[str] = set()
    try:
        result = os.popen(f"grep -rl 'supabase\\|next-auth\\|@auth\\|@clerk\\|passport\\|firebase.auth' --include='*.ts' --include='*.js' --include='*.py' --include='*.env*' {repo_path} 2>/dev/null | head -5").read().strip()
        if result:
            content_sample = ""
            for line in result.split("\n")[:3]:
                try:
                    with open(line.strip(), errors="ignore") as f:
                        content_sample += f.read(5000)
                except Exception:
                    pass
            low = content_sample.lower()
            if "supabase" in low:
                providers.add("supabase")
            if "next-auth" in low or "@auth" in low:
                providers.add("next-auth")
            if "@clerk" in low:
                providers.add("clerk")
            if "passport" in low:
                providers.add("passport")
            if "firebase" in low:
                providers.add("firebase")
    except Exception:
        pass
    return providers


def _detect_orm(repo_path: str) -> str:
    """Detect ORM/database tool used."""
    if os.path.isfile(os.path.join(repo_path, "prisma", "schema.prisma")):
        return "prisma"
    # Check for sqlalchemy
    try:
        result = os.popen(f"grep -rl 'SQLAlchemy\\|sqlalchemy\\|from.*import.*Model' --include='*.py' {repo_path} 2>/dev/null | head -3").read().strip()
        if result:
            return "sqlalchemy"
    except Exception:
        pass
    # Check for drizzle
    if os.path.isfile(os.path.join(repo_path, "drizzle.config.ts")) or os.path.isfile(os.path.join(repo_path, "drizzle.config.js")):
        return "drizzle"
    return ""


def _detect_deploy_platform(repo_path: str) -> str:
    """Detect deployment platform."""
    if os.path.isfile(os.path.join(repo_path, "vercel.json")):
        return "vercel"
    if os.path.isfile(os.path.join(repo_path, "fly.toml")):
        return "fly"
    if os.path.isfile(os.path.join(repo_path, "render.yaml")):
        return "render"
    if os.path.isfile(os.path.join(repo_path, "Dockerfile")):
        return "docker"
    return ""


def _has_prisma_refs(repo_path: str) -> set[str]:
    """Check for Prisma schema model references."""
    refs: set[str] = set()
    schema_path = os.path.join(repo_path, "prisma", "schema.prisma")
    if os.path.isfile(schema_path):
        try:
            with open(schema_path) as f:
                content = f.read()
            for m in re.finditer(r'model\s+(\w+)', content):
                refs.add(m.group(1))
        except Exception:
            pass
    return refs


def detect_relationships() -> list[dict]:
    """Scan all registered repos and populate repo_relationships table.
    
    Detects: shared_packages, shared_apis, shared_auth, shared_db,
    shared_deploy, shared_schema, fork relationships.
    
    Returns list of detected relationships.
    """
    repos = fetchall("SELECT * FROM repos ORDER BY name")
    if len(repos) < 2:
        return []

    # Pre-compute per-repo data
    repo_data: dict[str, dict] = {}
    for r in repos:
        rid = r["repo_id"]
        path = r["canonical_path"]
        repo_data[rid] = {
            "name": r["name"],
            "path": path,
            "packages": _collect_packages(path),
            "routes": _collect_api_routes(path),
            "auth": _detect_auth_providers(path),
            "orm": _detect_orm(path),
            "deploy": _detect_deploy_platform(path),
            "remote": r.get("remote_url", "") or "",
            "prisma_models": _has_prisma_refs(path),
        }

    relationships: list[dict] = []
    repo_ids = list(repo_data.keys())

    for i in range(len(repo_ids)):
        for j in range(i + 1, len(repo_ids)):
            a_id, b_id = repo_ids[i], repo_ids[j]
            a, b = repo_data[a_id], repo_data[b_id]

            # 1. shared_packages
            shared_pkgs = a["packages"] & b["packages"]
            # Filter out trivially common packages
            _trivial = {"setuptools", "pip", "wheel", "python", "node", "npm"}
            meaningful_pkgs = shared_pkgs - _trivial
            if len(meaningful_pkgs) >= 3:
                relationships.append({
                    "source": a_id, "target": b_id,
                    "type": "shared_packages",
                    "confidence": min(0.9, 0.3 + len(meaningful_pkgs) * 0.1),
                    "evidence": {"shared": sorted(meaningful_pkgs)[:20]},
                })

            # 2. shared_apis
            shared_routes = a["routes"] & b["routes"]
            if shared_routes:
                relationships.append({
                    "source": a_id, "target": b_id,
                    "type": "shared_apis",
                    "confidence": min(0.95, 0.4 + len(shared_routes) * 0.15),
                    "evidence": {"shared_routes": sorted(shared_routes)[:10]},
                })

            # 3. shared_auth
            shared_auth = a["auth"] & b["auth"]
            if shared_auth:
                relationships.append({
                    "source": a_id, "target": b_id,
                    "type": "shared_auth",
                    "confidence": 0.8,
                    "evidence": {"providers": sorted(shared_auth)},
                })

            # 4. shared_db
            if a["orm"] and a["orm"] == b["orm"]:
                relationships.append({
                    "source": a_id, "target": b_id,
                    "type": "shared_db",
                    "confidence": 0.75,
                    "evidence": {"orm": a["orm"]},
                })

            # 5. shared_deploy
            if a["deploy"] and a["deploy"] == b["deploy"]:
                relationships.append({
                    "source": a_id, "target": b_id,
                    "type": "shared_deploy",
                    "confidence": 0.7,
                    "evidence": {"platform": a["deploy"]},
                })

            # 6. shared_schema (Prisma model overlap)
            if a["prisma_models"] and b["prisma_models"]:
                shared_models = a["prisma_models"] & b["prisma_models"]
                if shared_models:
                    relationships.append({
                        "source": a_id, "target": b_id,
                        "type": "shared_schema",
                        "confidence": 0.85,
                        "evidence": {"shared_models": sorted(shared_models)},
                    })

            # 7. fork (same remote base URL)
            if a["remote"] and b["remote"]:
                base_a = _parse_remote_base(a["remote"])
                base_b = _parse_remote_base(b["remote"])
                if base_a and base_b and base_a == base_b:
                    relationships.append({
                        "source": a_id, "target": b_id,
                        "type": "fork",
                        "confidence": 0.95,
                        "evidence": {"remote_base": base_a},
                    })

    # Persist to DB
    # Clear old computed relationships first
    execute("DELETE FROM repo_relationships WHERE relationship_type != 'manual'")

    for rel in relationships:
        execute(
            """
            INSERT INTO repo_relationships (
                source_repo, target_repo, relationship_type,
                evidence, confidence
            ) VALUES (
                :source, :target, :rel_type,
                :evidence, :confidence
            )
            ON CONFLICT (source_repo, target_repo, relationship_type) DO UPDATE SET
                evidence = EXCLUDED.evidence,
                confidence = EXCLUDED.confidence
            """,
            {
                "source": rel["source"],
                "target": rel["target"],
                "rel_type": rel["type"],
                "evidence": json.dumps(rel["evidence"]),
                "confidence": rel["confidence"],
            },
        )

    return relationships