#!/usr/bin/env python3
"""
goal_tracker.py
Persistent goal tracking — connects goal_engine.py to proactive_planner.

Goals persist in goals_state.json. Each goal has:
  - target metric + threshold
  - current value (updated each run)
  - status: active/achieved/blocked
  - history of evaluations

Usage:
  python3 goal_tracker.py --status    # show all goals
  python3 goal_tracker.py --evaluate  # evaluate + update
  python3 goal_tracker.py --add "uptime > 99%"
"""

import argparse
import json
from datetime import datetime
from pathlib import Path

STATE_PATH = Path(__file__).parent / "goals_state.json"
LOG_PATH = Path(__file__).parent / "logs" / "goal_tracker.log"

DEFAULT_GOALS = [
    {"id": "uptime", "name": "Container uptime", "metric": "containers_up_pct",
     "target": 95, "operator": ">=", "unit": "%"},
    {"id": "router_accuracy", "name": "Router classification accuracy", "metric": "gemma_accuracy",
     "target": 90, "operator": ">=", "unit": "%"},
    {"id": "response_time", "name": "Skill response under 5s", "metric": "avg_response_ms",
     "target": 5000, "operator": "<=", "unit": "ms"},
    {"id": "feedback_positive", "name": "Positive feedback rate", "metric": "feedback_positive_pct",
     "target": 70, "operator": ">=", "unit": "%"},
]


def _log(entry: dict):
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    entry["ts"] = datetime.now().astimezone().isoformat()
    with open(LOG_PATH, "a") as f:
        f.write(json.dumps(entry, default=str) + "\n")


def _load_state() -> dict:
    try:
        return json.loads(STATE_PATH.read_text()) if STATE_PATH.exists() else {}
    except Exception:
        return {}


def _save_state(state: dict):
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2, default=str))


def _collect_metric(metric_id: str) -> float | None:
    """Collect current value for a metric."""
    import subprocess
    try:
        if metric_id == "containers_up_pct":
            r = subprocess.run(["docker", "ps", "-a", "--format", "{{.Status}}"],
                               capture_output=True, text=True, timeout=10)
            lines = [l for l in r.stdout.strip().splitlines() if l]
            up = sum(1 for l in lines if "Up" in l)
            return round(up / len(lines) * 100, 1) if lines else 0

        elif metric_id == "feedback_positive_pct":
            fb_path = Path(__file__).parent / "learning" / "feedback_log.jsonl"
            if not fb_path.exists():
                return None
            pos = neg = 0
            for line in fb_path.read_text().splitlines():
                try:
                    e = json.loads(line)
                    if e.get("feedback") == "positive":
                        pos += 1
                    elif "negative" in e.get("feedback", ""):
                        neg += 1
                except Exception:
                    continue
            total = pos + neg
            return round(pos / total * 100, 1) if total > 0 else None

        elif metric_id == "gemma_accuracy":
            log_path = Path(__file__).parent / "logs" / "router_classify.log"
            if not log_path.exists():
                return None
            gemma = total = 0
            for line in log_path.read_text().splitlines()[-100:]:
                try:
                    e = json.loads(line)
                    total += 1
                    if e.get("tier") == "gemma":
                        gemma += 1
                except Exception:
                    continue
            return round(gemma / total * 100, 1) if total > 0 else None

        elif metric_id == "avg_response_ms":
            log_path = Path(__file__).parent / "logs" / "router_classify.log"
            if not log_path.exists():
                return None
            times = []
            for line in log_path.read_text().splitlines()[-50:]:
                try:
                    e = json.loads(line)
                    if e.get("ms"):
                        times.append(e["ms"])
                except Exception:
                    continue
            return round(sum(times) / len(times)) if times else None

    except Exception:
        return None
    return None


def _check_goal(goal: dict, value: float) -> bool:
    """Check if a goal is met."""
    op = goal.get("operator", ">=")
    target = goal.get("target", 0)
    if op == ">=":
        return value >= target
    elif op == "<=":
        return value <= target
    elif op == ">":
        return value > target
    elif op == "<":
        return value < target
    return False


def init_goals():
    """Initialize goals from defaults if not exists."""
    state = _load_state()
    if state.get("goals"):
        return state

    state["goals"] = {}
    for g in DEFAULT_GOALS:
        state["goals"][g["id"]] = {
            **g,
            "status": "active",
            "current_value": None,
            "history": [],
            "created_at": datetime.now().astimezone().isoformat(),
        }
    _save_state(state)
    return state


def evaluate():
    """Evaluate all active goals, update state."""
    state = init_goals()
    now = datetime.now().astimezone().isoformat()
    results = []

    for gid, goal in state["goals"].items():
        if goal.get("status") != "active":
            continue

        value = _collect_metric(goal["metric"])
        if value is None:
            continue

        met = _check_goal(goal, value)
        goal["current_value"] = value
        goal["status"] = "achieved" if met else "active"
        goal["last_evaluated"] = now
        goal.setdefault("history", []).append({
            "ts": now, "value": value, "met": met,
        })
        # Keep last 50 history entries
        goal["history"] = goal["history"][-50:]

        icon = "OK" if met else "MISS"
        results.append(f"[{icon}] {goal['name']}: {value}{goal.get('unit','')} "
                       f"(target: {goal['operator']}{goal['target']})")

    state["last_evaluation"] = now
    _save_state(state)
    _log({"action": "evaluate", "goals": len(results)})
    return results


def get_blocked_goals() -> list[dict]:
    """Return goals that are not met — for planner integration."""
    state = init_goals()
    blocked = []
    for gid, goal in state["goals"].items():
        if goal.get("status") == "active" and goal.get("current_value") is not None:
            if not _check_goal(goal, goal["current_value"]):
                blocked.append(goal)
    return blocked


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--status", action="store_true")
    p.add_argument("--evaluate", action="store_true")
    args = p.parse_args()

    if args.evaluate:
        results = evaluate()
        for r in results:
            print(r)
    elif args.status:
        state = init_goals()
        for gid, g in state["goals"].items():
            val = g.get("current_value", "?")
            status = g.get("status", "?")
            print(f"  [{status:8s}] {g['name']:35s} {val}{g.get('unit','')} "
                  f"(target: {g['operator']}{g['target']})")
    else:
        p.print_help()
