#!/usr/bin/env python3
"""
Tenazas Runtime — lightweight event-driven AI orchestration layer.
Runs every 5 minutes via cron. Idempotent and safe to re-run.

Subsystems:
  1. State Engine     — snapshot of infra health
  2. Event Detection  — diff between current and previous state
  3. Task Queue       — events → pending tasks
  4. Executors        — dispatch to executors.py
  5. Memory Log       — append-only interaction history
  6. Pattern Engine   — detect time-based behavioral habits
"""

import datetime
import json
import subprocess
import sys
from pathlib import Path

# ── Paths ─────────────────────────────────────────────────────────────────────
BASE          = Path(__file__).parent
STATE_FILE    = BASE / "state.json"
EVENTS_FILE   = BASE / "events.json"
TASKS_FILE    = BASE / "tasks.json"
MEMORY_LOG    = BASE / "memory_log.json"
PATTERNS_FILE = BASE / "patterns.json"
REMINDERS     = BASE.parent / "memory" / "reminders.json"
LOGS_DIR      = BASE / "logs"
RUNTIME_LOG   = LOGS_DIR / "runtime.log"

LOGS_DIR.mkdir(exist_ok=True)

# ── Constants ─────────────────────────────────────────────────────────────────
EXPECTED_CONTAINERS = {
    "gluetun", "qbittorrent", "sonarr", "radarr", "lidarr", "prowlarr",
    "seerr", "requestrr", "tautulli", "sabnzbd",
    "hireswarm-agent-server", "hireswarm-inngest",
    "backing-track-generator", "openclaw", "ollama",
    "chromadb", "piper", "portainer", "watchtower",
}

# Only auto-restart these — others need manual attention
AUTO_RESTART = {"gluetun", "openclaw", "chromadb", "piper"}


# ── Helpers ───────────────────────────────────────────────────────────────────
def now_iso() -> str:
    tz = datetime.timezone(datetime.timedelta(hours=-3))
    return datetime.datetime.now(tz).isoformat()

def log(msg: str):
    line = f"[{now_iso()}] {msg}\n"
    with open(RUNTIME_LOG, "a") as f:
        f.write(line)
    # Keep log under 500KB — truncate oldest half when large
    if RUNTIME_LOG.stat().st_size > 500_000:
        lines = RUNTIME_LOG.read_text().splitlines()
        RUNTIME_LOG.write_text("\n".join(lines[len(lines)//2:]) + "\n")

def sh(cmd: str, timeout: int = 15) -> tuple[int, str]:
    try:
        r = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=timeout
        )
        return r.returncode, (r.stdout + r.stderr).strip()
    except subprocess.TimeoutExpired:
        return 1, "timeout"
    except Exception as e:
        return 1, str(e)

def load(path: Path, default):
    if path.exists():
        try:
            return json.loads(path.read_text())
        except Exception:
            pass
    return default

def save(path: Path, data):
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False))


# ══════════════════════════════════════════════════════════════════════════════
# SUBSYSTEM 1 — STATE ENGINE
# Reuses the same shell commands as skills/infra-monitor/SKILL.md
# ══════════════════════════════════════════════════════════════════════════════
def update_state() -> dict:
    state: dict = {"timestamp": now_iso()}

    # 1. Running containers
    rc, out = sh("docker ps --format '{{.Names}}'", timeout=10)
    running = set(out.splitlines()) if rc == 0 and out else set()
    state["containers_running"] = sorted(running)
    state["containers_total"]   = len(running)
    state["containers_down"]    = sorted(EXPECTED_CONTAINERS - running)

    # 2. Internet
    rc, _ = sh("ping -c 2 -W 3 8.8.8.8", timeout=12)
    state["internet"] = "ok" if rc == 0 else "down"

    # 3. VPN IP via gluetun HTTP proxy
    rc, vpn = sh("curl -sf --max-time 8 -x http://192.168.2.2:8888 https://ifconfig.me", timeout=12)
    state["vpn_ip"] = vpn.strip() if rc == 0 else ""

    # 4. Real public IP
    rc, pub = sh("curl -sf --max-time 8 https://ifconfig.me", timeout=12)
    state["public_ip"] = pub.strip() if rc == 0 else ""

    # 5. Devices on LAN
    rc, nmap = sh("nmap -sn 192.168.2.0/24 --host-timeout 3s 2>/dev/null", timeout=35)
    prev = load(STATE_FILE, {})
    state["devices_online"] = nmap.count("Host is up") if rc == 0 else prev.get("devices_online", 0)

    # 6. Ollama notebook (primary GPU)
    rc, _ = sh("curl -sf --max-time 5 http://192.168.2.76:11434/api/tags", timeout=8)
    state["ollama_notebook"] = "ok" if rc == 0 else "down"

    # 7. Ollama NAS (CPU fallback)
    rc, _ = sh("curl -sf --max-time 5 http://127.0.0.1:11434/api/tags", timeout=8)
    state["ollama_nas"] = "ok" if rc == 0 else "down"

    save(STATE_FILE, state)
    log(f"state containers={state['containers_total']} "
        f"down={state['containers_down']} "
        f"internet={state['internet']} vpn={state['vpn_ip'] or 'n/a'}")
    return state


