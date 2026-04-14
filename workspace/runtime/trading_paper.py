#!/usr/bin/env python3
"""
Trading Paper — Phase 4: Paper trading (simulation, NO real money).

SAFETY: NEVER calls authenticated Binance APIs. Only public price data.
NEVER imports binance_api_key or binance_secret.

Virtual portfolio: $10,000 USDT.
Max 10% per trade. Stop loss -5%. Fees 0.1%. No leverage.

Usage:
    python3 trading_paper.py --run        # check hypotheses + open/close trades
    python3 trading_paper.py --portfolio  # show virtual portfolio
    python3 trading_paper.py --trades     # show trade history
"""

import json
from datetime import datetime
from hashlib import sha256
from pathlib import Path

BASE_DIR = Path(__file__).parent
STATE_DIR = Path("/home/tenazas/.openclaw/state") if Path("/home/tenazas").exists() else BASE_DIR.parent / "state"
TRADES_FILE = STATE_DIR / "trading" / "paper_trades.json"
PORTFOLIO_FILE = STATE_DIR / "trading" / "paper_portfolio.json"
LOG_FILE = BASE_DIR / "logs" / "trading_paper.log"

INITIAL_BALANCE = 10000.0  # USDT
MAX_POSITION_PCT = 0.10     # 10% per trade
STOP_LOSS_PCT = 0.05        # -5%
FEE_PCT = 0.001             # 0.1%
MIN_HYPOTHESIS_CONFIDENCE = 0.70


def _now(): return datetime.now().astimezone()

def _log(e):
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    e["ts"] = _now().isoformat()
    try:
        with open(LOG_FILE, "a") as f: f.write(json.dumps(e, default=str) + "\n")
    except OSError: pass

def _load(p, d=None):
    try:
        with open(p) as f: return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError): return d if d is not None else {}

def _save(p, data):
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w") as f: json.dump(data, f, indent=2, default=str)


def _get_portfolio() -> dict:
    port = _load(PORTFOLIO_FILE, None)
    if port is None:
        port = {"balance": INITIAL_BALANCE, "initial": INITIAL_BALANCE, "trades_count": 0}
        _save(PORTFOLIO_FILE, port)
    return port


def _get_current_price(symbol: str = "BTCUSDT") -> float:
    """Get current price from trading_observer (public data only)."""
    try:
        from trading_observer import get_latest
        latest = get_latest()
        return latest.get("tickers", {}).get(symbol, {}).get("price", 0)
    except Exception:
        return 0


def _get_trades() -> list:
    return _load(TRADES_FILE, [])


def _save_trades(trades: list):
    _save(TRADES_FILE, trades)


# ── Core API ────────────────────────────────────────────────────


def open_trade(hypothesis: dict) -> dict | None:
    """Open a paper trade based on a hypothesis.

    NEVER touches real money. Simulation only.
    """
    portfolio = _get_portfolio()
    balance = portfolio["balance"]

    # Position size: max 10% of portfolio
    size = balance * MAX_POSITION_PCT
    if size < 10:
        return None

    symbol = f"{hypothesis['asset']}USDT"
    price = _get_current_price(symbol)
    if price == 0:
        return None

    # Apply entry fee
    fee = size * FEE_PCT
    size_after_fee = size - fee

    # Calculate stop loss
    direction = "long" if hypothesis["direction"] == "up" else "short"
    if direction == "long":
        stop_loss = price * (1 - STOP_LOSS_PCT)
    else:
        stop_loss = price * (1 + STOP_LOSS_PCT)

    trade = {
        "trade_id": sha256(f"{hypothesis['hypothesis_id']}:{_now().isoformat()}".encode()).hexdigest()[:12],
        "hypothesis_id": hypothesis["hypothesis_id"],
        "asset": hypothesis["asset"],
        "direction": direction,
        "entry_price": price,
        "size_usdt": round(size_after_fee, 2),
        "stop_loss": round(stop_loss, 2),
        "take_profit": hypothesis["target_price"],
        "fee_paid": round(fee, 2),
        "status": "open",
        "opened_at": _now().isoformat(),
        "pnl_usdt": None,
        "pnl_pct": None,
    }

    # Deduct from balance
    portfolio["balance"] = round(balance - size, 2)
    portfolio["trades_count"] += 1
    _save(PORTFOLIO_FILE, portfolio)

    trades = _get_trades()
    trades.append(trade)
    _save_trades(trades)

    _log({"action": "open_trade", "trade_id": trade["trade_id"],
          "asset": trade["asset"], "direction": direction, "size": size})
    return trade


