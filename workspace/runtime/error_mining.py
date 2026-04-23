#!/usr/bin/env python3
"""
Error Mining — Extract production failures from logs.

Detects:
- tool_failure: tools that failed in model_router.log / orchestrator.log
- timeout: queries that exceeded timeout
- dubious_classification: router fell to keyword/regex (not model)
- chain_failure: skill_chain_executor errors
- repeated_incident: same incident base 3+ times
- false_alert: incidents auto-resolved in < 5 min
- rejected_proposal: approval_queue entries with status rejected

Output: state/error_mining/daily_YYYY-MM-DD.json
Log: logs/error_mining.log

Usage:
    python3 error_mining.py --mine       # generate daily batch
    python3 error_mining.py --summary    # send summary to Telegram
    python3 error_mining.py --show       # print today's errors
"""

import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from hashlib import sha256
from pathlib import Path
from urllib.request import Request, urlopen

try:
    from .runtime_config import get_default_telegram_chat_id
except ImportError:
    from runtime_config import get_default_telegram_chat_id

BASE_DIR = Path(__file__).parent
LOGS_DIR = BASE_DIR / "logs"
STATE_DIR = Path("/home/tenazas/.openclaw/state") if Path("/home/tenazas").exists() else BASE_DIR.parent / "state"
OUTPUT_DIR = STATE_DIR / "error_mining"
LOG_FILE = LOGS_DIR / "error_mining.log"
CONFIG_PATH = Path("/home/tenazas/.openclaw/openclaw.json") if Path("/home/tenazas").exists() else BASE_DIR.parent / "state" / "openclaw.json"
CHAT_ID = get_default_telegram_chat_id()

# Log files to scan
ROUTER_LOG = LOGS_DIR / "model_router.log"
ORCHESTRATOR_LOG = LOGS_DIR / "orchestrator.log"
CHAIN_LOG = LOGS_DIR / "skill_chain.log"
APPROVAL_LOG = LOGS_DIR / "approval_queue.log"
PENDING_APPROVALS = BASE_DIR / "pending_approvals.json"
DIAGNOSTICS_LOG = LOGS_DIR / "diagnostics.log"
INCIDENTS_DIR = STATE_DIR / "incidents"


def _now():
    return datetime.now().astimezone()


def _today():
    return _now().strftime("%Y-%m-%d")


def _log(entry: dict):
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    entry["ts"] = _now().isoformat()
    try:
        with open(LOG_FILE, "a") as f:
            f.write(json.dumps(entry, default=str) + "\n")
    except OSError:
        pass


def _load_jsonl(path: Path, max_age_hours: int = 24) -> list:
    """Load JSONL log entries from last N hours."""
    entries = []
    cutoff = _now() - timedelta(hours=max_age_hours)
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    ts_str = entry.get("ts", "")
                    if ts_str:
                        ts = datetime.fromisoformat(ts_str)
                        if ts >= cutoff:
                            entries.append(entry)
                except (json.JSONDecodeError, ValueError):
                    continue
    except FileNotFoundError:
        pass
    return entries


def _load_json(path: Path):
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _error_id(error: dict) -> str:
    key = f"{error['type']}:{error.get('detail', '')}:{error.get('source', '')}"
    return sha256(key.encode()).hexdigest()[:12]


# ── Miners ──────────────────────────────────────────────────────


def mine_tool_failures() -> list:
    """Detect tool/routing failures from model_router.log and orchestrator.log."""
    errors = []

    for entry in _load_jsonl(ROUTER_LOG):
        if entry.get("fallback_used"):
            errors.append({
                "type": "tool_failure",
                "subtype": "router_fallback",
                "detail": f"model={entry.get('model_chosen')} role={entry.get('role')} fallback triggered",
                "source": "model_router.log",
                "ts": entry.get("ts"),
                "severity": "medium",
            })

    for entry in _load_jsonl(ORCHESTRATOR_LOG):
        if entry.get("error") and entry.get("type") != "cloud_fallback":
            errors.append({
                "type": "tool_failure",
                "subtype": "execution_error",
                "detail": str(entry.get("error", ""))[:200],
                "source": "orchestrator.log",
                "ts": entry.get("ts"),
                "severity": "medium",
            })

    return errors


