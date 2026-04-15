#!/usr/bin/env python3
"""
Finance Exporter — Prometheus metrics on :9109.

Exports:
  # Portfolio general
  tenazas_iol_portfolio_ars              — IOL portfolio total ARS
  tenazas_iol_pnl_day_ars               — IOL PNL del dia ARS
  tenazas_binance_total_usd             — Binance portfolio total USD
  tenazas_binance_pnl_day_usd           — Binance PNL del dia USD
  tenazas_binance_pnl_day_pct           — Binance PNL del dia %

  # Per asset (label: asset)
  tenazas_binance_asset_balance_usd     — valor USD de cada asset
  tenazas_binance_asset_qty             — cantidad de cada asset
  tenazas_binance_asset_price_usd       — precio actual de cada asset
  tenazas_binance_asset_change_24h_pct  — variacion 24h % de cada asset

  # BTC especifico
  tenazas_binance_btc_price_usd         — precio BTC actual
  tenazas_binance_btc_dominance_pct     — BTC dominance (CoinGecko)
  tenazas_binance_fear_greed_index      — Fear & Greed (alternative.me)

  # Paper trading
  tenazas_paper_portfolio_usd           — valor portfolio paper trading
  tenazas_paper_win_rate_pct            — win rate %
  tenazas_paper_total_trades            — trades completados

Sources:
  - Binance API (signed, via gluetun VPN proxy)
  - CoinGecko /api/v3/global (public)
  - alternative.me /fng/ (public)
  - state/finance_history/*.json (IOL snapshots)
  - state/trading/paper_trades.json

Usage: python3 finance_exporter.py &
"""

import hashlib
import hmac
import json
import logging
import time
import urllib.request
from pathlib import Path
from urllib.parse import urlencode
from urllib.error import URLError

log = logging.getLogger("finance_exporter")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

# ── Paths ─────────────────────────────────────────────────────

BASE_DIR = Path(__file__).parent
STATE_DIR = (Path("/home/tenazas/.openclaw/state")
             if Path("/home/tenazas").exists()
             else BASE_DIR.parent / "state")
HISTORY_DIR = STATE_DIR / "finance_history"
TENAZAS_CONFIG = (Path("/home/tenazas/.openclaw/tenazas.json")
                  if Path("/home/tenazas").exists()
                  else BASE_DIR.parent.parent / "state" / "tenazas.json")
PAPER_TRADES = STATE_DIR / "trading" / "paper_trades.json"

GLUETUN_PROXY = "http://192.168.2.2:8888"
POLL_INTERVAL = 300  # 5 minutes
PORT = 9109

# ── Prometheus setup ──────────────────────────────────────────

try:
    from prometheus_client import Gauge, start_http_server
    PROM_AVAILABLE = True
except ImportError:
    PROM_AVAILABLE = False

if PROM_AVAILABLE:
    # IOL
    G_IOL_TOTAL = Gauge("tenazas_iol_portfolio_ars", "IOL portfolio total ARS")
    G_IOL_PNL = Gauge("tenazas_iol_pnl_day_ars", "IOL PNL del dia ARS")

    # Binance portfolio
    G_BIN_TOTAL = Gauge("tenazas_binance_total_usd", "Binance portfolio total USD")
    G_BIN_PNL = Gauge("tenazas_binance_pnl_day_usd", "Binance PNL del dia USD")
    G_BIN_PNL_PCT = Gauge("tenazas_binance_pnl_day_pct", "Binance PNL del dia pct")

    # Per-asset (labeled)
    G_ASSET_BAL = Gauge("tenazas_binance_asset_balance_usd",
                        "Valor USD de cada asset", ["asset"])
    G_ASSET_QTY = Gauge("tenazas_binance_asset_qty",
                        "Cantidad de cada asset", ["asset"])
    G_ASSET_PRICE = Gauge("tenazas_binance_asset_price_usd",
                          "Precio actual USD de cada asset", ["asset"])
    G_ASSET_CHG = Gauge("tenazas_binance_asset_change_24h_pct",
                        "Variacion 24h pct de cada asset", ["asset"])

    # BTC specific
    G_BTC_PRICE = Gauge("tenazas_binance_btc_price_usd", "BTC price USD")
    G_BTC_DOM = Gauge("tenazas_binance_btc_dominance_pct", "BTC dominance pct")
    G_FEAR_GREED = Gauge("tenazas_binance_fear_greed_index", "Fear and Greed index 0-100")

    # Paper trading
    G_PAPER_TOTAL = Gauge("tenazas_paper_portfolio_usd", "Paper trading portfolio USD")
    G_PAPER_WINRATE = Gauge("tenazas_paper_win_rate_pct", "Paper trading win rate pct")
    G_PAPER_TRADES = Gauge("tenazas_paper_total_trades", "Paper trading total trades")


# ── Helpers ───────────────────────────────────────────────────