# ══════════════════════════════════════════════════════════════════════════════
# SUBSYSTEM 2 — EVENT DETECTION
# Diffs current state vs previous state and checks reminders.
# ══════════════════════════════════════════════════════════════════════════════
def detect_events(state: dict, prev: dict) -> list[dict]:
    events: list[dict] = []
    ts = state["timestamp"]

    # Container down (new failures only)
    prev_down = set(prev.get("containers_down", []))
    for c in state.get("containers_down", []):
        if c not in prev_down:
            events.append({"event": "container_down", "container": c, "time": ts})
            log(f"event container_down:{c}")

    # Internet lost / restored
    if state["internet"] == "down" and prev.get("internet") == "ok":
        events.append({"event": "internet_lost", "time": ts})
        log("event internet_lost")
    elif state["internet"] == "ok" and prev.get("internet") == "down":
        events.append({"event": "internet_restored", "time": ts})
        log("event internet_restored")

    # VPN IP changed (possible leak or reconnect)
    if state["vpn_ip"] and prev.get("vpn_ip") and state["vpn_ip"] != prev["vpn_ip"]:
        events.append({
            "event": "vpn_changed",
            "from":  prev["vpn_ip"],
            "to":    state["vpn_ip"],
            "time":  ts,
        })
        log(f"event vpn_changed {prev['vpn_ip']}→{state['vpn_ip']}")

    # New devices on LAN (more than 1 new host)
    if state["devices_online"] > prev.get("devices_online", 0) + 1:
        delta = state["devices_online"] - prev.get("devices_online", 0)
        events.append({"event": "device_joined", "count": delta, "time": ts})
        log(f"event device_joined count={delta}")

    # Reminder due
    tz = datetime.timezone(datetime.timedelta(hours=-3))
    now = datetime.datetime.now(tz)
    for r in load(REMINDERS, {}).get("reminders", []):
        if r.get("done"):
            continue
        try:
            due = datetime.datetime.fromisoformat(r["due"])
            if due.tzinfo is None:
                due = due.replace(tzinfo=tz)
            if due <= now:
                events.append({
                    "event":       "reminder_due",
                    "reminder_id": r["id"],
                    "text":        r["text"],
                    "time":        ts,
                })
                log(f"event reminder_due:{r['text'][:40]}")
        except Exception:
            pass

    # Persist — keep last 200
    history = load(EVENTS_FILE, [])
    history.extend(events)
    save(EVENTS_FILE, history[-200:])
    return events


# ══════════════════════════════════════════════════════════════════════════════
# SUBSYSTEM 3 — TASK QUEUE
# Maps events to executor tasks. Deduplicates pending tasks.
# ══════════════════════════════════════════════════════════════════════════════
def enqueue_tasks(events: list[dict]):
    tasks = load(TASKS_FILE, [])
    pending = {(t["task"], t.get("target", "")) for t in tasks if t["status"] == "pending"}

    def enqueue(task_name: str, target: str = "", meta: dict | None = None):
        key = (task_name, target)
        if key in pending:
            return
        entry = {"task": task_name, "target": target, "status": "pending", "enqueued_at": now_iso()}
        if meta:
            entry.update(meta)
        tasks.append(entry)
        pending.add(key)
        log(f"enqueue {task_name} target={target!r}")

    for ev in events:
        etype = ev["event"]

        if etype == "container_down":
            c = ev["container"]
            if c in AUTO_RESTART:
                enqueue("docker.restart", c)
                # Gluetun is the VPN gateway — restart arr-stack after it
                if c == "gluetun":
                    enqueue("docker.start", "arr-stack")

        elif etype in ("internet_lost", "internet_restored", "vpn_changed"):
            enqueue("network.scan")

    save(TASKS_FILE, tasks[-500:])


