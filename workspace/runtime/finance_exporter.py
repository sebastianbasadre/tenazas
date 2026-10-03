#!/usr/bin/env python3
"""
Finance Exporter — Prometheus metrics on :9109.

Exports:
  # Portfolio general
  tenazas_iol_portfolio_ars              — IOL portfolio total ARS
  tenazas_iol_pnl_day_ars               — IOL PNL del dia ARS
  tenazas_binance_total_usd             — Binance portfolio total USD
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
  - IOL API live (via iol_client), snapshot fallback
  - state/trading/paper_trades.json

Usage: python3 finance_exporter.py &
"""

import hashlib
import hmac
import json
import logging
import math
import os
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode
from urllib.error import URLError
from iol_dependency import status as iol_dependency_status

try:
    from runtime_config import GLUETUN_PROXY
except ImportError:
    from workspace.runtime.runtime_config import GLUETUN_PROXY

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
ATH_FILE = STATE_DIR / "finance_ath.json"
PORTFOLIO_HISTORY_FILE = STATE_DIR / "finance_portfolio_history.json"

POLL_INTERVAL = 60  # 1 minute
PORT = 9109
OPENCLAW_CONFIG = (Path("/home/tenazas/.openclaw/openclaw.json")
                   if Path("/home/tenazas").exists()
                   else BASE_DIR.parent.parent / "state" / "openclaw.json")
CHAT_ID = "1565794551"
PNL_NOTIFY_INTERVAL = 3600  # 1 hour
ENABLE_FINANCE_EXPORTER_TELEGRAM = (
    os.getenv("ENABLE_FINANCE_EXPORTER_TELEGRAM", "false").strip().lower()
    in {"1", "true", "yes", "on"}
)

# ── Prometheus setup ──────────────────────────────────────────

try:
    from prometheus_client import Gauge, start_http_server
    PROM_AVAILABLE = True
except ImportError:
    PROM_AVAILABLE = False

if PROM_AVAILABLE:
    G_IOL_DEGRADED = Gauge("tenazas_iol_degraded", "IOL unavailable, stale or using a snapshot")
    G_IOL_FRESHNESS = Gauge("tenazas_iol_last_success_timestamp", "Last verified live IOL read")
    # IOL totals
    G_IOL_TOTAL = Gauge("tenazas_iol_portfolio_ars", "IOL portfolio total ARS")
    G_IOL_PNL = Gauge("tenazas_iol_pnl_day_ars", "IOL PNL del dia ARS")
    G_IOL_TOTAL_USD = Gauge("tenazas_iol_portfolio_usd_native", "IOL portfolio total USD nativo")
    G_IOL_PNL_USD = Gauge("tenazas_iol_pnl_day_usd_native", "IOL PNL del dia USD nativo")

    # IOL cash balances
    G_IOL_CASH_ARS = Gauge("tenazas_iol_cash_ars", "IOL cash disponible ARS")
    G_IOL_CASH_USD = Gauge("tenazas_iol_cash_usd", "IOL cash disponible USD")
    G_IOL_CASH_USD_EEUU = Gauge("tenazas_iol_cash_usd_eeuu", "IOL cash USD cuenta EEUU")

    # IOL per-position (labeled)
    G_IOL_POS_VALUE = Gauge("tenazas_iol_position_value_ars",
                            "Valor ARS de cada posicion IOL", ["symbol", "type"])
    G_IOL_POS_QTY = Gauge("tenazas_iol_position_qty",
                          "Cantidad de cada posicion IOL", ["symbol", "type"])
    G_IOL_POS_PRICE = Gauge("tenazas_iol_position_price",
                            "Precio actual de cada posicion IOL", ["symbol", "type"])
    G_IOL_POS_PPC = Gauge("tenazas_iol_position_ppc",
                          "Precio promedio de compra IOL", ["symbol", "type"])
    G_IOL_POS_GAIN_PCT = Gauge("tenazas_iol_position_gain_pct",
                               "Ganancia total % de cada posicion IOL", ["symbol", "type"])
    G_IOL_POS_CHANGE_DAY = Gauge("tenazas_iol_position_change_day_pct",
                                 "Variacion diaria % de cada posicion IOL", ["symbol", "type"])
    G_IOL_POS_PNL_DAY = Gauge("tenazas_iol_position_pnl_day_ars",
                              "PNL diario ARS de cada posicion IOL", ["symbol", "type"])
    G_IOL_POS_VALUE_NATIVE = Gauge("tenazas_iol_position_value_native",
                                   "Valor nativo de cada posicion IOL", ["symbol", "type", "currency"])
    G_IOL_POS_PNL_DAY_NATIVE = Gauge("tenazas_iol_position_pnl_day_native",
                                     "PNL diario nativo de cada posicion IOL", ["symbol", "type", "currency"])

    # Binance portfolio
    G_BIN_TOTAL = Gauge("tenazas_binance_total_usd", "Binance portfolio total USD")
    # Per-asset (labeled with wallet)
    G_ASSET_BAL = Gauge("tenazas_binance_asset_balance_usd",
                        "Valor USD de cada asset", ["asset", "wallet"])
    G_ASSET_QTY = Gauge("tenazas_binance_asset_qty",
                        "Cantidad de cada asset", ["asset", "wallet"])
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

    # Phase 1: Portfolio-level metrics
    G_PORTFOLIO_TOTAL_USD = Gauge("tenazas_portfolio_total_usd", "Combined portfolio value USD")
    G_USD_ARS_RATE = Gauge("tenazas_usd_ars_rate", "USD/ARS blue rate")
    G_BIN_DRAWDOWN = Gauge("tenazas_portfolio_drawdown_pct", "Binance drawdown from ATH pct")
    G_IOL_DRAWDOWN = Gauge("tenazas_iol_drawdown_pct", "IOL drawdown from ATH pct")
    G_BIN_ALLOC = Gauge("tenazas_binance_allocation_pct", "Binance allocation pct per asset", ["asset"])
    G_FETCH_TS = Gauge("tenazas_fetch_last_success_ts", "Last successful fetch unix ts", ["source"])
    G_FETCH_ERRORS = Gauge("tenazas_fetch_errors_total", "Fetch error counter", ["source"])

    # Phase 2: Unified positions
    G_POS_VALUE = Gauge("tenazas_position_value_usd", "Unified position value USD", ["symbol", "source"])
    G_POS_CHANGE = Gauge("tenazas_position_change_pct", "Unified position daily change pct", ["symbol", "source"])

    # Phase 3: Rules
    G_RULES_TRIGGERED = Gauge("tenazas_rules_triggered_total", "Total rule triggers")
    G_RULES_ACTIVE = Gauge("tenazas_rules_active", "Number of enabled rules")

    # Phase 4: Indicators
    G_PORT_MA7 = Gauge("tenazas_portfolio_ma7_usd", "Portfolio 7-day moving average USD")
    G_PORT_MA30 = Gauge("tenazas_portfolio_ma30_usd", "Portfolio 30-day moving average USD")
    G_PORT_TREND = Gauge("tenazas_portfolio_trend", "Portfolio trend 1=bull 0=side -1=bear")
    G_PORT_VOL = Gauge("tenazas_portfolio_volatility_pct", "Portfolio volatility pct")
    G_PORT_RETURN = Gauge("tenazas_portfolio_daily_return_pct", "Portfolio daily return pct")


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


