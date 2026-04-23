#!/usr/bin/env python3
"""
Tenazas Runtime — Event-driven orchestration layer.

Runs every 5 minutes via cron. Idempotent.

Loop: update_state → detect_events → enqueue_tasks → run_tasks
      → append_memory → analyze_patterns → trigger_patterns
"""

import json
import logging
import logging.handlers
import os
import signal
import sys
import time
import traceback
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

# Allow running as script or module.
if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).parent.parent))

if __package__:
    from . import executors
    from . import freeze_detector
    from . import metrics_exporter
else:
    from runtime import executors
    from runtime import freeze_detector
    from runtime import metrics_exporter

# ── SIGALRM watchdog (Linux only, ignored on Windows) ────────────

def _timeout_handler(signum, frame):
    raise TimeoutError("runtime watchdog triggered")

if hasattr(signal, "SIGALRM"):
    signal.signal(signal.SIGALRM, _timeout_handler)

# ── Paths ─────────────────────────────────────────────────────────

BASE_DIR = Path(__file__).parent
STATE_FILE = BASE_DIR / "state.json"
EVENTS_FILE = BASE_DIR / "events.json"
TASKS_FILE = BASE_DIR / "tasks.json"
MEMORY_FILE = BASE_DIR / "memory_log.json"
PATTERNS_FILE = BASE_DIR / "patterns.json"
LOGS_DIR = BASE_DIR / "logs"

# ── Config ────────────────────────────────────────────────────────

ARR_CONTAINERS = {
    "gluetun", "qbittorrent", "sonarr", "radarr", "lidarr",
    "prowlarr", "seerr", "requestrr", "tautulli", "sabnzbd",
}
SKIP_CONTAINERS = {"openclaw", "watchtower"}
MAX_MEMORY_ENTRIES = 1000
PATTERN_ANALYSIS_INTERVAL = 3600  # seconds
PATTERN_MIN_OCCURRENCES = 3
PATTERN_LOOKBACK_DAYS = 7
TASK_COOLDOWN_SECONDS = 1200  # 20 min — don't re-restart same target
NMAP_INTERVAL_SECONDS = 1800  # 30 min — rate-limit nmap scans
GLOBAL_TIMEOUT_SECONDS = 120  # 2 min — kill if stuck
LOCK_FILE = Path("/tmp/tenazas_runtime.lock")
LOCK_STALE_SECONDS = 600      # 10 min — treat lock as stale
NMAP_CACHE_FILE = BASE_DIR / "nmap_cache.json"

# ── JSON helpers ──────────────────────────────────────────────────


def _now():
    return datetime.now().astimezone()


def load_json(path: Path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default if default is not None else {}


def save_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2, default=str)


# ── Lockfile ──────────────────────────────────────────────────────


def _acquire_lock() -> bool:
    """Acquire lockfile. Returns False if already running.

    Uses file age for stale detection (10 min).
    """
    if LOCK_FILE.exists():
        try:
            age = time.time() - LOCK_FILE.stat().st_mtime
        except OSError:
            age = 0
        if age < LOCK_STALE_SECONDS:
            return False  # Recent lock — another instance running
        # Stale lock — remove and proceed
        _runtime_log(f"stale lock removed (age={int(age)}s)")

    LOCK_FILE.write_text(str(os.getpid()))
    return True


def _release_lock():
    """Release lockfile."""
    try:
        if LOCK_FILE.exists():
            LOCK_FILE.unlink()
    except OSError:
        pass


# ── Rotating Logger ──────────────────────────────────────────────

_logger = None


def _get_logger() -> logging.Logger:
    global _logger
    if _logger is not None:
        return _logger
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    _logger = logging.getLogger("tenazas.runtime")
    _logger.setLevel(logging.INFO)
    if not _logger.handlers:
        handler = logging.handlers.RotatingFileHandler(
            LOGS_DIR / "runtime.log",
            maxBytes=5 * 1024 * 1024,  # 5MB
            backupCount=5,
        )
        handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
        _logger.addHandler(handler)
    return _logger


