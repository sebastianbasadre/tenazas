#!/usr/bin/env python3
"""
finance_daily.py
Daily finance summary: Binance PNL + IOL portfolio → Telegram.

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
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from urllib.error import URLError

CONFIG_PATH = Path("/home/tenazas/.openclaw/openclaw.json")
IOL_CONFIG = Path("/home/tenazas/.openclaw/iol_config.json")
CHAT_ID = "1565794551"
GLUETUN_PROXY = "http://192.168.2.2:8888"


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
    """Get Binance account balance + 24h PNL."""
    cfg = _load_config().get("tenazas", {})
    api_key = cfg.get("binance_api_key", "")
    secret = cfg.get("binance_secret", "")

    if not api_key or not secret:
        if test:
            print("[binance] no API key configured in tenazas.binance_api_key")
        return None

    try:
        # Get account balances
        params = {"timestamp": int(time.time() * 1000)}
        signed = _binance_sign(params, secret)
        req = Request(
            f"https://api.binance.com/api/v3/account?{signed}",
            headers={"X-MBX-APIKEY": api_key},
        )
        # Route through gluetun for VPN
        import urllib.request
        proxy = urllib.request.ProxyHandler({"https": GLUETUN_PROXY})
        opener = urllib.request.build_opener(proxy)
        resp = opener.open(req, timeout=10)
        account = json.loads(resp.read())

        # Filter assets with balance > 0
        assets = []
        for b in account.get("balances", []):
            free = float(b.get("free", 0))
            locked = float(b.get("locked", 0))
            total = free + locked
            if total > 0.0001:
                assets.append({"asset": b["asset"], "total": total})

        if not assets:
            return {"assets": [], "total_usd": 0, "pnl_usd": 0}

        # Get 24h tickers for price + change
        req2 = Request("https://api.binance.com/api/v3/ticker/24hr")
        resp2 = opener.open(req2, timeout=10)
        tickers = {t["symbol"]: t for t in json.loads(resp2.read())}

        total_usd = 0
        total_pnl = 0
        asset_details = []

        for a in assets:
            symbol = a["asset"] + "USDT"
            ticker = tickers.get(symbol)
            if ticker:
                price = float(ticker.get("lastPrice", 0))
                change_pct = float(ticker.get("priceChangePercent", 0))
                value = a["total"] * price
                pnl = value * (change_pct / 100) / (1 + change_pct / 100)
                total_usd += value
                total_pnl += pnl
                asset_details.append({
                    "asset": a["asset"],
                    "amount": a["total"],
                    "price": price,
                    "value_usd": round(value, 2),
                    "change_pct": round(change_pct, 2),
                    "pnl_usd": round(pnl, 2),
                })
            elif a["asset"] in ("USDT", "USDC", "BUSD"):
                total_usd += a["total"]
                asset_details.append({
                    "asset": a["asset"],
                    "amount": a["total"],
                    "value_usd": round(a["total"], 2),
                    "change_pct": 0,
                    "pnl_usd": 0,
                })

        # Sort by value
        asset_details.sort(key=lambda x: -x.get("value_usd", 0))

        return {
            "assets": asset_details,
            "total_usd": round(total_usd, 2),
            "pnl_usd": round(total_pnl, 2),
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
            simbolo = titulo.get("simbolo", "?")
            cantidad = a.get("cantidad", 0)
            precio = a.get("ultimoPrecio", 0)
            variacion = a.get("variacionDiaria", 0)
            valor = a.get("valorizado", cantidad * precio)

            total += valor
            day_pnl = valor * (variacion / 100) if variacion else 0
            pnl_day += day_pnl

            positions.append({
                "symbol": simbolo,
                "name": nombre[:30],
                "quantity": cantidad,
                "price": precio,
                "value": round(valor, 2),
                "change_pct": round(variacion, 2),
                "pnl_day": round(day_pnl, 2),
            })

        positions.sort(key=lambda x: -x.get("value", 0))

        return {
            "positions": positions,
            "total_ars": round(total, 2),
            "pnl_day_ars": round(pnl_day, 2),
        }

    except Exception as e:
        if test:
            print(f"[iol] error: {e}")
        return None


# ── Format + Notify ──────────────────────────────────────────────

def format_daily(binance: dict | None, iol: dict | None) -> str:
    """Format daily finance summary."""
    today = datetime.now().strftime("%d/%m/%Y")
    lines = [f"Finance daily — {today}", ""]

    if binance:
        pnl_sign = "+" if binance["pnl_usd"] >= 0 else ""
        lines.append(f"Binance: ${binance['total_usd']:,.0f} | PNL hoy: {pnl_sign}${binance['pnl_usd']:,.0f}")
        for a in binance["assets"][:5]:
            if a["value_usd"] > 10:
                sign = "+" if a["change_pct"] >= 0 else ""
                lines.append(f"  {a['asset']}: ${a['value_usd']:,.0f} ({sign}{a['change_pct']}%)")
    else:
        lines.append("Binance: sin configurar (agregar binance_api_key en openclaw.json)")

    lines.append("")

    if iol:
        pnl_sign = "+" if iol["pnl_day_ars"] >= 0 else ""
        lines.append(f"IOL: ${iol['total_ars']:,.0f} ARS | hoy: {pnl_sign}${iol['pnl_day_ars']:,.0f}")
        for p in iol["positions"][:3]:
            sign = "+" if p["change_pct"] >= 0 else ""
            lines.append(f"  {p['symbol']}: ${p['value']:,.0f} ({sign}{p['change_pct']}%)")
    else:
        lines.append("IOL: no disponible")

    return "\n".join(lines)


def run(test: bool = False):
    """Run daily finance check."""
    binance = get_binance_pnl(test=test)
    iol = get_iol_portfolio(test=test)

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
