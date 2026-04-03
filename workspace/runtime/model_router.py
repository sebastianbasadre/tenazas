#!/usr/bin/env python3
"""
Model Router — Hardware-aware model selection for Tenazas runtime.

Detects available Ollama backends (notebook GPU, NAS CPU) by pinging endpoints.
Maps models to VRAM requirements and selects the best model for a given task
based on available hardware. Supports future scaling to 13B/30B models.

Logs to logs/model_router.log.
"""

import json
import subprocess
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).parent
LOG_FILE = BASE_DIR / "logs" / "model_router.log"

# ── Backend definitions ──────────────────────────────────────────

BACKENDS = {
    "notebook_gpu": {
        "url": "http://192.168.2.6:11434",
        "tags_url": "http://192.168.2.6:11434/api/tags",
        "type": "gpu",
        "gpu": "RTX 4050",
        "vram_gb": 6,
        "description": "HomeLab GPU",
        "priority": 1,
    },
    "nas_cpu": {
        "url": "http://127.0.0.1:11434",
        "tags_url": "http://127.0.0.1:11434/api/tags",
        "type": "cpu",
        "gpu": None,
        "vram_gb": 0,
        "description": "NAS Ryzen CPU (fallback)",
        "priority": 2,
    },
}

# ── Model catalog ────────────────────────────────────────────────
# vram_gb is approximate for loaded model

MODEL_CATALOG = {
    "qwen2.5:3b": {
        "params": "3B", "vram_gb": 2.5, "use": "system",
        "capabilities": ["system", "json", "logs", "fast"],
    },
    "qwen2.5:7b": {
        "params": "7B", "vram_gb": 5.0, "use": "reasoning",
        "capabilities": ["reasoning", "analysis", "general"],
    },
    "qwen3:8b": {
        "params": "8B", "vram_gb": 5.5, "use": "alternative",
        "capabilities": ["reasoning", "general"],
    },
    "phi4-mini": {
        "params": "~4B", "vram_gb": 3.0, "use": "fallback",
        "capabilities": ["system", "fast", "lightweight"],
    },
    "nomic-embed-text": {
        "params": "-", "vram_gb": 0.5, "use": "embeddings",
        "capabilities": ["embeddings"],
    },
    # Future models (not yet deployed)
    "qwen2.5:14b": {
        "params": "14B", "vram_gb": 10.0, "use": "advanced_reasoning",
        "capabilities": ["reasoning", "analysis", "complex"],
        "future": True,
    },
    "qwen2.5:32b": {
        "params": "32B", "vram_gb": 22.0, "use": "expert",
        "capabilities": ["reasoning", "analysis", "complex", "expert"],
        "future": True,
    },
}

# ── Task-to-capability mapping ───────────────────────────────────

TASK_REQUIREMENTS = {
    "system": ["system", "fast"],
    "reason": ["reasoning"],
    "embed": ["embeddings"],
    "analysis": ["analysis"],
    "general": ["general"],
    "fast": ["fast"],
}


def _now():
    return datetime.now().astimezone()


def _log(entry: dict):
    """Append a JSON line to model_router.log."""
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "a") as f:
        f.write(json.dumps(entry, default=str) + "\n")


def _ping_backend(backend_id: str) -> bool:
    """Check if a backend is reachable."""
    backend = BACKENDS.get(backend_id)
    if not backend:
        return False

    try:
        result = subprocess.run(
            ["curl", "-sf", "--max-time", "5", backend["tags_url"]],
            capture_output=True, text=True, timeout=10,
        )
        return result.returncode == 0
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return False


def detect_backends() -> dict:
    """
    Ping all backends and return their availability.

    Returns:
        dict mapping backend_id -> {available: bool, ...backend_info}
    """
    status = {}
    for bid, info in BACKENDS.items():
        available = _ping_backend(bid)
        status[bid] = {
            "available": available,
            "url": info["url"],
            "type": info["type"],
            "priority": info["priority"],
            "description": info["description"],
        }

    _log({
        "ts": _now().isoformat(),
        "action": "detect_backends",
        "results": {bid: s["available"] for bid, s in status.items()},
    })

    return status


def select_model(task: str = "general", prefer_fast: bool = False) -> dict:
    """
    Select the best model for a given task based on available hardware.

    Args:
        task: Task type (system, reason, embed, analysis, general, fast)
        prefer_fast: If True, prefer smaller/faster models

    Returns:
        dict with: model, backend, url, reason
        Empty dict if no suitable backend is available.
    """
    backends = detect_backends()
    required_caps = TASK_REQUIREMENTS.get(task, ["general"])

    # Find available backends sorted by priority
    available = sorted(
        [(bid, info) for bid, info in backends.items() if info["available"]],
        key=lambda x: x[1]["priority"],
    )

    if not available:
        _log({
            "ts": _now().isoformat(),
            "action": "select_model",
            "task": task,
            "result": "no_backend_available",
        })
        return {}

    best_backend_id, best_backend = available[0]
    vram = BACKENDS[best_backend_id].get("vram_gb", 0)

    # Find candidate models (not future, fits VRAM, has required capability)
    candidates = []
    for model_name, info in MODEL_CATALOG.items():
        if info.get("future"):
            continue
        if info["vram_gb"] > vram and best_backend["type"] == "gpu":
            continue
        cap_match = any(c in info["capabilities"] for c in required_caps)
        if cap_match:
            candidates.append((model_name, info))

    if not candidates:
        # Fallback: any model that fits
        for model_name, info in MODEL_CATALOG.items():
            if info.get("future"):
                continue
            if info["vram_gb"] <= vram or best_backend["type"] == "cpu":
                candidates.append((model_name, info))
        if not candidates:
            return {}

    # Sort candidates: smaller first if prefer_fast, else larger first
    candidates.sort(key=lambda x: x[1]["vram_gb"], reverse=not prefer_fast)

    # For CPU backend, only allow small models
    if best_backend["type"] == "cpu":
        candidates = [(n, i) for n, i in candidates if i["vram_gb"] <= 3.5]
        if not candidates:
            candidates = [("phi4-mini", MODEL_CATALOG["phi4-mini"])]

    chosen_name, chosen_info = candidates[0]

    result = {
        "model": chosen_name,
        "backend": best_backend_id,
        "url": BACKENDS[best_backend_id]["url"],
        "reason": f"Best match for task='{task}' on {best_backend['description']}",
    }

    _log({
        "ts": _now().isoformat(),
        "action": "select_model",
        "task": task,
        "prefer_fast": prefer_fast,
        "model": chosen_name,
        "backend": best_backend_id,
    })

    return result


def route(task: str = "general") -> tuple:
    """
    Convenience function matching ollama-check.sh --route interface.

    Args:
        task: system, reason, embed, analysis, general

    Returns:
        (backend_id, model_name, endpoint_url) or (None, None, None) if offline
    """
    selection = select_model(task)
    if not selection:
        return (None, None, None)
    return (selection["backend"], selection["model"], selection["url"])


def get_model_info(model_name: str) -> dict:
    """Return catalog info for a specific model."""
    return MODEL_CATALOG.get(model_name, {})


def list_models(include_future: bool = False) -> dict:
    """List all known models, optionally including future ones."""
    if include_future:
        return dict(MODEL_CATALOG)
    return {k: v for k, v in MODEL_CATALOG.items() if not v.get("future")}
