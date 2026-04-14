#!/usr/bin/env python3
"""
Trading Journal — Phase 2: Automatic daily market journaling.

Generates daily entry with prices, sentiment, portfolio. Sends to Telegram at 20:00.
SAFETY: Public data only. No API keys used.

Usage:
    python3 trading_journal.py --log        # generate + send today's entry
    python3 trading_journal.py --show       # show today's entry
"""

import json
from datetime import datetime
from pathlib import Path
from urllib.request import Request, urlopen

BASE_DIR = Path(__file__).parent
STATE_DIR = Path("/home/tenazas/.openclaw/state") if Path("/home/tenazas").exists() else BASE_DIR.parent / "state"
JOURNAL_DIR = STATE_DIR / "trading" / "journal"
LOG_FILE = BASE_DIR / "logs" / "trading_journal.log"
CONFIG_PATH = Path("/home/tenazas/.openclaw/openclaw.json") if Path("/home/tenazas").exists() else BASE_DIR.parent / "state" / "openclaw.json"
CHAT_ID = "1565794551"


def _now(): return datetime.now().astimezone()

def _log(e):
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    e["ts"] = _now().isoformat()
    try:
        with open(LOG_FILE, "a") as f: f.write(json.dumps(e, default=str) + "\n")
    except OSError: pass


def generate_entry(date: str = None) -> dict:
    """Generate daily journal entry from trading_observer data."""
    if not date:
        date = _now().strftime("%Y-%m-%d")

    entry = {
        "date": date,
        "btc_open": 0, "btc_close": 0, "btc_change_pct": 0,
        "eth_close": 0, "eth_change_pct": 0,
        "fear_greed": 0, "trend": "unknown", "volatility": "unknown",
        "notable_events": [],
        "my_portfolio_value": 0, "my_portfolio_change_pct": 0,
        "notes": "",
    }

    try:
        from trading_observer import get_latest, get_history
        latest = get_latest()
        if latest:
            btc = latest.get("tickers", {}).get("BTCUSDT", {})
            eth = latest.get("tickers", {}).get("ETHUSDT", {})
            fng = latest.get("fear_greed", {})
            derived = latest.get("derived", {})

            entry["btc_close"] = btc.get("price", 0)
            entry["btc_change_pct"] = btc.get("change_pct", 0)
            entry["eth_close"] = eth.get("price", 0)
            entry["eth_change_pct"] = eth.get("change_pct", 0)
            entry["fear_greed"] = fng.get("value", 0) if fng else 0
            entry["trend"] = derived.get("trend", "unknown")
            entry["volatility"] = derived.get("volatility_class", "unknown")

            # Notable events
            if abs(entry["btc_change_pct"]) > 5:
                entry["notable_events"].append(f"BTC moved {entry['btc_change_pct']:+.1f}%")
            if entry["fear_greed"] < 25:
                entry["notable_events"].append("Extreme Fear")
            elif entry["fear_greed"] > 75:
                entry["notable_events"].append("Extreme Greed")

            # Open price from history
            history = get_history(24)
            if history:
                first_btc = history[0].get("tickers", {}).get("BTCUSDT", {})
                entry["btc_open"] = first_btc.get("price", entry["btc_close"])
    except Exception:
        pass

    # Portfolio from finance_daily (real portfolio, not paper)
    try:
        from finance_daily import get_binance_pnl
        pnl = get_binance_pnl()
        if pnl:
            entry["my_portfolio_value"] = pnl.get("total_usdt", 0)
            entry["my_portfolio_change_pct"] = pnl.get("change_pct", 0)
    except Exception:
        pass

    # Notes via LLM (best effort)
    entry["notes"] = _generate_notes(entry)

    # Save
    JOURNAL_DIR.mkdir(parents=True, exist_ok=True)
    with open(JOURNAL_DIR / f"{date}.json", "w") as f:
        json.dump(entry, f, indent=2, default=str)

    _log({"action": "entry_generated", "date": date, "btc": entry["btc_close"]})
    return entry


def _generate_notes(entry: dict) -> str:
    """Generate brief narrative via Ollama (best effort, fallback to template)."""
    try:
        payload = json.dumps({
            "model": "qwen2.5:3b",
            "prompt": (f"Resumen de mercado crypto en 2 lineas. "
                       f"BTC: ${entry['btc_close']:,.0f} ({entry['btc_change_pct']:+.1f}%), "
                       f"Fear&Greed: {entry['fear_greed']}, trend: {entry['trend']}."),
            "stream": False,
            "options": {"temperature": 0.3, "num_predict": 100},
        }).encode()
        resp = urlopen(Request("http://192.168.2.6:11434/api/generate",
                               data=payload, headers={"Content-Type": "application/json"}),
                       timeout=15)
        text = json.loads(resp.read()).get("response", "")
        return text.strip()[:200] if text else ""
    except Exception:
        return f"BTC {entry['btc_change_pct']:+.1f}%, F&G {entry['fear_greed']}, {entry['trend']}."


def format_telegram(entry: dict) -> str:
    lines = [f"Trading Journal — {entry['date']}"]
    lines.append(f"BTC: ${entry['btc_close']:,.0f} ({entry['btc_change_pct']:+.1f}%)")
    if entry["eth_close"]:
        lines.append(f"ETH: ${entry['eth_close']:,.0f} ({entry['eth_change_pct']:+.1f}%)")
    lines.append(f"F&G: {entry['fear_greed']} | Trend: {entry['trend']} | Vol: {entry['volatility']}")
    if entry["notable_events"]:
        lines.append("Events: " + ", ".join(entry["notable_events"]))
    if entry["my_portfolio_value"]:
        lines.append(f"Portfolio: ${entry['my_portfolio_value']:,.0f} ({entry['my_portfolio_change_pct']:+.1f}%)")
    if entry["notes"]:
        lines.append(entry["notes"][:100])
    return "\n".join(lines)


def send_daily():
    entry = generate_entry()
    msg = format_telegram(entry)
    try:
        token = json.loads(CONFIG_PATH.read_text())["channels"]["telegram"]["botToken"]
        payload = json.dumps({"chat_id": CHAT_ID, "text": msg[:4090]}).encode()
        urlopen(Request(f"https://api.telegram.org/bot{token}/sendMessage",
                        data=payload, headers={"Content-Type": "application/json"}), timeout=10)
    except Exception as e:
        _log({"action": "telegram_error", "error": str(e)[:200]})


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="Trading Journal")
    p.add_argument("--log", action="store_true")
    p.add_argument("--show", action="store_true")
    args = p.parse_args()

    if args.log:
        entry = generate_entry()
        print(format_telegram(entry))
        send_daily()
    elif args.show:
        entry = generate_entry()
        print(json.dumps(entry, indent=2, default=str))
    else:
        p.print_help()
