"""Model Capability Registry and Resource Scheduler for Agent Governance Phase 2."""
import json
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone

from db import execute, fetchall, fetchone

# Ollama endpoints by GPU lane
OLLAMA_ENDPOINTS = {
    "v100": {"url": "http://127.0.0.1:11434", "vram_mb": 16384, "gpu": "V100"},
    "p40":  {"url": "http://127.0.0.1:11435", "vram_mb": 23040, "gpu": "P40"},
    "3060": {"url": "http://127.0.0.1:11436", "vram_mb": 12288, "gpu": "RTX3060"},
    "router": {"url": "http://127.0.0.1:11437", "vram_mb": 0, "gpu": "router"},
}

LITELLM_ENDPOINT = "http://127.0.0.1:4000"

# Task class → preferred model characteristics
TASK_ROUTING = {
    "coding":         {"prefer_capabilities": ["tools", "insert"], "prefer_families": ["qwen2", "qwen35"], "min_params_b": 14},
    "reasoning":      {"prefer_capabilities": ["thinking"], "prefer_families": ["qwen35"], "min_params_b": 9},
    "analysis":       {"prefer_capabilities": ["tools"], "prefer_families": ["qwen35", "qwen2"], "min_params_b": 9},
    "vision":         {"prefer_capabilities": ["vision"], "prefer_families": ["qwen35", "qwen3vl"], "min_params_b": 8},
    "general":        {"prefer_capabilities": ["completion"], "prefer_families": [], "min_params_b": 0},
    "lightweight":    {"prefer_capabilities": [], "prefer_families": [], "min_params_b": 0},
}


def _parse_params(param_str: str) -> float:
    """Extract numeric parameter count from strings like '14.8B' -> 14.8."""
    if not param_str:
        return 0.0
    s = param_str.strip().upper().replace("B", "").replace("M", "")
    try:
        val = float(s)
        if "M" in param_str.upper():
            val /= 1000.0
        return val
    except ValueError:
        return 0.0


def _ollama_request(url: str, path: str, timeout: int = 10) -> dict | None:
    """Make an HTTP request to an Ollama endpoint."""
    try:
        req = urllib.request.Request(f"{url}{path}")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except (urllib.error.URLError, OSError, json.JSONDecodeError, TimeoutError):
        return None


def _class_for_model(name: str, capabilities: list, family: str, param_b: float) -> list:
    """Determine routing classes for a model based on its metadata."""
    classes = []
    caps_lower = [c.lower() for c in capabilities]
    if "insert" in caps_lower or "tools" in caps_lower and family in ("qwen2", "qwen35"):
        classes.append("coding")
    if "thinking" in caps_lower and param_b >= 9:
        classes.append("reasoning")
    if "tools" in caps_lower:
        classes.append("analysis")
    if "vision" in caps_lower:
        classes.append("vision")
    classes.append("general")
    if param_b <= 9:
        classes.append("lightweight")
    return list(set(classes))


def _estimate_vram(param_b: float, quant: str) -> int:
    """Rough VRAM estimate in MB for a model."""
    # Q4_K_M ≈ ~0.6 bytes/param, Q4_0 similar, F16 ≈ 2 bytes/param
    if "F16" in quant.upper():
        return int(param_b * 1e9 * 2 / 1e6)
    return int(param_b * 1e9 * 0.6 / 1e6)


def _coding_capability(name: str, capabilities: list, family: str) -> str:
    caps = [c.lower() for c in capabilities]
    if "coder" in name.lower() or ("insert" in caps and family in ("qwen2",)):
        return "strong"
    if "tools" in caps and family in ("qwen35", "qwen2"):
        return "moderate"
    return "basic"


def _reasoning_capability(name: str, capabilities: list) -> str:
    caps = [c.lower() for c in capabilities]
    if "thinking" in caps and ("deepseek" in name.lower() or "qwen3" in name.lower()):
        return "strong"
    if "thinking" in caps:
        return "moderate"
    return "basic"