# ══════════════════════════════════════════════════════════════════════════════
# SUBSYSTEM 4 — TASK RUNNER
# Dispatches pending tasks to executors.py. Updates status after run.
# ══════════════════════════════════════════════════════════════════════════════
def run_tasks():
    import executors  # local import — only needed when tasks are pending
    tasks = load(TASKS_FILE, [])
    dirty = False
    for task in tasks:
        if task["status"] != "pending":
            continue
        try:
            ok = executors.execute(task)
            task["status"] = "done" if ok else "failed"
        except Exception as e:
            task["status"] = "failed"
            task["error"]  = str(e)
            log(f"executor_error {task['task']}: {e}")
        task["executed_at"] = now_iso()
        dirty = True
    if dirty:
        save(TASKS_FILE, tasks)


# ══════════════════════════════════════════════════════════════════════════════
# SUBSYSTEM 5 — MEMORY LOG  (public API for external callers)
# Skills can import this function to record interactions.
# ══════════════════════════════════════════════════════════════════════════════
def log_interaction(interaction_type: str, text: str, result: str = ""):
    """
    Record a Tenazas interaction for pattern analysis.
    Call from any skill: python3 -c "import runtime; runtime.log_interaction('command', 'luces relax')"
    """
    memory = load(MEMORY_LOG, [])
    memory.append({"time": now_iso(), "type": interaction_type, "text": text, "result": result})
    save(MEMORY_LOG, memory[-1000:])


# ══════════════════════════════════════════════════════════════════════════════
# SUBSYSTEM 6 — PATTERN ENGINE
# Detects time-based behavioral habits from memory_log.json.
# Requires at least 20 entries and 3 occurrences to declare a pattern.
# ══════════════════════════════════════════════════════════════════════════════
_HUE_KEYWORDS = {
    "hue.relax":   ["relax", "relajar", "descanso"],
    "hue.cinema":  ["cinema", "cine", "película", "pelicula", "peli"],
    "hue.work":    ["work", "trabajo", "trabajar", "estudiar"],
    "hue.off":     ["apagar", "apagá", "dormir", "noche", "off"],
    "hue.on":      ["encender", "prender", "all on", "full"],
}

def _classify_hue(text: str) -> str:
    t = text.lower()
    for action, words in _HUE_KEYWORDS.items():
        if any(w in t for w in words):
            return action
    return ""

def analyze_patterns():
    memory = load(MEMORY_LOG, [])
    if len(memory) < 20:
        return

    from collections import Counter
    buckets: Counter = Counter()

    for entry in memory:
        try:
            t = datetime.datetime.fromisoformat(entry["time"])
            minute_bucket = (t.minute // 30) * 30
            time_key = f"{t.hour:02d}:{minute_bucket:02d}"
            action = _classify_hue(entry.get("text", ""))
            if action:
                buckets[(time_key, action)] += 1
        except Exception:
            pass

    patterns = []
    for (time_key, action), count in buckets.items():
        if count >= 3:
            patterns.append({
                "pattern":     "time_trigger",
                "time":        time_key,
                "action":      action,
                "occurrences": count,
            })

    if patterns:
        save(PATTERNS_FILE, sorted(patterns, key=lambda p: -p["occurrences"]))
        log(f"patterns {len(patterns)} detected")

def trigger_patterns():
    patterns = load(PATTERNS_FILE, [])
    if not patterns:
        return

    tz = datetime.timezone(datetime.timedelta(hours=-3))
    now = datetime.datetime.now(tz)
    now_bucket = f"{now.hour:02d}:{(now.minute // 30) * 30:02d}"

    tasks  = load(TASKS_FILE, [])
    pending = {(t["task"], t.get("target", "")) for t in tasks if t["status"] == "pending"}

    for p in patterns:
        if p.get("pattern") != "time_trigger" or p.get("time") != now_bucket:
            continue
        action = p.get("action", "")
        if not action.startswith("hue."):
            continue
        scene = action.split(".", 1)[1]
        key = ("hue.scene", scene)
        if key not in pending:
            tasks.append({
                "task":        "hue.scene",
                "target":      scene,
                "status":      "pending",
                "enqueued_at": now_iso(),
                "source":      "pattern",
            })
            pending.add(key)
            log(f"pattern_triggered hue.scene:{scene} at {now_bucket}")

    save(TASKS_FILE, tasks)


# ══════════════════════════════════════════════════════════════════════════════
# MAIN LOOP
# ══════════════════════════════════════════════════════════════════════════════
def main():
    log("── cycle start ──")
    prev  = load(STATE_FILE, {})
    state = update_state()
    events = detect_events(state, prev)
    enqueue_tasks(events)
    run_tasks()
    analyze_patterns()
    trigger_patterns()
    log(f"── cycle done | events={len(events)} ──\n")


if __name__ == "__main__":
    main()
