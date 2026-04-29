#!/usr/bin/env python3
"""
decision_quality.py — Sprint 5: Governance & Stability

Evaluates every executed decision post-outcome.
Reads from decision_trace.py + action_validator.py outcomes.
Scores: good / neutral / bad.

Integrates additively with:
  - action_validator.py — after validation, call score()
  - approval_queue.py — rejection = bad score automatically
  - preference_learning.py — bad scores feed preference signals

Persist: state/decision_quality/daily_YYYY-MM-DD.json

Usage:
  python3 decision_quality.py --today        # show today's scores
  python3 decision_quality.py --summary      # aggregate stats
  python3 decision_quality.py --days 7       # last N days
"""

import json
import hashlib
import logging
from datetime import datetime, date, timedelta
from logging.handlers import RotatingFileHandler
from pathlib import Path

# ── Paths ─────────────────────────────────────────────────────

BASE = Path(__file__).parent
STATE_DIR = BASE.parent.parent / "state" / "decision_quality"
LOG_PATH = BASE / "logs" / "decision_quality.log"

# ── Logging ───────────────────────────────────────────────────

LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
logger = logging.getLogger("decision_quality")
logger.setLevel(logging.INFO)
if not logger.handlers:
    h = RotatingFileHandler(LOG_PATH, maxBytes=5_000_000, backupCount=3)
    h.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(h)


def _log(entry: dict):
    entry["ts"] = datetime.now().astimezone().isoformat()
    logger.info(json.dumps(entry, default=str, ensure_ascii=False))


# ── Persistence ───────────────────────────────────────────────

def _daily_path(d: date | None = None) -> Path:
    if d is None:
        d = date.today()
    return STATE_DIR / f"daily_{d.isoformat()}.json"


def _load_daily(d: date | None = None) -> list:
    p = _daily_path(d)
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text())
    except Exception:
        return []


def _save_daily(entries: list, d: date | None = None):
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    p = _daily_path(d)
    p.write_text(json.dumps(entries, indent=2, default=str, ensure_ascii=False))


def _make_id(action: str, target: str, ts: str) -> str:
    raw = f"{action}:{target}:{ts}"
    return hashlib.sha256(raw.encode()).hexdigest()[:12]


# ── Scoring ───────────────────────────────────────────────────

def score(action: str, target: str,
          validation_result: dict | None = None,
          approval_status: str | None = None,
          rollback: bool = False,
          confidence: float = 0.0,
          context: str = "") -> dict:
    """Score a decision post-outcome.

    Args:
        action: action name (e.g. "restart_container")
        target: target of the action (e.g. "sonarr")
        validation_result: dict from action_validator (needs "success" key)
        approval_status: "approved" | "rejected" | "expired" | None
        rollback: True if a rollback was triggered
        confidence: original confidence score (0-1)
        context: extra context string

    Returns:
        Scored decision dict.
    """
    now = datetime.now().astimezone()
    ts = now.isoformat()

    # Determine quality
    quality = "neutral"
    reason = "no clear signal"
    impact = 0.0

    # Bad signals
    if rollback:
        quality = "bad"
        reason = "rollback triggered"
        impact = -0.8
    elif approval_status == "rejected":
        quality = "bad"
        reason = "user rejected action"
        impact = -0.6
    elif approval_status == "expired":
        quality = "bad"
        reason = "approval expired (user ignored)"
        impact = -0.3
    elif validation_result and not validation_result.get("success", True):
        quality = "bad"
        reason = validation_result.get("reason", "validation failed")
        impact = -0.5
    # Good signals
    elif validation_result and validation_result.get("success", False):
        quality = "good"
        reason = validation_result.get("reason", "validation confirmed success")
        impact = 0.5
    elif approval_status == "approved":
        quality = "good"
        reason = "user approved action"
        impact = 0.3

    # Scale impact by confidence delta (overconfident bad = worse)
    if quality == "bad" and confidence > 0.8:
        impact *= 1.3  # overconfident failure is worse
    elif quality == "good" and confidence < 0.5:
        impact *= 1.2  # low-confidence success is more valuable signal

    decision_id = _make_id(action, target, ts)

    entry = {
        "decision_id": decision_id,
        "timestamp": ts,
        "action": action,
        "target": target,
        "quality": quality,
        "reason": reason,
        "impact_score": round(impact, 3),
        "confidence": round(confidence, 3),
        "context": context[:500],
        "feeds_into": ["self_benchmark", "confidence_engine", "proactive_planner"],
    }

    # Persist
    today = now.date()
    daily = _load_daily(today)
    daily.append(entry)
    _save_daily(daily, today)

    _log({"action": "scored", "decision_id": decision_id,
          "quality": quality, "impact": impact})

    # Feed into preference_learning if bad
    if quality == "bad":
        _feed_preference_learning(action, reason)

    return entry


