#!/usr/bin/env python3
"""
finance_daily.py
Daily finance summary: Binance balance + IOL portfolio → Telegram.

Binance: requires tenazas.binance_api_key + binance_secret in openclaw.json
IOL: reuses existing iol_client.py with bearer token auth

Usage:
  python3 finance_daily.py --test     # test both + print
  python3 finance_daily.py            # run + notify Telegram
"""

import argparse
import hashlib
import hmac
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import URLError

try:
    from .runtime_config import GLUETUN_PROXY, get_default_telegram_chat_id
except ImportError:
    from runtime_config import GLUETUN_PROXY, get_default_telegram_chat_id

try:
    from .finance_positions import _normalize_iol_symbol
except ImportError:
    from finance_positions import _normalize_iol_symbol

CONFIG_PATH = Path("/home/tenazas/.openclaw/openclaw.json")
IOL_CONFIG = Path("/home/tenazas/.openclaw/iol_config.json")
STATE_DIR = Path("/home/tenazas/.openclaw/state") if Path("/home/tenazas").exists() else Path(__file__).parent.parent / "state"
HISTORY_DIR = STATE_DIR / "finance_history"
CHAT_ID = get_default_telegram_chat_id()


def _is_legacy_iol_symbol(symbol: str) -> bool:
    """Detect stale/legacy IOL placeholder symbols."""
    return symbol.upper().endswith("OLD")


def _normalize_iol_change_pct(variacion: float, puntos: float, precio: float) -> float:
    """Normalize IOL daily change to percentage points.

    Some IOL payloads appear to encode `variacionDiaria` as a fraction
    (0.06 => 6%) while others behave like percentage points (0.06 => 0.06%).
    When `puntosVariacion` is present, infer the right scale from the implied
    absolute price move and keep the reported percentage consistent with it.
    """
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


def _currency_prefix(currency: str) -> str:
    return "US$" if currency == "USD" else "$"


def _currency_suffix(currency: str) -> str:
    return "" if currency == "USD" else f" {currency}"


def _load_config() -> dict:
    try:
        return json.loads(CONFIG_PATH.read_text())
    except Exception:
        return {}


def _notify(msg: str):
    try:
        cfg = _load_config()
        token = cfg["channels"]["telegram"]["botToken"]
        payload = json.dumps({"chat_id": CHAT_ID, "text": msg[:4090]}).encode()
        urlopen(Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=payload, headers={"Content-Type": "application/json"},
        ), timeout=10)
    except Exception as e:
        print(f"[notify] error: {e}")


# ── Binance ──────────────────────────────────────────────────────

def _binance_sign(params: dict, secret: str) -> str:
    """Sign params with HMAC-SHA256 for Binance API."""
    query = urlencode(params)
    signature = hmac.new(secret.encode(), query.encode(), hashlib.sha256).hexdigest()
    return query + f"&signature={signature}"