def _runtime_log(msg: str):
    """Log to rotating runtime.log (5MB, 5 backups)."""
    try:
        _get_logger().info(msg)
    except Exception:
        pass  # Never let logging crash the runtime


def _log(entry: dict):
    """Log structured JSON entry to runtime.log."""
    try:
        entry["ts"] = _now().isoformat()
        _get_logger().info(json.dumps(entry, default=str, ensure_ascii=False))
    except Exception:
        pass


# ── 1. State Engine ──────────────────────────────────────────────


def update_state() -> tuple:
    """Collect current system state. Returns (prev_state, current_state)."""
    prev = load_json(STATE_FILE, {})

    vpn = executors.check_vpn()
    if vpn.get("ok") and vpn.get("ip"):
        vpn["country"] = executors.check_vpn_country(vpn["ip"])

    ollama_notebook = executors.check_ollama_notebook()
    executors.write_ollama_status(ollama_notebook.get("ok", False))

    # Rate-limit nmap scans via cache file (every 30 min)
    # Also updates device_inventory.json for discovery tracking
    nmap_cache = load_json(NMAP_CACHE_FILE, {"last_scan": 0, "hosts": 0})
    if time.time() - nmap_cache.get("last_scan", 0) > NMAP_INTERVAL_SECONDS:
        network_devices = executors.check_network_devices()
        scan_ts = time.time()
        save_json(NMAP_CACHE_FILE, {
            "last_scan": scan_ts,
            "hosts": network_devices.get("count", 0),
        })
        # Write freshness timestamp for Prometheus metric
        try:
            Path("/home/tenazas/runtime/network_scan_last_ts").write_text(str(scan_ts))
        except Exception:
            pass
        # Update device inventory (quick scan + ARP enrichment)
        try:
            if __package__:
                from . import device_discovery
            else:
                from runtime import device_discovery
            result = device_discovery.discover(mode="quick")
            _log({"action": "device_inventory_updated",
                  "total": result.get("summary", {}).get("total", 0)})
        except Exception as e:
            _log({"action": "device_inventory_error", "error": str(e)})
    else:
        network_devices = {"count": nmap_cache.get("hosts", 0)}

    curr = {
        "ts": _now().isoformat(),
        "containers": executors.check_containers(),
        "internet": executors.check_internet(),
        "vpn": vpn,
        "public_ip": executors.check_public_ip(),
        "ollama_notebook": ollama_notebook,
        "ollama_nas": executors.check_ollama_nas(),
        "network_devices": network_devices,
    }

    save_json(STATE_FILE, curr)
    return prev, curr


# ── 2. Event Detection ───────────────────────────────────────────


def _container_map(state: dict) -> dict:
    """Build {name: up} from state containers list."""
    return {c["name"]: c["up"] for c in state.get("containers", [])}


def detect_events(prev: dict, curr: dict) -> list:
    """Compare prev vs curr state, return list of event dicts."""
    if not prev:
        return []  # First run = baseline

    ts = curr.get("ts", _now().isoformat())
    events = []

    # Container transitions
    prev_c = _container_map(prev)
    curr_c = _container_map(curr)

    for name, up in curr_c.items():
        if name.lower() in SKIP_CONTAINERS:
            continue
        was_up = prev_c.get(name)
        if was_up is True and not up:
            events.append({"type": "container_down", "container": name, "ts": ts})
        elif was_up is False and up:
            events.append({"type": "container_recovered", "container": name, "ts": ts})

    # Internet transitions
    prev_inet = prev.get("internet", {}).get("ok")
    curr_inet = curr.get("internet", {}).get("ok")
    if prev_inet is True and curr_inet is False:
        events.append({"type": "internet_lost", "ts": ts})
    elif prev_inet is False and curr_inet is True:
        events.append({"type": "internet_restored", "ts": ts})

    # VPN transitions
    prev_vpn = prev.get("vpn", {})
    curr_vpn = curr.get("vpn", {})
    if prev_vpn.get("ok") is True and curr_vpn.get("ok") is False:
        events.append({"type": "vpn_down", "ts": ts})
    elif curr_vpn.get("ok") and curr_vpn.get("country") == "AR":
        events.append({
            "type": "vpn_leak",
            "ip": curr_vpn.get("ip"), "country": "AR", "ts": ts,
        })
    elif (prev_vpn.get("ip") and curr_vpn.get("ip")
          and prev_vpn["ip"] != curr_vpn["ip"]):
        events.append({
            "type": "vpn_changed",
            "prev_ip": prev_vpn["ip"], "new_ip": curr_vpn["ip"], "ts": ts,
        })

    # Network device count change (threshold: >2 change)
    prev_dev = prev.get("network_devices", {}).get("count", 0)
    curr_dev = curr.get("network_devices", {}).get("count", 0)
    if abs(curr_dev - prev_dev) > 2:
        events.append({
            "type": "device_count_changed",
            "prev": prev_dev, "curr": curr_dev, "ts": ts,
        })

    # Ollama notebook transitions
    prev_oll = prev.get("ollama_notebook", {}).get("ok")
    curr_oll = curr.get("ollama_notebook", {}).get("ok")
    if prev_oll is True and curr_oll is False:
        events.append({"type": "ollama_notebook_down", "ts": ts})
    elif prev_oll is False and curr_oll is True:
        events.append({"type": "ollama_notebook_restored", "ts": ts})

    return events