def _load_tenazas_config() -> dict:
    try:
        return json.loads(TENAZAS_CONFIG.read_text())
    except Exception:
        return {}


def _binance_sign(params: dict, secret: str) -> str:
    query = urlencode(params)
    sig = hmac.new(secret.encode(), query.encode(), hashlib.sha256).hexdigest()
    return query + f"&signature={sig}"


def _http_get(url: str, headers: dict | None = None,
              use_proxy: bool = False, timeout: int = 10) -> bytes | None:
    """GET request with optional VPN proxy. Returns None on failure."""
    try:
        req = urllib.request.Request(url, headers=headers or {})
        if use_proxy:
            proxy = urllib.request.ProxyHandler({"https": GLUETUN_PROXY,
                                                 "http": GLUETUN_PROXY})
            opener = urllib.request.build_opener(proxy)
            resp = opener.open(req, timeout=timeout)
        else:
            resp = urllib.request.urlopen(req, timeout=timeout)
        return resp.read()
    except Exception as e:
        log.warning("HTTP GET failed %s: %s", url[:80], e)
        return None


# ── Data fetchers ─────────────────────────────────────────────

def fetch_binance() -> dict | None:
    """Fetch Binance portfolio via signed API. Returns structured dict or None."""
    cfg = _load_tenazas_config()
    api_key = cfg.get("binance_api_key", "")
    secret = cfg.get("binance_secret", "")
    if not api_key or not secret:
        return None

    try:
        # Account balances (signed)
        params = {"timestamp": int(time.time() * 1000)}
        signed = _binance_sign(params, secret)
        raw = _http_get(
            f"https://api.binance.com/api/v3/account?{signed}",
            headers={"X-MBX-APIKEY": api_key},
            use_proxy=True)
        if not raw:
            return None
        account = json.loads(raw)

        # Filter assets with balance > 0
        assets = []
        for b in account.get("balances", []):
            total = float(b.get("free", 0)) + float(b.get("locked", 0))
            if total > 0.0001:
                assets.append({"asset": b["asset"], "total": total})

        if not assets:
            return {"assets": [], "total_usd": 0, "pnl_usd": 0, "pnl_pct": 0}

        # 24h tickers for prices and changes
        raw2 = _http_get("https://api.binance.com/api/v3/ticker/24hr",
                         use_proxy=True, timeout=15)
        if not raw2:
            return None
        tickers = {t["symbol"]: t for t in json.loads(raw2)}

        total_usd = 0.0
        total_pnl = 0.0
        details = []
        stables = {"USDT", "USDC", "BUSD", "FDUSD", "DAI"}

        for a in assets:
            sym = a["asset"]
            if sym in stables:
                total_usd += a["total"]
                details.append({
                    "asset": sym, "qty": a["total"], "price": 1.0,
                    "value_usd": round(a["total"], 2), "change_24h_pct": 0.0,
                })
                continue

            ticker = tickers.get(sym + "USDT")
            if not ticker:
                continue

            price = float(ticker.get("lastPrice", 0))
            change_pct = float(ticker.get("priceChangePercent", 0))
            value = a["total"] * price

            if value < 1.0:
                continue  # Skip dust (< $1)

            pnl = value * (change_pct / 100) / (1 + change_pct / 100)
            total_usd += value
            total_pnl += pnl
            details.append({
                "asset": sym, "qty": a["total"], "price": price,
                "value_usd": round(value, 2),
                "change_24h_pct": round(change_pct, 2),
            })

        details.sort(key=lambda x: -x.get("value_usd", 0))
        pnl_pct = (total_pnl / (total_usd - total_pnl) * 100) if total_usd > total_pnl else 0

        return {
            "assets": details,
            "total_usd": round(total_usd, 2),
            "pnl_usd": round(total_pnl, 2),
            "pnl_pct": round(pnl_pct, 2),
        }

    except Exception as e:
        log.warning("Binance fetch failed: %s", e)
        return None


def fetch_fear_greed() -> int | None:
    """Fetch Fear & Greed index from alternative.me (public, no key)."""
    raw = _http_get("https://api.alternative.me/fng/?limit=1")
    if not raw:
        return None
    try:
        data = json.loads(raw)
        return int(data["data"][0]["value"])
    except Exception:
        return None


def fetch_btc_dominance() -> float | None:
    """Fetch BTC dominance from CoinGecko (public, no key)."""
    raw = _http_get("https://api.coingecko.com/api/v3/global")
    if not raw:
        return None
    try:
        data = json.loads(raw)
        return round(data["data"]["market_cap_percentage"]["btc"], 2)
    except Exception:
        return None


def fetch_iol_from_snapshot() -> dict | None:
    """Load IOL data from most recent finance_history snapshot."""
    if not HISTORY_DIR.exists():
        return None
    files = sorted(HISTORY_DIR.glob("*.json"), reverse=True)
    for f in files[:3]:
        try:
            snap = json.loads(f.read_text())
            return snap.get("iol")
        except Exception:
            continue
    return None


