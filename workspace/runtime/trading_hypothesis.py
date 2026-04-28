#!/usr/bin/env python3
"""
Trading Hypothesis — Phase 3: Generate and track verifiable predictions.

NEVER acts on hypotheses. Only generates, tracks, and evaluates.
SAFETY: No API keys. Public data only.

Usage:
    python3 trading_hypothesis.py --generate   # create new hypotheses
    python3 trading_hypothesis.py --evaluate   # check expired ones
    python3 trading_hypothesis.py --list       # show all active
"""

import json
from datetime import datetime, timedelta
from hashlib import sha256
from pathlib import Path
from runtime_utils import now

BASE_DIR = Path(__file__).parent
STATE_DIR = Path("/home/tenazas/.openclaw/state") if Path("/home/tenazas").exists() else BASE_DIR.parent / "state"
HYPOTHESES_FILE = STATE_DIR / "trading" / "hypotheses.json"
LOG_FILE = BASE_DIR / "logs" / "trading_hypothesis.log"

MAX_ACTIVE = 10


def _log(e):
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    e["ts"] = now().isoformat()
    try:
        with open(LOG_FILE, "a") as f: f.write(json.dumps(e, default=str) + "\n")
    except OSError: pass

def _load_hypotheses() -> list:
    try:
        with open(HYPOTHESES_FILE) as f: return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError): return []

def _save_hypotheses(h: list):
    HYPOTHESES_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(HYPOTHESES_FILE, "w") as f: json.dump(h[-100:], f, indent=2, default=str)


def generate(observation: dict = None) -> list:
    """Generate hypotheses from current market observation."""
    if observation is None:
        try:
            from trading_observer import get_latest
            observation = get_latest()
        except Exception:
            return []

    if not observation:
        return []

    fng = observation.get("fear_greed", {})
    fng_value = fng.get("value", 50) if isinstance(fng, dict) else 50
    derived = observation.get("derived", {})
    trend = derived.get("trend", "unknown")
    vol_class = derived.get("volatility_class", "unknown")
    btc_price = observation.get("tickers", {}).get("BTCUSDT", {}).get("price", 0)

    if btc_price == 0:
        return []

    hypotheses = _load_hypotheses()
    active = [h for h in hypotheses if h.get("status") == "active"]
    if len(active) >= MAX_ACTIVE:
        return []

    new_hyps = []

    # Rule 1: Extreme fear + price below MA30 → bullish
    ma30 = derived.get("ma30", 0)
    if fng_value < 25 and ma30 > 0 and btc_price < ma30:
        h = _create_hypothesis(
            asset="BTC", direction="up",
            target_price=round(btc_price * 1.10, 0),
            timeframe_days=14,
            confidence=0.65,
            reasoning=f"Fear&Greed={fng_value} (extreme fear) + BTC below MA30 (${ma30:,.0f})",
            current_price=btc_price,
        )
        new_hyps.append(h)

    # Rule 2: Extreme greed + price above MA30 → bearish
    if fng_value > 75 and ma30 > 0 and btc_price > ma30 * 1.05:
        h = _create_hypothesis(
            asset="BTC", direction="down",
            target_price=round(btc_price * 0.90, 0),
            timeframe_days=14,
            confidence=0.60,
            reasoning=f"Fear&Greed={fng_value} (extreme greed) + BTC 5%+ above MA30",
            current_price=btc_price,
        )
        new_hyps.append(h)

    # Rule 3: Very high volatility → sideways
    if vol_class == "high" and trend == "sideways":
        h = _create_hypothesis(
            asset="BTC", direction="sideways",
            target_price=btc_price,
            timeframe_days=7,
            confidence=0.55,
            reasoning=f"High volatility + sideways trend → expect range-bound",
            current_price=btc_price,
        )
        new_hyps.append(h)

    # Dedup against existing
    existing_keys = {(h["asset"], h["direction"]) for h in active}
    new_hyps = [h for h in new_hyps if (h["asset"], h["direction"]) not in existing_keys]

    hypotheses.extend(new_hyps)
    _save_hypotheses(hypotheses)
    _log({"action": "generate", "new": len(new_hyps), "total_active": len(active) + len(new_hyps)})

    return new_hyps


