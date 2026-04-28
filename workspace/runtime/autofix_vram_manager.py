#!/usr/bin/env python3
"""
autofix_vram_manager.py — VRAM management for autofix Tier 2.

Before Aider runs, ensures enough free VRAM on HOMELAB GPU by
temporarily unloading non-essential models. After the fix,
restores them.

RTX 3090 = 24GB. qwen2.5-coder:14b needs ~11GB.
qwen3:8b (Tenazas chat) uses ~10GB. Both fit but at the limit.

All stdlib: urllib.request, json, time.
"""

import json
import logging
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

try:
    from runtime_config import OLLAMA_GPU_URL
except ImportError:
    from workspace.runtime.runtime_config import OLLAMA_GPU_URL

log = logging.getLogger("autofix_vram")

_IN_CONTAINER = Path("/home/tenazas").exists()
STATE_DIR = Path("/home/tenazas/.openclaw/state") if _IN_CONTAINER else Path(__file__).parent.parent / "state"
CONFIG_PATH = STATE_DIR / "autofix_config.json"
LOG_PATH = STATE_DIR / "autofix_log.jsonl"

_DEFAULT_OLLAMA_URL = OLLAMA_GPU_URL
_REQUEST_TIMEOUT = 10


def _get_config() -> dict:
    """Load vram_management config section."""
    try:
        if CONFIG_PATH.exists():
            cfg = json.loads(CONFIG_PATH.read_text())
            return cfg.get("vram_management", {})
    except Exception:
        pass
    return {}


def _get_ollama_url() -> str:
    """Get Ollama URL from config."""
    try:
        if CONFIG_PATH.exists():
            cfg = json.loads(CONFIG_PATH.read_text())
            return cfg.get("llm_backends", {}).get("ollama_base_url", _DEFAULT_OLLAMA_URL)
    except Exception:
        pass
    return _DEFAULT_OLLAMA_URL