# ── Telegram helpers ──────────────────────────────────────────

_last_pnl_notify_hour: int = -1


def _get_bot_token() -> str | None:
    try:
        cfg = json.loads(OPENCLAW_CONFIG.read_text())
        return cfg["channels"]["telegram"]["botToken"]
    except Exception:
        return None


def _send_telegram(text: str):
    token = _get_bot_token()
    if not token:
        log.warning("No Telegram bot token — skipping notification")
        return
    try:
        payload = json.dumps({"chat_id": CHAT_ID, "text": text[:4090],
                              "parse_mode": "Markdown"}).encode()
        urllib.request.urlopen(
            urllib.request.Request(
                f"https://api.telegram.org/bot{token}/sendMessage",
                data=payload,
                headers={"Content-Type": "application/json"}),
            timeout=10)
    except Exception as e:
        log.warning("Telegram send failed: %s", e)


def _format_hourly_binance(binance: dict, btc_price: float, fear_greed: int | None) -> str:
    """Format hourly Binance balance message for Telegram."""
    now = datetime.now().astimezone()
    lines = [f"*Binance* — {now.strftime('%H:%M')}"]

    total = binance["total_usd"]

    lines.append(f"Portfolio: *${total:,.2f}*")

    if btc_price > 0:
        lines.append(f"BTC: ${btc_price:,.0f}")
    if fear_greed is not None:
        lines.append(f"F&G: {fear_greed}")

    # Top movers (top 3 by absolute change)
    assets = [a for a in binance["assets"]
              if a.get("change_24h_pct", 0) != 0 and a["value_usd"] >= 5]
    if assets:
        assets.sort(key=lambda a: abs(a["change_24h_pct"]), reverse=True)
        movers = []
        for a in assets[:3]:
            e = "↑" if a["change_24h_pct"] > 0 else "↓"
            movers.append(f"{a['asset']} {e}{a['change_24h_pct']:+.1f}%")
        lines.append(" | ".join(movers))

    return "\n".join(lines)


def _maybe_send_hourly_binance(binance: dict | None):
    """Send hourly Binance balance notification if a new hour has started."""
    global _last_pnl_notify_hour
    if not ENABLE_FINANCE_EXPORTER_TELEGRAM:
        return
    if not binance:
        return
    now = datetime.now().astimezone()
    current_hour = now.hour
    if current_hour == _last_pnl_notify_hour:
        return
    _last_pnl_notify_hour = current_hour

    btc_price = 0.0
    for a in binance.get("assets", []):
        if a["asset"] == "BTC":
            btc_price = a["price"]
            break
    fg = fetch_fear_greed()
    msg = _format_hourly_binance(binance, btc_price, fg)
    _send_telegram(msg)
    log.info("Hourly Binance balance sent (hour=%d, total=$%.2f)",
             current_hour, binance["total_usd"])


