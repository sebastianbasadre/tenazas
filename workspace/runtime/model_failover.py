#!/usr/bin/env python3
"""
Model Failover — Monitors LLM errors and switches models automatically.

Watches OpenClaw logs for rate limit (429) errors. When detected:
1. Switches openclaw.json primary model to Ollama fallback
2. Periodically retries Gemini and switches back when available

Runs as a background process from startup.sh.
Log: workspace/runtime/logs/model_failover.log
"""

import json
import os
import re
import subprocess
import time
from datetime import datetime
from pathlib import Path

CONFIG_FILE = Path("/home/tenazas/.openclaw/openclaw.json")
OPENCLAW_LOG_DIR = Path("/tmp/openclaw")
LOG_FILE = Path("/home/tenazas/runtime/logs/model_failover.log")

# Models
PRIMARY_MODEL = "google/gemini-2.5-flash"
FALLBACK_MODEL = "ollama/qwen2.5:7b"
OLLAMA_PRELOAD_MODEL = "qwen2.5:7b"
OLLAMA_URL = "http://192.168.2.6:11434"

# Timing
CHECK_INTERVAL = 30          # Check logs every 30s
GEMINI_RETRY_INTERVAL = 300  # Retry Gemini every 5 min after failover
GEMINI_TEST_URL = "https://generativelanguage.googleapis.com/v1beta/models"

# State
_current_model = None
_failover_active = False
_last_gemini_retry = 0


def log(msg: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "a") as f:
        f.write(f"{ts} {msg}\n")


def get_current_model() -> str:
    """Read current model from openclaw.json."""
    try:
        with open(CONFIG_FILE) as f:
            config = json.load(f)
        return config.get("agents", {}).get("defaults", {}).get("model", "")
    except (FileNotFoundError, json.JSONDecodeError):
        return ""


def set_model(model: str):
    """Update primary model in openclaw.json (hot-reloaded by OpenClaw)."""
    try:
        with open(CONFIG_FILE) as f:
            config = json.load(f)
        config["agents"]["defaults"]["model"] = model
        with open(CONFIG_FILE, "w") as f:
            json.dump(config, f, indent=2)
        log(f"Model switched to {model}")
    except Exception as e:
        log(f"ERROR switching model: {e}")


def check_gemini_available() -> bool:
    """Quick check if Gemini API responds (not rate limited)."""
    try:
        api_key = os.environ.get("GOOGLE_API_KEY", "")
        if not api_key:
            try:
                with open(CONFIG_FILE) as f:
                    config = json.load(f)
                # Try to find API key in env or config
                api_key = config.get("auth", {}).get("profiles", {}).get(
                    "google:default", {}).get("apiKey", "")
            except Exception:
                pass

        if not api_key:
            return False

        result = subprocess.run(
            ["curl", "-sf", "--max-time", "10",
             f"{GEMINI_TEST_URL}?key={api_key}"],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0 and "429" not in result.stdout:
            return True
        return False
    except Exception:
        return False


def check_logs_for_429() -> bool:
    """Check recent OpenClaw logs for rate limit errors."""
    today = datetime.now().strftime("%Y-%m-%d")
    log_file = OPENCLAW_LOG_DIR / f"openclaw-{today}.log"

    if not log_file.exists():
        return False

    try:
        # Read last 50 lines
        result = subprocess.run(
            ["tail", "-50", str(log_file)],
            capture_output=True, text=True, timeout=5,
        )
        recent = result.stdout

        # Check for 429 in last few minutes
        if "429" in recent and "rate_limit" in recent:
            return True
        if "exceeded your current quota" in recent:
            return True
    except Exception:
        pass

    return False


def preload_ollama_model():
    """Pre-load fallback model into Ollama VRAM."""
    try:
        log(f"Pre-loading {OLLAMA_PRELOAD_MODEL} into VRAM...")
        result = subprocess.run(
            ["curl", "-s", "--max-time", "120",
             f"{OLLAMA_URL}/api/generate",
             "-d", json.dumps({
                 "model": OLLAMA_PRELOAD_MODEL,
                 "prompt": "",
                 "keep_alive": "30m",
             })],
            capture_output=True, text=True, timeout=130,
        )
        if result.returncode == 0:
            log(f"{OLLAMA_PRELOAD_MODEL} pre-loaded successfully")
        else:
            log(f"Pre-load failed: {result.stderr[:100]}")
    except Exception as e:
        log(f"Pre-load error: {e}")


def run_failover_loop():
    """Main loop: monitor and switch models as needed."""
    global _failover_active, _last_gemini_retry

    log("Model failover monitor started")

    # Pre-load Ollama model on startup
    preload_ollama_model()

    current = get_current_model()
    log(f"Current model: {current}")

    while True:
        try:
            current = get_current_model()

            if current == PRIMARY_MODEL or current == "":
                # Using Gemini — check for 429
                if check_logs_for_429():
                    log(f"429 detected! Switching to {FALLBACK_MODEL}")
                    set_model(FALLBACK_MODEL)
                    preload_ollama_model()
                    _failover_active = True
                    _last_gemini_retry = time.time()

            elif _failover_active and current == FALLBACK_MODEL:
                # Using fallback — periodically try Gemini
                if time.time() - _last_gemini_retry >= GEMINI_RETRY_INTERVAL:
                    _last_gemini_retry = time.time()
                    if check_gemini_available():
                        log(f"Gemini available again! Switching back to {PRIMARY_MODEL}")
                        set_model(PRIMARY_MODEL)
                        _failover_active = False
                    else:
                        log("Gemini still rate-limited, staying on fallback")

        except Exception as e:
            log(f"Loop error: {e}")

        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    run_failover_loop()
