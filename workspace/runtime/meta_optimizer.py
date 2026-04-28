#!/usr/bin/env python3
"""
Meta-Optimizer — Optimizes HOW Tenazas learns, not WHAT it learns.

Three dimensions:
- learning_frequency: how often to run error_mining
- signal_quality: which error types produce useful candidates
- experiment_calibration: are experiments well-designed

Output: state/meta_optimizer.json (config) + state/meta_optimizer_report.json (weekly)

Usage:
    python3 meta_optimizer.py --analyze     # run analysis
    python3 meta_optimizer.py --status      # show current config
"""

import json
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from runtime_utils import now
try:
    from .runtime_utils import load_json, save_json
except ImportError:
    from runtime_utils import load_json, save_json

BASE_DIR = Path(__file__).parent
STATE_DIR = Path("/home/tenazas/.openclaw/state") if Path("/home/tenazas").exists() else BASE_DIR.parent / "state"
CONFIG_FILE = STATE_DIR / "meta_optimizer.json"
REPORT_FILE = STATE_DIR / "meta_optimizer_report.json"
CANDIDATES_FILE = STATE_DIR / "improvement_candidates.json"
EXPERIMENTS_DIR = STATE_DIR / "experiments"
ERROR_MINING_DIR = STATE_DIR / "error_mining"
LOG_FILE = BASE_DIR / "logs" / "meta_optimizer.log"

# Defaults
DEFAULT_CONFIG = {
    "learning_frequency_hours": 24,
    "min_learning_frequency_hours": 6,
    "max_learning_frequency_hours": 48,
    "deprecated_signals": [],
    "promoted_signals": [],
    "max_experiment_traffic_pct": 10,
}

DEPRECATION_THRESHOLD = 0.10    # < 10% approval rate after 20 samples → deprecate
PROMOTION_THRESHOLD = 0.70      # > 70% approval rate → promote
MIN_SAMPLES_FOR_JUDGMENT = 20
ROLLBACK_RATE_LIMIT = 0.20      # if > 20% experiments rollback → lower traffic



def _log(entry: dict):
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    entry["ts"] = now().isoformat()
    try:
        with open(LOG_FILE, "a") as f:
            f.write(json.dumps(entry, default=str) + "\n")
    except OSError:
        pass


def load_config() -> dict:
    config = load_json(CONFIG_FILE, {})
    merged = dict(DEFAULT_CONFIG)
    merged.update(config)
    return merged


def save_config(config: dict):
    save_json(CONFIG_FILE, config)


# ── Analyzers ───────────────────────────────────────────────────


def analyze_learning_frequency() -> dict:
    """Analyze error rates to suggest optimal mining frequency."""
    config = load_config()

    # Count recent errors from mining reports
    total_errors = 0
    report_count = 0
    if ERROR_MINING_DIR.exists():
        for f in sorted(ERROR_MINING_DIR.glob("daily_*.json"))[-7:]:
            report = load_json(f, {})
            total_errors += report.get("total_errors", 0)
            report_count += 1

    avg_errors = total_errors / max(report_count, 1)
    current = config["learning_frequency_hours"]

    if avg_errors > 20:
        suggested = max(config["min_learning_frequency_hours"], current - 6)
        reason = f"high error rate ({avg_errors:.0f}/day) → increase frequency"
    elif avg_errors < 5 and report_count >= 3:
        suggested = min(config["max_learning_frequency_hours"], current + 12)
        reason = f"low error rate ({avg_errors:.0f}/day) → decrease frequency"
    else:
        suggested = current
        reason = f"stable error rate ({avg_errors:.0f}/day) → no change"

    return {
        "current_hours": current,
        "suggested_hours": suggested,
        "avg_errors_per_day": round(avg_errors, 1),
        "reports_analyzed": report_count,
        "reason": reason,
    }


