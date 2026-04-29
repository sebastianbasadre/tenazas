#!/usr/bin/env python3
"""
confidence_calibration.py — Sprint 5: Governance & Stability

Calibrates confidence_engine.py against real outcomes from decision_quality.
Detects overconfidence (high confidence + bad outcome) and underconfidence
(low confidence + good outcome). Writes adjustments to staging — never
auto-applies.

Usage:
  python3 confidence_calibration.py --calibrate    # generate report + staging
  python3 confidence_calibration.py --report       # show last calibration report
  python3 confidence_calibration.py --status       # show current thresholds
"""

import json
import logging
from datetime import datetime, date, timedelta
from logging.handlers import RotatingFileHandler
from pathlib import Path

# ── Paths ─────────────────────────────────────────────────────

BASE = Path(__file__).parent
STATE_DIR = BASE.parent.parent / "state"
CALIBRATION_PATH = STATE_DIR / "confidence_calibration.json"
REPORT_PATH = STATE_DIR / "confidence_calibration_report.json"
LOG_PATH = BASE / "logs" / "confidence_calibration.log"

# Default thresholds (mirror confidence_engine.py)
DEFAULT_THRESHOLDS = {
    "high": 0.80,
    "medium": 0.60,
}

# ── Logging ───────────────────────────────────────────────────

LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
logger = logging.getLogger("confidence_calibration")
logger.setLevel(logging.INFO)
if not logger.handlers:
    h = RotatingFileHandler(LOG_PATH, maxBytes=5_000_000, backupCount=3)
    h.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(h)


def _log(entry: dict):
    entry["ts"] = datetime.now().astimezone().isoformat()
    logger.info(json.dumps(entry, default=str, ensure_ascii=False))


# ── State ─────────────────────────────────────────────────────

def _load_state() -> dict:
    if CALIBRATION_PATH.exists():
        try:
            return json.loads(CALIBRATION_PATH.read_text())
        except Exception:
            pass
    return {
        "thresholds": dict(DEFAULT_THRESHOLDS),
        "staged_adjustments": None,
        "last_calibrated": None,
        "history": [],
    }


def _save_state(state: dict):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    CALIBRATION_PATH.write_text(
        json.dumps(state, indent=2, default=str, ensure_ascii=False))


# ── Calibration logic ─────────────────────────────────────────

def _load_decisions(days: int = 14) -> list:
    """Load scored decisions from decision_quality."""
    try:
        from runtime.decision_quality import get_range
        return get_range(days)
    except Exception:
        # Fallback: read files directly
        dq_dir = STATE_DIR / "decision_quality"
        if not dq_dir.exists():
            return []
        entries = []
        for i in range(days):
            d = date.today() - timedelta(days=i)
            p = dq_dir / f"daily_{d.isoformat()}.json"
            if p.exists():
                try:
                    entries.extend(json.loads(p.read_text()))
                except Exception:
                    continue
        return entries


