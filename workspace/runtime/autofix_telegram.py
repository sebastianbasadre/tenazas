#!/usr/bin/env python3
"""
autofix_telegram.py — Telegram notifications for autofix agent.

Notify, approval flow, summary formatting.
"""

import json
from pathlib import Path
from urllib.request import Request, urlopen

try:
    from .runtime_config import get_default_telegram_chat_id
except ImportError:
    from runtime_config import get_default_telegram_chat_id

CHAT_ID = get_default_telegram_chat_id()

_IN_CONTAINER = Path("/home/tenazas").exists()
CONFIG_PATH = Path("/home/tenazas/.openclaw/openclaw.json") if _IN_CONTAINER else Path(__file__).parent.parent / "state" / "openclaw.json"


def _get_token() -> str:
    try:
        cfg = json.loads(CONFIG_PATH.read_text())
        return cfg["channels"]["telegram"]["botToken"]
    except Exception:
        return ""


def notify(msg: str, parse_mode: str = "Markdown"):
    """Send Telegram message. Falls back to plain text if Markdown fails."""
    token = _get_token()
    if not token:
        return

    payload = json.dumps({
        "chat_id": CHAT_ID, "text": msg[:4090],
        "parse_mode": parse_mode,
    }).encode()
    try:
        urlopen(Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=payload, headers={"Content-Type": "application/json"},
        ), timeout=10)
    except Exception:
        # Retry without parse_mode
        try:
            payload2 = json.dumps({"chat_id": CHAT_ID, "text": msg[:4090]}).encode()
            urlopen(Request(
                f"https://api.telegram.org/bot{token}/sendMessage",
                data=payload2, headers={"Content-Type": "application/json"},
            ), timeout=10)
        except Exception:
            pass


def notify_tier1(probe: str, action: str, result: str, score_before: int = 0,
                 score_after: int = 0):
    """Notify Tier 1 ops action."""
    icon = "✅" if "ok" in result.lower() or "success" in result.lower() else "⚠️"
    delta = f" ({score_before}→{score_after})" if score_before and score_after else ""
    notify(f"🔧 Tier 1: {action} `{probe}`{delta} → {icon} {result}")


def notify_tier2(probe: str, model: str, files: list, changes: str,
                 sha: str, score_delta: str = ""):
    """Notify successful Tier 2 merge."""
    file_list = ", ".join(f"`{f}`" for f in files[:3])
    notify(
        f"🔧 *Tier 2 Auto-fix merged*\n"
        f"Probe: `{probe}`\n"
        f"Model: {model}\n"
        f"Files: {file_list}\n"
        f"Changes: {changes}\n"
        f"SHA: `{sha}`{score_delta}\n\n"
        f"Rollback: `/rollback {sha}`"
    )


def notify_tier2_rollback(sha: str, reason: str, score_before: int, score_after: int):
    """Notify automatic rollback."""
    notify(
        f"⏪ *Rollback automático*\n"
        f"SHA: `{sha}`\n"
        f"Razón: {reason}\n"
        f"Score: {score_before}→{score_after}"
    )


def notify_tier3_proposal(probe: str, regression: str, detail: dict,
                          escalation_reason: str = "", diff: str = "",
                          history: list = None):
    """Send Tier 3 proposal for human review."""
    msg = detail.get("message", "")
    suggestion = detail.get("suggestion", "")

    text = (
        f"📋 *Propuesta de fix (Tier 3)*\n\n"
        f"Probe: `{probe}`\n"
        f"Status: {regression}\n"
        f"Detail: {msg[:200]}\n"
    )

    if escalation_reason:
        text += f"Escalado: {escalation_reason[:200]}\n"
    if suggestion:
        text += f"Sugerencia: `{suggestion}`\n"

    if history:
        hist_str = ", ".join(h.get("status", "?") for h in history[:5])
        text += f"Últimos 5: {hist_str}\n"

    if diff:
        text += f"\n```\n{diff[:500]}\n```\n"

    notify(text)


def notify_degraded(reason: str):
    """Notify that Tier 2 is degraded to Tier 3."""
    notify(
        f"⚠️ Tier 2 degradado a Tier 3: {reason}.\n"
        f"Fixes solo reportados, no aplicados."
    )


def format_status(status: dict) -> str:
    """Format status dict for display."""
    lines = [
        f"Kill switch: {'🔴 ON' if status.get('kill_switch') else '🟢 OFF'}",
        f"Tiers: T1={'✅' if status.get('tier1') else '❌'} "
        f"T2={'✅' if status.get('tier2') else '❌'} "
        f"T3={'✅' if status.get('tier3') else '❌'}",
        f"LLM: {status.get('llm_backend', 'none')} ({status.get('llm_reason', '')})",
        f"Last run: {status.get('last_run', 'never')}",
        f"Total: {status.get('total_fixes', 0)} fixes, "
        f"{status.get('total_proposals', 0)} proposals",
        f"Last hour: T1={status.get('t1_hour', 0)} "
        f"T2={status.get('t2_hour', 0)} T3={status.get('t3_hour', 0)}",
    ]
    return "\n".join(lines)
