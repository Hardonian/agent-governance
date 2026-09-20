"""Incremental Indexing — persist, retrieve, and detect stale intelligence indexes."""

import json
import subprocess

from db import execute, fetchone, fetchall


def _git_head(repo_path: str) -> str:
    """Get current git HEAD hash for a repo path."""
    try:
        r = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return r.stdout.strip()
    except Exception:
        return ""


def _resolve_repo_id(repo_id_or_name: str) -> str:
    """Resolve repo name to actual repo_id if needed."""
    from db import fetchone
    row = fetchone("SELECT repo_id FROM repos WHERE repo_id = :id OR name = :id", {"id": repo_id_or_name})
    return row["repo_id"] if row else repo_id_or_name

def persist_index(repo_id: str, index_data: dict) -> int:
    """Write index_repo() results to repo_intelligence and repo_files tables.
    
    On conflict, update. Tracks index_version (increments on each persist).
    Returns the new index_version.
    """
    repo_id = _resolve_repo_id(repo_id)
    repo_map = index_data.get("repo_map", {})
    files = index_data.get("files", [])

    # Get current version and increment
    existing = fetchone(
        "SELECT index_version FROM repo_intelligence WHERE repo_id = :rid",
        {"rid": repo_id},
    )
    new_version = (existing["index_version"] + 1) if existing else 1

    # Derive fields from repo_map
    entry_points = repo_map.get("entry_points", [])
    major_modules = repo_map.get("modules", [])
    services = repo_map.get("services", [])
    api_endpoints = repo_map.get("api_endpoints", [])
    db_layer = repo_map.get("database", {})
    auth_layer = repo_map.get("auth", {})
    billing_layer = repo_map.get("billing", {})
    deployment_config = repo_map.get("deployment", {})
    ci_config = repo_map.get("ci", {})

    # Upsert repo_intelligence
    execute(
        """
        INSERT INTO repo_intelligence (
            repo_id, index_version, entry_points, major_modules, services,
            api_endpoints, db_layer, auth_layer, billing_layer,
            external_integrations, background_workers, queues,
            test_structure, deployment_config, ci_config,
            observability, security_components, repo_map,
            stale, indexed_at
        ) VALUES (
            :repo_id, :index_version, :entry_points, :major_modules, :services,
            :api_endpoints, :db_layer, :auth_layer, :billing_layer,
            :external_integrations, :background_workers, :queues,
            :test_structure, :deployment_config, :ci_config,
            :observability, :security_components, :repo_map,
            FALSE, NOW()
        )
        ON CONFLICT (repo_id) DO UPDATE SET
            index_version = EXCLUDED.index_version,
            entry_points = EXCLUDED.entry_points,
            major_modules = EXCLUDED.major_modules,
            services = EXCLUDED.services,
            api_endpoints = EXCLUDED.api_endpoints,
            db_layer = EXCLUDED.db_layer,
            auth_layer = EXCLUDED.auth_layer,
            billing_layer = EXCLUDED.billing_layer,
            repo_map = EXCLUDED.repo_map,
            deployment_config = EXCLUDED.deployment_config,
            ci_config = EXCLUDED.ci_config,
            stale = FALSE,
            indexed_at = NOW()
        """,
        {
            "repo_id": repo_id,
            "index_version": new_version,
            "entry_points": json.dumps(entry_points),
            "major_modules": json.dumps(major_modules),
            "services": json.dumps(services),
            "api_endpoints": json.dumps(api_endpoints),
            "db_layer": json.dumps(db_layer),
            "auth_layer": json.dumps(auth_layer),
            "billing_layer": json.dumps(billing_layer),
            "external_integrations": json.dumps([]),
            "background_workers": json.dumps([]),
            "queues": json.dumps([]),
            "test_structure": json.dumps({}),
            "deployment_config": json.dumps(deployment_config),
            "ci_config": json.dumps(ci_config),
            "observability": json.dumps({}),
            "security_components": json.dumps([]),
            "repo_map": json.dumps(repo_map),
        },
    )

    # Upsert repo_files — delete old entries, insert fresh
    execute(
        "DELETE FROM repo_files WHERE repo_id = :rid",
        {"rid": repo_id},
    )
    for f in files:
        symbols = f.get("symbols", {})
        execute(
            """
            INSERT INTO repo_files (
                repo_id, file_path, file_hash, language, size_bytes,
                symbols, imports, exports, functions, classes, routes_found
            ) VALUES (
                :repo_id, :file_path, :file_hash, :language, :size_bytes,
                :symbols, :imports, :exports, :functions, :classes, :routes_found
            )
            ON CONFLICT (repo_id, file_path) DO UPDATE SET
                file_hash = EXCLUDED.file_hash,
                language = EXCLUDED.language,
                size_bytes = EXCLUDED.size_bytes,
                symbols = EXCLUDED.symbols,
                imports = EXCLUDED.imports,
                exports = EXCLUDED.exports,
                functions = EXCLUDED.functions,
                classes = EXCLUDED.classes,
                last_indexed = NOW()
            """,
            {
                "repo_id": repo_id,
                "file_path": f["file_path"],
                "file_hash": f.get("file_hash", ""),
                "language": f.get("language", "unknown"),
                "size_bytes": f.get("size_bytes", 0),
                "symbols": json.dumps(symbols),
                "imports": json.dumps(symbols.get("imports", [])),
                "exports": json.dumps(symbols.get("exports", [])),
                "functions": json.dumps(symbols.get("functions", [])),
                "classes": json.dumps(symbols.get("classes", [])),
                "routes_found": json.dumps([]),
            },
        )

    return new_version


def get_index(repo_id_or_name: str) -> dict | None:
    """Read back a persisted index for a repo. Returns None if not indexed."""
    repo_id = _resolve_repo_id(repo_id_or_name)
    intel = fetchone(
        "SELECT * FROM repo_intelligence WHERE repo_id = :rid",
        {"rid": repo_id},
    )
    if not intel:
        return None

    files = fetchall(
        "SELECT * FROM repo_files WHERE repo_id = :rid ORDER BY file_path",
        {"rid": repo_id},
    )
    intel["files"] = files
    return intel


def detect_stale_index(repo_id_or_name: str) -> dict:
    """Compare stored HEAD vs current git HEAD to detect staleness.
    
    Returns {"stale": bool, "stored_head": str, "current_head": str, "repo_id": str}.
    """
    repo_id = _resolve_repo_id(repo_id_or_name)
    repo = fetchone(
        "SELECT canonical_path, head_hash FROM repos WHERE repo_id = :rid",
        {"rid": repo_id},
    )
    if not repo:
        return {"stale": True, "stored_head": "", "current_head": "", "repo_id": repo_id,
                "error": "repo not found"}

    current_head = _git_head(repo["canonical_path"])
    stored_head = repo.get("head_hash", "") or ""
    stale = (current_head != stored_head) if current_head else True

    # Mark the index as stale in DB if so
    if stale:
        execute(
            "UPDATE repo_intelligence SET stale = TRUE WHERE repo_id = :rid",
            {"rid": repo_id},
        )

    return {
        "stale": stale,
        "stored_head": stored_head,
        "current_head": current_head,
        "repo_id": repo_id,
    }