#!/usr/bin/env python3
"""
autofix_llm_check.py — LLM backend availability for autofix.

Checks Ollama HOMELAB, then OpenRouter/Kimi cloud fallback, then legacy Gemini.
Reads model config from autofix_config.json if available.
"""

import json
import os
from pathlib import Path
from urllib.request import urlopen

try:
    from runtime_config import OLLAMA_GPU_URL
except ImportError:
    from workspace.runtime.runtime_config import OLLAMA_GPU_URL

_IN_CONTAINER = Path("/home/tenazas").exists()
STATE_DIR = Path("/home/tenazas/.openclaw/state") if _IN_CONTAINER else Path(__file__).parent.parent / "state"
CONFIG_PATH = STATE_DIR / "autofix_config.json"

# Defaults (overridden by config)
_DEFAULT_OLLAMA_URL = OLLAMA_GPU_URL
_DEFAULT_MODEL = "qwen2.5-coder:14b"
_DEFAULT_FALLBACK = "openrouter/moonshotai/kimi-k2"


def _load_llm_config() -> dict:
    """Load llm_backends section from autofix_config.json."""
    try:
        if CONFIG_PATH.exists():
            cfg = json.loads(CONFIG_PATH.read_text())
            return cfg.get("llm_backends", {})
    except Exception:
        pass
    return {}


def check_ollama(model: str | None = None) -> dict:
    """Check if Ollama HOMELAB has the requested model."""
    llm_cfg = _load_llm_config()
    ollama_url = llm_cfg.get("ollama_base_url", _DEFAULT_OLLAMA_URL)
    if model is None:
        model = llm_cfg.get("aider_model", _DEFAULT_MODEL)

    try:
        resp = urlopen(f"{ollama_url}/api/tags", timeout=5)
        if resp.status == 200:
            data = json.loads(resp.read())
            models = {m.get("name", "") for m in data.get("models", [])}
            if model in models:
                return {"available": True, "model": model, "backend": "ollama"}
            # Try fallbacks
            for fb in ["qwen2.5-coder:14b", "qwen2.5:7b"]:
                if fb != model and fb in models:
                    return {"available": True, "model": fb, "backend": "ollama",
                            "fallback": True}
    except Exception:
        pass
    return {"available": False, "backend": "ollama", "reason": "unreachable or model missing"}


def check_openrouter() -> dict:
    """Check whether OpenRouter/Kimi cloud fallback is configured."""
    if os.environ.get("TENAZAS_ALLOW_CLOUD", "true").lower() != "true":
        return {"available": False, "backend": "openrouter", "reason": "cloud disabled"}
    llm_cfg = _load_llm_config()
    fallback_model = llm_cfg.get("aider_fallback_model", _DEFAULT_FALLBACK)
    if fallback_model.startswith("openrouter/") and os.environ.get("OPENROUTER_API_KEY"):
        return {"available": True, "backend": "openrouter", "model": fallback_model}
    if os.environ.get("OPENROUTER_API_KEY"):
        return {"available": True, "backend": "openrouter",
                "model": "openrouter/moonshotai/kimi-k2"}
    return {"available": False, "backend": "openrouter", "reason": "no API key"}


def check_gemini() -> dict:
    """Check legacy Gemini fallback from cached state or env."""
    if os.environ.get("TENAZAS_ALLOW_CLOUD", "true").lower() != "true":
        return {"available": False, "backend": "gemini", "reason": "cloud disabled"}
    llm_cfg = _load_llm_config()
    fallback_model = llm_cfg.get("gemini_fallback_model", "gemini/gemini-2.5-flash")

    quota_file = STATE_DIR / "gemini_quota.json"
    try:
        if quota_file.exists():
            quota = json.loads(quota_file.read_text())
            if quota.get("exhausted", False):
                return {"available": False, "backend": "gemini",
                        "reason": f"quota exhausted, resets {quota.get('reset_at', '?')}"}
            return {"available": True, "backend": "gemini", "model": fallback_model}
    except Exception:
        pass
    # No quota file — check env
    if os.environ.get("GOOGLE_API_KEY"):
        return {"available": True, "backend": "gemini", "model": fallback_model}
    return {"available": False, "backend": "gemini", "reason": "no API key"}


def check_availability() -> dict:
    """Check all LLM backends in priority order.

    Returns:
        {recommended, model, backend, reason, all_backends}
    """
    ollama = check_ollama()
    openrouter = check_openrouter()
    gemini = check_gemini()

    result = {
        "recommended": None,
        "model": None,
        "backend": None,
        "reason": "",
        "all_backends": {"ollama": ollama, "openrouter": openrouter, "gemini": gemini},
    }

    if ollama["available"]:
        result.update(recommended="ollama", model=ollama["model"],
                      backend="ollama", reason="local, free, private")
    elif openrouter["available"]:
        result.update(recommended="openrouter", model=openrouter["model"],
                      backend="openrouter", reason="cloud fallback via Kimi/OpenRouter")
    elif gemini["available"]:
        result.update(recommended="gemini", model=gemini["model"],
                      backend="gemini", reason="legacy cloud fallback")
    else:
        reasons = []
        if ollama.get("reason"):
            reasons.append(f"Ollama: {ollama['reason']}")
        if openrouter.get("reason"):
            reasons.append(f"OpenRouter: {openrouter['reason']}")
        if gemini.get("reason"):
            reasons.append(f"Gemini: {gemini['reason']}")
        result["reason"] = "; ".join(reasons) or "no LLM available"

    return result