def fetch_paper_trading() -> dict | None:
    """Load paper trading state."""
    if not PAPER_TRADES.exists():
        return None
    try:
        data = json.loads(PAPER_TRADES.read_text())
        trades = data.get("trades", [])
        closed = [t for t in trades if t.get("status") == "closed"]
        wins = sum(1 for t in closed if t.get("pnl", 0) > 0)
        total_value = data.get("portfolio_usd", 0)
        win_rate = (wins / len(closed) * 100) if closed else 0

        return {
            "portfolio_usd": total_value,
            "win_rate_pct": round(win_rate, 1),
            "total_trades": len(closed),
        }
    except Exception:
        return None


# ── Metric update ─────────────────────────────────────────────

# Track previous asset labels to clear stale ones
_prev_assets: set = set()


def update_metrics():
    """Fetch all data and update Prometheus gauges."""
    global _prev_assets
    if not PROM_AVAILABLE:
        return

    # ── IOL (from snapshot) ───────────────────────────────────
    iol = fetch_iol_from_snapshot()
    if iol:
        G_IOL_TOTAL.set(iol.get("total_ars", 0))
        G_IOL_PNL.set(iol.get("pnl_day_ars", 0))

    # ── Binance (live API) ────────────────────────────────────
    binance = fetch_binance()
    if binance:
        G_BIN_TOTAL.set(binance["total_usd"])
        G_BIN_PNL.set(binance["pnl_usd"])
        G_BIN_PNL_PCT.set(binance["pnl_pct"])

        current_assets = set()
        for a in binance["assets"]:
            sym = a["asset"]
            current_assets.add(sym)
            G_ASSET_BAL.labels(asset=sym).set(a["value_usd"])
            G_ASSET_QTY.labels(asset=sym).set(a["qty"])
            G_ASSET_PRICE.labels(asset=sym).set(a["price"])
            G_ASSET_CHG.labels(asset=sym).set(a["change_24h_pct"])

            if sym == "BTC":
                G_BTC_PRICE.set(a["price"])

        # Clear stale asset labels
        for old in _prev_assets - current_assets:
            try:
                G_ASSET_BAL.remove(old)
                G_ASSET_QTY.remove(old)
                G_ASSET_PRICE.remove(old)
                G_ASSET_CHG.remove(old)
            except Exception:
                pass
        _prev_assets = current_assets

        log.info("Binance updated: $%.2f (%+.2f USD, %+.2f%%)",
                 binance["total_usd"], binance["pnl_usd"], binance["pnl_pct"])

    # ── Market indicators ─────────────────────────────────────
    fg = fetch_fear_greed()
    if fg is not None:
        G_FEAR_GREED.set(fg)

    dom = fetch_btc_dominance()
    if dom is not None:
        G_BTC_DOM.set(dom)

    # ── Paper trading ─────────────────────────────────────────
    paper = fetch_paper_trading()
    if paper:
        G_PAPER_TOTAL.set(paper["portfolio_usd"])
        G_PAPER_WINRATE.set(paper["win_rate_pct"])
        G_PAPER_TRADES.set(paper["total_trades"])


# ── Main ──────────────────────────────────────────────────────

if __name__ == "__main__":
    if not PROM_AVAILABLE:
        # Dry run: fetch and print
        print("prometheus_client not available — dry run mode")
        print("\n== Binance ==")
        b = fetch_binance()
        if b:
            print(f"Total: ${b['total_usd']:,.2f}  PNL: ${b['pnl_usd']:+,.2f} ({b['pnl_pct']:+.2f}%)")
            for a in b["assets"][:10]:
                print(f"  {a['asset']:6s} qty={a['qty']:.6f}  "
                      f"price=${a['price']:,.2f}  "
                      f"value=${a['value_usd']:,.2f}  "
                      f"24h={a['change_24h_pct']:+.2f}%")
        else:
            print("  (no data)")

        print("\n== Market ==")
        fg = fetch_fear_greed()
        dom = fetch_btc_dominance()
        print(f"  Fear & Greed: {fg}")
        print(f"  BTC Dominance: {dom}%")

        print("\n== Paper Trading ==")
        p = fetch_paper_trading()
        print(f"  {p}" if p else "  (no data)")

        print("\n== IOL (snapshot) ==")
        iol = fetch_iol_from_snapshot()
        print(f"  {iol}" if iol else "  (no data)")
        exit(0)

    start_http_server(PORT)
    log.info("Finance exporter on :%d (poll every %ds)", PORT, POLL_INTERVAL)

    while True:
        try:
            update_metrics()
        except Exception as e:
            log.warning("Update cycle failed: %s", e)
        time.sleep(POLL_INTERVAL)