def _create_hypothesis(asset: str, direction: str, target_price: float,
                       timeframe_days: int, confidence: float,
                       reasoning: str, current_price: float) -> dict:
    hid = sha256(f"{asset}:{direction}:{now().isoformat()}".encode()).hexdigest()[:12]
    return {
        "hypothesis_id": hid,
        "created_at": now().isoformat(),
        "asset": asset,
        "direction": direction,
        "entry_price": current_price,
        "target_price": target_price,
        "timeframe_days": timeframe_days,
        "confidence": confidence,
        "reasoning": reasoning,
        "status": "active",
        "result": None,
    }


def evaluate() -> list:
    """Evaluate expired hypotheses against current prices."""
    hypotheses = _load_hypotheses()
    evaluated = []

    try:
        from trading_observer import get_latest
        latest = get_latest()
    except Exception:
        return []

    _now = now()
    for h in hypotheses:
        if h.get("status") != "active":
            continue

        created = datetime.fromisoformat(h["created_at"])
        expiry = created + timedelta(days=h.get("timeframe_days", 7))

        if _now < expiry:
            continue

        # Get current price
        symbol = f"{h['asset']}USDT"
        current = latest.get("tickers", {}).get(symbol, {}).get("price", 0)
        if current == 0:
            continue

        entry = h.get("entry_price", h.get("target_price", 0))
        direction = h["direction"]
        target = h["target_price"]

        if direction == "up":
            hit = current >= target
        elif direction == "down":
            hit = current <= target
        else:  # sideways
            hit = abs(current - entry) / entry < 0.05  # within 5%

        h["status"] = "hit" if hit else "missed"
        h["result"] = {
            "final_price": current,
            "target_hit": hit,
            "actual_change_pct": round((current - entry) / entry * 100, 2) if entry else 0,
            "evaluated_at": now.isoformat(),
        }
        evaluated.append(h)

    _save_hypotheses(hypotheses)
    _log({"action": "evaluate", "evaluated": len(evaluated),
          "hit": sum(1 for h in evaluated if h["status"] == "hit")})
    return evaluated


def get_active() -> list:
    return [h for h in _load_hypotheses() if h.get("status") == "active"]


def get_accuracy() -> dict:
    """Calculate hypothesis accuracy."""
    all_h = _load_hypotheses()
    evaluated = [h for h in all_h if h.get("status") in ("hit", "missed")]
    if not evaluated:
        return {"total": 0, "hit": 0, "missed": 0, "accuracy": 0.0}
    hit = sum(1 for h in evaluated if h["status"] == "hit")
    return {
        "total": len(evaluated),
        "hit": hit,
        "missed": len(evaluated) - hit,
        "accuracy": round(hit / len(evaluated), 4),
    }


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="Trading Hypothesis")
    p.add_argument("--generate", action="store_true")
    p.add_argument("--evaluate", action="store_true")
    p.add_argument("--list", action="store_true")
    p.add_argument("--accuracy", action="store_true")
    args = p.parse_args()

    if args.generate:
        new = generate()
        print(f"Generated {len(new)} new hypotheses")
        for h in new:
            print(f"  [{h['confidence']:.0%}] {h['asset']} {h['direction']} → ${h['target_price']:,.0f}")
    elif args.evaluate:
        evd = evaluate()
        print(f"Evaluated {len(evd)} hypotheses")
        for h in evd:
            print(f"  {h['hypothesis_id']}: {h['status']}")
    elif args.list:
        for h in get_active():
            print(f"  [{h['confidence']:.0%}] {h['asset']} {h['direction']} → ${h['target_price']:,.0f}")
    elif args.accuracy:
        print(json.dumps(get_accuracy(), indent=2))
    else:
        p.print_help()