def calibrate(days: int = 14) -> dict:
    """Run calibration against real outcomes.

    Returns calibration report dict.
    """
    decisions = _load_decisions(days)

    if not decisions:
        report = {
            "calibrated_at": datetime.now().astimezone().isoformat(),
            "days_analyzed": days,
            "total_decisions": 0,
            "calibration_error": 0.0,
            "overconfidence_rate": 0.0,
            "underconfidence_rate": 0.0,
            "suggested_adjustments": None,
            "message": "No decisions to calibrate against",
        }
        _log({"action": "calibrate", "result": "no_data"})
        return report

    # Classify each decision
    overconfident = []  # high confidence + bad outcome
    underconfident = []  # low confidence + good outcome
    well_calibrated = []

    total = len(decisions)
    confidence_errors = []

    for d in decisions:
        conf = d.get("confidence", 0.5)
        quality = d.get("quality", "neutral")

        # Map quality to actual accuracy (1=good, 0.5=neutral, 0=bad)
        actual = {"good": 1.0, "neutral": 0.5, "bad": 0.0}.get(quality, 0.5)
        confidence_errors.append(abs(conf - actual))

        if conf >= 0.7 and quality == "bad":
            overconfident.append(d)
        elif conf <= 0.5 and quality == "good":
            underconfident.append(d)
        else:
            well_calibrated.append(d)

    cal_error = sum(confidence_errors) / len(confidence_errors) if confidence_errors else 0.0
    over_rate = len(overconfident) / total if total else 0.0
    under_rate = len(underconfident) / total if total else 0.0

    # Suggest adjustments if miscalibrated
    suggested = None
    state = _load_state()
    current = state.get("thresholds", dict(DEFAULT_THRESHOLDS))

    if over_rate > 0.15:
        # Too many overconfident decisions — raise the high threshold
        new_high = min(current["high"] + 0.05, 0.95)
        suggested = {
            "high": round(new_high, 2),
            "medium": current["medium"],
            "reason": f"overconfidence rate {over_rate:.0%} > 15% — raise high threshold",
        }
    elif under_rate > 0.20:
        # Too many underconfident — lower medium threshold
        new_med = max(current["medium"] - 0.05, 0.40)
        suggested = {
            "high": current["high"],
            "medium": round(new_med, 2),
            "reason": f"underconfidence rate {under_rate:.0%} > 20% — lower medium threshold",
        }

    report = {
        "calibrated_at": datetime.now().astimezone().isoformat(),
        "days_analyzed": days,
        "total_decisions": total,
        "calibration_error": round(cal_error, 4),
        "overconfidence_rate": round(over_rate, 4),
        "underconfidence_rate": round(under_rate, 4),
        "overconfident_count": len(overconfident),
        "underconfident_count": len(underconfident),
        "well_calibrated_count": len(well_calibrated),
        "current_thresholds": current,
        "suggested_adjustments": suggested,
    }

    # Save report
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(
        json.dumps(report, indent=2, default=str, ensure_ascii=False))

    # Stage adjustment (never auto-apply)
    if suggested:
        state["staged_adjustments"] = suggested
        state["last_calibrated"] = report["calibrated_at"]
        state["history"] = (state.get("history", []) + [report])[-20:]
        _save_state(state)
        _log({"action": "staged_adjustment", "suggested": suggested})
    else:
        state["staged_adjustments"] = None
        state["last_calibrated"] = report["calibrated_at"]
        state["history"] = (state.get("history", []) + [report])[-20:]
        _save_state(state)

    _log({"action": "calibrate", "total": total,
          "cal_error": round(cal_error, 4),
          "over_rate": round(over_rate, 4),
          "under_rate": round(under_rate, 4)})

    return report


def get_current_thresholds() -> dict:
    """Get active thresholds (not staged ones)."""
    state = _load_state()
    return state.get("thresholds", dict(DEFAULT_THRESHOLDS))


def get_staged_adjustments() -> dict | None:
    """Get pending staged adjustments (awaiting Sebas review)."""
    state = _load_state()
    return state.get("staged_adjustments")


def apply_staged(confirm: bool = False) -> bool:
    """Apply staged adjustments. Requires confirm=True as safety."""
    if not confirm:
        return False
    state = _load_state()
    staged = state.get("staged_adjustments")
    if not staged:
        return False
    state["thresholds"]["high"] = staged["high"]
    state["thresholds"]["medium"] = staged["medium"]
    state["staged_adjustments"] = None
    _save_state(state)
    _log({"action": "applied_adjustment", "new_thresholds": state["thresholds"]})
    return True


def get_last_report() -> dict | None:
    if REPORT_PATH.exists():
        try:
            return json.loads(REPORT_PATH.read_text())
        except Exception:
            return None
    return None


# ── CLI ───────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="Confidence calibration")
    p.add_argument("--calibrate", action="store_true", help="Run calibration")
    p.add_argument("--report", action="store_true", help="Show last report")
    p.add_argument("--status", action="store_true", help="Show current state")
    p.add_argument("--days", type=int, default=14, help="Days to analyze")
    args = p.parse_args()

    if args.calibrate:
        r = calibrate(args.days)
        print(f"Calibration ({r['days_analyzed']} days, {r['total_decisions']} decisions):")
        print(f"  Calibration error: {r['calibration_error']:.2%}")
        print(f"  Overconfidence:    {r['overconfidence_rate']:.0%} "
              f"({r.get('overconfident_count', 0)} decisions)")
        print(f"  Underconfidence:   {r['underconfidence_rate']:.0%} "
              f"({r.get('underconfident_count', 0)} decisions)")
        if r.get("suggested_adjustments"):
            s = r["suggested_adjustments"]
            print(f"\n  Staged adjustment: {s['reason']}")
            print(f"    high={s['high']}, medium={s['medium']}")
            print("  (awaiting manual apply)")
        else:
            print("\n  No adjustment needed.")

    elif args.report:
        r = get_last_report()
        if r:
            print(json.dumps(r, indent=2))
        else:
            print("No calibration report found. Run --calibrate first.")

    elif args.status:
        t = get_current_thresholds()
        s = get_staged_adjustments()
        print(f"Active thresholds: high={t['high']}, medium={t['medium']}")
        if s:
            print(f"Staged (pending):  high={s['high']}, medium={s['medium']}")
            print(f"  Reason: {s['reason']}")
        else:
            print("No staged adjustments.")
    else:
        p.print_help()