def analyze_signal_quality() -> dict:
    """Analyze which error types produce useful vs rejected candidates."""
    candidates = load_json(CANDIDATES_FILE, [])
    if not isinstance(candidates, list):
        candidates = []

    by_type = {}
    for c in candidates:
        ctype = c.get("type", "unknown")
        if ctype not in by_type:
            by_type[ctype] = {"total": 0, "approved": 0, "rejected": 0}
        by_type[ctype]["total"] += 1
        status = c.get("status", "")
        if status == "approved":
            by_type[ctype]["approved"] += 1
        elif status == "rejected":
            by_type[ctype]["rejected"] += 1

    deprecated = []
    promoted = []
    for ctype, counts in by_type.items():
        if counts["total"] < MIN_SAMPLES_FOR_JUDGMENT:
            continue
        approval_rate = counts["approved"] / counts["total"]
        if approval_rate < DEPRECATION_THRESHOLD:
            deprecated.append({"type": ctype, "approval_rate": round(approval_rate, 3),
                               "samples": counts["total"]})
        elif approval_rate > PROMOTION_THRESHOLD:
            promoted.append({"type": ctype, "approval_rate": round(approval_rate, 3),
                             "samples": counts["total"]})

    return {
        "by_type": by_type,
        "deprecated": deprecated,
        "promoted": promoted,
        "total_candidates": len(candidates),
    }


def analyze_experiment_calibration() -> dict:
    """Analyze experiment outcomes to calibrate design."""
    config = load_config()

    if not EXPERIMENTS_DIR.exists():
        return {"experiments_total": 0, "rollback_rate": 0,
                "current_traffic_pct": config["max_experiment_traffic_pct"],
                "suggested_traffic_pct": config["max_experiment_traffic_pct"],
                "reason": "no experiments"}

    total = 0
    rollbacks = 0
    inconclusive = 0

    for f in EXPERIMENTS_DIR.glob("*.json"):
        try:
            exp = json.loads(f.read_text())
            total += 1
            reason = exp.get("stop_reason", "")
            if "rollback" in reason or "metric dropped" in reason:
                rollbacks += 1
            elif reason == "expired" or reason == "manual":
                status = exp.get("status", "")
                results = exp.get("results", {})
                ctrl = results.get("control", {}).get("count", 0)
                var = results.get("variant", {}).get("count", 0)
                if ctrl < 5 or var < 3:
                    inconclusive += 1
        except Exception:
            continue

    rollback_rate = rollbacks / max(total, 1)
    suggested_traffic = config["max_experiment_traffic_pct"]

    if rollback_rate > ROLLBACK_RATE_LIMIT:
        suggested_traffic = max(5, suggested_traffic - 5)
        reason = f"high rollback rate ({rollback_rate:.0%}) → lower traffic"
    elif inconclusive > total * 0.5 and total >= 3:
        reason = f"many inconclusive ({inconclusive}/{total}) → increase duration"
    else:
        reason = "experiments performing well"

    return {
        "experiments_total": total,
        "rollbacks": rollbacks,
        "inconclusive": inconclusive,
        "rollback_rate": round(rollback_rate, 3),
        "current_traffic_pct": config["max_experiment_traffic_pct"],
        "suggested_traffic_pct": suggested_traffic,
        "reason": reason,
    }


# ── Main API ────────────────────────────────────────────────────


def analyze() -> dict:
    """Run full meta-optimization analysis."""
    report = {
        "analyzed_at": now().isoformat(),
        "learning_frequency": analyze_learning_frequency(),
        "signal_quality": analyze_signal_quality(),
        "experiment_calibration": analyze_experiment_calibration(),
    }

    # Apply suggestions to config
    config = load_config()
    freq = report["learning_frequency"]
    if freq["suggested_hours"] != freq["current_hours"]:
        config["learning_frequency_hours"] = freq["suggested_hours"]

    sig = report["signal_quality"]
    config["deprecated_signals"] = [s["type"] for s in sig["deprecated"]]
    config["promoted_signals"] = [s["type"] for s in sig["promoted"]]

    exp = report["experiment_calibration"]
    if exp.get("suggested_traffic_pct") and exp.get("current_traffic_pct"):
        if exp["suggested_traffic_pct"] != exp["current_traffic_pct"]:
            config["max_experiment_traffic_pct"] = exp["suggested_traffic_pct"]

    save_config(config)
    save_json(REPORT_FILE, report)
    _log({"action": "analyze_complete", "changes": {
        "frequency": freq["suggested_hours"],
        "deprecated": len(sig["deprecated"]),
        "traffic": exp["suggested_traffic_pct"],
    }})

    return report


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="Meta-Optimizer")
    p.add_argument("--analyze", action="store_true", help="Run analysis")
    p.add_argument("--status", action="store_true", help="Show config")
    args = p.parse_args()

    if args.analyze:
        report = analyze()
        print(json.dumps(report, indent=2, default=str))
    elif args.status:
        print(json.dumps(load_config(), indent=2))
    else:
        p.print_help()
