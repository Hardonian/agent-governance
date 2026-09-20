"""Agent Memory Boundaries - separates repo facts from ephemeral reasoning.

Repository facts are traceable to repository state. When HEAD changes,
potentially stale facts are detected.

Memory types:
- REPO_FACTS: durable, traceable to repo state, invalidated on HEAD change
- ACTION_HISTORY: durable, immutable log of actions taken
- SEMANTIC: vector-indexed, useful for retrieval (Qdrant)
- EPHEMERAL: session reasoning, not persisted
- PREFERENCES: user/project config, manual curation
"""
import json
import hashlib
from datetime import datetime, timedelta
from db import execute, fetchall, fetchone

def _resolve_repo_id(repo_id_or_name: str) -> str:
    """Resolve repo name to actual repo_id if needed."""
    from db import fetchone
    row = fetchone("SELECT repo_id FROM repos WHERE repo_id = :id OR name = :id", {"id": repo_id_or_name})
    return row["repo_id"] if row else repo_id_or_name



class MemoryBoundary:
    """Manages separation of memory types for agent governance."""

    def store_repo_fact(self, repo_id: str, fact_type: str, key: str, value: dict, head_hash: str):
        """Store a repo fact tied to a specific HEAD commit."""
        repo_id = _resolve_repo_id(repo_id)
        fact_id = hashlib.md5(f"{repo_id}:{fact_type}:{key}".encode()).hexdigest()
        execute("""
            INSERT INTO repo_memory (fact_id, repo_id, fact_type, key, value, head_hash, created_at, updated_at)
            VALUES (:fid, :repo, :ft, :key, :val, :head, NOW(), NOW())
            ON CONFLICT (fact_id) DO UPDATE SET
                value = EXCLUDED.value,
                head_hash = EXCLUDED.head_hash,
                updated_at = NOW()
        """, {"fid": fact_id, "repo": repo_id, "ft": fact_type, "key": key,
              "val": json.dumps(value), "head": head_hash})

    def get_repo_facts(self, repo_id: str, fact_type: str = None, current_head: str = None) -> list:
        """Get repo facts, optionally filtering by type and marking stale ones."""
        repo_id = _resolve_repo_id(repo_id)
        if fact_type:
            facts = fetchall("""
                SELECT * FROM repo_memory WHERE repo_id = :repo AND fact_type = :ft ORDER BY updated_at DESC
            """, {"repo": repo_id, "ft": fact_type})
        else:
            facts = fetchall("""
                SELECT * FROM repo_memory WHERE repo_id = :repo ORDER BY updated_at DESC
            """, {"repo": repo_id})

        if current_head:
            for f in facts:
                f["stale"] = f.get("head_hash") != current_head
        return facts

    def invalidate_stale(self, repo_id: str, new_head: str):
        """Mark facts as stale when HEAD changes."""
        repo_id = _resolve_repo_id(repo_id)
        execute("""
            UPDATE repo_memory SET stale = TRUE
            WHERE repo_id = :repo AND head_hash != :head AND stale = FALSE
        """, {"repo": repo_id, "head": new_head})

    def get_stale_count(self, repo_id: str, current_head: str) -> int:
        """Count stale facts for a repo."""
        repo_id = _resolve_repo_id(repo_id)
        result = fetchone("""
            SELECT COUNT(*) as cnt FROM repo_memory
            WHERE repo_id = :repo AND head_hash != :head
        """, {"repo": repo_id, "head": current_head})
        return result["cnt"] if result else 0

    def cleanup_old_facts(self, max_age_days: int = 90):
        """Remove facts older than max_age."""
        execute("""
            DELETE FROM repo_memory WHERE updated_at < NOW() - INTERVAL ':days days'
        """, {"days": max_age_days})

    def get_action_history(self, repo_id: str = None, limit: int = 50) -> list:
        """Get immutable action history."""
        if repo_id:
            return fetchall("""
                SELECT * FROM action_receipts WHERE repo_id = :repo
                ORDER BY created_at DESC LIMIT :limit
            """, {"repo": repo_id, "limit": limit})
        return fetchall("""
            SELECT * FROM action_receipts ORDER BY created_at DESC LIMIT :limit
        """, {"limit": limit})

    def store_preference(self, scope: str, key: str, value: dict):
        """Store a user/project preference (manual curation)."""
        pref_id = hashlib.md5(f"{scope}:{key}".encode()).hexdigest()
        execute("""
            INSERT INTO agent_preferences (pref_id, scope, key, value, created_at, updated_at)
            VALUES (:pid, :scope, :key, :val, NOW(), NOW())
            ON CONFLICT (pref_id) DO UPDATE SET
                value = EXCLUDED.value,
                updated_at = NOW()
        """, {"pid": pref_id, "scope": scope, "key": key, "val": json.dumps(value)})

    def get_preferences(self, scope: str = None) -> list:
        """Get stored preferences."""
        if scope:
            return fetchall("SELECT * FROM agent_preferences WHERE scope = :s", {"s": scope})
        return fetchall("SELECT * FROM agent_preferences ORDER BY scope, key")


def ensure_memory_tables():
    """Create memory tables if they don't exist."""
    execute("""
        CREATE TABLE IF NOT EXISTS repo_memory (
            fact_id TEXT PRIMARY KEY,
            repo_id TEXT REFERENCES repos(repo_id) ON DELETE CASCADE,
            fact_type TEXT NOT NULL,
            key TEXT NOT NULL,
            value JSONB DEFAULT '{}',
            head_hash TEXT,
            stale BOOLEAN DEFAULT FALSE,
            created_at TIMESTAMPTZ DEFAULT NOW(),
            updated_at TIMESTAMPTZ DEFAULT NOW()
        )
    """)
    execute("""
        CREATE TABLE IF NOT EXISTS agent_preferences (
            pref_id TEXT PRIMARY KEY,
            scope TEXT NOT NULL,
            key TEXT NOT NULL,
            value JSONB DEFAULT '{}',
            created_at TIMESTAMPTZ DEFAULT NOW(),
            updated_at TIMESTAMPTZ DEFAULT NOW()
        )
    """)
    execute("CREATE INDEX IF NOT EXISTS idx_memory_repo ON repo_memory(repo_id)")
    execute("CREATE INDEX IF NOT EXISTS idx_memory_stale ON repo_memory(stale) WHERE stale = TRUE")
    execute("CREATE INDEX IF NOT EXISTS idx_prefs_scope ON agent_preferences(scope)")