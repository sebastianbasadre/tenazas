#!/usr/bin/env python3
"""
notification_poller.py
Polls calendar and email for time-sensitive notifications.

Calendar: alerts for events in the next 2 hours.
Email: classifies new emails via Gemma 12B, alerts if urgent/important.

Usage:
  python3 notification_poller.py --calendar
  python3 notification_poller.py --email
  python3 notification_poller.py --test-calendar
  python3 notification_poller.py --test-email
"""

import argparse
import json
import subprocess
import sys
import re
from datetime import datetime, timedelta
from pathlib import Path
from urllib.request import Request, urlopen

try:
    from .runtime_config import get_default_telegram_chat_id, get_telegram_bot_token
except ImportError:
    from runtime_config import get_default_telegram_chat_id, get_telegram_bot_token

# ── Paths ────────────────────────────────────────────────────────

CONFIG_PATH = Path("/home/tenazas/.openclaw/openclaw.json")
STATE_DIR = Path("/home/tenazas/.openclaw/state")
NOTIFIED_EVENTS = STATE_DIR / "notified_events.json"
NOTIFIED_EMAILS = STATE_DIR / "notified_emails.json"
GCAL_SCRIPT = "/home/tenazas/.openclaw/workspace/skills/calendar-agenda/gcal.py"
GEMMA_URL = "http://192.168.2.6:11434/v1/chat/completions"
GEMMA_MODEL = "gemma3:12b"
CHAT_ID = get_default_telegram_chat_id()


def _load_token() -> str:
    return get_telegram_bot_token()


def _notify(msg: str):
    token = _load_token()
    if not token:
        print(f"[notify] no token: {msg}")
        return
    try:
        payload = json.dumps({"chat_id": CHAT_ID, "text": msg[:4090]}).encode()
        urlopen(Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=payload, headers={"Content-Type": "application/json"},
        ), timeout=10)
    except Exception as e:
        print(f"[notify] telegram error: {e}")


def _load_json(path: Path) -> dict | list:
    try:
        return json.loads(path.read_text()) if path.exists() else {}
    except Exception:
        return {}


def _save_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, default=str))


# ── Calendar ─────────────────────────────────────────────────────


def check_calendar(test: bool = False):
    """Check for upcoming events in the next 2 hours."""
    # Get events via gcal.py
    try:
        r = subprocess.run(
            ["python3", GCAL_SCRIPT, "1"],
            capture_output=True, text=True, timeout=15,
        )
        if r.returncode != 0 or not r.stdout.strip():
            if test:
                print("[calendar] no events or gcal error")
            return
        raw = r.stdout.strip()
    except Exception as e:
        if test:
            print(f"[calendar] error: {e}")
        return

    # Parse events
    now = datetime.now().astimezone()
    cutoff = now + timedelta(hours=2)
    notified = _load_json(NOTIFIED_EVENTS)
    if not isinstance(notified, dict):
        notified = {}

    # Clean old entries (>24h)
    yesterday = (now - timedelta(hours=24)).isoformat()
    notified = {k: v for k, v in notified.items() if v > yesterday}

    new_alerts = []
    for line in raw.splitlines():
        if "|" not in line:
            continue
        ts_str, title = line.split("|", 1)
        ts_str = ts_str.strip()
        title = title.strip()

        try:
            event_dt = datetime.fromisoformat(ts_str)
        except Exception:
            continue

        # Check if in the next 2 hours
        if now <= event_dt <= cutoff:
            event_id = f"{ts_str}_{title[:30]}"
            if event_id in notified:
                continue

            # Calculate minutes until event
            delta = event_dt - now
            minutes = int(delta.total_seconds() / 60)

            if minutes <= 0:
                time_str = "ahora"
            elif minutes < 60:
                time_str = f"en {minutes} min"
            else:
                hours = minutes // 60
                mins = minutes % 60
                time_str = f"en {hours}h{mins:02d}"

            alert = f"{time_str.capitalize()}: {title}"
            new_alerts.append(alert)
            notified[event_id] = now.isoformat()

            if test:
                print(f"[calendar] ALERT: {alert}")

    if new_alerts:
        # Check temporal_guard before sending
        send = True
        try:
            from temporal_guard import should_notify as _tg_check
            send = _tg_check(severity="info", category="calendar")
        except ImportError:
            pass

        if send or test:
            msg = "\n".join(new_alerts)
            if send:
                _notify(msg)
            if test:
                print(f"[calendar] sent {len(new_alerts)} alerts")
        _save_json(NOTIFIED_EVENTS, notified)
    elif test:
        print("[calendar] no new alerts")
    else:
        _save_json(NOTIFIED_EVENTS, notified)


