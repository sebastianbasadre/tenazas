#!/usr/bin/env python3
"""
OpenRouter Usage — Balance and usage tracking for Tenazas.

Queries the OpenRouter API to check credits, usage, and rate limits.
Logs each query to logs/openrouter_usage.log (JSON lines).

API docs: https://openrouter.ai/docs/api-reference/api-keys
"""

import json
import os
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).parent
LOG_FILE = BASE_DIR / "logs" / "openrouter_usage.log"

_API_BASE = "https://openrouter.ai/api/v1"


def _now():
    return datetime.now().astimezone()


def _log(entry: dict):
    """Append a JSON line to openrouter_usage.log."""
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    entry["ts"] = _now().isoformat()
    with open(LOG_FILE, "a") as f:
        f.write(json.dumps(entry, default=str) + "\n")


def _api_get(path: str) -> dict:
    """GET request to OpenRouter API. Returns parsed JSON or raises."""
    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY not set")

    url = f"{_API_BASE}{path}"
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode()[:500]
        except Exception:
            pass
        _log({"action": "api_error", "path": path, "status": e.code, "body": body})
        if e.code == 401:
            raise RuntimeError(f"OpenRouter API key invalid (401): {body}")
        if e.code == 429:
            raise RuntimeError(f"OpenRouter rate limited (429): {body}")
        raise RuntimeError(f"OpenRouter API error ({e.code}): {body}")
    except urllib.error.URLError as e:
        _log({"action": "api_error", "path": path, "error": str(e)})
        raise RuntimeError(f"OpenRouter connection error: {e}")
    except json.JSONDecodeError as e:
        _log({"action": "api_error", "path": path, "error": f"JSON parse: {e}"})
        raise RuntimeError(f"OpenRouter returned invalid JSON: {e}")


def get_balance() -> dict:
    """Query OpenRouter API key info.

    Returns:
        {
            "balance_usd": float,     # credits available
            "usage_usd": float,       # total spent
            "limit_usd": float,       # configured limit (0 if none)
            "is_free_tier": bool,
            "rate_limit": dict,       # requests/interval info
            "raw": dict               # full response for debug
        }
    """
    data = _api_get("/auth/key")
    key_data = data.get("data", data)

    usage = key_data.get("usage", 0.0)
    limit = key_data.get("limit", 0.0) or 0.0
    is_free = key_data.get("is_free_tier", False)
    rate_limit = key_data.get("rate_limit", {})

    # Balance = limit - usage.  Fallback to known account limit if API
    # does not report one (OpenRouter free-tier often omits it).
    KNOWN_LIMIT_USD = 10.0
    if limit <= 0:
        limit = KNOWN_LIMIT_USD
    balance = max(0.0, limit - usage)

    result = {
        "balance_usd": round(balance, 4),
        "usage_usd": round(usage, 4),
        "limit_usd": round(limit, 4),
        "is_free_tier": is_free,
        "rate_limit": rate_limit,
        "raw": data,
    }

    _log({"action": "get_balance", "usage": result["usage_usd"],
          "balance": result["balance_usd"], "limit": result["limit_usd"]})

    return result


def get_usage_summary() -> str:
    """Human-readable summary for Telegram.

    Returns formatted string, never raises (returns error message on failure).
    """
    try:
        b = get_balance()
    except RuntimeError as e:
        return f"OpenRouter\nError: {e}"

    lines = ["OpenRouter"]

    lines.append(f"Balance: ${b['balance_usd']:.2f}")

    lines.append(f"Usado: ${b['usage_usd']:.2f}")

    if b["limit_usd"] > 0:
        lines.append(f"Limite: ${b['limit_usd']:.2f}")

    if b["is_free_tier"]:
        lines.append("Tier: free")

    rl = b.get("rate_limit", {})
    if rl:
        req = rl.get("requests", "?")
        interval = rl.get("interval", "?")
        lines.append(f"Rate limit: {req} req/{interval}")

    return "\n".join(lines)


def check_balance_alert(threshold_usd: float = 1.0) -> bool:
    """Return True if balance is below threshold. For proactive alerts.

    Returns False on errors (fail-open: don't alert if we can't check).
    """
    try:
        b = get_balance()
    except RuntimeError:
        return False

    below = b["balance_usd"] < threshold_usd
    if below:
        _log({"action": "balance_alert", "balance": b["balance_usd"],
              "threshold": threshold_usd})
    return below


if __name__ == "__main__":
    import sys
    if "--json" in sys.argv:
        print(json.dumps(get_balance(), indent=2))
    elif "--alert" in sys.argv:
        threshold = 1.0
        for i, arg in enumerate(sys.argv):
            if arg == "--threshold" and i + 1 < len(sys.argv):
                threshold = float(sys.argv[i + 1])
        alert = check_balance_alert(threshold)
        print(f"Alert: {alert}")
    else:
        print(get_usage_summary())
