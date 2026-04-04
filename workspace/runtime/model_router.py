#!/usr/bin/env python3
"""
Model Router — Hardware-aware model selection for Tenazas runtime.

Detects available Ollama backends (HomeLab GPU, NAS CPU) by pinging endpoints.
Maps models to VRAM requirements and selects the best model for a given task.

V1 API: select_model(task), route(task) — capability-based, VRAM-filtered
V2 API: classify_task(prompt), route_by_role(prompt) — role-based, Ollama-offloaded

Logs to logs/model_router.log.
"""

import json
import re
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
        "gpu": "RTX 3090",
        "vram_gb": 24,
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
# vram_gb is approximate for loaded model.
# Ollama handles partial CPU offloading for models exceeding GPU VRAM.

MODEL_CATALOG = {
    # ── HomeLab models (192.168.2.6) ──
    "qwen2.5:7b": {
        "params": "7B", "vram_gb": 4.7, "use": "general",
        "capabilities": ["general", "reasoning", "analysis", "fast", "system", "json", "logs"],
        "role": "fast",
    },
    "deepseek-r1:14b": {
        "params": "14B", "vram_gb": 9.0, "use": "reasoning",
        "capabilities": ["reasoning", "chain_of_thought", "analysis"],
        "role": "reason",
    },
    "qwen2.5:32b": {
        "params": "32B", "vram_gb": 20.0, "use": "heavy",
        "capabilities": ["reasoning", "analysis", "complex", "expert", "critical"],
        "role": "heavy",
    },
    "qwen2.5-coder:14b": {
        "params": "14B", "vram_gb": 9.0, "use": "code",
        "capabilities": ["code", "refactoring", "code_review"],
        "role": "code",
    },
    "qwen2.5vl:7b": {
        "params": "7B", "vram_gb": 6.0, "use": "vision",
        "capabilities": ["vision", "multimodal", "image_analysis"],
        "role": "vision",
    },
    "qwen3:8b": {
        "params": "8B", "vram_gb": 5.2, "use": "alternative",
        "capabilities": ["reasoning", "general"],
        "role": "fast",
    },
    "nomic-embed-text": {
        "params": "-", "vram_gb": 0.3, "use": "embeddings",
        "capabilities": ["embeddings"],
        "role": "embed",
    },
    # ── Legacy models (NAS CPU fallback only) ──
    "qwen2.5:3b": {
        "params": "3B", "vram_gb": 2.5, "use": "system",
        "capabilities": ["system", "json", "logs", "fast"],
        "role": "fast", "legacy": True,
    },
    "phi4-mini": {
        "params": "~4B", "vram_gb": 3.0, "use": "fallback",
        "capabilities": ["system", "fast", "lightweight"],
        "role": "fast", "legacy": True,
    },
}

# ── V1: Task-to-capability mapping (backward compat) ────────────

TASK_REQUIREMENTS = {
    "system": ["system", "fast"],
    "reason": ["reasoning"],
    "embed": ["embeddings"],
    "analysis": ["analysis"],
    "general": ["general"],
    "fast": ["fast"],
}

# ── V2: Role-based routing ──────────────────────────────────────
# Each role has an ordered fallback chain: primary → alternatives

ROLE_MAP = {
    "fast":   ["qwen2.5:7b", "qwen3:8b"],
    "reason": ["deepseek-r1:14b", "qwen2.5:7b"],
    "heavy":  ["qwen2.5:32b", "deepseek-r1:14b", "qwen2.5:7b"],
    "code":   ["qwen2.5-coder:14b", "qwen2.5:7b"],
    "vision": ["qwen2.5vl:7b", "qwen2.5:7b"],
    "embed":  ["nomic-embed-text"],
}

VALID_ROLES = set(ROLE_MAP.keys())
DEFAULT_ROLE = "fast"

# Models that are expensive to load/run (large parameter count, high VRAM usage)
# On RTX 3090 (24GB) all fit in VRAM, but 32b still takes significant load time
HEAVY_MODELS = {"qwen2.5:32b"}

# ── V2: Guardrails ─────────────────────────────────────────────
# Prevents overuse of heavy models when the system is under load.

GUARDRAIL_WINDOW = 600       # 10 min window for frequency tracking
GUARDRAIL_MAX_HEAVY = 3      # Max heavy model calls per window
GUARDRAIL_COOLDOWN = 120     # 2 min cooldown after a heavy model call
GUARDRAIL_LATENCY_THRESHOLD = 30.0  # If last heavy latency > 30s, degrade

