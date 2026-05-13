#!/usr/bin/env python3
"""
Finance rules engine — JSON-configurable portfolio alerts with Telegram.

Rule types: price_above, price_below, pnl_threshold, drawdown, allocation_max
Each rule has a cooldown to prevent spam.

Usage:
    python3 finance_rules.py --list       # show all rules + last trigger
    python3 finance_rules.py --check      # dry-run evaluation
    python3 finance_rules.py --test ID    # force-evaluate ignoring cooldown
"""

import json
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.request import Request, urlopen

try:
    from .runtime_config import get_default_telegram_chat_id
except ImportError:
    from runtime_config import get_default_telegram_chat_id

BASE_DIR = Path(__file__).parent
RULES_FILE = BASE_DIR / "finance_rules.json"
STATE_DIR = (Path("/home/tenazas/.openclaw/state")
             if Path("/home/tenazas").exists()
             else BASE_DIR.parent / "state")
RULES_STATE_FILE = STATE_DIR / "finance_rules_state.json"
TENAZAS_CONFIG = (Path("/home/tenazas/.openclaw/tenazas.json")
                  if Path("/home/tenazas").exists()
                  else BASE_DIR.parent.parent / "state" / "tenazas.json")


def load_rules() -> list:
    try:
        data = json.loads(RULES_FILE.read_text())
        return data.get("rules", [])
    except Exception:
        return []


def _load_state() -> dict:
    try:
        return json.loads(RULES_STATE_FILE.read_text())
    except Exception:
        return {}


def _save_state(state: dict):
    try:
        RULES_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        RULES_STATE_FILE.write_text(json.dumps(state, indent=2))
    except Exception:
        pass


def _check_cooldown(rule_id: str, state: dict, cooldown_min: int) -> bool:
    """Returns True if cooldown has expired (ok to trigger)."""
    entry = state.get(rule_id, {})
    last = entry.get("last_triggered", "")
    if not last:
        return True
    try:
        last_ts = datetime.fromisoformat(last).timestamp()
        return (time.time() - last_ts) > (cooldown_min * 60)
    except Exception:
        return True


def _find_position(positions: list, asset: str) -> dict | None:
    for p in positions:
        if p["symbol"].upper() == asset.upper():
            return p
    return None


def evaluate_rule(rule: dict, positions: list,
                  drawdown: float, total_pnl_usd: float) -> bool:
    """Evaluate a single rule. Returns True if triggered."""
    rtype = rule.get("type", "")
    asset = rule.get("asset")
    threshold = rule.get("threshold", 0)

    if rtype == "price_above":
        pos = _find_position(positions, asset) if asset else None
        return pos is not None and pos["price"] > threshold

    if rtype == "price_below":
        pos = _find_position(positions, asset) if asset else None
        return pos is not None and pos["price"] < threshold

    if rtype == "pnl_threshold":
        return total_pnl_usd < threshold  # threshold is negative for loss

    if rtype == "drawdown":
        return drawdown > threshold

    if rtype == "allocation_max":
        pos = _find_position(positions, asset) if asset else None
        return pos is not None and pos["allocation_pct"] > threshold

    return False


def _send_telegram(message: str):
    """Send alert to Telegram."""
    try:
        cfg = json.loads(TENAZAS_CONFIG.read_text())
    except Exception:
        return
    token = cfg.get("channels", {}).get("telegram", {}).get("botToken", "")
    if not token:
        # Try flat key
        token = cfg.get("telegram_bot_token", "")
    if not token:
        return
    chat_id = get_default_telegram_chat_id()
    payload = json.dumps({
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "Markdown"
    }).encode()
    try:
        req = Request(f"https://api.telegram.org/bot{token}/sendMessage",
                      data=payload,
                      headers={"Content-Type": "application/json"})
        urlopen(req, timeout=10)
    except Exception:
        pass


def evaluate_all_rules(positions: list, drawdown: float,
                       total_pnl_usd: float) -> list:
    """Evaluate all enabled rules, respecting cooldowns. Returns triggered rules."""
    rules = load_rules()
    state = _load_state()
    triggered = []

    for rule in rules:
        if not rule.get("enabled", True):
            continue
        rid = rule.get("id", "")
        cooldown = rule.get("cooldown_minutes", 60)

        if not _check_cooldown(rid, state, cooldown):
            continue

        if evaluate_rule(rule, positions, drawdown, total_pnl_usd):
            triggered.append(rule)
            state[rid] = {"last_triggered": datetime.now().isoformat()}

    if triggered:
        _save_state(state)
        # Batch all alerts into one message
        lines = ["*Portfolio Alert*"]
        for r in triggered:
            lines.append(f"  {r.get('message', r.get('id', '?'))}")
        _send_telegram("\n".join(lines))

    return triggered


# ── CLI ───────────────────────────────────────────────────────

if __name__ == "__main__":
    if "--list" in sys.argv:
        rules = load_rules()
        state = _load_state()
        if not rules:
            print("No rules configured. Create runtime/finance_rules.json")
            exit(0)
        print(f"\n{'ID':25s} {'Type':16s} {'Asset':8s} {'Threshold':>10s} {'Enabled':>8s} {'Last Trigger':>20s}")
        print("-" * 95)
        for r in rules:
            rid = r.get("id", "?")
            last = state.get(rid, {}).get("last_triggered", "never")
            if last != "never":
                last = last[:19]
            print(f"{rid:25s} {r.get('type', '?'):16s} {r.get('asset', '-'):8s} "
                  f"{r.get('threshold', 0):>10g} {'yes' if r.get('enabled', True) else 'no':>8s} "
                  f"{last:>20s}")
        exit(0)

    if "--check" in sys.argv or "--test" in sys.argv:
        # Need current data
        sys.path.insert(0, str(BASE_DIR))
        from finance_exporter import fetch_binance, fetch_iol_live, fetch_usd_ars_rate, _calc_drawdown, _load_ath_state
        from finance_positions import build_unified_positions

        b = fetch_binance()
        iol = fetch_iol_live()
        rate = fetch_usd_ars_rate() or 0
        positions = build_unified_positions(b, iol, rate)
        ath = _load_ath_state()
        dd = _calc_drawdown(b["total_usd"] if b else 0, ath.get("binance_ath_usd", 0))
        pnl = (b["pnl_usd"] if b else 0) + (iol.get("pnl_day_usd_native", 0) if iol else 0)
        if iol and rate > 0:
            pnl += iol.get("pnl_day_ars_native", 0) / rate

        if "--test" in sys.argv:
            test_id = sys.argv[sys.argv.index("--test") + 1] if len(sys.argv) > sys.argv.index("--test") + 1 else None
            rules = load_rules()
            for r in rules:
                if r.get("id") == test_id:
                    result = evaluate_rule(r, positions, dd, pnl)
                    print(f"Rule '{test_id}': {'TRIGGERED' if result else 'not triggered'}")
                    print(f"  Type: {r.get('type')}  Asset: {r.get('asset')}  Threshold: {r.get('threshold')}")
                    exit(0)
            print(f"Rule '{test_id}' not found")
            exit(1)

        # Dry-run all rules (ignore cooldowns)
        rules = load_rules()
        print(f"\nDry-run evaluation ({len(positions)} positions, DD={dd:.1f}%, PNL=${pnl:+,.2f})\n")
        for r in rules:
            if not r.get("enabled", True):
                print(f"  [{r['id']}] DISABLED")
                continue
            result = evaluate_rule(r, positions, dd, pnl)
            status = "TRIGGERED" if result else "ok"
            print(f"  [{r['id']}] {status} — {r.get('message', '')}")
        exit(0)