def _feed_preference_learning(action: str, reason: str):
    """Send bad decision signal to preference_learning (additive)."""
    try:
        from runtime.preference_learning import record_signal
        record_signal("rejection", action, reason,
                      metadata={"source": "decision_quality"})
    except Exception:
        # preference_learning not available — fail open
        pass


# ── Query ─────────────────────────────────────────────────────

def get_today() -> list:
    return _load_daily(date.today())


def get_range(days: int = 7) -> list:
    """Get all scored decisions for the last N days."""
    all_entries = []
    for i in range(days):
        d = date.today() - timedelta(days=i)
        all_entries.extend(_load_daily(d))
    return all_entries


def summary(days: int = 7) -> dict:
    """Aggregate quality stats over N days."""
    entries = get_range(days)
    if not entries:
        return {"total": 0, "good": 0, "neutral": 0, "bad": 0,
                "good_rate": 0.0, "bad_rate": 0.0, "avg_impact": 0.0}

    total = len(entries)
    good = sum(1 for e in entries if e.get("quality") == "good")
    neutral = sum(1 for e in entries if e.get("quality") == "neutral")
    bad = sum(1 for e in entries if e.get("quality") == "bad")
    avg_impact = sum(e.get("impact_score", 0) for e in entries) / total

    return {
        "total": total,
        "good": good,
        "neutral": neutral,
        "bad": bad,
        "good_rate": round(good / total, 3) if total else 0.0,
        "bad_rate": round(bad / total, 3) if total else 0.0,
        "avg_impact": round(avg_impact, 3),
    }


def get_bad_decisions(days: int = 7) -> list:
    """Get only bad decisions — useful for calibration and learning."""
    return [e for e in get_range(days) if e.get("quality") == "bad"]


# ── CLI ───────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="Decision quality scorer")
    p.add_argument("--today", action="store_true", help="Show today's scores")
    p.add_argument("--summary", action="store_true", help="Aggregate stats")
    p.add_argument("--days", type=int, default=7, help="Days to look back")
    args = p.parse_args()

    if args.today:
        entries = get_today()
        if not entries:
            print("No decisions scored today.")
        else:
            for e in entries:
                q = e["quality"].upper()
                print(f"  [{q:7s}] {e['action']} → {e['target']} "
                      f"(impact={e['impact_score']:+.2f}) — {e['reason']}")
            s = summary(1)
            print(f"\nToday: {s['good']}G / {s['neutral']}N / {s['bad']}B "
                  f"(good rate: {s['good_rate']:.0%})")

    elif args.summary:
        s = summary(args.days)
        print(f"Last {args.days} days:")
        print(f"  Total: {s['total']}")
        print(f"  Good:    {s['good']} ({s['good_rate']:.0%})")
        print(f"  Neutral: {s['neutral']}")
        print(f"  Bad:     {s['bad']} ({s['bad_rate']:.0%})")
        print(f"  Avg impact: {s['avg_impact']:+.3f}")
    else:
        p.print_help()