# Persistent state file for guardrails (survives cron restarts)
GUARDRAIL_STATE_FILE = BASE_DIR / "guardrail_state.json"

# In-memory state, initialized from file on first access
_guardrail_state = {
    "heavy_timestamps": [],   # timestamps of recent heavy model selections
    "last_heavy_model": None,
    "last_heavy_ts": 0.0,
    "last_heavy_latency": 0.0,
    "vram_swaps_window": [],  # timestamps of recent vram swaps
    "degradations": {         # counters for dashboard
        "cooldown": 0,
        "frequency": 0,
        "latency": 0,
        "vram_storm": 0,
    },
}
_guardrail_loaded = False


def _load_guardrail_state():
    """Load guardrail state from JSON file on first access."""
    global _guardrail_loaded
    if _guardrail_loaded:
        return
    _guardrail_loaded = True

    try:
        with open(GUARDRAIL_STATE_FILE) as f:
            saved = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return

    import time
    now = time.monotonic()
    wall_now = _now().timestamp()

    st = _guardrail_state

    # Restore timestamps: convert wall-clock offsets to monotonic
    for key in ("heavy_timestamps", "vram_swaps_window"):
        wall_times = saved.get(key, [])
        st[key] = [now - (wall_now - wt) for wt in wall_times
                   if wall_now - wt < GUARDRAIL_WINDOW]

    # Restore scalars
    st["last_heavy_model"] = saved.get("last_heavy_model")
    st["last_heavy_latency"] = saved.get("last_heavy_latency", 0.0)

    # Restore last_heavy_ts as monotonic offset
    last_wall = saved.get("last_heavy_wall_ts", 0)
    if last_wall > 0 and wall_now - last_wall < GUARDRAIL_COOLDOWN:
        st["last_heavy_ts"] = now - (wall_now - last_wall)
    else:
        st["last_heavy_ts"] = 0.0

    # Restore degradation counters
    st["degradations"] = saved.get("degradations", {
        "cooldown": 0, "frequency": 0, "latency": 0, "vram_storm": 0,
    })


def _save_guardrail_state():
    """Persist guardrail state to JSON file."""
    import time
    now = time.monotonic()
    wall_now = _now().timestamp()
    st = _guardrail_state

    # Convert monotonic timestamps to wall-clock for persistence
    saved = {
        "heavy_timestamps": [wall_now - (now - t) for t in st["heavy_timestamps"]],
        "vram_swaps_window": [wall_now - (now - t) for t in st["vram_swaps_window"]],
        "last_heavy_model": st["last_heavy_model"],
        "last_heavy_latency": st["last_heavy_latency"],
        "last_heavy_wall_ts": wall_now - (now - st["last_heavy_ts"]) if st["last_heavy_ts"] > 0 else 0,
        "degradations": st["degradations"],
        "saved_at": _now().isoformat(),
    }

    try:
        GUARDRAIL_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(GUARDRAIL_STATE_FILE, "w") as f:
            json.dump(saved, f, indent=2)
    except OSError:
        pass


def _guardrail_cleanup(now: float):
    """Remove expired entries from guardrail tracking."""
    cutoff = now - GUARDRAIL_WINDOW
    st = _guardrail_state
    st["heavy_timestamps"] = [t for t in st["heavy_timestamps"] if t > cutoff]
    st["vram_swaps_window"] = [t for t in st["vram_swaps_window"] if t > cutoff]


