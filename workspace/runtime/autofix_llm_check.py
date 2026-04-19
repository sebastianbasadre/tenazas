#!/usr/bin/env python3
"""
autofix_llm_check.py — LLM backend availability for autofix.

Checks Ollama HOMELAB, Gemini quota, in priority order.
"""

import json
import os
from pathlib import Path
from urllib.request import urlopen

HOMELAB_OLLAMA = "http://192.168.2.6:11434"

_IN_CONTAINER = Path("/home/tenazas").exists()
STATE_DIR = Path("/home/tenazas/.openclaw/state") if _IN_CONTAINER else Path(__file__).parent.parent / "state"


def check_ollama(model: str = "qwen2.5-coder:32b") -> dict:
    """Check if Ollama HOMELAB has the requested model."""
    try:
        resp = urlopen(f"{HOMELAB_OLLAMA}/api/tags", timeout=5)
        if resp.status == 200:
            data = json.loads(resp.read())
            models = {m.get("name", "") for m in data.get("models", [])}
            if model in models:
                return {"available": True, "model": model, "backend": "ollama"}
            # Try fallbacks
            for fb in ["qwen2.5-coder:14b", "qwen2.5:7b"]:
                if fb in models:
                    return {"available": True, "model": fb, "backend": "ollama",
                            "fallback": True}
    except Exception:
        pass
    return {"available": False, "backend": "ollama", "reason": "unreachable or model missing"}


def check_gemini() -> dict:
    """Check Gemini quota from cached state."""
    quota_file = STATE_DIR / "gemini_quota.json"
    try:
        if quota_file.exists():
            quota = json.loads(quota_file.read_text())
            if quota.get("exhausted", False):
                return {"available": False, "backend": "gemini",
                        "reason": f"quota exhausted, resets {quota.get('reset_at', '?')}"}
            return {"available": True, "backend": "gemini", "model": "gemini/gemini-2.5-flash"}
    except Exception:
        pass
    # No quota file — check env
    if os.environ.get("GOOGLE_API_KEY"):
        return {"available": True, "backend": "gemini", "model": "gemini/gemini-2.5-flash"}
    return {"available": False, "backend": "gemini", "reason": "no API key"}


def check_availability() -> dict:
    """Check all LLM backends in priority order.

    Returns:
        {recommended, model, backend, reason, all_backends}
    """
    ollama = check_ollama()
    gemini = check_gemini()

    result = {
        "recommended": None,
        "model": None,
        "backend": None,
        "reason": "",
        "all_backends": {"ollama": ollama, "gemini": gemini},
    }

    if ollama["available"]:
        result.update(recommended="ollama", model=ollama["model"],
                      backend="ollama", reason="local, free, private")
    elif gemini["available"]:
        result.update(recommended="gemini", model=gemini["model"],
                      backend="gemini", reason="cloud fallback (free tier)")
    else:
        reasons = []
        if ollama.get("reason"):
            reasons.append(f"Ollama: {ollama['reason']}")
        if gemini.get("reason"):
            reasons.append(f"Gemini: {gemini['reason']}")
        result["reason"] = "; ".join(reasons) or "no LLM available"

    return result
