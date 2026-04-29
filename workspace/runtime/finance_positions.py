#!/usr/bin/env python3
"""
Unified position model — merges Binance + IOL into normalized positions.

Used by finance_exporter.py and finance_rules.py.
"""

import re


def _normalize_iol_symbol(symbol: str) -> str:
    """Strip IOL settlement suffixes (48hs, CI, 24hs)."""
    return re.sub(r'\s*(48hs|24hs|CI)\s*$', '', symbol, flags=re.IGNORECASE).strip()


def build_unified_positions(binance: dict | None, iol: dict | None,
                            usd_ars_rate: float) -> list[dict]:
    """Build unified position list from Binance + IOL data."""
    positions = []
    total_usd = 0

    # Binance assets
    if binance:
        for a in binance.get("assets", []):
            val = a.get("value_usd", 0)
            total_usd += val
            positions.append({
                "symbol": a["asset"],
                "source": "binance",
                "qty": a.get("qty", 0),
                "price": a.get("price", 0),
                "value_usd": round(val, 2),
                "currency": "USD",
                "pnl_day_usd": 0,  # computed below
                "change_pct": a.get("change_24h_pct", 0),
                "allocation_pct": 0,
                "wallet": a.get("wallet", "spot"),
            })
        # Compute per-asset PNL
        if binance.get("total_usd", 0) > 0:
            for p in positions:
                if p["source"] == "binance" and p["change_pct"] != 0:
                    cp = p["change_pct"]
                    p["pnl_day_usd"] = round(
                        p["value_usd"] * (cp / 100) / (1 + cp / 100), 2)

    # IOL positions
    if iol and usd_ars_rate > 0:
        for p in iol.get("positions", []):
            val_ars = p.get("value", 0)
            val_usd = round(val_ars / usd_ars_rate, 2)
            pnl_ars = p.get("pnl_day", 0)
            pnl_usd = round(pnl_ars / usd_ars_rate, 2)
            total_usd += val_usd
            positions.append({
                "symbol": _normalize_iol_symbol(p.get("symbol", "?")),
                "source": "iol",
                "qty": p.get("quantity", 0),
                "price": p.get("price", 0),
                "value_usd": val_usd,
                "currency": "ARS",
                "pnl_day_usd": pnl_usd,
                "change_pct": p.get("change_pct", 0),
                "allocation_pct": 0,
                "wallet": p.get("type", ""),
            })

    # Compute allocation %
    if total_usd > 0:
        for p in positions:
            p["allocation_pct"] = round(p["value_usd"] / total_usd * 100, 2)

    positions.sort(key=lambda x: -x["value_usd"])
    return positions


def format_positions_table(positions: list) -> str:
    """Format positions as an aligned text table."""
    lines = []
    header = f"{'Symbol':10s} {'Source':8s} {'Value USD':>12s} {'PNL Day':>10s} {'Chg%':>7s} {'Alloc%':>7s}"
    lines.append(header)
    lines.append("-" * len(header))

    total_value = 0
    total_pnl = 0
    for p in positions:
        total_value += p["value_usd"]
        total_pnl += p["pnl_day_usd"]
        lines.append(
            f"{p['symbol']:10s} {p['source']:8s} "
            f"${p['value_usd']:>10,.2f} "
            f"${p['pnl_day_usd']:>+8,.2f} "
            f"{p['change_pct']:>+6.2f}% "
            f"{p['allocation_pct']:>6.1f}%"
        )

    lines.append("-" * len(header))
    lines.append(f"{'TOTAL':10s} {'':8s} ${total_value:>10,.2f} ${total_pnl:>+8,.2f}")
    return "\n".join(lines)


def get_position(positions: list, symbol: str, source: str = None) -> dict | None:
    """Find a position by symbol, optionally filtered by source."""
    for p in positions:
        if p["symbol"].upper() == symbol.upper():
            if source is None or p["source"] == source:
                return p
    return None