def apply_guardrails(role: str, flags: dict | None = None) -> tuple:
    """Check if a heavy role should be degraded based on recent usage.

    Args:
        role: the classified role
        flags: routing flags (force_heavy=True bypasses guardrails)

    Returns:
        (effective_role, degraded, reason)
        - effective_role: the role to actually use (may be degraded)
        - degraded: True if the role was changed
        - reason: human-readable reason for degradation (or "")
    """
    flags = flags or {}

    # Only guard heavy/code roles (they use HEAVY_MODELS)
    if role not in ("heavy", "code"):
        return role, False, ""

    # Explicit bypass
    if flags.get("force_heavy"):
        return role, False, ""

    _load_guardrail_state()

    import time
    now = time.monotonic()
    _guardrail_cleanup(now)
    st = _guardrail_state

    # 1. Cooldown: if a heavy model was used recently, degrade
    if st["last_heavy_ts"] > 0:
        elapsed = now - st["last_heavy_ts"]
        if elapsed < GUARDRAIL_COOLDOWN:
            degrade_to = "reason" if role == "heavy" else "fast"
            st["degradations"]["cooldown"] = st["degradations"].get("cooldown", 0) + 1
            _save_guardrail_state()
            return degrade_to, True, (
                f"cooldown: {GUARDRAIL_COOLDOWN - elapsed:.0f}s remaining "
                f"after {st['last_heavy_model']}"
            )

    # 2. Frequency: too many heavy calls in window
    if len(st["heavy_timestamps"]) >= GUARDRAIL_MAX_HEAVY:
        degrade_to = "reason" if role == "heavy" else "fast"
        st["degradations"]["frequency"] = st["degradations"].get("frequency", 0) + 1
        _save_guardrail_state()
        return degrade_to, True, (
            f"frequency: {len(st['heavy_timestamps'])}/{GUARDRAIL_MAX_HEAVY} "
            f"heavy calls in {GUARDRAIL_WINDOW}s window"
        )

    # 3. Latency: last heavy call was too slow
    if st["last_heavy_latency"] > GUARDRAIL_LATENCY_THRESHOLD:
        degrade_to = "reason" if role == "heavy" else "fast"
        st["degradations"]["latency"] = st["degradations"].get("latency", 0) + 1
        _save_guardrail_state()
        return degrade_to, True, (
            f"latency: last heavy took {st['last_heavy_latency']:.1f}s "
            f"(threshold: {GUARDRAIL_LATENCY_THRESHOLD}s)"
        )

    # 4. VRAM swap storm: too many swaps in window
    if len(st["vram_swaps_window"]) >= GUARDRAIL_MAX_HEAVY:
        degrade_to = "reason" if role == "heavy" else "fast"
        st["degradations"]["vram_storm"] = st["degradations"].get("vram_storm", 0) + 1
        _save_guardrail_state()
        return degrade_to, True, (
            f"vram_swaps: {len(st['vram_swaps_window'])} swaps in "
            f"{GUARDRAIL_WINDOW}s window"
        )

    return role, False, ""


def record_heavy_usage(model: str, latency: float = 0.0, vram_swap: bool = False):
    """Record that a heavy model was selected.

    Args:
        model: model name that was used
        latency: inference latency in seconds (0 if not yet known)
        vram_swap: whether this required VRAM swap
    """
    _load_guardrail_state()
    import time
    now = time.monotonic()
    st = _guardrail_state

    if model in HEAVY_MODELS:
        st["heavy_timestamps"].append(now)
        st["last_heavy_model"] = model
        st["last_heavy_ts"] = now
        st["last_heavy_latency"] = latency

    if vram_swap:
        st["vram_swaps_window"].append(now)

    _save_guardrail_state()


def report_inference_complete(model: str, latency: float, vram_swap: bool = False):
    """Report actual inference latency after LLM call completes.

    This closes the guardrail feedback loop: route_by_role() records the
    selection, and this function updates the latency once we know how
    long it actually took. The latency feeds into the next guardrail check.

    Args:
        model: model that was used
        latency: actual inference time in seconds
        vram_swap: whether VRAM swap occurred
    """
    import time
    st = _guardrail_state

    if model in HEAVY_MODELS:
        st["last_heavy_latency"] = latency
        _log({
            "ts": _now().isoformat(),
            "action": "inference_complete",
            "model": model,
            "latency": round(latency, 2),
            "vram_swap": vram_swap,
            "heavy": True,
        })

    if vram_swap:
        st["vram_swaps_window"].append(time.monotonic())

    # Persist updated state
    _save_guardrail_state()


def get_guardrail_stats() -> dict:
    """Return guardrail degradation counters for Prometheus/dashboard."""
    _load_guardrail_state()
    return dict(_guardrail_state.get("degradations", {}))


def reset_guardrails():
    """Reset guardrail state (for testing). Marks as loaded to skip file read."""
    global _guardrail_loaded
    _guardrail_loaded = True  # Mark as loaded to prevent reading stale file
    st = _guardrail_state
    st["heavy_timestamps"] = []
    st["last_heavy_model"] = None
    st["last_heavy_ts"] = 0.0
    st["last_heavy_latency"] = 0.0
    st["vram_swaps_window"] = []
    st["degradations"] = {"cooldown": 0, "frequency": 0, "latency": 0, "vram_storm": 0}


