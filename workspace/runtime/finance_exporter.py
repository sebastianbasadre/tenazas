#!/usr/bin/env python3
"""
Finance Exporter — Prometheus metrics from finance_history on :9109.

Reads the most recent state/finance_history/YYYY-MM-DD.json and exports:
  tenazas_iol_portfolio_ars
  tenazas_iol_pnl_day_ars
  tenazas_binance_total_usd
  tenazas_binance_pnl_usd
  tenazas_binance_btc_price

Usage: python3 finance_exporter.py &
"""

import json
import logging
import time
from pathlib import Path

log = logging.getLogger("finance_exporter")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

BASE_DIR = Path(__file__).parent
STATE_DIR = Path("/home/tenazas/.openclaw/state") if Path("/home/tenazas").exists() else BASE_DIR.parent / "state"
HISTORY_DIR = STATE_DIR / "finance_history"
POLL_INTERVAL = 60  # seconds
PORT = 9109

try:
    from prometheus_client import Gauge, start_http_server
    PROM_AVAILABLE = True
except ImportError:
    PROM_AVAILABLE = False

if PROM_AVAILABLE:
    G_IOL_TOTAL = Gauge("tenazas_iol_portfolio_ars", "IOL portfolio total ARS")
    G_IOL_PNL = Gauge("tenazas_iol_pnl_day_ars", "IOL PNL del dia ARS")
    G_BINANCE_TOTAL = Gauge("tenazas_binance_total_usd", "Binance portfolio total USD")
    G_BINANCE_PNL = Gauge("tenazas_binance_pnl_usd", "Binance PNL del dia USD")
    G_BTC_PRICE = Gauge("tenazas_binance_btc_price", "BTC price USD")


def _get_latest_snapshot() -> dict | None:
    """Load most recent finance history file."""
    if not HISTORY_DIR.exists():
        return None
    files = sorted(HISTORY_DIR.glob("*.json"), reverse=True)
    for f in files[:3]:
        try:
            with open(f) as fh:
                return json.load(fh)
        except (json.JSONDecodeError, OSError):
            continue
    return None


def update_metrics():
    if not PROM_AVAILABLE:
        return

    snap = _get_latest_snapshot()
    if not snap:
        return

    iol = snap.get("iol")
    if iol:
        G_IOL_TOTAL.set(iol.get("total_ars", 0))
        G_IOL_PNL.set(iol.get("pnl_day_ars", 0))

    binance = snap.get("binance")
    if binance:
        G_BINANCE_TOTAL.set(binance.get("total_usd", 0))
        G_BINANCE_PNL.set(binance.get("pnl_usd", 0))
        # BTC price from assets
        for a in binance.get("assets", []):
            if a.get("asset") == "BTC":
                G_BTC_PRICE.set(a.get("price", 0))
                break


if __name__ == "__main__":
    if not PROM_AVAILABLE:
        print("prometheus_client not available")
        exit(1)

    start_http_server(PORT)
    log.info("Finance exporter on :%d", PORT)

    while True:
        try:
            update_metrics()
        except Exception as e:
            log.warning("Update failed: %s", e)
        time.sleep(POLL_INTERVAL)
