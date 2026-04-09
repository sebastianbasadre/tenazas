#!/usr/bin/env python3
"""
dreaming.py — Memory consolidation in idle (REM layer).

Inspired by OpenClaw 2026.4.x memory/dreaming.
Runs in background during idle; never in the path of an active query.

Flow:
  1. Read recent episodic memory (memory_log.json + shared_memory.json)
  2. Call LLM to extract "lasting truths" (patterns, preferences, stable facts)
  3. Deduplicate against existing dreams via content hash
  4. Write new entries to learning/dreams.md (staging area)
  5. Log session to learning/dream_log.json

State files:
  learning/dreams.md      — staging area (never auto-deleted)
  learning/dream_log.json — session history
"""

import json
import hashlib
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.request import Request, urlopen

# ---------------------------------------------------------------------------
# Paths — all relative to runtime dir (workspace/runtime/)
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).parent
MEMORY_LOG_FILE = BASE_DIR / "memory_log.json"
SHARED_MEMORY_FILE = BASE_DIR / "shared_memory.json"
LEARNING_DIR = BASE_DIR / "learning"
DREAMS_FILE = LEARNING_DIR / "dreams.md"
DREAM_LOG = LEARNING_DIR / "dream_log.json"
LOG_FILE = BASE_DIR / "logs" / "dreaming.log"
CONFIG_FILE = Path("/home/tenazas/.openclaw/openclaw.json")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
MAX_EPISODIC_ENTRIES = 50
MAX_TRUTHS_PER_REM = 5
OLLAMA_GPU_URL = os.environ.get("OLLAMA_GPU_URL", "http://192.168.2.6:11434")
OLLAMA_NAS_URL = "http://127.0.0.1:11434"

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def _log(entry: dict):
    """Append a JSON line to dreaming.log."""
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    entry["ts"] = datetime.now().astimezone().isoformat()
    with open(LOG_FILE, "a") as f:
        f.write(json.dumps(entry, default=str) + "\n")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _content_hash(text: str) -> str:
    """Deterministic hash for deduplication. Strips whitespace, lowercases."""
    return hashlib.sha256(text.strip().lower().encode()).hexdigest()[:16]


def _load_existing_hashes() -> set:
    """Read dreams.md and extract hashes of existing entries."""
    hashes = set()
    if DREAMS_FILE.exists():
        for line in DREAMS_FILE.read_text().splitlines():
            if line.startswith("<!-- hash:"):
                h = line.replace("<!-- hash:", "").replace("-->", "").strip()
                hashes.add(h)
    return hashes


def _load_recent_episodic(n: int) -> list[str]:
    """Load the N most recent episodic entries from memory_log.json and shared_memory.json."""
    entries = []

    # Primary source: memory_log.json (runtime events)
    if MEMORY_LOG_FILE.exists():
        try:
            data = json.loads(MEMORY_LOG_FILE.read_text())
            if isinstance(data, list):
                for entry in data[-n:]:
                    # Extract meaningful content from each log entry
                    events = entry.get("events", [])
                    ts = entry.get("ts", "")
                    for ev in events:
                        ev_type = ev.get("type", "unknown")
                        summary = f"[{ts[:16]}] {ev_type}"
                        # Add relevant details
                        if "prev" in ev and "curr" in ev:
                            summary += f": {ev['prev']} → {ev['curr']}"
                        if ev.get("target"):
                            summary += f" target={ev['target']}"
                        entries.append(summary)
                    # Also include task results
                    for result in entry.get("results", []):
                        if isinstance(result, dict):
                            entries.append(f"[{ts[:16]}] task: {result.get('action', '?')} "
                                           f"target={result.get('target', '?')} "
                                           f"ok={result.get('ok', '?')}")
        except (json.JSONDecodeError, OSError):
            pass

    # Secondary source: shared_memory.json episodic entries
    if SHARED_MEMORY_FILE.exists():
        try:
            data = json.loads(SHARED_MEMORY_FILE.read_text())
            for ep in data.get("episodic", [])[-20:]:
                content = ep.get("content", str(ep))
                entries.append(f"[episodic] {content[:200]}")
        except (json.JSONDecodeError, OSError):
            pass

    return entries[-n:]


def _load_config() -> dict:
    try:
        return json.loads(CONFIG_FILE.read_text())
    except Exception:
        return {}


def _call_llm(prompt: str, model: str, url: str, timeout: int = 60) -> str | None:
    """Call Ollama generate API. Returns response text or None."""
    payload = json.dumps({
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {"num_predict": 512, "temperature": 0.3},
    }).encode()
    req = Request(f"{url}/api/generate", data=payload,
                  headers={"Content-Type": "application/json"})
    try:
        with urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read())
            return data.get("response", "").strip()
    except Exception as e:
        _log({"action": "llm_error", "model": model, "url": url, "error": str(e)})
        return None