# ── 3. Task Enqueueing ───────────────────────────────────────────


def _recent_actions(target: str) -> dict | None:
    """Check memory_log for a recent successful action on target within cooldown."""
    memory = load_json(MEMORY_FILE, [])
    if not isinstance(memory, list):
        return None

    cutoff = _now() - timedelta(seconds=TASK_COOLDOWN_SECONDS)

    for entry in reversed(memory):
        try:
            entry_dt = datetime.fromisoformat(entry["ts"])
        except (KeyError, ValueError):
            continue
        if entry_dt < cutoff:
            break  # Entries are chronological, no need to keep looking
        for result in entry.get("results", []):
            if result.get("target") == target and result.get("ok"):
                return result
    return None


def enqueue_tasks(events: list) -> list:
    """Map events to executable tasks. Returns deduplicated task list with cooldown."""
    tasks = []
    seen_targets = set()

    # Sort: gluetun-related events first
    def priority(e):
        if e.get("container") == "gluetun" or e.get("type") in ("vpn_down", "vpn_leak"):
            return 0
        return 1

    for event in sorted(events, key=priority):
        etype = event["type"]

        if etype == "container_down":
            container = event["container"]
            if (container in ARR_CONTAINERS
                    and container not in seen_targets
                    and not _recent_actions(container)):
                tasks.append({
                    "action": "docker.restart", "target": container,
                    "reason": "container_down", "source": "event",
                })
                seen_targets.add(container)

        elif etype == "vpn_down" and "gluetun" not in seen_targets:
            if not _recent_actions("gluetun"):
                tasks.append({
                    "action": "docker.restart", "target": "gluetun",
                    "reason": "vpn_down", "source": "event",
                })
                seen_targets.add("gluetun")

        elif etype == "vpn_leak" and "gluetun" not in seen_targets:
            if not _recent_actions("gluetun"):
                tasks.append({
                    "action": "docker.restart", "target": "gluetun",
                    "reason": "vpn_leak_AR", "source": "event",
                })
                seen_targets.add("gluetun")

    return tasks


# ── 4. Task Execution ────────────────────────────────────────────


def run_tasks(tasks: list) -> list:
    """Execute all tasks and return results."""
    results = []
    for task in tasks:
        result = executors.execute_task(task)
        results.append(result)
    return results


# ── 5. Memory Log ────────────────────────────────────────────────