def mine_backend_offline() -> list:
    """Detect HomeLab/backend offline events, grouped by outage session.

    Reads cloud_fallback entries from orchestrator.log. Groups events
    within 5 minutes of each other into a single outage session.
    Returns one error per session with timestamps, count, and duration.
    """
    raw_events = []

    for entry in _load_jsonl(ORCHESTRATOR_LOG):
        if entry.get("type") == "cloud_fallback":
            ts_str = entry.get("ts", "")
            try:
                ts = datetime.fromisoformat(ts_str)
            except (ValueError, TypeError):
                continue
            raw_events.append({
                "ts": ts,
                "ts_str": ts_str,
                "reason": entry.get("reason", ""),
                "model": entry.get("model", ""),
                "category": entry.get("category", ""),
            })

    if not raw_events:
        return []

    # Sort by timestamp and group into outage sessions (gap > 5 min = new session)
    raw_events.sort(key=lambda e: e["ts"])
    SESSION_GAP = timedelta(minutes=5)

    sessions = []
    current_session = [raw_events[0]]

    for event in raw_events[1:]:
        if event["ts"] - current_session[-1]["ts"] <= SESSION_GAP:
            current_session.append(event)
        else:
            sessions.append(current_session)
            current_session = [event]
    sessions.append(current_session)

    # One error per outage session
    errors = []
    for session in sessions:
        first = session[0]
        last = session[-1]
        duration = last["ts"] - first["ts"]
        duration_sec = int(duration.total_seconds())
        models_used = list({e["model"] for e in session})
        categories = list({e["category"] for e in session if e["category"]})
        timestamps = [e["ts_str"] for e in session]

        errors.append({
            "type": "backend_offline",
            "subtype": "outage_session",
            "detail": f"HomeLab offline {duration_sec}s — {len(session)} fallbacks "
                      f"({', '.join(models_used)})",
            "source": "orchestrator.log",
            "ts": first["ts_str"],
            "severity": "high",
            "count": len(session),
            "duration_seconds": duration_sec,
            "session_start": first["ts_str"],
            "session_end": last["ts_str"],
            "timestamps": timestamps,
            "models_used": models_used,
            "categories": categories,
        })

    return errors


def mine_timeouts() -> list:
    """Detect timeout events from logs."""
    errors = []

    for log_path in [ROUTER_LOG, ORCHESTRATOR_LOG, CHAIN_LOG]:
        for entry in _load_jsonl(log_path):
            if "timeout" in str(entry).lower():
                errors.append({
                    "type": "timeout",
                    "detail": str(entry.get("error", entry.get("action", "")))[:200],
                    "source": log_path.name,
                    "ts": entry.get("ts"),
                    "severity": "medium",
                })

    return errors


def mine_dubious_classifications() -> list:
    """Detect queries where orchestrator classification may be wrong.

    The Tenazas router uses keyword/regex heuristics in orchestrator.py
    (not a model like Gemma). We flag cases where:
    - Router V2 signals show keyword-only classification (no model confidence)
    - Orchestrator logged a classify event with low-confidence method
    """
    errors = []

    # From orchestrator: explicit classify events with method info
    for entry in _load_jsonl(ORCHESTRATOR_LOG):
        if entry.get("type") == "classify" and entry.get("method") in ("keyword", "regex"):
            errors.append({
                "type": "dubious_classification",
                "detail": f"query='{entry.get('query', '')[:60]}' "
                          f"classified as {entry.get('category', '?')} via {entry.get('method')}",
                "source": "orchestrator.log",
                "ts": entry.get("ts"),
                "severity": "low",
            })

    # From router: V2 routing where all signals are keyword-based (no model)
    for entry in _load_jsonl(ROUTER_LOG):
        parsed_by = entry.get("_parsed_by", "")
        if parsed_by in ("keyword", "regex", "extracted"):
            errors.append({
                "type": "dubious_classification",
                "detail": f"prompt='{entry.get('prompt_preview', '')[:60]}' "
                          f"routed as {entry.get('task_type', '?')} via {parsed_by}",
                "source": "model_router.log",
                "ts": entry.get("ts"),
                "severity": "low",
            })

    return errors