def _extract_truths(episodic_entries: list[str]) -> list[str]:
    """Call LLM to extract lasting truths from episodic memories.

    Uses model_router to select the best model, with Ollama fallbacks.
    """
    context = "\n".join(episodic_entries[:MAX_EPISODIC_ENTRIES])

    prompt = f"""Sos el sistema de memoria de Tenazas, asistente personal de Sebastián.

Analizá estas interacciones recientes y extraé hasta {MAX_TRUTHS_PER_REM} "lasting truths":
- Patrones de infraestructura recurrentes (containers que fallan, horarios de problemas)
- Hechos operativos estables (servicios confiables vs problemáticos)
- Patrones de uso del sistema (qué modelos se usan más, qué skills son populares)

REGLAS:
- Cada truth: una oración concisa y accionable (máx 100 caracteres)
- NO incluir IPs, tokens, passwords ni datos sensibles
- Solo truths útiles para futuras decisiones operativas
- Si no hay nada nuevo, devolvé una lista vacía

EVENTOS RECIENTES:
{context}

Respondé SOLO con un JSON array de strings, sin markdown, sin explicaciones:
["truth 1", "truth 2"]"""

    # Try model_router first for best model selection
    model = "qwen2.5:7b"
    url = OLLAMA_GPU_URL
    try:
        sys.path.insert(0, str(BASE_DIR)) if str(BASE_DIR) not in sys.path else None
        from model_router import route_by_role
        result = route_by_role("analyze patterns", flags={"role": "fast"})
        if result and result.get("model") and result.get("url"):
            model = result["model"]
            url = result["url"]
    except Exception:
        pass

    # Try GPU
    response = _call_llm(prompt, model, url)
    if not response:
        # Fallback: NAS CPU with small model
        response = _call_llm(prompt, "phi4-mini", OLLAMA_NAS_URL, timeout=120)

    if not response:
        _log({"action": "extract_failed", "reason": "all_backends_offline"})
        return []

    # Parse JSON from response
    try:
        text = response.strip().strip("```json").strip("```").strip()
        # Find the JSON array in the response
        start = text.find("[")
        end = text.rfind("]")
        if start >= 0 and end > start:
            text = text[start:end + 1]
        truths = json.loads(text)
        if isinstance(truths, list):
            return [str(t).strip() for t in truths if t and len(str(t).strip()) > 5][:MAX_TRUTHS_PER_REM]
    except (json.JSONDecodeError, ValueError) as e:
        _log({"action": "parse_error", "error": str(e), "response_preview": response[:200]})

    return []


def _write_to_dreams(truths: list[str], existing_hashes: set) -> list[str]:
    """Write new truths to dreams.md (staging). Returns the ones that were new."""
    new_truths = []
    DREAMS_FILE.parent.mkdir(parents=True, exist_ok=True)

    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines_to_append = []

    for truth in truths:
        h = _content_hash(truth)
        if h in existing_hashes:
            continue
        lines_to_append.append(f"<!-- hash:{h} -->\n- [{now}] {truth}")
        existing_hashes.add(h)
        new_truths.append(truth)

    if lines_to_append:
        with open(DREAMS_FILE, "a") as f:
            f.write(f"\n## REM Session {now}\n")
            f.write("\n".join(lines_to_append) + "\n")

    return new_truths


def _log_session(truths_found: int, new_truths: int, duration_s: float):
    """Save dreaming session log."""
    DREAM_LOG.parent.mkdir(parents=True, exist_ok=True)
    log = []
    if DREAM_LOG.exists():
        try:
            log = json.loads(DREAM_LOG.read_text())
        except Exception:
            log = []

    log.append({
        "timestamp": datetime.now().astimezone().isoformat(),
        "truths_found": truths_found,
        "new_truths": new_truths,
        "duration_seconds": round(duration_s, 1),
    })

    # Keep last 100 records
    DREAM_LOG.write_text(json.dumps(log[-100:], indent=2))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def run_rem_session(force: bool = False) -> dict:
    """Run a REM memory consolidation session.

    Args:
        force: if True, skip idle check

    Returns:
        dict with session metrics
    """
    start = time.time()
    _log({"action": "rem_start"})

    # Load recent episodic memories
    episodes = _load_recent_episodic(MAX_EPISODIC_ENTRIES)
    if not episodes:
        _log({"action": "rem_skip", "reason": "no_episodic_data"})
        return {"status": "skip", "reason": "no_episodic_data"}

    _log({"action": "episodic_loaded", "count": len(episodes)})

    # Extract truths via LLM
    truths = _extract_truths(episodes)
    if not truths:
        duration = time.time() - start
        _log_session(0, 0, duration)
        _log({"action": "rem_done", "truths": 0, "new": 0, "duration": round(duration, 1)})
        return {"status": "ok", "truths_found": 0, "new_truths": 0}

    # Load existing hashes for deduplication
    existing_hashes = _load_existing_hashes()

    # Write to dreams.md (staging)
    new_truths = _write_to_dreams(truths, existing_hashes)

    duration = time.time() - start
    _log_session(len(truths), len(new_truths), duration)
    _log({"action": "rem_done", "truths": len(truths), "new": len(new_truths),
          "duration": round(duration, 1)})

    return {
        "status": "ok",
        "truths_found": len(truths),
        "new_truths": len(new_truths),
        "duration_s": round(duration, 1),
    }


if __name__ == "__main__":
    force = "--force" in sys.argv
    result = run_rem_session(force=force)
    print(json.dumps(result, indent=2))