def append_memory(events: list, results: list):
    """Append run to memory_log.json (capped at MAX_MEMORY_ENTRIES)."""
    if not events and not results:
        return

    memory = load_json(MEMORY_FILE, [])
    if not isinstance(memory, list):
        memory = []

    memory.append({
        "ts": _now().isoformat(),
        "events": events,
        "tasks_run": len(results),
        "results": results,
    })

    # Trim if over capacity
    if len(memory) > MAX_MEMORY_ENTRIES:
        memory = memory[-(MAX_MEMORY_ENTRIES // 2):]

    save_json(MEMORY_FILE, memory)


# ── 6. Pattern Engine ────────────────────────────────────────────


def analyze_patterns():
    """Detect recurring time-correlated events. Throttled to 1x/hour."""
    patterns_data = load_json(PATTERNS_FILE, {"patterns": [], "last_analysis": ""})
    last = patterns_data.get("last_analysis", "")

    if last:
        try:
            last_dt = datetime.fromisoformat(last)
            if (_now() - last_dt).total_seconds() < PATTERN_ANALYSIS_INTERVAL:
                return  # Too soon
        except (ValueError, TypeError):
            pass

    memory = load_json(MEMORY_FILE, [])
    if not isinstance(memory, list) or len(memory) < PATTERN_MIN_OCCURRENCES:
        return

    cutoff = _now() - timedelta(days=PATTERN_LOOKBACK_DAYS)

    # Count (event_type, target, hour) occurrences
    counts = defaultdict(int)
    for entry in memory:
        try:
            entry_dt = datetime.fromisoformat(entry["ts"])
        except (KeyError, ValueError):
            continue
        if entry_dt < cutoff:
            continue
        for event in entry.get("events", []):
            key = (event.get("type", ""), event.get("container", ""), entry_dt.hour)
            counts[key] += 1

    # Build patterns from frequent occurrences
    new_patterns = []
    for (etype, target, hour), count in counts.items():
        if count >= PATTERN_MIN_OCCURRENCES:
            new_patterns.append({
                "event": etype,
                "target": target,
                "hour": hour,
                "occurrences": count,
                "last_seen": _now().isoformat(),
                "last_triggered": "",
            })

    # Merge with existing (keep last_triggered from old patterns)
    old_map = {(p["event"], p["target"], p["hour"]): p
               for p in patterns_data.get("patterns", [])}
    for p in new_patterns:
        key = (p["event"], p["target"], p["hour"])
        if key in old_map:
            p["last_triggered"] = old_map[key].get("last_triggered", "")

    patterns_data["patterns"] = new_patterns
    patterns_data["last_analysis"] = _now().isoformat()
    save_json(PATTERNS_FILE, patterns_data)


def trigger_patterns() -> list:
    """Check if current hour matches any pattern. Returns preemptive tasks."""
    patterns_data = load_json(PATTERNS_FILE, {"patterns": []})
    current_hour = _now().hour
    today = _now().date().isoformat()
    tasks = []

    for pattern in patterns_data.get("patterns", []):
        if pattern.get("hour") != current_hour:
            continue

        # Skip if already triggered today
        triggered = pattern.get("last_triggered", "")
        if triggered.startswith(today):
            continue

        etype = pattern.get("event", "")
        target = pattern.get("target", "")

        # Only generate preemptive restart tasks for container_down patterns
        if (etype == "container_down" and target in ARR_CONTAINERS
                and not _recent_actions(target)):
            tasks.append({
                "action": "docker.restart", "target": target,
                "reason": f"pattern:{etype}@{current_hour}h",
                "source": "pattern",
            })
            pattern["last_triggered"] = _now().isoformat()

    if tasks:
        save_json(PATTERNS_FILE, patterns_data)

    return tasks


# ── Main Loop ─────────────────────────────────────────────────────


def _build_notification(events: list, results: list, curr: dict) -> str | None:
    """Build Telegram message if there's anything worth reporting."""
    lines = []
    for r in results:
        ok = r.get("ok", False)
        icon = "ok" if ok else "FAIL"
        lines.append(f"[{icon}] {r.get('action')} {r.get('target')} ({r.get('reason','')})")

    # Notify on ollama down even without restart actions
    ollama = curr.get("ollama_notebook", {})
    if not ollama.get("ok"):
        lines.append("[info] Notebook Ollama offline — Cloud/NAS CPU mode")

    if not lines:
        return None

    ts = datetime.fromisoformat(curr["ts"]).strftime("%H:%M")
    header = f"Runtime — {ts} ART"
    return header + "\n" + "\n".join(lines)


def _profiled(name: str, fn, *args, **kwargs):
    """Run a function with heartbeat + step profiling."""
    freeze_detector.heartbeat(name)
    t0 = time.time()
    result = fn(*args, **kwargs)
    dur = time.time() - t0
    metrics_exporter.record_step(name, dur)
    return result


def _watchdog_thread():
    """Background thread that checks for freezes every 5 seconds."""
    import threading
    while threading.current_thread().is_alive():
        time.sleep(5)
        status = freeze_detector.check()
        if status["frozen"]:
            step = status["last_step"]
            dur = status["since_last_beat"]
            _runtime_log(f"FREEZE detected at step={step} stalled={dur}s")
            metrics_exporter.record_freeze()
            try:
                msg = f"Runtime freeze detected\nLast step: {step}\nDuration: {dur}s"
                executors.notify_telegram(msg)
            except Exception:
                pass
            break  # Only alert once per cycle


def main():
    start = time.time()

    # SIGALRM hard watchdog (Linux only)
    if hasattr(signal, "alarm"):
        signal.alarm(GLOBAL_TIMEOUT_SECONDS)

    # Lockfile: prevent concurrent runs
    if not _acquire_lock():
        if hasattr(signal, "alarm"):
            signal.alarm(0)
        return

    # Start freeze detector + watchdog thread
    freeze_detector.start()
    import threading
    watchdog = threading.Thread(target=_watchdog_thread, daemon=True)
    watchdog.start()

    try:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        _runtime_log("cycle start")

        prev, curr = _profiled("update_state", update_state)

        events = _profiled("detect_events", detect_events, prev, curr)
        save_json(EVENTS_FILE, {"ts": curr["ts"], "events": events})

        # Record infra events as metrics
        for ev in events:
            etype = ev.get("type", "")
            if etype in ("vpn_down", "vpn_leak"):
                metrics_exporter.record_vpn_disconnect()
            elif etype == "ollama_notebook_down":
                metrics_exporter.record_ollama_failure()

        tasks = _profiled("enqueue_tasks", enqueue_tasks, events)
        pattern_tasks = _profiled("trigger_patterns", trigger_patterns)
        all_tasks = tasks + pattern_tasks
        save_json(TASKS_FILE, {"ts": curr["ts"], "tasks": all_tasks})

        freeze_detector.heartbeat("run_tasks")
        results = run_tasks(all_tasks)
        for r in results:
            if r.get("action") == "docker.restart":
                metrics_exporter.record_container_restart()

        _profiled("append_memory", append_memory, events, results)
        _profiled("analyze_patterns", analyze_patterns)

        freeze_detector.heartbeat("notify")
        msg = _build_notification(events, results, curr)
        if msg:
            executors.notify_telegram(msg)

        elapsed = round(time.time() - start, 1)

        # Health score
        step_durations = freeze_detector.get_detector().get_step_durations()
        health = metrics_exporter.calculate_health(
            elapsed, step_durations,
            freeze_count=freeze_detector.get_detector().freeze_count,
            restart_count=len(results),
        )
        metrics_exporter.set_health_score(health)
        metrics_exporter.record_run(elapsed)

        _runtime_log(
            f"cycle done events={len(events)} tasks={len(all_tasks)} "
            f"elapsed={elapsed}s health={health}"
        )

    except TimeoutError:
        _runtime_log("KILLED: SIGALRM watchdog triggered — cycle took too long")
        metrics_exporter.record_watchdog()
        metrics_exporter.record_failure()
        try:
            from runtime.devin_lite import capture_crash
            capture_crash(TimeoutError("watchdog triggered"), load_json(STATE_FILE, {}))
        except Exception:
            pass

    except Exception as e:
        _runtime_log(f"ERROR: {e}")
        metrics_exporter.record_failure()
        try:
            from runtime.devin_lite import capture_crash
            capture_crash(e, load_json(STATE_FILE, {}))
        except Exception:
            pass
        raise

    finally:
        freeze_detector.stop()
        if hasattr(signal, "alarm"):
            signal.alarm(0)
        _release_lock()


if __name__ == "__main__":
    main()
