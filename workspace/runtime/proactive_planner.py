#!/usr/bin/env python3
"""
proactive_planner.py
Inspects system state every 5 min, generates task queue for orchestrator.

Sources: infra, calendar, finance, memory, metrics, GPU.
Output: task_queue.json + optional Telegram alerts for critical items.
"""

import json
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from urllib.request import Request, urlopen

RUNTIME = Path(__file__).parent
QUEUE_PATH = RUNTIME / "task_queue.json"
LOG_PATH = RUNTIME / "logs" / "proactive_planner.log"
CONFIG_PATH = Path("/home/tenazas/.openclaw/openclaw.json")
OLLAMA_URL = "http://192.168.2.6:11434"
CHAT_ID = "1565794551"


def _log(entry: dict):
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    entry["ts"] = datetime.now().astimezone().isoformat()
    with open(LOG_PATH, "a") as f:
        f.write(json.dumps(entry, default=str) + "\n")


def _notify(msg: str):
    try:
        token = json.loads(CONFIG_PATH.read_text())["channels"]["telegram"]["botToken"]
        payload = json.dumps({"chat_id": CHAT_ID, "text": msg[:4090]}).encode()
        urlopen(Request(f"https://api.telegram.org/bot{token}/sendMessage",
                        data=payload, headers={"Content-Type": "application/json"}), timeout=10)
    except Exception:
        pass


# ── Inspectors ───────────────────────────────────────────────────

def _check_infra() -> list:
    """Check Docker containers."""
    tasks = []
    try:
        r = subprocess.run(["docker", "ps", "-a", "--format", "{{.Names}}\t{{.Status}}"],
                           capture_output=True, text=True, timeout=10)
        for line in r.stdout.strip().splitlines():
            parts = line.split("\t")
            if len(parts) == 2 and "Up" not in parts[1]:
                tasks.append({"type": "infra", "priority": "high",
                              "action": f"restart_{parts[0]}", "detail": f"{parts[0]} is {parts[1]}"})
    except Exception:
        pass
    return tasks


def _check_calendar() -> list:
    """Check events in next 2 hours."""
    tasks = []
    try:
        r = subprocess.run(["python3", "/home/tenazas/.openclaw/workspace/skills/calendar-agenda/gcal.py", "1"],
                           capture_output=True, text=True, timeout=15)
        now = datetime.now().astimezone()
        cutoff = now + timedelta(hours=2)
        for line in r.stdout.strip().splitlines():
            if "|" not in line:
                continue
            ts_str, title = line.split("|", 1)
            try:
                dt = datetime.fromisoformat(ts_str.strip())
                if now <= dt <= cutoff:
                    mins = int((dt - now).total_seconds() / 60)
                    tasks.append({"type": "calendar", "priority": "medium",
                                  "action": "remind", "detail": f"In {mins}min: {title.strip()}"})
            except Exception:
                continue
    except Exception:
        pass
    return tasks


def _check_gpu() -> list:
    """Check GPU VRAM state."""
    tasks = []
    try:
        resp = urlopen(Request(f"{OLLAMA_URL}/api/ps"), timeout=5)
        ps = json.loads(resp.read())
        models = ps.get("models", [])
        has_gemma = any("gemma3:12b" in m["name"] for m in models)
        if not has_gemma:
            tasks.append({"type": "gpu", "priority": "high",
                          "action": "load_gemma", "detail": "gemma3:12b not in VRAM"})
    except Exception:
        tasks.append({"type": "gpu", "priority": "high",
                      "action": "check_ollama", "detail": "Ollama not responding"})
    return tasks


def _check_metrics() -> list:
    """Check key metrics for anomalies."""
    tasks = []
    try:
        r = urlopen(Request("http://localhost:9105/metrics"), timeout=5)
        text = r.read().decode()
        for line in text.splitlines():
            if line.startswith("runtime_health_score "):
                score = float(line.split()[-1])
                if score < 50:
                    tasks.append({"type": "metrics", "priority": "high",
                                  "action": "investigate_health", "detail": f"Health score: {score}"})
    except Exception:
        pass
    return tasks


def _check_feedback() -> list:
    """Check recent negative feedback."""
    tasks = []
    fb_path = RUNTIME / "learning" / "feedback_log.jsonl"
    if not fb_path.exists():
        return tasks
    negatives = 0
    recent = time.time() - 3600  # last hour
    for line in fb_path.read_text().splitlines()[-50:]:
        try:
            e = json.loads(line)
            if e.get("ts") and time.mktime(datetime.fromisoformat(e["ts"]).timetuple()) > recent:
                if "negative" in e.get("feedback", ""):
                    negatives += 1
        except Exception:
            continue
    if negatives >= 3:
        tasks.append({"type": "feedback", "priority": "medium",
                      "action": "review_skills", "detail": f"{negatives} negative feedback in last hour"})
    return tasks


# ── Main ─────────────────────────────────────────────────────────

def plan():
    """Run all inspectors, generate SUGGESTIONS (never auto-execute).

    Policy: Tenazas suggests, Sebas decides. No auto-enqueue to orchestrator.
    """
    suggestions = []
    for checker in [_check_infra, _check_calendar, _check_gpu, _check_metrics, _check_feedback]:
        try:
            suggestions.extend(checker())
        except Exception:
            continue

    suggestions.sort(key=lambda t: {"high": 0, "medium": 1, "low": 2}.get(t.get("priority", "low"), 2))

    # Save for reference only (not an execution queue)
    QUEUE_PATH.write_text(json.dumps(suggestions, indent=2, default=str))

    # Send suggestions to Telegram — Sebas responds "hacer X" to execute
    if suggestions:
        lines = ["Tenazas sugiere:"]
        for s in suggestions:
            icon = {"high": "!", "medium": "~", "low": "."}[s.get("priority", "low")]
            lines.append(f"  {icon} {s['detail']}")
        if any(s.get("priority") == "high" for s in suggestions):
            lines.append("\nResponde 'hacer X' para ejecutar.")
        _notify("\n".join(lines))

    _log({"action": "plan_complete", "suggestions": len(suggestions),
          "high": sum(1 for s in suggestions if s.get("priority") == "high")})
    return suggestions


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--run", action="store_true")
    p.add_argument("--status", action="store_true")
    args = p.parse_args()

    if args.status:
        if QUEUE_PATH.exists():
            tasks = json.loads(QUEUE_PATH.read_text())
            print(f"Tasks: {len(tasks)}")
            for t in tasks:
                print(f"  [{t['priority']:6s}] {t['type']:10s} {t['detail']}")
        else:
            print("No task queue yet")
    elif args.run:
        tasks = plan()
        print(f"Generated {len(tasks)} tasks")
        for t in tasks:
            print(f"  [{t['priority']:6s}] {t['type']:10s} {t['detail']}")
    else:
        p.print_help()