def mine_chain_failures() -> list:
    """Detect skill chain execution failures."""
    errors = []

    for entry in _load_jsonl(CHAIN_LOG):
        if entry.get("status") == "error" or entry.get("error"):
            errors.append({
                "type": "chain_failure",
                "detail": f"chain={entry.get('chain_id', '?')} "
                          f"skill={entry.get('skill', '?')} error={entry.get('error', '')[:150]}",
                "source": "skill_chain.log",
                "ts": entry.get("ts"),
                "severity": "high",
            })

    return errors


def mine_repeated_incidents() -> list:
    """Detect incidents that appear 3+ times."""
    errors = []

    # From diagnostics log: count recurring issues
    issue_counts = Counter()
    issue_examples = {}

    for entry in _load_jsonl(DIAGNOSTICS_LOG, max_age_hours=168):  # 7 days
        for issue in entry.get("issues", []):
            problem = issue.get("problem", "")
            key = re.sub(r"\d+", "N", problem)  # normalize numbers
            issue_counts[key] += 1
            if key not in issue_examples:
                issue_examples[key] = {"ts": entry.get("ts"), "detail": problem}

    for key, count in issue_counts.items():
        if count >= 3:
            errors.append({
                "type": "repeated_incident",
                "detail": f"({count}x) {issue_examples[key]['detail'][:150]}",
                "source": "diagnostics.log",
                "ts": issue_examples[key]["ts"],
                "severity": "medium",
                "count": count,
            })

    # From incidents directory if it exists
    if INCIDENTS_DIR.exists():
        incident_bases = Counter()
        for f in INCIDENTS_DIR.glob("*.json"):
            try:
                inc = json.loads(f.read_text())
                base = inc.get("category", inc.get("source", f.stem))
                incident_bases[base] += 1
            except Exception:
                continue

        for base, count in incident_bases.items():
            if count >= 3:
                errors.append({
                    "type": "repeated_incident",
                    "detail": f"incident '{base}' appeared {count} times",
                    "source": "state/incidents/",
                    "ts": _now().isoformat(),
                    "severity": "medium",
                    "count": count,
                })

    return errors


def mine_false_alerts() -> list:
    """Detect incidents that auto-resolved in < 5 minutes."""
    errors = []

    entries = _load_jsonl(DIAGNOSTICS_LOG, max_age_hours=168)

    # Track issue appearances and disappearances
    timeline = defaultdict(list)
    for entry in entries:
        ts = entry.get("ts", "")
        for issue in entry.get("issues", []):
            key = issue.get("problem", "")
            timeline[key].append(ts)

    # Check against follow-up entries: if issue appears then disappears quickly
    for entry in _load_jsonl(APPROVAL_LOG):
        if entry.get("action") == "auto_resolved":
            created = entry.get("created_at", 0)
            resolved = entry.get("resolved_at", 0)
            if created and resolved and (resolved - created) < 300:  # 5 min
                errors.append({
                    "type": "false_alert",
                    "detail": f"auto-resolved in {int(resolved - created)}s: "
                              f"{entry.get('action_summary', '')[:100]}",
                    "source": "approval_queue.log",
                    "ts": entry.get("ts"),
                    "severity": "low",
                    "duration_seconds": int(resolved - created),
                })

    return errors


def mine_rejected_proposals() -> list:
    """Detect proposals rejected via approval queue."""
    errors = []

    approvals = _load_json(PENDING_APPROVALS)
    if isinstance(approvals, dict):
        for aid, entry in approvals.items():
            status = entry.get("status", "")
            result = entry.get("result", "")
            if status == "rejected" or result == "rejected":
                errors.append({
                    "type": "rejected_proposal",
                    "detail": f"id={aid} action='{entry.get('action_summary', '')[:100]}'",
                    "source": "pending_approvals.json",
                    "ts": entry.get("created_at", ""),
                    "severity": "low",
                })

    return errors


