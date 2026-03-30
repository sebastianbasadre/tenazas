#!/usr/bin/env python3
"""
Learning Worker — Runs idle learning, background agents, and goal evaluation.

Low-priority worker. Safe to skip or timeout without affecting core runtime.
"""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, "/home/tenazas")

BASE_DIR = Path(__file__).resolve().parent.parent
LOG_FILE = BASE_DIR / "logs" / "learning_worker.log"


def _log(msg: str):
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG_FILE, "a") as f:
        f.write(f"{ts} {msg}\n")


def run():
    start = time.time()
    _log("learning_worker start")
    results = {}

    # Background agents (infra_watcher, pattern_detector, metrics_collector)
    try:
        from runtime import background_agents
        agent_results = background_agents.check_and_run()
        results["agents"] = {
            "ran": len(agent_results),
            "ok": sum(1 for r in agent_results if r.get("ok")),
        }
    except Exception as e:
        results["agents"] = {"error": str(e)[:200]}

    # Goal evaluation
    try:
        from runtime import goal_engine
        goals = goal_engine.evaluate_goals()
        achieved = sum(1 for g in goals if g.get("status") == "achieved")
        results["goals"] = {"total": len(goals), "achieved": achieved}
    except Exception as e:
        results["goals"] = {"error": str(e)[:200]}

    # Idle learning (only if total elapsed < 30s to avoid blocking)
    if time.time() - start < 30:
        try:
            from runtime import idle_learning_engine
            analysis = idle_learning_engine.analyze_idle()
            results["idle_learning"] = {
                "suggestions": len(analysis.get("suggestions", [])),
            }
        except Exception as e:
            results["idle_learning"] = {"error": str(e)[:200]}

    elapsed = round(time.time() - start, 2)
    _log(f"learning_worker done elapsed={elapsed}s")
    print(json.dumps({"ok": True, "elapsed": elapsed, **results}))


if __name__ == "__main__":
    run()