def _append_log(entry: dict):
    try:
        entry["ts"] = time.time()
        entry["dt"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        entry["component"] = "vram_manager"
        with open(LOG_PATH, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        pass


def get_loaded_models(ollama_url: str | None = None) -> list[dict]:
    """Query Ollama /api/ps for currently loaded models.

    Returns list of {name, size_vram_gb, expires_at}.
    """
    url = ollama_url or _get_ollama_url()
    try:
        resp = urlopen(f"{url}/api/ps", timeout=_REQUEST_TIMEOUT)
        data = json.loads(resp.read())
        models = []
        for m in data.get("models", []):
            name = m.get("name", "")
            size_bytes = m.get("size", 0)
            size_gb = round(size_bytes / 1e9, 1)
            expires = m.get("expires_at", "")
            models.append({
                "name": name,
                "size_vram_gb": size_gb,
                "expires_at": expires,
            })
        return models
    except Exception as e:
        log.warning("get_loaded_models failed: %s", e)
        return []


def _is_expired(expires_at: str) -> bool:
    """Check if a model's keep_alive has expired (evictable by Ollama)."""
    if not expires_at:
        return False
    try:
        from datetime import datetime, timezone
        # Parse ISO format with timezone
        exp = expires_at.replace("Z", "+00:00")
        # Remove sub-second precision beyond 6 digits for Python compat
        if "." in exp:
            base, rest = exp.split(".", 1)
            frac = ""
            tz = ""
            for i, c in enumerate(rest):
                if c in "+-Z":
                    tz = rest[i:]
                    frac = rest[:i]
                    break
            else:
                frac = rest
            exp = f"{base}.{frac[:6]}{tz}"
        exp_dt = datetime.fromisoformat(exp)
        now = datetime.now(timezone.utc)
        return exp_dt <= now
    except Exception:
        return False


def get_vram_free_gb(ollama_url: str | None = None) -> float:
    """Estimate free VRAM based on loaded models.

    RTX 3090 = 24GB total, ~2GB reserved by Windows/driver.
    Models with expired keep_alive are counted as free (Ollama will
    evict them when another model needs the VRAM).
    """
    total_gb = 24.0
    reserved_gb = 2.0
    loaded = get_loaded_models(ollama_url)
    used = sum(m["size_vram_gb"] for m in loaded
               if not _is_expired(m.get("expires_at", "")))
    return max(0, total_gb - reserved_gb - used)


def unload_model(name: str, ollama_url: str | None = None) -> bool:
    """Mark a model for immediate eviction via keep_alive=0.

    Ollama won't physically remove the model from VRAM until another
    model needs the space, but it sets expires_at to now so it will
    be evicted immediately when Aider loads qwen2.5-coder:14b.
    """
    url = ollama_url or _get_ollama_url()
    try:
        payload = json.dumps({
            "model": name,
            "keep_alive": 0,
        }).encode()
        resp = urlopen(Request(
            f"{url}/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
        ), timeout=_REQUEST_TIMEOUT)
        _append_log({"action": "unload", "model": name, "success": True})
        log.info("unloaded %s from VRAM", name)
        return True
    except Exception as e:
        _append_log({"action": "unload", "model": name, "success": False, "error": str(e)})
        log.warning("unload %s failed: %s", name, e)
        return False


def ensure_vram_available(required_gb: float = 12.0,
                          protected_models: list | None = None,
                          warning_models: list | None = None,
                          aider_model: str = "",
                          ollama_url: str | None = None) -> list[str]:
    """Ensure enough free VRAM for Aider.

    Args:
        required_gb: VRAM needed (default 12 for qwen2.5-coder:14b)
        protected_models: models to never unload
        warning_models: models that trigger a warning when unloaded
        aider_model: the model Aider will use (never unload this one)
        ollama_url: Ollama endpoint

    Returns:
        List of model names that were unloaded (for restore later).
    """
    url = ollama_url or _get_ollama_url()
    protected = set(protected_models or [])
    warnings = set(warning_models or [])
    if aider_model:
        protected.add(aider_model)

    free = get_vram_free_gb(url)
    _append_log({"action": "check_vram", "free_gb": round(free, 1),
                 "required_gb": required_gb})

    if free >= required_gb:
        log.info("VRAM OK: %.1fGB free >= %.1fGB required", free, required_gb)
        return []

    # Need to free up VRAM
    loaded = get_loaded_models(url)
    # Sort by size DESC — unload biggest first
    loaded.sort(key=lambda m: -m["size_vram_gb"])

    unloaded = []
    for m in loaded:
        name = m["name"]
        if name in protected:
            log.info("skip protected model: %s", name)
            continue

        if name in warnings:
            log.warning("unloading %s — Tenazas chat may be affected for 30-60s", name)

        if unload_model(name, url):
            unloaded.append(name)
            time.sleep(3)  # wait for VRAM to free
            free = get_vram_free_gb(url)
            if free >= required_gb:
                log.info("VRAM freed: %.1fGB free after unloading %d models",
                         free, len(unloaded))
                break

    _append_log({"action": "ensure_vram", "unloaded": unloaded,
                 "free_gb_after": round(get_vram_free_gb(url), 1)})
    return unloaded


def restore_models(model_names: list[str],
                   ollama_url: str | None = None) -> list[str]:
    """Reload models that were temporarily unloaded.

    Returns list of models successfully restored.
    """
    url = ollama_url or _get_ollama_url()
    restored = []
    for name in model_names:
        try:
            payload = json.dumps({
                "model": name,
                "prompt": "ok",
                "stream": False,
                "keep_alive": "5m",
                "options": {"num_predict": 1},
            }).encode()
            resp = urlopen(Request(
                f"{url}/api/generate",
                data=payload,
                headers={"Content-Type": "application/json"},
            ), timeout=30)  # loading can take ~10s
            restored.append(name)
            _append_log({"action": "restore", "model": name, "success": True})
            log.info("restored %s to VRAM", name)
        except Exception as e:
            _append_log({"action": "restore", "model": name, "success": False,
                         "error": str(e)})
            log.warning("restore %s failed: %s", name, e)
    return restored
