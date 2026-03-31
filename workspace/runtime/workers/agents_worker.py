#!/usr/bin/env python3
"""
Agents Worker — Runs multi-agent reasoning and skill analysis.

Isolated subprocess. Low priority — safe to skip or timeout.
"""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, "/home/tenazas")

BASE_DIR = Path(__file__).resolve().parent.parent
LOG_FILE = BASE_DIR / "logs" / "agents_worker.log"


def _log(msg: str):
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG_FILE, "a") as f:
        f.write(f"{ts} {msg}\n")


def run():
    start = time.time()
    _log("agents_worker start")
    results = {}

    # Skill analytics
    try:
        from runtime import skill_analytics
        stats = skill_analytics.get_usage_stats()
        results["skill_analytics"] = {
            "skills_tracked": len(stats) if isinstance(stats, dict) else 0,
        }
    except Exception as e:
        results["skill_analytics"] = {"error": str(e)[:200]}

    # Skill generator — check for new proposals
    try:
        from runtime import skill_generator
        proposals = skill_generator.generate_proposals()
        results["skill_proposals"] = {
            "new": len(proposals),
        }
    except Exception as e:
        results["skill_proposals"] = {"error": str(e)[:200]}

    # Self-dev engine — generate improvement proposals
    try:
        from runtime import self_dev_engine
        dev_proposals = self_dev_engine.generate_proposals()
        results["dev_proposals"] = {
            "new": len(dev_proposals),
        }
    except Exception as e:
        results["dev_proposals"] = {"error": str(e)[:200]}

    elapsed = round(time.time() - start, 2)
    _log(f"agents_worker done elapsed={elapsed}s")
    print(json.dumps({"ok": True, "elapsed": elapsed, **results}))


if __name__ == "__main__":
    run()