# ── Phase 1: ATH & drawdown ──────────────────────────────────

def _load_ath_state() -> dict:
    try:
        return json.loads(ATH_FILE.read_text())
    except Exception:
        return {"binance_ath_usd": 0, "iol_ath_ars": 0}


def _save_ath_state(state: dict):
    try:
        ATH_FILE.parent.mkdir(parents=True, exist_ok=True)
        ATH_FILE.write_text(json.dumps(state, indent=2))
    except Exception as e:
        log.warning("Failed to save ATH state: %s", e)


def _calc_drawdown(current: float, ath: float) -> float:
    if ath <= 0 or current >= ath:
        return 0.0
    return round((ath - current) / ath * 100, 2)


def fetch_usd_ars_rate() -> float | None:
    """Fetch USD/ARS blue rate. Try dolarapi, fallback bluelytics."""
    for url in ["https://dolarapi.com/v1/dolares/blue",
                "https://api.bluelytics.com.ar/v2/latest"]:
        raw = _http_get(url, headers={"User-Agent": "Tenazas/1.0"}, timeout=5)
        if not raw:
            continue
        try:
            data = json.loads(raw)
            if "venta" in data:
                return float(data["venta"])
            if "blue" in data:
                return float(data["blue"].get("value_sell", 0))
        except Exception:
            continue
    return None


# ── Phase 4: Portfolio history & indicators ──────────────────

_HISTORY_MAX = 720  # ~30 days at 1 sample/hour


def _load_portfolio_history() -> list:
    try:
        return json.loads(PORTFOLIO_HISTORY_FILE.read_text())
    except Exception:
        return []


def _save_portfolio_snapshot(total_usd: float, binance_usd: float, iol_usd: float):
    """Save hourly portfolio snapshot (dedup by hour)."""
    history = _load_portfolio_history()
    now = datetime.now()
    hour_key = now.strftime("%Y-%m-%d %H")
    if history and history[-1].get("hour") == hour_key:
        history[-1] = {"ts": now.isoformat(), "hour": hour_key,
                       "total_usd": total_usd, "binance_usd": binance_usd, "iol_usd": iol_usd}
    else:
        history.append({"ts": now.isoformat(), "hour": hour_key,
                        "total_usd": total_usd, "binance_usd": binance_usd, "iol_usd": iol_usd})
    if len(history) > _HISTORY_MAX:
        history = history[-_HISTORY_MAX:]
    try:
        PORTFOLIO_HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
        PORTFOLIO_HISTORY_FILE.write_text(json.dumps(history))
    except Exception as e:
        log.warning("Failed to save portfolio history: %s", e)


def calc_portfolio_ma(history: list, window_hours: int) -> float | None:
    if len(history) < window_hours:
        return None
    values = [h["total_usd"] for h in history[-window_hours:]]
    return round(sum(values) / len(values), 2)


def calc_portfolio_volatility(history: list, window: int = 24) -> float | None:
    if len(history) < 3:
        return None
    recent = history[-window:] if len(history) >= window else history
    returns = []
    for i in range(1, len(recent)):
        prev = recent[i - 1]["total_usd"]
        if prev > 0:
            returns.append((recent[i]["total_usd"] - prev) / prev * 100)
    if len(returns) < 2:
        return None
    mean = sum(returns) / len(returns)
    variance = sum((r - mean) ** 2 for r in returns) / len(returns)
    return round(math.sqrt(variance), 2)


def classify_portfolio_trend(current: float, ma7: float | None, ma30: float | None) -> int:
    if ma7 is None or ma30 is None:
        return 0
    if current > ma7 > ma30:
        return 1   # bullish
    if current < ma7 < ma30:
        return -1  # bearish
    return 0       # sideways


# ── Module-level state ───────────────────────────────────────

_fetch_errors: dict = {"binance": 0, "iol": 0, "market": 0}
_last_positions: list = []
_last_binance: dict | None = None
_last_iol: dict | None = None
_last_usd_ars: float = 0
_last_drawdown_bin: float = 0
_last_total_pnl_usd: float = 0


# ── Data fetchers ─────────────────────────────────────────────

def _normalize_iol_change_pct(variacion: float, puntos: float, precio: float) -> float:
    """Normalize IOL daily change to percentage points."""
    try:
        variacion = float(variacion or 0)
        puntos = float(puntos or 0)
        precio = float(precio or 0)
    except (TypeError, ValueError):
        return 0.0

    if not variacion:
        return 0.0
    if not puntos or precio <= 0:
        return variacion

    move_as_pct = abs(precio * (variacion / 100.0))
    move_as_fraction = abs(precio * variacion)
    if abs(move_as_fraction - abs(puntos)) < abs(move_as_pct - abs(puntos)):
        return variacion * 100.0
    return variacion


def _normalize_iol_currency(raw_currency: str | None) -> str:
    value = str(raw_currency or "").strip().lower()
    if value in {"dolar_estadounidense", "usd", "dolar"}:
        return "USD"
    if value in {"peso_argentino", "ars", "peso"}:
        return "ARS"
    return value.upper() if value else "ARS"


