"""
finetuned_router.py
Intent classifier with 3-tier fallback:
  1. Gemma 4 26B via OpenRouter (free, ~1.2s, 100% accuracy)
  2. Finetuned 7B via Ollama HomeLab (local, ~0.5s)
  3. Keyword regex fallback (instant, ~70% accuracy)
"""

import json
import logging
import re
import time
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

import requests

logger = logging.getLogger(__name__)

# ── Config ───────────────────────────────────────────────────────

CONFIG_PATH = Path("/home/tenazas/.openclaw/openclaw.json")

# Tier 1: Gemma via OpenRouter (free)
OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
OPENROUTER_MODEL = "google/gemma-4-26b-a4b-it:free"
OPENROUTER_TIMEOUT = 5
OPENROUTER_SYSTEM = (
    'You are a tool classifier. Respond ONLY with JSON: {"tool": "<name>"}. '
    "Tools: weather, chat, smart-home-on, smart-home-off, smart-home-cinema, "
    "arr-monitor, email, calendar, infra-monitor, tts, reminders, "
    "self-evolve, smoking-tracker."
)

# Tier 2: Finetuned model via Ollama
FINETUNED_MODEL = "tenazas-qwen25-7b-v6"
OLLAMA_URL = "http://192.168.2.6:11434/api/generate"
OLLAMA_TIMEOUT = 15

# Tier 3: Keyword regex
KEYWORD_RULES = [
    (["cinema", "pelicula", "movie", "cine"], "smart-home-cinema"),
    (["apag", "turn off", "luces off"], "smart-home-off"),
    (["luz", "luces", "light", "encend", "prender"], "smart-home-on"),
    (["clima", "lluvia", "temperatura", "weather"], "weather"),
    (["container", "docker", "infra", "caido"], "infra-monitor"),
    (["busca", "buscar", "serie", "episodio"], "arr-monitor"),
    (["fumando", "pucho", "cigarrillo"], "smoking-tracker"),
    (["recordame", "avisame", "reminder"], "reminders"),
    (["hablame", "audio", "voz"], "tts"),
    (["mail", "correo", "email", "inbox"], "email"),
]

KNOWN_TOOLS = {
    "smart-home-on", "smart-home-off", "smart-home-cinema",
    "weather", "email", "calendar", "arr-monitor", "infra-monitor",
    "self-evolve", "tts", "reminders", "smoking-tracker", "chat",
}

# Map tool → intent for normalizer compatibility
_TOOL_TO_INTENT = {
    "smart-home-on": "lights_on", "smart-home-off": "lights_off",
    "smart-home-cinema": "cinema_mode", "arr-monitor": "infra_check",
    "infra-monitor": "infra_check",
}

# Telemetry log
LOG_PATH = Path(__file__).parent / "logs" / "router_classify.log"


def _log_classify(query: str, tool: str, tier: str, ms: int):
    """Log classification decision for Grafana metrics."""
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        entry = json.dumps({
            "query": query[:80], "tool": tool, "tier": tier,
            "ms": ms, "ts": time.time(),
        })
        with open(LOG_PATH, "a") as f:
            f.write(entry + "\n")
    except Exception:
        pass


def _load_openrouter_key() -> str:
    try:
        cfg = json.loads(CONFIG_PATH.read_text())
        key = cfg.get("tenazas", {}).get("openrouter_api_key", "")
        if not key:
            import os
            key = os.environ.get("OPENROUTER_API_KEY", "")
        return key
    except Exception:
        return ""


# ── Tier 1: Gemma via OpenRouter ─────────────────────────────────

def _classify_gemma(message: str) -> dict | None:
    """Call Gemma 4 26B via OpenRouter. Returns parsed result or None."""
    key = _load_openrouter_key()
    if not key:
        return None

    payload = json.dumps({
        "model": OPENROUTER_MODEL,
        "messages": [
            {"role": "system", "content": OPENROUTER_SYSTEM},
            {"role": "user", "content": message},
        ],
        "max_tokens": 20,
    }).encode()

    req = Request(OPENROUTER_URL, data=payload, headers={
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    })

    try:
        resp = urlopen(req, timeout=OPENROUTER_TIMEOUT)
        data = json.loads(resp.read())
        raw = data["choices"][0]["message"]["content"].strip()
        parsed = json.loads(raw)
        tool = parsed.get("tool", "")
        if tool and tool in KNOWN_TOOLS:
            return {"tool": tool, "intent": _TOOL_TO_INTENT.get(tool, tool),
                    "args": {}, "_parsed_by": "gemma", "_raw": raw}
    except Exception:
        pass
    return None


# ── Tier 2: Finetuned via Ollama ─────────────────────────────────

def _classify_finetuned(message: str) -> dict | None:
    """Call finetuned model via Ollama. Returns parsed result or None."""
    try:
        resp = requests.post(OLLAMA_URL, json={
            "model": FINETUNED_MODEL, "stream": False,
            "prompt": message,
            "options": {"temperature": 0.1, "num_predict": 128},
        }, timeout=OLLAMA_TIMEOUT)
        resp.raise_for_status()
        raw = resp.json().get("response", "")
        parsed = json.loads(raw.strip())
        tool = parsed.get("tool", "")
        if tool and tool in KNOWN_TOOLS:
            return {"tool": tool, "intent": _TOOL_TO_INTENT.get(tool, tool),
                    "args": {}, "_parsed_by": "finetuned", "_raw": raw}
    except Exception:
        pass
    return None


# ── Tier 3: Keyword regex ────────────────────────────────────────

def _classify_keyword(message: str) -> dict | None:
    text = message.lower()
    for keywords, tool in KEYWORD_RULES:
        for k in keywords:
            if k in text:
                return {"tool": tool, "intent": _TOOL_TO_INTENT.get(tool, tool),
                        "args": {}, "_parsed_by": "keyword", "_raw": ""}
    return None


# ── Main ─────────────────────────────────────────────────────────

def route(message: str) -> dict:
    """Classify a message with 3-tier fallback. Never raises."""
    t0 = time.time()

    # Tier 1: Gemma (fast, free, accurate)
    result = _classify_gemma(message)
    if result:
        ms = int((time.time() - t0) * 1000)
        _log_classify(message, result["tool"], "gemma", ms)
        result["_error"] = None
        return result

    # Tier 2: Finetuned model (local)
    result = _classify_finetuned(message)
    if result:
        ms = int((time.time() - t0) * 1000)
        _log_classify(message, result["tool"], "finetuned", ms)
        result["_error"] = None
        return result

    # Tier 3: Keyword fallback
    result = _classify_keyword(message)
    if result:
        ms = int((time.time() - t0) * 1000)
        _log_classify(message, result["tool"], "keyword", ms)
        result["_error"] = None
        return result

    # Nothing matched
    ms = int((time.time() - t0) * 1000)
    _log_classify(message, "none", "no_match", ms)
    return {"tool": None, "intent": None, "args": {},
            "_parsed_by": "no_match", "_raw": "", "_error": None}


if __name__ == "__main__":
    import sys
    msgs = sys.argv[1:] or [
        "quiero ver el clima",
        "hola, como estas",
        "prende las luces del living",
        "hay algo nuevo en sonarr",
        "manda un mail a juan",
    ]
    for msg in msgs:
        t0 = time.time()
        r = route(msg)
        ms = int((time.time() - t0) * 1000)
        print(f"[{r['_parsed_by']:10s} {ms:4d}ms] {msg:35s} -> {r.get('tool', '?')}")
