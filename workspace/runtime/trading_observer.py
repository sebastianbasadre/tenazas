#!/usr/bin/env python3
"""
Trading Observer — Phase 1: Passive market observation.

Public APIs only, NO authentication. Observes BTC, ETH, BNB prices,
fear & greed index, calculates MA7/MA30, volatility.

SAFETY: This module NEVER imports or uses API keys. Public endpoints only.

Usage:
    python3 trading_observer.py --observe    # fetch + save
    python3 trading_observer.py --status     # show current
"""

import json
import math
import time
from datetime import datetime, timedelta
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import URLError

BASE_DIR = Path(__file__).parent
STATE_DIR = Path("/home/tenazas/.openclaw/state") if Path("/home/tenazas").exists() else BASE_DIR.parent / "state"
TRADING_DIR = STATE_DIR / "trading"
OBS_FILE = TRADING_DIR / "market_observations.json"
LOG_FILE = BASE_DIR / "logs" / "trading_observer.log"

# Public endpoints — NO API keys
BINANCE_TICKER = "https://api.binance.com/api/v3/ticker/24hr?symbol={symbol}"
BINANCE_KLINES = "https://api.binance.com/api/v3/klines?symbol={symbol}&interval=1h&limit=30"
FNG_URL = "https://api.alternative.me/fng/"
SYMBOLS = ["BTCUSDT", "ETHUSDT", "BNBUSDT"]
MAX_OBSERVATIONS = 720  # 30 days × 24h


def _now(): return datetime.now().astimezone()

def _log(e):
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    e["ts"] = _now().isoformat()
    try:
        with open(LOG_FILE, "a") as f: f.write(json.dumps(e, default=str) + "\n")
    except OSError: pass

def _fetch_json(url: str, timeout: int = 10):
    try:
        resp = urlopen(Request(url, headers={"User-Agent": "Tenazas/1.0"}), timeout=timeout)
        return json.loads(resp.read())
    except Exception:
        return None

def _load_observations() -> list:
    try:
        with open(OBS_FILE) as f: return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError): return []

def _save_observations(obs: list):
    TRADING_DIR.mkdir(parents=True, exist_ok=True)
    with open(OBS_FILE, "w") as f: json.dump(obs[-MAX_OBSERVATIONS:], f, indent=2, default=str)


def fetch_ticker(symbol: str) -> dict | None:
    """Fetch 24h ticker from Binance public API."""
    data = _fetch_json(BINANCE_TICKER.format(symbol=symbol))
    if not data:
        return None
    return {
        "symbol": symbol,
        "price": float(data.get("lastPrice", 0)),
        "change_pct": float(data.get("priceChangePercent", 0)),
        "volume": float(data.get("volume", 0)),
        "high_24h": float(data.get("highPrice", 0)),
        "low_24h": float(data.get("lowPrice", 0)),
    }


def fetch_fear_greed() -> dict | None:
    """Fetch Fear & Greed Index (public, no key)."""
    data = _fetch_json(FNG_URL)
    if not data or not data.get("data"):
        return None
    fng = data["data"][0]
    return {
        "value": int(fng.get("value", 50)),
        "classification": fng.get("value_classification", "Neutral"),
    }


def calc_moving_averages(observations: list, symbol: str = "BTCUSDT") -> dict:
    """Calculate MA7 and MA30 from historical observations."""
    prices = [o["tickers"].get(symbol, {}).get("price", 0)
              for o in observations if "tickers" in o]
    prices = [p for p in prices if p > 0]

    ma7 = sum(prices[-7:]) / max(len(prices[-7:]), 1) if len(prices) >= 7 else 0
    ma30 = sum(prices[-30:]) / max(len(prices[-30:]), 1) if len(prices) >= 30 else 0

    return {"ma7": round(ma7, 2), "ma30": round(ma30, 2)}


def calc_volatility(observations: list, symbol: str = "BTCUSDT") -> float:
    """Standard deviation of last 24 hourly prices."""
    prices = [o["tickers"].get(symbol, {}).get("price", 0)
              for o in observations[-24:] if "tickers" in o]
    prices = [p for p in prices if p > 0]
    if len(prices) < 3:
        return 0.0
    mean = sum(prices) / len(prices)
    variance = sum((p - mean) ** 2 for p in prices) / len(prices)
    return round(math.sqrt(variance), 2)


def classify_trend(price: float, ma7: float, ma30: float) -> str:
    if ma7 == 0 or ma30 == 0:
        return "unknown"
    if price > ma7 > ma30:
        return "bullish"
    if price < ma7 < ma30:
        return "bearish"
    return "sideways"


def classify_volatility(vol: float, price: float) -> str:
    if price == 0:
        return "unknown"
    pct = (vol / price) * 100
    if pct > 3:
        return "high"
    if pct > 1:
        return "medium"
    return "low"


# ── Main API ────────────────────────────────────────────────────


def observe() -> dict:
    """Fetch all market data and save observation."""
    observation = {"ts": _now().isoformat(), "tickers": {}, "fear_greed": None}

    # Fetch tickers
    for symbol in SYMBOLS:
        ticker = fetch_ticker(symbol)
        if ticker:
            observation["tickers"][symbol] = ticker

    # Fetch fear & greed
    fng = fetch_fear_greed()
    if fng:
        observation["fear_greed"] = fng

    # Load history and calculate derived metrics
    history = _load_observations()
    history.append(observation)

    mas = calc_moving_averages(history)
    btc_price = observation["tickers"].get("BTCUSDT", {}).get("price", 0)
    vol = calc_volatility(history)

    observation["derived"] = {
        "ma7": mas["ma7"],
        "ma30": mas["ma30"],
        "trend": classify_trend(btc_price, mas["ma7"], mas["ma30"]),
        "volatility": vol,
        "volatility_class": classify_volatility(vol, btc_price),
    }

    _save_observations(history)
    _log({"action": "observe", "btc": btc_price,
          "fng": fng["value"] if fng else None,
          "trend": observation["derived"]["trend"]})

    return observation


def get_latest() -> dict:
    obs = _load_observations()
    return obs[-1] if obs else {}


def get_history(hours: int = 24) -> list:
    obs = _load_observations()
    return obs[-hours:]


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="Trading Observer")
    p.add_argument("--observe", action="store_true")
    p.add_argument("--status", action="store_true")
    args = p.parse_args()

    if args.observe:
        o = observe()
        btc = o["tickers"].get("BTCUSDT", {})
        fng = o.get("fear_greed", {})
        d = o.get("derived", {})
        print(f"BTC: ${btc.get('price', 0):,.0f} ({btc.get('change_pct', 0):+.1f}%)")
        print(f"Fear & Greed: {fng.get('value', '?')} ({fng.get('classification', '?')})")
        print(f"Trend: {d.get('trend', '?')} | Vol: {d.get('volatility_class', '?')}")
    elif args.status:
        o = get_latest()
        print(json.dumps(o, indent=2, default=str))
    else:
        p.print_help()
