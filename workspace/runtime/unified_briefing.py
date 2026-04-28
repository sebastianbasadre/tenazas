#!/usr/bin/env python3
"""
Unified Briefing — Single daily Telegram message with everything relevant.

Sections: agenda, emails, finance, homelab, goals, suggestions, smoking.
Max 30 lines. Each section has 5s timeout — failures omitted silently.
If >50% sections fail → don't send, log error.

Cron: 0 8 * * * docker exec openclaw python3 /home/tenazas/runtime/unified_briefing.py

Usage:
    python3 unified_briefing.py           # send briefing to Telegram
    python3 unified_briefing.py --dry-run # print without sending
    python3 unified_briefing.py --json    # JSON payload
"""

import json
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.request import Request, urlopen
from runtime_utils import now

try:
    from .runtime_config import get_default_telegram_chat_id
except ImportError:
    from runtime_config import get_default_telegram_chat_id

BASE_DIR = Path(__file__).parent
LOG_FILE = BASE_DIR / "logs" / "unified_briefing.log"
CONFIG_PATH = Path("/home/tenazas/.openclaw/openclaw.json") if Path("/home/tenazas").exists() else BASE_DIR.parent / "state" / "openclaw.json"
CHAT_ID = get_default_telegram_chat_id()
SECTION_TIMEOUT = 5  # seconds per section
MAX_LINES = 30



def _log(entry: dict):
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    entry["ts"] = now().isoformat()
    try:
        with open(LOG_FILE, "a") as f:
            f.write(json.dumps(entry, default=str) + "\n")
    except OSError:
        pass


def _safe_call(func, *args, **kwargs):
    """Call function with timeout protection. Returns None on failure."""
    try:
        import signal
        def _timeout_handler(signum, frame):
            raise TimeoutError()
        old = signal.signal(signal.SIGALRM, _timeout_handler)
        signal.alarm(SECTION_TIMEOUT)
        try:
            result = func(*args, **kwargs)
        finally:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, old)
        return result
    except Exception:
        # Fallback without signal (Windows, or signal not available)
        try:
            return func(*args, **kwargs)
        except Exception:
            return None


def _notify(msg: str):
    try:
        token = json.loads(CONFIG_PATH.read_text())["channels"]["telegram"]["botToken"]
        payload = json.dumps({"chat_id": CHAT_ID, "text": msg[:4090]}).encode()
        urlopen(Request(f"https://api.telegram.org/bot{token}/sendMessage",
                        data=payload, headers={"Content-Type": "application/json"}), timeout=10)
    except Exception as e:
        _log({"action": "telegram_error", "error": str(e)[:200]})


# ── Section builders ────────────────────────────────────────────


def _section_header() -> str:
    _now = now()
    dias = ["Lunes", "Martes", "Miercoles", "Jueves", "Viernes", "Sabado", "Domingo"]
    dia = dias[_now.weekday()]
    return f"Buenos dias Sebas — {dia} {_now.strftime('%d/%m')}"


def _section_calendar() -> str | None:
    try:
        from notification_poller import check_calendar
        events = check_calendar()
        if not events:
            return None
        lines = ["Agenda hoy"]
        for e in events[:4]:
            time_str = e.get("time", e.get("start", ""))[:5]
            title = e.get("title", e.get("summary", "?"))[:40]
            lines.append(f"  {time_str} {title}")
        return "\n".join(lines)
    except Exception:
        return None


def _section_email() -> str | None:
    try:
        from notification_poller import check_email
        emails = check_email()
        if not emails:
            return None
        lines = ["Emails"]
        for e in emails[:3]:
            sender = e.get("from", "?")[:20]
            subject = e.get("subject", "?")[:35]
            lines.append(f"  {sender}: {subject}")
        return "\n".join(lines)
    except Exception:
        return None


def _section_finance() -> str | None:
    try:
        from finance_daily import format_daily
        text = format_daily()
        if not text:
            return None
        # Compact: take first 4 lines
        lines = text.strip().split("\n")[:4]
        return "Finanzas\n" + "\n".join(f"  {l.strip()}" for l in lines if l.strip())
    except Exception:
        return None