def get_binance_pnl(test: bool = False) -> dict | None:
    """Get Binance account balance and asset trend data."""
    # Try env vars first, then tenazas.json fallback
    api_key = os.environ.get("BINANCE_API_KEY", "")
    secret = os.environ.get("BINANCE_SECRET", "")
    if not api_key or not secret:
        _tcfg_path = Path("/home/tenazas/.openclaw/tenazas.json")
        try:
            cfg = json.loads(_tcfg_path.read_text()) if _tcfg_path.exists() else {}
        except Exception:
            cfg = {}
        api_key = api_key or cfg.get("binance_api_key", "")
        secret = secret or cfg.get("binance_secret", "")

    if not api_key or not secret:
        if test:
            print("[binance] no API key configured in tenazas.binance_api_key")
        return None

    try:
        import subprocess

        def _curl_binance(url, headers=None):
            """Call Binance API via gluetun proxy using curl."""
            cmd = ["curl", "-s", "--max-time", "10",
                   "--proxy", GLUETUN_PROXY, url]
            for k, v in (headers or {}).items():
                cmd += ["-H", f"{k}: {v}"]
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            return json.loads(r.stdout)

        # Use Binance server time to avoid clock skew
        try:
            server_ts = _curl_binance("https://api.binance.com/api/v3/time")["serverTime"]
        except Exception:
            server_ts = int(time.time() * 1000)

        # Get account balances
        params = {"timestamp": server_ts, "recvWindow": 10000}
        signed = _binance_sign(params, secret)
        account = _curl_binance(
            f"https://api.binance.com/api/v3/account?{signed}",
            headers={"X-MBX-APIKEY": api_key},
        )

        # Filter assets with balance > 0
        assets = []
        for b in account.get("balances", []):
            free = float(b.get("free", 0))
            locked = float(b.get("locked", 0))
            total = free + locked
            if total > 0.0001:
                assets.append({"asset": b["asset"], "total": total})

        if not assets:
            return {"assets": [], "total_usd": 0}

        # Get 24h tickers only for assets we hold (avoid huge /ticker/24hr response)
        symbols_needed = [a["asset"] + "USDT" for a in assets
                          if a["asset"] not in ("USDT", "USDC", "BUSD")]
        tickers = {}
        for sym in symbols_needed:
            try:
                t = _curl_binance(
                    f"https://api.binance.com/api/v3/ticker/24hr?symbol={sym}")
                tickers[sym] = t
            except Exception:
                pass

        total_usd = 0
        asset_details = []

        for a in assets:
            symbol = a["asset"] + "USDT"
            ticker = tickers.get(symbol)
            if ticker:
                price = float(ticker.get("lastPrice", 0))
                change_pct = float(ticker.get("priceChangePercent", 0))
                value = a["total"] * price
                total_usd += value
                asset_details.append({
                    "asset": a["asset"],
                    "amount": a["total"],
                    "price": price,
                    "value_usd": round(value, 2),
                    "change_pct": round(change_pct, 2),
                })
            elif a["asset"] in ("USDT", "USDC", "BUSD"):
                total_usd += a["total"]
                asset_details.append({
                    "asset": a["asset"],
                    "amount": a["total"],
                    "value_usd": round(a["total"], 2),
                    "change_pct": 0,
                })

        # Sort by value
        asset_details.sort(key=lambda x: -x.get("value_usd", 0))

        return {
            "assets": asset_details,
            "total_usd": round(total_usd, 2),
        }

    except Exception as e:
        if test:
            print(f"[binance] error: {e}")
        return None


# ── IOL ──────────────────────────────────────────────────────────

def get_iol_portfolio(test: bool = False) -> dict | None:
    """Get IOL portfolio using existing iol_client."""
    try:
        sys.path.insert(0, "/home/tenazas/.openclaw/workspace/skills/iol_broker")
        from iol_client import IOLClient
        client = IOLClient()

        # Get portfolio
        portfolio = client.get("/api/v2/portafolio/argentina")
        if not portfolio:
            if test:
                print("[iol] empty portfolio response")
            return None

        activos = portfolio.get("activos", [])
        total = 0
        pnl_day = 0
        positions = []

        for a in activos:
            titulo = a.get("titulo", {})
            nombre = titulo.get("descripcion", titulo.get("simbolo", "?"))
            raw_symbol = titulo.get("simbolo", "?")
            simbolo = _normalize_iol_symbol(raw_symbol)
            currency = _normalize_iol_currency(titulo.get("moneda"))
            cantidad = a.get("cantidad", 0)
            precio = a.get("ultimoPrecio", 0)
            puntos = a.get("puntosVariacion", 0)
            variacion = _normalize_iol_change_pct(a.get("variacionDiaria", 0), puntos, precio)
            valor = a.get("valorizado", cantidad * precio)

            total += valor
            # puntosVariacion * cantidad = exact daily PNL
            day_pnl = puntos * cantidad if puntos else (
                valor * (variacion / 100) if variacion else 0)
            pnl_day += day_pnl

            positions.append({
                "symbol": simbolo,
                "raw_symbol": raw_symbol,
                "name": nombre[:30],
                "quantity": cantidad,
                "price": precio,
                "value": round(valor, 2),
                "currency": currency,
                "change_pct": round(variacion, 2),
                "pnl_day": round(day_pnl, 2),
            })

        positions.sort(key=lambda x: -x.get("value", 0))
        currencies = {p.get("currency", "ARS") for p in positions if p.get("currency")}
        portfolio_currency = currencies.pop() if len(currencies) == 1 else "MIX"

        return {
            "positions": positions,
            "total_ars": round(total, 2),
            "pnl_day_ars": round(pnl_day, 2),
            "portfolio_currency": portfolio_currency,
        }

    except Exception as e:
        if test:
            print(f"[iol] error: {e}")
        return None


# ── Format + Notify ──────────────────────────────────────────────

def _trend_icon(change_pct: float) -> str:
    """Return trend indicator based on change percentage."""
    if change_pct >= 3:
        return "🟢🔥"  # strong up
    elif change_pct >= 0.5:
        return "🟢"    # up
    elif change_pct > -0.5:
        return "⚪"    # flat
    elif change_pct > -3:
        return "🔴"    # down
    else:
        return "🔴📉"  # strong down


