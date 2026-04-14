#!/usr/bin/env python3
"""
Trading Scorer — Phase 5: Score the trading system.

Calculates: win rate, profit factor, max drawdown, sharpe ratio, hypothesis accuracy.
NEVER activates real trading. Only notifies if thresholds met.

Usage:
    python3 trading_scorer.py --report    # full report
    python3 trading_scorer.py --ready     # check if ready for phase 6
"""

import json
import math
from datetime import datetime
from pathlib import Path
from urllib.request import Request, urlopen

BASE_DIR = Path(__file__).parent
STATE_DIR = Path("/home/tenazas/.openclaw/state") if Path("/home/tenazas").exists() else BASE_DIR.parent / "state"
TRADES_FILE = STATE_DIR / "trading" / "paper_trades.json"
SCORER_FILE = STATE_DIR / "trading" / "scorer_report.json"
LOG_FILE = BASE_DIR / "logs" / "trading_scorer.log"
CONFIG_PATH = Path("/home/tenazas/.openclaw/openclaw.json") if Path("/home/tenazas").exists() else BASE_DIR.parent / "state" / "openclaw.json"
CHAT_ID = "1565794551"

# Phase 6 readiness thresholds
THRESHOLDS = {
    "win_rate": 0.55,
    "profit_factor": 1.5,
    "max_drawdown_pct": 20.0,
    "hypothesis_accuracy": 0.60,
    "min_trades": 50,
}


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


def calculate_metrics() -> dict:
    """Calculate all trading metrics from paper trades."""
    trades = _load(TRADES_FILE, [])
    closed = [t for t in trades if t.get("status") not in ("open", None)]

    if not closed:
        return {
            "total_trades": 0, "win_rate": 0, "profit_factor": 0,
            "max_drawdown_pct": 0, "sharpe_ratio": 0,
            "hypothesis_accuracy": 0, "meets_thresholds": False,
        }

    # Win rate
    winners = [t for t in closed if (t.get("pnl_usdt") or 0) > 0]
    losers = [t for t in closed if (t.get("pnl_usdt") or 0) <= 0]
    win_rate = len(winners) / len(closed)

    # Profit factor
    gross_profit = sum(t.get("pnl_usdt", 0) for t in winners)
    gross_loss = abs(sum(t.get("pnl_usdt", 0) for t in losers))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    # Max drawdown
    balance = 10000.0
    peak = balance
    max_dd = 0
    for t in sorted(closed, key=lambda x: x.get("closed_at", "")):
        balance += t.get("pnl_usdt", 0)
        if balance > peak:
            peak = balance
        dd = (peak - balance) / peak * 100
        if dd > max_dd:
            max_dd = dd

    # Sharpe ratio (simplified)
    returns = [t.get("pnl_pct", 0) / 100 for t in closed]
    avg_return = sum(returns) / len(returns) if returns else 0
    if len(returns) > 1:
        variance = sum((r - avg_return) ** 2 for r in returns) / (len(returns) - 1)
        std = math.sqrt(variance)
        sharpe = avg_return / std if std > 0 else 0
    else:
        sharpe = 0

    # Hypothesis accuracy
    try:
        from trading_hypothesis import get_accuracy
        hyp_acc = get_accuracy()
    except Exception:
        hyp_acc = {"accuracy": 0, "total": 0}

    report = {
        "ts": _now().isoformat(),
        "total_trades": len(closed),
        "winners": len(winners),
        "losers": len(losers),
        "win_rate": round(win_rate, 4),
        "gross_profit": round(gross_profit, 2),
        "gross_loss": round(gross_loss, 2),
        "profit_factor": round(profit_factor, 4) if profit_factor != float("inf") else 999,
        "max_drawdown_pct": round(max_dd, 2),
        "sharpe_ratio": round(sharpe, 4),
        "hypothesis_accuracy": hyp_acc.get("accuracy", 0),
        "hypotheses_evaluated": hyp_acc.get("total", 0),
        "final_balance": round(balance, 2),
        "total_pnl": round(balance - 10000, 2),
    }

    # Check thresholds
    report["meets_thresholds"] = (
        report["total_trades"] >= THRESHOLDS["min_trades"] and
        report["win_rate"] >= THRESHOLDS["win_rate"] and
        report["profit_factor"] >= THRESHOLDS["profit_factor"] and
        report["max_drawdown_pct"] <= THRESHOLDS["max_drawdown_pct"] and
        report["hypothesis_accuracy"] >= THRESHOLDS["hypothesis_accuracy"]
    )

    # Save report
    SCORER_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(SCORER_FILE, "w") as f:
        json.dump(report, f, indent=2, default=str)

    _log({"action": "report", "trades": report["total_trades"],
          "win_rate": report["win_rate"], "ready": report["meets_thresholds"]})

    return report


def format_report(report: dict) -> str:
    lines = [f"Trading Scorer — {report.get('ts', '?')[:10]}"]
    lines.append(f"Trades: {report['total_trades']} ({report['winners']}W / {report['losers']}L)")
    lines.append(f"Win rate: {report['win_rate']:.0%} (threshold: {THRESHOLDS['win_rate']:.0%})")
    lines.append(f"Profit factor: {report['profit_factor']:.2f} (threshold: {THRESHOLDS['profit_factor']})")
    lines.append(f"Max drawdown: {report['max_drawdown_pct']:.1f}% (threshold: {THRESHOLDS['max_drawdown_pct']}%)")
    lines.append(f"Sharpe: {report['sharpe_ratio']:.2f}")
    lines.append(f"Hypothesis accuracy: {report['hypothesis_accuracy']:.0%}")
    lines.append(f"Balance: ${report['final_balance']:,.0f} (PnL: ${report['total_pnl']:+,.0f})")
    status = "READY" if report["meets_thresholds"] else "NOT READY"
    lines.append(f"\nPhase 6: {status}")
    return "\n".join(lines)


def check_and_notify():
    """Calculate metrics and notify if thresholds met."""
    report = calculate_metrics()
    if report["meets_thresholds"] and report["total_trades"] >= THRESHOLDS["min_trades"]:
        msg = ("Trading system met minimum thresholds.\n"
               f"Win rate: {report['win_rate']:.0%}, PF: {report['profit_factor']:.2f}\n"
               f"Trades: {report['total_trades']}, Balance: ${report['final_balance']:,.0f}\n"
               "Review for phase 6 activation?")
        try:
            token = json.loads(CONFIG_PATH.read_text())["channels"]["telegram"]["botToken"]
            payload = json.dumps({"chat_id": CHAT_ID, "text": msg}).encode()
            urlopen(Request(f"https://api.telegram.org/bot{token}/sendMessage",
                            data=payload, headers={"Content-Type": "application/json"}), timeout=10)
        except Exception:
            pass
    return report


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="Trading Scorer")
    p.add_argument("--report", action="store_true")
    p.add_argument("--ready", action="store_true")
    args = p.parse_args()

    if args.report:
        report = calculate_metrics()
        print(format_report(report))
    elif args.ready:
        report = calculate_metrics()
        print("READY" if report["meets_thresholds"] else "NOT READY")
        print(f"  Trades: {report['total_trades']}/{THRESHOLDS['min_trades']}")
        print(f"  Win rate: {report['win_rate']:.0%}/{THRESHOLDS['win_rate']:.0%}")
    else:
        p.print_help()