# ── Main API ────────────────────────────────────────────────────


def mine_all() -> dict:
    """Run all miners and produce daily error report."""
    all_errors = []
    miners = [
        ("tool_failures", mine_tool_failures),
        ("backend_offline", mine_backend_offline),
        ("timeouts", mine_timeouts),
        ("dubious_classifications", mine_dubious_classifications),
        ("chain_failures", mine_chain_failures),
        ("repeated_incidents", mine_repeated_incidents),
        ("false_alerts", mine_false_alerts),
        ("rejected_proposals", mine_rejected_proposals),
    ]

    counts = {}
    for name, miner in miners:
        try:
            errors = miner()
            all_errors.extend(errors)
            counts[name] = len(errors)
        except Exception as e:
            _log({"action": "miner_error", "miner": name, "error": str(e)[:200]})
            counts[name] = 0

    # Add IDs
    for error in all_errors:
        error["id"] = _error_id(error)

    report = {
        "date": _today(),
        "mined_at": _now().isoformat(),
        "total_errors": len(all_errors),
        "by_type": counts,
        "errors": all_errors,
    }

    # Save daily report
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_path = OUTPUT_DIR / f"daily_{_today()}.json"
    with open(output_path, "w") as f:
        json.dump(report, f, indent=2, default=str)

    _log({"action": "mine_complete", "total": len(all_errors), "by_type": counts})
    return report


def get_today() -> dict:
    """Load today's mined errors."""
    path = OUTPUT_DIR / f"daily_{_today()}.json"
    return _load_json(path)


def format_summary(report: dict) -> str:
    """Format error report as Telegram message."""
    if not report:
        return "No hay errores minados para hoy."

    lines = [f"Error Mining — {report.get('date', '?')}"]
    lines.append(f"Total: {report.get('total_errors', 0)} errores\n")

    by_type = report.get("by_type", {})
    severity_icons = {"high": "!", "medium": "*", "low": "-"}

    for error_type, count in sorted(by_type.items(), key=lambda x: -x[1]):
        if count > 0:
            lines.append(f"  {error_type}: {count}")

    # Top 5 errors by severity
    errors = report.get("errors", [])
    high = [e for e in errors if e.get("severity") == "high"]
    if high:
        lines.append("\nCriticos:")
        for e in high[:5]:
            lines.append(f"  ! {e['type']}: {e['detail'][:80]}")

    return "\n".join(lines)


def send_summary():
    """Mine today's errors and send summary to Telegram."""
    report = mine_all()
    if report["total_errors"] == 0:
        return

    msg = format_summary(report)
    try:
        token = json.loads(CONFIG_PATH.read_text())["channels"]["telegram"]["botToken"]
        payload = json.dumps({"chat_id": CHAT_ID, "text": msg[:4090]}).encode()
        urlopen(Request(f"https://api.telegram.org/bot{token}/sendMessage",
                        data=payload, headers={"Content-Type": "application/json"}), timeout=10)
    except Exception as e:
        _log({"action": "telegram_error", "error": str(e)[:200]})


# ── CLI ─────────────────────────────────────────────────────────


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="Error Mining — extract production failures")
    p.add_argument("--mine", action="store_true", help="Generate daily error batch")
    p.add_argument("--summary", action="store_true", help="Mine + send summary to Telegram")
    p.add_argument("--show", action="store_true", help="Show today's errors")
    args = p.parse_args()

    if args.mine:
        report = mine_all()
        print(f"Mined {report['total_errors']} errors for {report['date']}")
        for t, c in report["by_type"].items():
            if c > 0:
                print(f"  {t}: {c}")
    elif args.summary:
        send_summary()
        print("Summary sent to Telegram")
    elif args.show:
        report = get_today()
        if report:
            print(format_summary(report))
        else:
            print("No errors mined today. Run --mine first.")
    else:
        p.print_help()