def format_daily(binance: dict | None, iol: dict | None) -> str:
    """Format daily finance summary with trend indicators."""
    today = datetime.now().strftime("%d/%m/%Y")
    lines = ["Finance daily — " + today, ""]

    if binance:
        lines.append(f"Binance: ${binance['total_usd']:,.0f}")
        for a in binance["assets"][:5]:
            if a["value_usd"] > 10:
                t = _trend_icon(a["change_pct"])
                sign = "+" if a["change_pct"] >= 0 else ""
                lines.append(f"  {t} {a['asset']}: ${a['value_usd']:,.0f} ({sign}{a['change_pct']}%)")
    else:
        lines.append("Binance: sin configurar")

    lines.append("")

    if iol:
        pnl = iol["pnl_day_ars"]
        icon = "👍" if pnl >= 0 else "👎"
        pnl_sign = "+" if pnl >= 0 else ""
        portfolio_currency = str(iol.get("portfolio_currency") or "ARS").upper()
        prefix = _currency_prefix(portfolio_currency)
        suffix = _currency_suffix(portfolio_currency)
        lines.append(f"{icon} IOL: {prefix}{iol['total_ars']:,.0f}{suffix} | hoy: {pnl_sign}{prefix}{pnl:,.0f}")
        display_positions = [
            p for p in iol["positions"]
            if not _is_legacy_iol_symbol(str(p.get("raw_symbol") or p.get("symbol") or ""))
        ]
        for p in display_positions[:3]:
            t = _trend_icon(p["change_pct"])
            sign = "+" if p["change_pct"] >= 0 else ""
            pos_currency = str(p.get("currency") or portfolio_currency or "ARS").upper()
            lines.append(
                f"  {t} {p['symbol']}: {_currency_prefix(pos_currency)}{p['value']:,.0f}"
                f"{_currency_suffix(pos_currency)} ({sign}{p['change_pct']}%)"
            )
    else:
        lines.append("IOL: no disponible")

    return "\n".join(lines)


def _build_snapshot(date_str: str, binance: dict | None, iol: dict | None, source: str = "live") -> dict:
    """Build a persisted finance snapshot payload."""
    snapshot = {
        "date": date_str,
        "ts": datetime.now().astimezone().isoformat(),
        "source": source,
        "binance": {
            "total_usd": binance["total_usd"],
            "assets": binance["assets"],
        } if binance else None,
        "iol": {
            "total_ars": iol["total_ars"],
            "pnl_day_ars": iol["pnl_day_ars"],
            "portfolio_currency": iol.get("portfolio_currency", "ARS"),
            "positions": iol["positions"],
        } if iol else None,
    }
    return snapshot


def save_snapshot_for_date(date_str: str, binance: dict | None, iol: dict | None, source: str = "manual") -> Path:
    """Persist a finance snapshot for an explicit YYYY-MM-DD date."""
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    snapshot = _build_snapshot(date_str, binance, iol, source=source)
    path = HISTORY_DIR / f"{date_str}.json"
    with open(path, "w") as f:
        json.dump(snapshot, f, indent=2, default=str)
    return path


def _save_daily_snapshot(binance: dict | None, iol: dict | None):
    """Persist daily finance snapshot to history."""
    try:
        today = datetime.now().strftime("%Y-%m-%d")
        save_snapshot_for_date(today, binance, iol, source="live")
    except Exception:
        pass


def get_history(days: int = 30) -> list:
    """Load last N days of finance history."""
    if not HISTORY_DIR.exists():
        return []
    files = sorted(HISTORY_DIR.glob("*.json"), reverse=True)[:days]
    history = []
    for f in files:
        try:
            with open(f) as fh:
                history.append(json.load(fh))
        except (json.JSONDecodeError, OSError):
            continue
    return history


def run(test: bool = False):
    """Run daily finance check."""
    binance = get_binance_pnl(test=test)
    iol = get_iol_portfolio(test=test)

    # Persist snapshot
    _save_daily_snapshot(binance, iol)

    msg = format_daily(binance, iol)

    if test:
        print(msg)
    else:
        _notify(msg)
        print("[finance_daily] notified")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Daily finance summary")
    parser.add_argument("--test", action="store_true", help="Test mode (print, don't notify)")
    args = parser.parse_args()
    run(test=args.test)