def _safe_float(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0

def _binance_signed_get(endpoint: str, api_key: str, secret: str,
                        extra_params: dict | None = None) -> dict | list | None:
    """Helper for signed Binance API GET requests."""
    params = {"timestamp": int(time.time() * 1000)}
    if extra_params:
        params.update(extra_params)
    signed = _binance_sign(params, secret)
    raw = _http_get(
        f"https://api.binance.com{endpoint}?{signed}",
        headers={"X-MBX-APIKEY": api_key},
        use_proxy=True)
    return json.loads(raw) if raw else None


def fetch_binance() -> dict | None:
    """Fetch Binance portfolio via signed API (Spot + Earn + Funding)."""
    cfg = _load_tenazas_config()
    api_key = cfg.get("binance_api_key", "")
    secret = cfg.get("binance_secret", "")
    if not api_key or not secret:
        return None

    try:
        # 1) Spot balances
        account = _binance_signed_get("/api/v3/account", api_key, secret)
        if not account:
            return None

        # Merge all wallet sources: {asset: {spot, earn, funding, total}}
        wallets = {}  # asset -> {"spot": qty, "earn": qty, "funding": qty}
        for b in account.get("balances", []):
            total = float(b.get("free", 0)) + float(b.get("locked", 0))
            if total > 0.0001:
                wallets.setdefault(b["asset"], {"spot": 0, "earn": 0, "funding": 0})
                wallets[b["asset"]]["spot"] += total

        # 2) Simple Earn (Flexible + Locked)
        earn_ok = False
        try:
            earn = _binance_signed_get("/sapi/v1/simple-earn/flexible/position",
                                       api_key, secret, {"size": 100})
            rows = earn.get("rows", []) if isinstance(earn, dict) else []
            for pos in rows:
                asset = pos.get("asset", "")
                amt = float(pos.get("totalAmount", 0))
                if asset and amt > 0.0001:
                    wallets.setdefault(asset, {"spot": 0, "earn": 0, "funding": 0})
                    wallets[asset]["earn"] += amt
                    earn_ok = True
        except Exception as e:
            log.warning("Simple Earn flexible fetch failed: %s", e)
        try:
            earn_locked = _binance_signed_get("/sapi/v1/simple-earn/locked/position",
                                              api_key, secret, {"size": 100})
            rows_l = earn_locked.get("rows", []) if isinstance(earn_locked, dict) else []
            for pos in rows_l:
                asset = pos.get("asset", "")
                amt = float(pos.get("amount", 0))
                if asset and amt > 0.0001:
                    wallets.setdefault(asset, {"spot": 0, "earn": 0, "funding": 0})
                    wallets[asset]["earn"] += amt
                    earn_ok = True
        except Exception as e:
            log.warning("Simple Earn locked fetch failed: %s", e)

        # 3) Funding wallet
        try:
            funding = _binance_signed_get("/sapi/v1/asset/get-funding-asset",
                                          api_key, secret)
            for f in (funding if isinstance(funding, list) else []):
                asset = f.get("asset", "")
                amt = float(f.get("free", 0)) + float(f.get("locked", 0)) + float(f.get("freeze", 0))
                if asset and amt > 0.0001:
                    wallets.setdefault(asset, {"spot": 0, "earn": 0, "funding": 0})
                    wallets[asset]["funding"] += amt
        except Exception:
            pass

        # Fallback: if earn fetch failed and we have a previous result with earn
        # data, merge the last-known earn balances to avoid reporting half the portfolio
        if not earn_ok and _last_binance:
            prev_wallets = _last_binance.get("_wallets", {})
            for sym, pw in prev_wallets.items():
                prev_earn = pw.get("earn", 0)
                if prev_earn > 0.0001:
                    wallets.setdefault(sym, {"spot": 0, "earn": 0, "funding": 0})
                    wallets[sym]["earn"] = prev_earn
                    log.info("Earn fallback: using last-known %s earn=%.6f", sym, prev_earn)

        assets = [{"asset": k, "total": v["spot"] + v["earn"] + v["funding"],
                   "wallets": v} for k, v in wallets.items()]

        if not assets:
            return {"assets": [], "total_usd": 0}

        # 24h tickers for prices and changes
        raw2 = _http_get("https://api.binance.com/api/v3/ticker/24hr",
                         use_proxy=True, timeout=15)
        if not raw2:
            return None
        tickers = {t["symbol"]: t for t in json.loads(raw2)}

        total_usd = 0.0
        details = []
        stables = {"USDT", "USDC", "BUSD", "FDUSD", "DAI"}

        for a in assets:
            sym = a["asset"]
            w = a.get("wallets", {"spot": a["total"], "earn": 0, "funding": 0})
            wallet = "earn" if w["earn"] > w["spot"] else (
                     "funding" if w["funding"] > w["spot"] else "spot")
            if sym in stables:
                total_usd += a["total"]
                details.append({
                    "asset": sym, "qty": a["total"], "price": 1.0,
                    "value_usd": round(a["total"], 2), "change_24h_pct": 0.0,
                    "wallet": wallet,
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

            total_usd += value
            details.append({
                "asset": sym, "qty": a["total"], "price": price,
                "value_usd": round(value, 2),
                "change_24h_pct": round(change_pct, 2),
                "wallet": wallet,
            })

        details.sort(key=lambda x: -x.get("value_usd", 0))

        return {
            "assets": details,
            "total_usd": round(total_usd, 2),
            "_wallets": {k: dict(v) for k, v in wallets.items()},
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


def fetch_iol_live(usd_ars_rate: float | None = None) -> dict | None:
    """Fetch IOL portfolio live via iol_client, fallback to snapshot."""
    # Try live API first
    try:
        sys.path.insert(0, "/home/tenazas/.openclaw/workspace/skills/iol_broker")
        from iol_client import IOLClient
        client = IOLClient()
        portfolio = client.get("/api/v2/portafolio/argentina")
        if isinstance(portfolio, dict) and "activos" in portfolio:
            total_ars_equiv = 0.0
            total_usd_native = 0.0
            total_ars_native = 0.0
            pnl_day_ars_equiv = 0.0
            pnl_day_usd_native = 0.0
            pnl_day_ars_native = 0.0
            positions = []
            for a in portfolio["activos"]:
                titulo = a.get("titulo", {})
                cantidad = a.get("cantidad", 0)
                precio = a.get("ultimoPrecio", 0)
                puntos = a.get("puntosVariacion", 0)
                variacion = _normalize_iol_change_pct(a.get("variacionDiaria", 0), puntos, precio)
                valor = a.get("valorizado", cantidad * precio)
                ppc = a.get("ppc", 0)
                gain_pct = a.get("gananciaPorcentaje", 0)
                asset_type = titulo.get("tipo", "")
                currency = _normalize_iol_currency(titulo.get("moneda"))
                day_pnl = puntos * cantidad if puntos else (
                    valor * (variacion / 100) if variacion else 0)
                if currency == "USD":
                    total_usd_native += valor
                    pnl_day_usd_native += day_pnl
                    value_ars_equiv = valor * usd_ars_rate if usd_ars_rate and usd_ars_rate > 0 else 0.0
                    pnl_day_ars = day_pnl * usd_ars_rate if usd_ars_rate and usd_ars_rate > 0 else 0.0
                else:
                    total_ars_native += valor
                    pnl_day_ars_native += day_pnl
                    value_ars_equiv = valor
                    pnl_day_ars = day_pnl
                total_ars_equiv += value_ars_equiv
                pnl_day_ars_equiv += pnl_day_ars
                positions.append({
                    "symbol": titulo.get("simbolo", "?"),
                    "name": titulo.get("descripcion", "?")[:30],
                    "type": asset_type,
                    "currency": currency,
                    "quantity": cantidad,
                    "price": precio,
                    "ppc": ppc,
                    "value": round(valor, 2),
                    "value_ars_equiv": round(value_ars_equiv, 2),
                    "gain_pct": round(gain_pct, 2),
                    "change_pct": round(variacion, 2),
                    "pnl_day": round(day_pnl, 2),
                    "pnl_day_ars": round(pnl_day_ars, 2),
                })
            # Fetch cash balances from estado de cuenta
            # Use 'saldo' (total balance) not 'disponible' (available after holds)
            # because 'comprometido' funds (pending settlement) are still real money
            cash = {"ars": 0, "usd": 0, "usd_eeuu": 0}
            account_total_ars = 0.0
            account_total_usd = 0.0
            implied_usd_ars_rate = 0.0
            try:
                estado = client.get("/api/v2/estadocuenta")
                if estado and "cuentas" in estado:
                    account_total_ars = _safe_float(estado.get("totalEnPesos", 0))
                    for c in estado["cuentas"]:
                        tipo = c.get("tipo", "")
                        saldo = c.get("saldo", 0)
                        if tipo == "inversion_Argentina_Pesos":
                            cash["ars"] = saldo
                        elif tipo == "inversion_Argentina_Dolares":
                            cash["usd"] = saldo
                            account_total_usd += _safe_float(c.get("total", 0))
                        elif tipo == "inversion_Estados_Unidos_Dolares":
                            cash["usd_eeuu"] = saldo
                            account_total_usd += _safe_float(c.get("total", 0))
                    if account_total_ars > 0 and account_total_usd > 0:
                        implied_usd_ars_rate = max(
                            (account_total_ars - _safe_float(cash.get("ars", 0))) / account_total_usd,
                            0.0,
                        )
            except Exception as e:
                log.warning("IOL estado de cuenta failed: %s", e)

            if implied_usd_ars_rate > 0:
                total_ars_equiv = total_ars_native
                pnl_day_ars_equiv = pnl_day_ars_native
                for p in positions:
                    if p.get("currency") == "USD":
                        p["value_ars_equiv"] = round(p.get("value", 0) * implied_usd_ars_rate, 2)
                        p["pnl_day_ars"] = round(p.get("pnl_day", 0) * implied_usd_ars_rate, 2)
                    total_ars_equiv += p.get("value_ars_equiv", 0)
                    pnl_day_ars_equiv += p.get("pnl_day_ars", 0)

            return {
                "source": "live", "degraded": iol_dependency_status()["degraded"],
                "total_ars": round(account_total_ars or total_ars_equiv, 2),
                "total_usd_native": round(account_total_usd or total_usd_native, 2),
                "total_ars_native": round(total_ars_native, 2),
                "pnl_day_ars_native": round(pnl_day_ars_native, 2),
                "pnl_day_ars": round(pnl_day_ars_equiv, 2),
                "pnl_day_usd_native": round(pnl_day_usd_native, 2),
                "ars_equiv_rate": round(implied_usd_ars_rate or _safe_float(usd_ars_rate), 4),
                "positions": positions,
                "cash": cash,
            }
    except Exception as e:
        log.warning("IOL live fetch failed: %s, falling back to snapshot", e)

    # Fallback: read from snapshot
    if not HISTORY_DIR.exists():
        return None
    files = sorted(HISTORY_DIR.glob("*.json"), reverse=True)
    for f in files[:3]:
        try:
            snap = json.loads(f.read_text())
            iol = snap.get("iol")
            if iol:
                return {**iol, "source": "snapshot", "degraded": True,
                        "snapshot_timestamp": f.stat().st_mtime}
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
    global _prev_assets, _last_positions, _last_binance, _last_iol
    global _last_usd_ars, _last_drawdown_bin, _last_total_pnl_usd
    if not PROM_AVAILABLE:
        return

    usd_ars = fetch_usd_ars_rate()
    if usd_ars:
        _last_usd_ars = usd_ars
        G_USD_ARS_RATE.set(usd_ars)

    # ── IOL (live API, snapshot fallback) ────────────────────
    iol = fetch_iol_live(_last_usd_ars)
    dependency = iol_dependency_status()
    G_IOL_DEGRADED.set(bool(dependency["degraded"] or not iol or iol.get("degraded")))
    G_IOL_FRESHNESS.set(dependency.get("last_success_at", 0))
    if iol:
        _last_iol = iol
        G_IOL_TOTAL.set(iol.get("total_ars", 0))
        G_IOL_PNL.set(iol.get("pnl_day_ars", 0))
        G_IOL_TOTAL_USD.set(iol.get("total_usd_native", 0))
        G_IOL_PNL_USD.set(iol.get("pnl_day_usd_native", 0))
        G_FETCH_TS.labels(source="iol").set(time.time())
        for p in iol.get("positions", []):
            sym = p["symbol"]
            atype = p.get("type", "")
            currency = p.get("currency", "ARS")
            G_IOL_POS_VALUE.labels(symbol=sym, type=atype).set(p.get("value_ars_equiv", p["value"]))
            G_IOL_POS_QTY.labels(symbol=sym, type=atype).set(p.get("quantity", 0))
            G_IOL_POS_PRICE.labels(symbol=sym, type=atype).set(p.get("price", 0))
            G_IOL_POS_PPC.labels(symbol=sym, type=atype).set(p.get("ppc", 0))
            G_IOL_POS_GAIN_PCT.labels(symbol=sym, type=atype).set(p.get("gain_pct", 0))
            G_IOL_POS_CHANGE_DAY.labels(symbol=sym, type=atype).set(p.get("change_pct", 0))
            G_IOL_POS_PNL_DAY.labels(symbol=sym, type=atype).set(p.get("pnl_day_ars", p.get("pnl_day", 0)))
            G_IOL_POS_VALUE_NATIVE.labels(symbol=sym, type=atype, currency=currency).set(p.get("value", 0))
            G_IOL_POS_PNL_DAY_NATIVE.labels(symbol=sym, type=atype, currency=currency).set(p.get("pnl_day", 0))
        # Cash balances
        cash = iol.get("cash", {})
        G_IOL_CASH_ARS.set(cash.get("ars", 0))
        G_IOL_CASH_USD.set(cash.get("usd", 0))
        G_IOL_CASH_USD_EEUU.set(cash.get("usd_eeuu", 0))

        log.info("IOL updated: $%.0f ARS eq / US$%.2f native (%+.0f ARS day PNL, %d positions, cash ARS=%.0f USD=%.2f)",
                 iol["total_ars"], iol.get("total_usd_native", 0), iol["pnl_day_ars"],
                 len(iol.get("positions", [])), cash.get("ars", 0), cash.get("usd", 0))
    else:
        _fetch_errors["iol"] += 1
        G_FETCH_ERRORS.labels(source="iol").set(_fetch_errors["iol"])

    # ── Binance (live API) ────────────────────────────────────
    binance = fetch_binance()
    if binance:
        _last_binance = binance
        G_BIN_TOTAL.set(binance["total_usd"])
        G_FETCH_TS.labels(source="binance").set(time.time())

        current_assets = set()
        for a in binance["assets"]:
            sym = a["asset"]
            wallet = a.get("wallet", "spot")
            current_assets.add(sym)
            G_ASSET_BAL.labels(asset=sym, wallet=wallet).set(a["value_usd"])
            G_ASSET_QTY.labels(asset=sym, wallet=wallet).set(a["qty"])
            G_ASSET_PRICE.labels(asset=sym).set(a["price"])
            G_ASSET_CHG.labels(asset=sym).set(a["change_24h_pct"])
            # Allocation %
            if binance["total_usd"] > 0:
                G_BIN_ALLOC.labels(asset=sym).set(
                    round(a["value_usd"] / binance["total_usd"] * 100, 2))

            if sym == "BTC":
                G_BTC_PRICE.set(a["price"])

        # Clear stale asset labels
        for old in _prev_assets - current_assets:
            try:
                G_ASSET_PRICE.remove(old)
                G_ASSET_CHG.remove(old)
                G_BIN_ALLOC.remove(old)
            except Exception:
                pass
        _prev_assets = current_assets

        log.info("Binance updated: $%.2f", binance["total_usd"])

        # Hourly balance notification
        _maybe_send_hourly_binance(binance)
    else:
        _fetch_errors["binance"] += 1
        G_FETCH_ERRORS.labels(source="binance").set(_fetch_errors["binance"])

    # ── Market indicators ─────────────────────────────────────
    fg = fetch_fear_greed()
    if fg is not None:
        G_FEAR_GREED.set(fg)
        G_FETCH_TS.labels(source="market").set(time.time())
    else:
        _fetch_errors["market"] += 1
        G_FETCH_ERRORS.labels(source="market").set(_fetch_errors["market"])

    dom = fetch_btc_dominance()
    if dom is not None:
        G_BTC_DOM.set(dom)

    # ── Paper trading ─────────────────────────────────────────
    paper = fetch_paper_trading()
    if paper:
        G_PAPER_TOTAL.set(paper["portfolio_usd"])
        G_PAPER_WINRATE.set(paper["win_rate_pct"])
        G_PAPER_TRADES.set(paper["total_trades"])

    # ── Phase 1: USD/ARS, ATH, drawdown, combined total ──────
    ath = _load_ath_state()
    binance_usd = binance["total_usd"] if binance else 0
    iol_ars = iol.get("total_ars", 0) if iol else 0
    iol_usd = (
        iol.get("total_usd_native", 0) + ((iol.get("total_ars_native", 0) / _last_usd_ars) if _last_usd_ars > 0 else 0)
    ) if iol else 0
    combined_usd = binance_usd + iol_usd

    if binance_usd > ath.get("binance_ath_usd", 0):
        ath["binance_ath_usd"] = binance_usd
    if iol_ars > ath.get("iol_ath_ars", 0):
        ath["iol_ath_ars"] = iol_ars
    _save_ath_state(ath)

    _last_drawdown_bin = _calc_drawdown(binance_usd, ath.get("binance_ath_usd", 0))
    iol_drawdown = _calc_drawdown(iol_ars, ath.get("iol_ath_ars", 0))
    G_BIN_DRAWDOWN.set(_last_drawdown_bin)
    G_IOL_DRAWDOWN.set(iol_drawdown)
    G_PORTFOLIO_TOTAL_USD.set(round(combined_usd, 2))

    _last_total_pnl_usd = (
        iol.get("pnl_day_usd_native", 0) + ((iol.get("pnl_day_ars_native", 0) / _last_usd_ars) if _last_usd_ars > 0 else 0)
        if iol else 0
    )

    # ── Phase 2: Unified positions ────────────────────────────
    try:
        from finance_positions import build_unified_positions
        _last_positions = build_unified_positions(binance, iol, _last_usd_ars)
        for pos in _last_positions:
            G_POS_VALUE.labels(symbol=pos["symbol"], source=pos["source"]).set(pos["value_usd"])
            G_POS_CHANGE.labels(symbol=pos["symbol"], source=pos["source"]).set(pos.get("change_pct", 0))
    except Exception as e:
        log.warning("Unified positions failed: %s", e)

    # ── Phase 3: Rules evaluation ─────────────────────────────
    try:
        from finance_rules import evaluate_all_rules
        triggered = evaluate_all_rules(_last_positions, _last_drawdown_bin, _last_total_pnl_usd)
        G_RULES_TRIGGERED.inc(len(triggered))
        from finance_rules import load_rules
        enabled = sum(1 for r in load_rules() if r.get("enabled", True))
        G_RULES_ACTIVE.set(enabled)
    except ImportError:
        pass
    except Exception as e:
        log.warning("Rules evaluation failed: %s", e)

    # ── Phase 4: Portfolio indicators ─────────────────────────
    if combined_usd > 0:
        _save_portfolio_snapshot(combined_usd, binance_usd, iol_usd)
    history = _load_portfolio_history()
    ma7 = calc_portfolio_ma(history, 168)   # 7 days * 24h
    ma30 = calc_portfolio_ma(history, 720)  # 30 days * 24h
    if ma7 is not None:
        G_PORT_MA7.set(ma7)
    if ma30 is not None:
        G_PORT_MA30.set(ma30)
    trend = classify_portfolio_trend(combined_usd, ma7, ma30)
    G_PORT_TREND.set(trend)
    vol = calc_portfolio_volatility(history)
    if vol is not None:
        G_PORT_VOL.set(vol)
    if len(history) >= 2:
        prev_val = history[-2]["total_usd"]
        if prev_val > 0:
            G_PORT_RETURN.set(round((combined_usd - prev_val) / prev_val * 100, 2))


# ── Main ──────────────────────────────────────────────────────

if __name__ == "__main__":
    if "--status" in sys.argv:
        b = fetch_binance()
        usd_ars = fetch_usd_ars_rate() or 0
        iol = fetch_iol_live(usd_ars)
        ath = _load_ath_state()

        print("\n== Binance ==")
        if b:
            dd = _calc_drawdown(b["total_usd"], ath.get("binance_ath_usd", 0))
            print(f"Total: ${b['total_usd']:,.2f}  DD: {dd:.1f}%")
            for a in b["assets"][:10]:
                alloc = (a["value_usd"] / b["total_usd"] * 100) if b["total_usd"] > 0 else 0
                print(f"  {a['asset']:6s} ${a['value_usd']:>9,.2f}  {alloc:5.1f}%  24h={a['change_24h_pct']:+.2f}%")
        else:
            print("  (no data)")

        print("\n== IOL ==")
        if iol:
            dd_iol = _calc_drawdown(iol["total_ars"], ath.get("iol_ath_ars", 0))
            print(
                f"Total: ${iol['total_ars']:,.0f} ARS eq"
                f" / US${iol.get('total_usd_native', 0):,.2f} native"
                f"  PNL: ${iol['pnl_day_ars']:+,.0f} ARS eq"
                f" / US${iol.get('pnl_day_usd_native', 0):+,.2f} native"
                f"  DD: {dd_iol:.1f}%"
            )
            for p in iol.get("positions", []):
                currency = p.get("currency", "ARS")
                value_native = p.get("value", 0)
                value_eq = p.get("value_ars_equiv", value_native)
                print(
                    f"  {p['symbol']:10s} {currency} {value_native:>10,.2f}"
                    f"  (ARS eq {value_eq:>10,.0f})  {p.get('change_pct', 0):+.2f}%"
                )
        else:
            print("  (no data)")

        print("\n== Combined ==")
        rate = usd_ars or 0
        iol_usd = (iol["total_ars"] / rate) if iol and rate > 0 else 0
        bin_usd = b["total_usd"] if b else 0
        total = bin_usd + iol_usd
        print(f"USD/ARS blue: ${rate:,.0f}")
        print(f"Total: ${total:,.2f} USD (Binance: ${bin_usd:,.2f} | IOL: ${iol_usd:,.2f})")

        print("\n== Market ==")
        fg = fetch_fear_greed()
        dom = fetch_btc_dominance()
        print(f"  Fear & Greed: {fg}  BTC Dominance: {dom}%")

        # Unified positions
        if "--positions" not in sys.argv:
            print("\n(use --positions for unified view)")
        exit(0)

    if "--positions" in sys.argv:
        b = fetch_binance()
        usd_ars = fetch_usd_ars_rate() or 0
        iol = fetch_iol_live(usd_ars)
        try:
            from finance_positions import build_unified_positions, format_positions_table
            positions = build_unified_positions(b, iol, usd_ars)
            print(f"\nUnified Positions (USD/ARS: ${usd_ars:,.0f})")
            if "--json" in sys.argv:
                print(json.dumps(positions, indent=2))
            else:
                print(format_positions_table(positions))
        except ImportError:
            print("finance_positions.py not found")
        exit(0)

    if "--indicators" in sys.argv:
        history = _load_portfolio_history()
        if not history:
            print("No portfolio history yet")
            exit(0)
        current = history[-1]["total_usd"]
        ma7 = calc_portfolio_ma(history, 168)
        ma30 = calc_portfolio_ma(history, 720)
        vol = calc_portfolio_volatility(history)
        trend = classify_portfolio_trend(current, ma7, ma30)
        trend_label = {1: "bullish", 0: "sideways", -1: "bearish"}[trend]
        daily_ret = 0
        if len(history) >= 2 and history[-2]["total_usd"] > 0:
            daily_ret = (current - history[-2]["total_usd"]) / history[-2]["total_usd"] * 100
        print(f"\nPortfolio Indicators ({len(history)} samples)")
        print(f"  Current: ${current:,.2f}")
        print(f"  MA7:  ${ma7:,.2f}" if ma7 else "  MA7:  (insufficient data)")
        print(f"  MA30: ${ma30:,.2f}" if ma30 else "  MA30: (insufficient data)")
        print(f"  Trend: {trend_label}")
        print(f"  Volatility: {vol:.2f}%" if vol else "  Volatility: (insufficient data)")
        print(f"  Daily return: {daily_ret:+.2f}%")
        exit(0)

    if not PROM_AVAILABLE:
        # Dry run: fetch and print
        print("prometheus_client not available — dry run mode")
        print("\n== Binance ==")
        b = fetch_binance()
        if b:
            print(f"Total: ${b['total_usd']:,.2f}")
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
        iol = fetch_iol_live()
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