def _section_homelab() -> str | None:
    try:
        from homelab_snapshot import snapshot
        snap = snapshot()
        c = snap.get("containers", {})
        o = snap.get("ollama", {})
        vpn = snap.get("vpn", {})
        disk = snap.get("disk", {})
        parts = []
        parts.append(f"Docker: {c.get('running', '?')}/{c.get('total', '?')}")
        parts.append(f"Ollama: {o.get('status', '?')}")
        vpn_country = vpn.get("country", "?")
        parts.append(f"VPN: {vpn_country}")
        disk_pct = disk.get("used_pct", "?")
        parts.append(f"Disco: {disk_pct}%")
        return "Homelab: " + " | ".join(parts)
    except Exception:
        return None


def _section_goals() -> str | None:
    try:
        from homelab_goals import get_failing_goals, evaluate_all
        evaluate_all()
        failing = get_failing_goals()
        if not failing:
            return "Goals: todo verde"
        lines = ["Goals"]
        for g in failing[:3]:
            lines.append(f"  ! {g.get('id', '?')}: {g.get('evidence', '')[:40]}")
        return "\n".join(lines)
    except Exception:
        return None


def _section_suggestions() -> str | None:
    try:
        from proactive_planner import plan
        suggestions = plan()
        if not suggestions:
            return None
        lines = ["Sugerencias"]
        for s in suggestions[:3]:
            lines.append(f"  {s.get('action', s.get('detail', '?'))[:50]}")
        return "\n".join(lines)
    except Exception:
        return None


def _section_smoking() -> str | None:
    try:
        from smoking import get_yesterday_stats
        stats = get_yesterday_stats()
        if stats and stats.get("count", 0) > 0:
            return f"Ayer: {stats['count']} cigarrillos"
    except Exception:
        pass
    return None


# ── Main API ────────────────────────────────────────────────────

SECTIONS = [
    ("header", _section_header),
    ("calendar", _section_calendar),
    ("email", _section_email),
    ("finance", _section_finance),
    ("homelab", _section_homelab),
    ("goals", _section_goals),
    ("suggestions", _section_suggestions),
    ("smoking", _section_smoking),
]


def build_briefing() -> dict:
    """Build the briefing payload. Returns {text, sections_ok, sections_failed}."""
    lines = []
    ok = 0
    failed = 0

    for name, func in SECTIONS:
        result = _safe_call(func)
        if result is not None:
            lines.append(result)
            ok += 1
        else:
            if name != "header":  # header is always expected
                failed += 1

    # Enforce max lines
    text = "\n\n".join(lines)
    text_lines = text.split("\n")
    if len(text_lines) > MAX_LINES:
        text = "\n".join(text_lines[:MAX_LINES])

    total_sections = len(SECTIONS) - 1  # exclude header
    return {
        "text": text,
        "sections_ok": ok,
        "sections_failed": failed,
        "total_sections": total_sections,
        "lines": len(text.split("\n")),
    }


def send_briefing(dry_run: bool = False) -> dict:
    """Build and send the daily briefing."""
    payload = build_briefing()

    # Don't send if >50% sections failed
    if payload["total_sections"] > 0:
        fail_rate = payload["sections_failed"] / payload["total_sections"]
        if fail_rate > 0.5:
            _log({"action": "briefing_aborted", "fail_rate": fail_rate,
                  "failed": payload["sections_failed"]})
            return {**payload, "sent": False, "reason": "too many failures"}

    if dry_run:
        return {**payload, "sent": False, "reason": "dry_run"}

    _notify(payload["text"])
    _log({"action": "briefing_sent", "lines": payload["lines"],
          "ok": payload["sections_ok"], "failed": payload["sections_failed"]})
    return {**payload, "sent": True}


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="Unified Briefing")
    p.add_argument("--dry-run", action="store_true", help="Print without sending")
    p.add_argument("--json", action="store_true", help="JSON payload")
    args = p.parse_args()

    result = send_briefing(dry_run=args.dry_run or args.json)
    if args.json:
        print(json.dumps(result, indent=2, default=str))
    else:
        print(result["text"])
        print(f"\n--- {result['sections_ok']} ok, {result['sections_failed']} failed, "
              f"{result['lines']} lines ---")
