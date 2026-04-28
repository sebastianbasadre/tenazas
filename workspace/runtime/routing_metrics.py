"""
Routing metrics — in-memory counters and Prometheus integration for orchestrator routing.

Extracted from orchestrator.py for independent importability.
"""

import json
import os
from collections import defaultdict
from pathlib import Path

try:
    from runtime_utils import now
except ImportError:
    from workspace.runtime.runtime_utils import now

# ── Config ───────────────────────────────────────────────────────

_LOG_PATH = Path(__file__).parent / "logs" / "orchestrator.log"

# ── Routing metrics (in-memory, reset on process restart) ────────

_routing_metrics = defaultdict(int)


def get_routing_metrics() -> dict:
    """Return current routing metrics snapshot. Resets on process restart."""
    return dict(_routing_metrics)


def reset_routing_metrics():
    """Reset all routing metrics (for testing)."""
    _routing_metrics.clear()


# ── Helpers ──────────────────────────────────────────────────────


def _log(entry: dict):
    _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    entry["ts"] = now().isoformat()
    with open(_LOG_PATH, "a") as f:
        f.write(json.dumps(entry, default=str) + "\n")


# ── Inference Reporting ──────────────────────────────────────────


def report_inference(model_id: str, latency: float, route_result: dict | None = None):
    """Report actual inference latency after LLM call completes.

    Call this after the LLM responds to close the guardrail feedback loop.
    The latency is used for future guardrail decisions and Prometheus metrics.

    Args:
        model_id: model ID from route result (e.g. "ollama/qwen2.5:7b")
        latency: actual inference time in seconds
        route_result: the full route_query() result (for extracting V2 info)
    """
    try:
        from runtime.model_router import report_inference_complete
        # Strip "ollama/" prefix if present
        model = model_id.replace("ollama/", "")
        vram_swap = False
        if route_result and "router_v2" in route_result:
            vram_swap = route_result["router_v2"].get("vram_swap", False)
        report_inference_complete(model, latency, vram_swap)
    except Exception:
        pass

    try:
        from runtime.metrics_exporter import record_model_latency
        model = model_id.replace("ollama/", "")
        role = "fast"
        if route_result and "router_v2" in route_result:
            role = route_result["router_v2"].get("role", "fast")
        record_model_latency(model, role, latency)
    except Exception:
        pass


def _record_metrics(**kwargs):
    """Record routing decision to Prometheus (best-effort, never raises)."""
    try:
        from runtime.metrics_exporter import record_routing_decision
        record_routing_decision(**kwargs)
    except Exception:
        pass


def _record_v1_fallback():
    """Record V1 fallback event to Prometheus (best-effort)."""
    try:
        from runtime.metrics_exporter import record_v1_fallback_from_v2
        record_v1_fallback_from_v2()
    except Exception:
        pass
