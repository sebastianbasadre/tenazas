#!/usr/bin/env python3
"""
gpu_scheduler.py
Manages which models stay loaded in RTX 3090 VRAM (24GB).

Rules:
  - gemma3:12b (~8GB) — always in VRAM (primary classifier)
  - qwen3:8b (~5GB) — keep if space allows (telegram chat LLM)
  - tenazas-qwen25-7b-v8 (~11GB) — load on demand only
  - If VRAM < 2GB free → unload least recently used

Keepalive: dummy 1-token request every 4 min to prevent Ollama auto-unload (5 min timeout).

Usage:
  python3 gpu_scheduler.py --status     # show VRAM state
  python3 gpu_scheduler.py --optimize   # apply scheduling rules
  python3 gpu_scheduler.py --keepalive  # ping priority models
"""

import argparse
import json
import time
from urllib.request import Request, urlopen

try:
    from runtime_config import OLLAMA_GPU_URL
except ImportError:
    from workspace.runtime.runtime_config import OLLAMA_GPU_URL

OLLAMA_URL = OLLAMA_GPU_URL
TOTAL_VRAM = 24 * 1024**3  # 24GB in bytes
MIN_FREE_VRAM = 2 * 1024**3  # 2GB minimum free

# Priority order: higher = more important to keep in VRAM
PRIORITY = {
    "gemma3:12b": 100,       # primary classifier — always loaded
    "qwen3:8b": 80,          # telegram chat LLM + humanization
    "qwen2.5:3b": 60,        # multi-agent, self-evolve, learn
    "tenazas-qwen25-7b-v8": 40,  # finetuned fallback — on demand
    "nomic-embed-text": 30,  # RAG embeddings — on demand
}

# Models that should always be in VRAM
ALWAYS_LOADED = ["gemma3:12b"]

# Models to keepalive (top priority)
KEEPALIVE_MODELS = ["gemma3:12b", "qwen3:8b"]


def _api(endpoint: str, data: dict = None, timeout: int = 10) -> dict:
    """Call Ollama API."""
    url = f"{OLLAMA_URL}{endpoint}"
    if data:
        payload = json.dumps(data).encode()
        req = Request(url, data=payload, headers={"Content-Type": "application/json"})
    else:
        req = Request(url)
    resp = urlopen(req, timeout=timeout)
    return json.loads(resp.read())


def get_status() -> dict:
    """Get current VRAM state."""
    try:
        ps = _api("/api/ps")
    except Exception as e:
        return {"error": str(e), "models": [], "used_gb": 0, "free_gb": 24}

    models = []
    total_used = 0
    for m in ps.get("models", []):
        vram = m.get("size_vram", 0)
        total_used += vram
        models.append({
            "name": m["name"],
            "vram_gb": round(vram / 1024**3, 1),
            "vram_bytes": vram,
            "expires": m.get("expires_at", ""),
            "priority": PRIORITY.get(m["name"].split(":")[0] + ":" + m["name"].split(":")[-1],
                        PRIORITY.get(m["name"], 0)),
        })

    free = TOTAL_VRAM - total_used
    return {
        "models": sorted(models, key=lambda x: -x["priority"]),
        "used_gb": round(total_used / 1024**3, 1),
        "free_gb": round(free / 1024**3, 1),
        "free_bytes": free,
    }


def print_status():
    """Print VRAM status."""
    s = get_status()
    if "error" in s:
        print(f"Error: {s['error']}")
        return

    print(f"GPU VRAM: {s['used_gb']}GB used / {s['free_gb']}GB free / 24GB total")
    print()
    if s["models"]:
        for m in s["models"]:
            prio = PRIORITY.get(m["name"], 0)
            keep = "KEEP" if m["name"] in ALWAYS_LOADED else ""
            print(f"  {m['name']:35s} {m['vram_gb']:5.1f} GB  prio={prio:3d}  {keep}")
    else:
        print("  (no models loaded)")


def unload_model(name: str):
    """Unload a model from VRAM."""
    try:
        _api("/api/generate", {"model": name, "keep_alive": 0})
        print(f"  Unloaded: {name}")
    except Exception as e:
        print(f"  Error unloading {name}: {e}")


def load_model(name: str):
    """Load a model into VRAM with a dummy request."""
    try:
        _api("/api/generate", {
            "model": name, "prompt": "hi", "stream": False,
            "options": {"num_predict": 1},
        }, timeout=60)
        print(f"  Loaded: {name}")
    except Exception as e:
        print(f"  Error loading {name}: {e}")


def keepalive(name: str):
    """Send keepalive to prevent auto-unload."""
    try:
        _api("/api/generate", {
            "model": name, "prompt": ".", "stream": False,
            "options": {"num_predict": 1},
        }, timeout=30)
    except Exception:
        pass


def optimize():
    """Apply scheduling rules to optimize VRAM usage."""
    s = get_status()
    if "error" in s:
        print(f"Error: {s['error']}")
        return

    loaded_names = {m["name"] for m in s["models"]}
    print(f"Current: {s['used_gb']}GB used, {s['free_gb']}GB free")
    print(f"Loaded: {', '.join(loaded_names) or 'none'}")

    # Rule 1: ensure ALWAYS_LOADED models are in VRAM
    for name in ALWAYS_LOADED:
        if name not in loaded_names:
            print(f"\n  Loading priority model: {name}")
            load_model(name)

    # Rule 2: if VRAM too full, unload lowest priority
    s = get_status()  # refresh after loads
    while s["free_bytes"] < MIN_FREE_VRAM and s["models"]:
        # Find lowest priority loaded model
        lowest = min(s["models"], key=lambda x: x["priority"])
        if lowest["name"] in ALWAYS_LOADED:
            print("  Cannot free more — only ALWAYS_LOADED models remain")
            break
        print(f"\n  VRAM low ({s['free_gb']}GB free) — unloading {lowest['name']}")
        unload_model(lowest["name"])
        s = get_status()

    print(f"\nFinal: {s['used_gb']}GB used, {s['free_gb']}GB free")


def run_keepalive():
    """Send keepalive to priority models that are loaded."""
    s = get_status()
    loaded_names = {m["name"] for m in s["models"]}

    for name in KEEPALIVE_MODELS:
        if name in loaded_names:
            keepalive(name)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="GPU VRAM scheduler")
    parser.add_argument("--status", action="store_true", help="Show VRAM state")
    parser.add_argument("--optimize", action="store_true", help="Apply scheduling rules")
    parser.add_argument("--keepalive", action="store_true", help="Keepalive priority models")
    args = parser.parse_args()

    if args.status:
        print_status()
    elif args.optimize:
        optimize()
    elif args.keepalive:
        run_keepalive()
    else:
        parser.print_help()