def check_trades() -> list:
    """Check open trades against current prices. Close if SL/TP hit."""
    trades = _get_trades()
    portfolio = _get_portfolio()
    closed = []

    for trade in trades:
        if trade["status"] != "open":
            continue

        symbol = f"{trade['asset']}USDT"
        price = _get_current_price(symbol)
        if price == 0:
            continue

        entry = trade["entry_price"]
        direction = trade["direction"]
        sl = trade["stop_loss"]
        tp = trade["take_profit"]

        hit_sl = (direction == "long" and price <= sl) or (direction == "short" and price >= sl)
        hit_tp = (direction == "long" and price >= tp) or (direction == "short" and price <= tp)

        if hit_sl or hit_tp:
            # Calculate PnL
            if direction == "long":
                pnl_pct = (price - entry) / entry
            else:
                pnl_pct = (entry - price) / entry

            pnl_usdt = trade["size_usdt"] * pnl_pct
            exit_fee = abs(pnl_usdt + trade["size_usdt"]) * FEE_PCT
            pnl_usdt -= exit_fee

            trade["status"] = "closed_sl" if hit_sl else "closed_profit"
            trade["exit_price"] = price
            trade["pnl_usdt"] = round(pnl_usdt, 2)
            trade["pnl_pct"] = round(pnl_pct * 100, 2)
            trade["closed_at"] = _now().isoformat()

            # Return funds to portfolio
            portfolio["balance"] = round(portfolio["balance"] + trade["size_usdt"] + pnl_usdt, 2)
            closed.append(trade)

            _log({"action": "close_trade", "trade_id": trade["trade_id"],
                  "status": trade["status"], "pnl": pnl_usdt})

    _save_trades(trades)
    _save(PORTFOLIO_FILE, portfolio)
    return closed


def run():
    """Full paper trading cycle: generate trades + check open ones."""
    # Check existing open trades
    closed = check_trades()

    # Look for new high-confidence hypotheses to trade
    try:
        from trading_hypothesis import get_active
        active_hyps = get_active()
    except Exception:
        active_hyps = []

    trades = _get_trades()
    traded_hyps = {t["hypothesis_id"] for t in trades}

    opened = []
    for h in active_hyps:
        if h["hypothesis_id"] in traded_hyps:
            continue
        if h["confidence"] < MIN_HYPOTHESIS_CONFIDENCE:
            continue
        if h["direction"] == "sideways":
            continue  # can't trade sideways
        trade = open_trade(h)
        if trade:
            opened.append(trade)

    return {"opened": len(opened), "closed": len(closed)}


def get_portfolio_summary() -> dict:
    portfolio = _get_portfolio()
    trades = _get_trades()
    open_trades = [t for t in trades if t["status"] == "open"]
    closed_trades = [t for t in trades if t["status"] != "open"]
    return {
        "balance": portfolio["balance"],
        "initial": portfolio["initial"],
        "pnl_total": round(portfolio["balance"] - portfolio["initial"], 2),
        "pnl_pct": round((portfolio["balance"] / portfolio["initial"] - 1) * 100, 2),
        "open_trades": len(open_trades),
        "closed_trades": len(closed_trades),
    }


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="Trading Paper")
    p.add_argument("--run", action="store_true")
    p.add_argument("--portfolio", action="store_true")
    p.add_argument("--trades", action="store_true")
    args = p.parse_args()

    if args.run:
        result = run()
        print(f"Opened: {result['opened']}, Closed: {result['closed']}")
    elif args.portfolio:
        print(json.dumps(get_portfolio_summary(), indent=2))
    elif args.trades:
        for t in _get_trades()[-10:]:
            print(f"  [{t['status']}] {t['asset']} {t['direction']} "
                  f"${t.get('pnl_usdt', '?')} ({t.get('pnl_pct', '?')}%)")
    else:
        p.print_help()