# ── V2: Classification heuristics ───────────────────────────────

_CODE_KEYWORDS = re.compile(
    r"\b(refactor|debug|fix\s+bug|code\s+review|implementa|genera?\s+codigo"
    r"|write\s+a?\s*function|write\s+a?\s*class|corregir?\s+codigo)\b", re.I,
)
_CODE_MARKERS = re.compile(r"```|def\s+\w+\(|function\s+\w+\(|class\s+\w+[:\(]|import\s+\w+")

_REASON_KEYWORDS = re.compile(
    r"\b(anali[zs]a|evalua|compar[aá]|explain\s+step|think\s+through"
    r"|por\s*qu[eé]|diagn[oó]stic|root\s+cause|investiga|reflej[aá]"
    r"|debug.*log|why\s+.{10,})\b", re.I,
)

_HEAVY_KEYWORDS = re.compile(
    r"\b(critical|production|deploy|migra(tion|ci[oó]n)|architecture"
    r"|s[ií]ntesis|resumen\s+(completo|general|ejecutivo)|comprehensive)\b", re.I,
)

_CODE_SPECIAL_CHARS = set("{}();=[]<>")
_CODE_CHAR_THRESHOLD = 0.08  # 8% special chars → likely code


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

    # Find candidate models (not legacy, fits VRAM, has required capability)
    candidates = []
    for model_name, info in MODEL_CATALOG.items():
        if info.get("legacy") or info.get("future"):
            continue
        if info["vram_gb"] > vram and best_backend["type"] == "gpu":
            continue
        cap_match = any(c in info["capabilities"] for c in required_caps)
        if cap_match:
            candidates.append((model_name, info))

    if not candidates:
        # Fallback: any model that fits (including legacy for CPU)
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


def list_models(include_future: bool = False, include_legacy: bool = False) -> dict:
    """List all known models, optionally including future/legacy ones."""
    if include_future and include_legacy:
        return dict(MODEL_CATALOG)
    return {k: v for k, v in MODEL_CATALOG.items()
            if (include_future or not v.get("future"))
            and (include_legacy or not v.get("legacy"))}


# ── V2: Role-based task classification ──────────────────────────


def classify_task(prompt: str, flags: dict | None = None) -> str:
    """Classify a prompt into a routing role using heuristics.

    Priority order: explicit flag > vision > embed > code > reason > heavy > fast

    Args:
        prompt: user/system prompt text
        flags: optional hints — role, has_image, embed, critical

    Returns:
        One of: fast, reason, heavy, code, vision, embed
    """
    flags = flags or {}

    # 1. Explicit role override
    explicit = flags.get("role", "")
    if explicit in VALID_ROLES:
        return explicit

    # 2. Vision (image input)
    if flags.get("has_image") or flags.get("has_attachment"):
        return "vision"

    # 3. Embed
    if flags.get("embed"):
        return "embed"

    # 4. Code detection
    if _CODE_KEYWORDS.search(prompt):
        return "code"
    if _CODE_MARKERS.search(prompt):
        return "code"
    if len(prompt) > 50:
        special = sum(1 for c in prompt if c in _CODE_SPECIAL_CHARS)
        if special / len(prompt) >= _CODE_CHAR_THRESHOLD:
            return "code"

    # 5. Reasoning detection
    if _REASON_KEYWORDS.search(prompt):
        return "reason"
    if len(prompt) > 500 and "?" in prompt:
        return "reason"

    # 6. Heavy / critical
    if flags.get("critical"):
        return "heavy"
    if _HEAVY_KEYWORDS.search(prompt):
        return "heavy"
    if len(prompt) > 2000:
        return "heavy"

    # 7. Default
    return DEFAULT_ROLE


# ── V2: Role-based routing ──────────────────────────────────────


