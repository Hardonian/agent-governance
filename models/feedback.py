"""Routing Feedback — log, aggregate, and recommend models based on historical performance."""
import json
from db import execute, fetchone, fetchall


def log_routing_feedback(
    job_id: str,
    model_id: str,
    task_class: str,
    duration_ms: int,
    passed: bool,
    rolled_back: bool = False,
) -> None:
    """Write a routing feedback entry to the routing_log table.

    Also updates aggregate stats on the models table.
    """
    provider = model_id.split("/")[0] if "/" in model_id else "unknown"

    execute(
        """INSERT INTO routing_log
           (job_id, model_id, task_class, provider, duration_ms,
            verification_passed, rollback_occurred)
           VALUES (:job_id, :model_id, :task_class, :provider, :duration_ms,
                   :verification_passed, :rollback_occurred)""",
        {
            "job_id": job_id,
            "model_id": model_id,
            "task_class": task_class,
            "provider": provider,
            "duration_ms": duration_ms,
            "verification_passed": passed,
            "rollback_occurred": rolled_back,
        },
    )

    # Update model aggregate stats (exponential moving average for latency)
    execute(
        """UPDATE models SET
               task_total_count = task_total_count + 1,
               task_success_count = task_success_count + CASE WHEN :passed THEN 1 ELSE 0 END,
               avg_latency_ms = CASE
                   WHEN avg_latency_ms IS NULL THEN :dur
                   ELSE avg_latency_ms * 0.8 + :dur * 0.2
               END,
               updated_at = NOW()
           WHERE model_id = :model_id""",
        {
            "model_id": model_id,
            "dur": float(duration_ms),
            "passed": passed,
        },
    )


def get_model_stats(model_id: str) -> dict:
    """Aggregate success rate, avg duration, and rollback rate from routing_log.

    Returns dict with: model_id, total_routes, passed, rollbacks, avg_duration_ms,
    success_rate, rollback_rate.
    """
    row = fetchone(
        """SELECT
               model_id,
               COUNT(*) AS total_routes,
               COUNT(*) FILTER (WHERE verification_passed = TRUE) AS passed,
               COUNT(*) FILTER (WHERE rollback_occurred = TRUE) AS rollbacks,
               AVG(duration_ms)::integer AS avg_duration_ms
           FROM routing_log
           WHERE model_id = :model_id
           GROUP BY model_id""",
        {"model_id": model_id},
    )

    if row is None:
        return {
            "model_id": model_id,
            "total_routes": 0,
            "passed": 0,
            "rollbacks": 0,
            "avg_duration_ms": 0,
            "success_rate": 0.0,
            "rollback_rate": 0.0,
        }

    total = row.get("total_routes", 0)
    passed = row.get("passed", 0)
    rollbacks = row.get("rollbacks", 0)

    return {
        "model_id": row["model_id"],
        "total_routes": total,
        "passed": passed,
        "rollbacks": rollbacks,
        "avg_duration_ms": row.get("avg_duration_ms", 0) or 0,
        "success_rate": round(passed / total, 3) if total > 0 else 0.0,
        "rollback_rate": round(rollbacks / total, 3) if total > 0 else 0.0,
    }


def recommend_model(task_class: str) -> str | None:
    """Recommend the best model for a task class based on historical routing data.

    Scoring: success_rate * 10 - rollback_rate * 5 - avg_duration_ms / 10000.
    Falls back to models.route() if no routing history exists.
    """
    rows = fetchall(
        """SELECT
               rl.model_id,
               COUNT(*) AS total,
               COUNT(*) FILTER (WHERE rl.verification_passed = TRUE) AS passed,
               COUNT(*) FILTER (WHERE rl.rollback_occurred = TRUE) AS rollbacks,
               AVG(rl.duration_ms)::integer AS avg_dur
           FROM routing_log rl
           WHERE rl.task_class = :tc
           GROUP BY rl.model_id
           HAVING COUNT(*) >= 1""",
        {"tc": task_class},
    )

    if not rows:
        # Fall back to the model registry route method
        try:
            from models import ModelRegistry
            return ModelRegistry().route(task_class)
        except Exception:
            return None

    best = None
    best_score = -999.0

    for r in rows:
        total = r["total"] or 0
        passed = r["passed"] or 0
        rollbacks = r["rollbacks"] or 0
        avg_dur = r["avg_dur"] or 0

        success_rate = passed / total if total > 0 else 0.0
        rollback_rate = rollbacks / total if total > 0 else 0.0
        score = success_rate * 10 - rollback_rate * 5 - avg_dur / 10000

        if score > best_score:
            best_score = score
            best = r["model_id"]

    return best