# ── Email ────────────────────────────────────────────────────────


def _classify_email_urgency(subject: str, sender: str) -> bool:
    """Ask Gemma if an email is urgent/important. Returns True if yes."""
    try:
        payload = json.dumps({
            "model": GEMMA_MODEL,
            "messages": [
                {"role": "system", "content":
                 "Clasificá si este email es urgente o importante. "
                 "Responde SOLO: si o no. "
                 "Urgente = requiere acción inmediata, dinero, salud, trabajo. "
                 "No urgente = newsletters, promos, notificaciones automáticas, backups."},
                {"role": "user", "content": f"De: {sender}\nAsunto: {subject}"},
            ],
            "max_tokens": 5,
        }).encode()
        resp = urlopen(Request(
            GEMMA_URL, data=payload,
            headers={"Content-Type": "application/json"},
        ), timeout=15)
        data = json.loads(resp.read())
        answer = data["choices"][0]["message"]["content"].strip().lower()
        return answer.startswith("si") or answer.startswith("sí")
    except Exception:
        return False


def check_email(test: bool = False):
    """Check for new important emails."""
    # Get recent emails via himalaya
    try:
        r = subprocess.run(
            ["himalaya", "envelope", "list", "--folder", "INBOX",
             "--page-size", "10", "--output", "json"],
            capture_output=True, text=True, timeout=15,
        )
        if r.returncode != 0 or not r.stdout.strip():
            if test:
                print("[email] no emails or himalaya error")
            return
        envelopes = json.loads(r.stdout)
    except Exception as e:
        if test:
            print(f"[email] error: {e}")
        return

    notified = _load_json(NOTIFIED_EMAILS)
    if not isinstance(notified, dict):
        notified = {}

    # Clean old entries (>7 days)
    week_ago = (datetime.now() - timedelta(days=7)).isoformat()
    notified = {k: v for k, v in notified.items() if v > week_ago}

    new_alerts = []
    for env in envelopes:
        email_id = str(env.get("id", ""))
        if not email_id or email_id in notified:
            continue

        subject = env.get("subject", "(sin asunto)")
        sender = env.get("from", {})
        if isinstance(sender, dict):
            sender_name = sender.get("name", sender.get("addr", "?"))
        elif isinstance(sender, list) and sender:
            s = sender[0]
            sender_name = s.get("name", s.get("addr", "?")) if isinstance(s, dict) else str(s)
        else:
            sender_name = str(sender)

        # Mark as processed regardless
        notified[email_id] = datetime.now().isoformat()

        # Check urgency via Gemma
        is_urgent = _classify_email_urgency(subject, sender_name)

        if test:
            status = "URGENT" if is_urgent else "skip"
            print(f"[email] {status}: {sender_name} — {subject}")

        if is_urgent:
            alert = f"Mail importante de {sender_name}: {subject}"
            new_alerts.append(alert)

    if new_alerts:
        # Check temporal_guard before sending
        send = True
        try:
            from temporal_guard import should_notify as _tg_check
            send = _tg_check(severity="warning", category="notification")
        except ImportError:
            pass

        if send:
            msg = "\n".join(new_alerts)
            _notify(msg)

    _save_json(NOTIFIED_EMAILS, notified)

    if test and not new_alerts:
        print("[email] no urgent emails")


# ── CLI ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Notification poller")
    parser.add_argument("--calendar", action="store_true", help="Check calendar")
    parser.add_argument("--email", action="store_true", help="Check email")
    parser.add_argument("--test-calendar", action="store_true", help="Test calendar (verbose)")
    parser.add_argument("--test-email", action="store_true", help="Test email (verbose)")
    args = parser.parse_args()

    if args.test_calendar:
        check_calendar(test=True)
    elif args.test_email:
        check_email(test=True)
    elif args.calendar:
        check_calendar()
    elif args.email:
        check_email()
    else:
        parser.print_help()