class ModelRegistry:
    """Discovers and tracks models across Ollama lanes and LiteLLM."""

    def discover(self) -> list[dict]:
        """Scan all endpoints, upsert discovered models into DB, return list."""
        discovered = []
        now = datetime.now(timezone.utc).isoformat()

        for lane, info in OLLAMA_ENDPOINTS.items():
            data = _ollama_request(info["url"], "/api/tags")
            if not data or "models" not in data:
                continue
            for m in data["models"]:
                name = m["name"]
                det = m.get("details", {})
                caps = m.get("capabilities", ["completion"])
                family = det.get("family", "unknown")
                param_size = det.get("parameter_size", "")
                quant = det.get("quantization_level", "")
                ctx_len = det.get("context_length", 0)
                param_b = _parse_params(param_size)
                vram_mb = _estimate_vram(param_b, quant)

                model_id = f"{lane}/{name}"
                endpoint = f"{info['url']}/v1"
                routing_classes = _class_for_model(name, caps, family, param_b)
                coding_cap = _coding_capability(name, caps, family)
                reasoning_cap = _reasoning_capability(name, caps)

                supports_tools = "tools" in caps or "insert" in caps
                supports_vision = "vision" in caps

                row = {
                    "model_id": model_id,
                    "endpoint": endpoint,
                    "provider": "ollama",
                    "context_capacity": ctx_len,
                    "supports_tools": supports_tools,
                    "supports_vision": supports_vision,
                    "coding_capability": coding_cap,
                    "reasoning_capability": reasoning_cap,
                    "routing_classes": json.dumps(routing_classes),
                    "vram_required_mb": vram_mb,
                    "availability": True,
                    "last_checked": now,
                }
                discovered.append(row)

                # Upsert into models table
                execute("""
                    INSERT INTO models (model_id, endpoint, provider, context_capacity,
                        supports_tools, supports_vision, coding_capability,
                        reasoning_capability, routing_classes, vram_required_mb,
                        availability, last_checked, updated_at)
                    VALUES (:model_id, :endpoint, :provider, :context_capacity,
                        :supports_tools, :supports_vision, :coding_capability,
                        :reasoning_capability, CAST(:routing_classes AS jsonb), :vram_required_mb,
                        :availability, :last_checked, NOW())
                    ON CONFLICT (model_id) DO UPDATE SET
                        endpoint = EXCLUDED.endpoint,
                        context_capacity = EXCLUDED.context_capacity,
                        supports_tools = EXCLUDED.supports_tools,
                        supports_vision = EXCLUDED.supports_vision,
                        coding_capability = EXCLUDED.coding_capability,
                        reasoning_capability = EXCLUDED.reasoning_capability,
                        routing_classes = EXCLUDED.routing_classes,
                        vram_required_mb = EXCLUDED.vram_required_mb,
                        availability = EXCLUDED.availability,
                        last_checked = EXCLUDED.last_checked,
                        updated_at = NOW()
                """, row)

        # Try LiteLLM (may be auth-protected or have no DB)
        litellm_data = _ollama_request(LITELLM_ENDPOINT, "/v1/models")
        if litellm_data and "data" in litellm_data:
            for m in litellm_data["data"]:
                model_id = f"litellm/{m['id']}"
                row = {
                    "model_id": model_id,
                    "endpoint": f"{LITELLM_ENDPOINT}/v1",
                    "provider": "litellm",
                    "context_capacity": 0,
                    "supports_tools": False,
                    "supports_vision": False,
                    "coding_capability": "unknown",
                    "reasoning_capability": "unknown",
                    "routing_classes": json.dumps(["general"]),
                    "vram_required_mb": 0,
                    "availability": True,
                    "last_checked": now,
                }
                discovered.append(row)
                execute("""
                    INSERT INTO models (model_id, endpoint, provider, context_capacity,
                        supports_tools, supports_vision, coding_capability,
                        reasoning_capability, routing_classes, vram_required_mb,
                        availability, last_checked, updated_at)
                    VALUES (:model_id, :endpoint, :provider, :context_capacity,
                        :supports_tools, :supports_vision, :coding_capability,
                        :reasoning_capability, CAST(:routing_classes AS jsonb), :vram_required_mb,
                        :availability, :last_checked, NOW())
                    ON CONFLICT (model_id) DO UPDATE SET
                        endpoint = EXCLUDED.endpoint,
                        availability = EXCLUDED.availability,
                        last_checked = EXCLUDED.last_checked,
                        updated_at = NOW()
                """, row)

        return discovered

    def get(self, model_id: str) -> dict | None:
        """Get a single model by its ID."""
        return fetchone("SELECT * FROM models WHERE model_id = :id", {"id": model_id})

    def list_models(self) -> list[dict]:
        """List all registered models."""
        return fetchall("SELECT * FROM models ORDER BY model_id")

    def route(self, task_class: str) -> str | None:
        """Pick the best available model for a given task class.

        Strategy: find models whose routing_classes include the task_class,
        prefer strong capability for the task domain, then larger parameter
        models, then lowest average latency.
        """
        cfg = TASK_ROUTING.get(task_class, TASK_ROUTING["general"])

        # Get all available models with matching routing class
        models = fetchall("""
            SELECT model_id, coding_capability, reasoning_capability,
                   supports_tools, supports_vision, context_capacity,
                   vram_required_mb, avg_latency_ms, task_success_count,
                   task_total_count, routing_classes
            FROM models
            WHERE availability = TRUE
        """)

        candidates = []
        for m in models:
            rclasses = m.get("routing_classes", [])
            if isinstance(rclasses, str):
                rclasses = json.loads(rclasses)
            if task_class not in rclasses:
                continue

            # Score: capability match → success rate → latency
            score = 0.0
            if task_class == "coding":
                cap_map = {"strong": 3, "moderate": 2, "basic": 1, "unknown": 0}
                score += cap_map.get(m.get("coding_capability", "unknown"), 0) * 10
            elif task_class == "reasoning":
                cap_map = {"strong": 3, "moderate": 2, "basic": 1, "unknown": 0}
                score += cap_map.get(m.get("reasoning_capability", "unknown"), 0) * 10
            elif task_class == "vision":
                score += 10 if m.get("supports_vision") else 0
            elif task_class in ("coding", "analysis"):
                score += 5 if m.get("supports_tools") else 0

            # Success rate bonus
            total = m.get("task_total_count", 0) or 0
            success = m.get("task_success_count", 0) or 0
            if total > 0:
                score += (success / total) * 5

            # Prefer larger context
            ctx = m.get("context_capacity", 0) or 0
            score += min(ctx / 32768, 3)  # up to 3 points for context

            # Penalty for high latency
            lat = m.get("avg_latency_ms") or 0
            if lat > 0:
                score -= min(lat / 1000, 2)

            candidates.append((score, m["model_id"]))

        if not candidates:
            return None

        candidates.sort(key=lambda x: x[0], reverse=True)
        return candidates[0][1]

    def log_routing(self, job_id: str, model_id: str, task_class: str,
                    duration_ms: int, verification_passed: bool,
                    rollback_occurred: bool = False) -> None:
        """Log a routing decision and its outcome."""
        execute("""
            INSERT INTO routing_log (job_id, model_id, task_class, provider,
                duration_ms, verification_passed, rollback_occurred)
            VALUES (:job_id, :model_id, :task_class, :provider,
                :duration_ms, :verification_passed, :rollback_occurred)
        """, {
            "job_id": job_id,
            "model_id": model_id,
            "task_class": task_class,
            "provider": model_id.split("/")[0] if "/" in model_id else "unknown",
            "duration_ms": duration_ms,
            "verification_passed": verification_passed,
            "rollback_occurred": rollback_occurred,
        })

        # Update model stats
        execute("""
            UPDATE models SET
                task_total_count = task_total_count + 1,
                task_success_count = task_success_count + CASE WHEN :passed THEN 1 ELSE 0 END,
                avg_latency_ms = CASE
                    WHEN avg_latency_ms IS NULL THEN :dur
                    ELSE avg_latency_ms * 0.8 + :dur * 0.2
                END,
                updated_at = NOW()
            WHERE model_id = :model_id
        """, {
            "model_id": model_id,
            "dur": float(duration_ms),
            "passed": verification_passed,
        })

    def get_routing_stats(self, model_id: str) -> dict:
        """Get aggregated routing statistics for a model."""
        row = fetchone("""
            SELECT
                model_id,
                COUNT(*) as total_routes,
                COUNT(*) FILTER (WHERE verification_passed = TRUE) as passed,
                COUNT(*) FILTER (WHERE rollback_occurred = TRUE) as rollbacks,
                AVG(duration_ms)::integer as avg_duration_ms,
                MIN(duration_ms) as min_duration_ms,
                MAX(duration_ms) as max_duration_ms,
                COUNT(DISTINCT task_class) as task_classes_used
            FROM routing_log
            WHERE model_id = :model_id
            GROUP BY model_id
        """, {"model_id": model_id})
        if row:
            total = row.get("total_routes", 0)
            passed = row.get("passed", 0)
            row["success_rate"] = round(passed / total, 3) if total > 0 else 0.0
            return row
        return {
            "model_id": model_id,
            "total_routes": 0,
            "passed": 0,
            "rollbacks": 0,
            "avg_duration_ms": 0,
            "success_rate": 0.0,
        }