def route_by_role(
    prompt: str,
    flags: dict | None = None,
    backend_status: dict | None = None,
) -> dict:
    """VRAM-aware role-based routing. Main entry point for Router V2.

    Classifies the prompt into a role, then walks the fallback chain
    for that role until a model is available on the best backend.
    Ollama handles partial CPU offloading transparently for models
    exceeding GPU VRAM — the router does NOT block on VRAM limits.

    Args:
        prompt: user/system prompt
        flags: optional hints (role, has_image, embed, critical)
        backend_status: pre-fetched detect_backends() result, or None to auto-detect

    Returns:
        {model, backend, url, role, classified_as, fallback_used, reason,
         vram_swap, signals}
        Empty dict if no backends available.

    Idle learning integration:
        Every call logs a structured JSON entry to model_router.log with
        action="route_v2". The idle_learning_engine can read these to
        analyze routing patterns, fallback rates, and VRAM swap frequency.
    """
    raw_role = classify_task(prompt, flags)
    signals = _collect_signals(prompt, flags, raw_role)

    # Apply guardrails: may degrade heavy/code roles
    role, degraded, degrade_reason = apply_guardrails(raw_role, flags)
    if degraded:
        signals.append(f"guardrail:{degrade_reason}")

    if backend_status is None:
        backend_status = detect_backends()

    # Find best available backend (sorted by priority)
    available = sorted(
        [(bid, info) for bid, info in backend_status.items() if info["available"]],
        key=lambda x: x[1]["priority"],
    )

    if not available:
        _log({
            "ts": _now().isoformat(),
            "action": "route_v2",
            "task_type": role,
            "result": "no_backend_available",
            "signals": signals,
        })
        return {}

    best_bid, best_info = available[0]
    gpu_vram = BACKENDS.get(best_bid, {}).get("vram_gb", 0)
    chain = ROLE_MAP.get(role, ROLE_MAP[DEFAULT_ROLE])

    # For CPU backend, filter to small models only
    if best_info["type"] == "cpu":
        cpu_candidates = [m for m in chain
                          if MODEL_CATALOG.get(m, {}).get("vram_gb", 99) <= 3.5]
        if not cpu_candidates:
            cpu_candidates = ["phi4-mini"]
        chain = cpu_candidates

    chosen = chain[0]
    fallback_used = False

    # Walk the chain — on GPU, all models work (Ollama offloads)
    # On CPU, chain is already filtered above
    if chosen not in MODEL_CATALOG:
        chosen = chain[0] if chain else "qwen2.5:7b"

    primary = ROLE_MAP.get(raw_role, [None])[0]
    if chosen != primary:
        fallback_used = True

    model_info = MODEL_CATALOG.get(chosen, {})
    vram_swap = model_info.get("vram_gb", 0) > gpu_vram

    # Record heavy usage for future guardrail checks
    if chosen in HEAVY_MODELS:
        record_heavy_usage(chosen, vram_swap=vram_swap)

    result = {
        "model": chosen,
        "backend": best_bid,
        "url": BACKENDS.get(best_bid, {}).get("url", ""),
        "role": role,
        "classified_as": raw_role,
        "fallback_used": fallback_used,
        "degraded": degraded,
        "degrade_reason": degrade_reason,
        "reason": f"role={role}, chain={chain}, selected={chosen}",
        "vram_swap": vram_swap,
        "signals": signals,
    }

    _log({
        "ts": _now().isoformat(),
        "action": "route_v2",
        "prompt_preview": prompt[:80] if prompt else "",
        "task_type": role,
        "classified_as": raw_role,
        "model_chosen": chosen,
        "role": role,
        "degraded": degraded,
        "degrade_reason": degrade_reason,
        "fallback_used": fallback_used,
        "backend": best_bid,
        "vram_swap": vram_swap,
        "signals": signals,
    })

    return result


def _collect_signals(prompt: str, flags: dict | None, role: str) -> list:
    """Collect classification signals for logging/debugging."""
    signals = []
    flags = flags or {}

    if flags.get("role"):
        signals.append(f"flag:role={flags['role']}")
    if flags.get("has_image"):
        signals.append("flag:has_image")
    if flags.get("critical"):
        signals.append("flag:critical")
    if flags.get("embed"):
        signals.append("flag:embed")

    if _CODE_KEYWORDS.search(prompt):
        match = _CODE_KEYWORDS.search(prompt)
        signals.append(f"keyword:{match.group().lower()}")
    if _CODE_MARKERS.search(prompt):
        signals.append("code_block_detected")
    if _REASON_KEYWORDS.search(prompt):
        match = _REASON_KEYWORDS.search(prompt)
        signals.append(f"keyword:{match.group().lower()}")
    if _HEAVY_KEYWORDS.search(prompt):
        match = _HEAVY_KEYWORDS.search(prompt)
        signals.append(f"keyword:{match.group().lower()}")

    if len(prompt) > 2000:
        signals.append("long_prompt")
    if len(prompt) > 500 and "?" in prompt:
        signals.append("question_long_prompt")

    return signals
