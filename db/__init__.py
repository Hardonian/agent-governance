"""Database connection pool for Agent Governance."""
import os
from contextlib import contextmanager
from sqlalchemy import create_engine, text
from sqlalchemy.pool import QueuePool

_DSN = os.environ.get(
    "AGENT_GOV_DB",
    "postgresql://agent:agent_local@127.0.0.1/agent_governance",
)

_engine = None


def get_engine():
    global _engine
    if _engine is None:
        _engine = create_engine(
            _DSN,
            poolclass=QueuePool,
            pool_size=5,
            max_overflow=10,
            pool_pre_ping=True,
        )
    return _engine


@contextmanager
def get_conn():
    engine = get_engine()
    conn = engine.connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def execute(sql: str, params: dict = None):
    with get_conn() as conn:
        result = conn.execute(text(sql), params or {})
        return result


def fetchall(sql: str, params: dict = None):
    with get_conn() as conn:
        result = conn.execute(text(sql), params or {})
        return [dict(row._mapping) for row in result]


def fetchone(sql: str, params: dict = None):
    with get_conn() as conn:
        result = conn.execute(text(sql), params or {})
        row = result.fetchone()
        return dict(row._mapping) if row else None