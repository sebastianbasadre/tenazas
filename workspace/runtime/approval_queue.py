#!/usr/bin/env python3
"""
approval_queue.py
Queue-based approval system with Telegram inline buttons.

EXTENDS exec_approvals.py:
  - Reuses its Telegram button infrastructure (_tg_request, _load_bot_token)
  - Adds: queue semantics, 30-min expiration, postpone, text-based approval,
    incident linkage, batch operations

State: state/approval_queue.json (persistent across restarts)
Log: logs/approval_queue.log

Usage:
  from approval_queue import enqueue, check_status, handle_callback
  aid = enqueue("restart_container", "sonarr", incident_id="abc123")
  status = check_status(aid)
"""

import hashlib
import json
import time
from datetime import datetime
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

RUNTIME = Path(__file__).parent
QUEUE_PATH = Path("/home/tenazas/.openclaw/state") / "approval_queue.json"
LOG_PATH = RUNTIME / "logs" / "approval_queue.log"
CONFIG_PATH = Path("/home/tenazas/.openclaw/openclaw.json")

CHAT_ID = "1565794551"
EXPIRATION_SECONDS = 1800  # 30 min
POSTPONE_SECONDS = 600     # 10 min extension
MAX_QUEUE_SIZE = 50


def _log(entry: dict):
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    entry["ts"] = datetime.now().astimezone().isoformat()
    with open(LOG_PATH, "a") as f:
        f.write(json.dumps(entry, default=str, ensure_ascii=False) + "\n")


def _load_queue() -> list:
    try:
        return json.loads(QUEUE_PATH.read_text()) if QUEUE_PATH.exists() else []
    except Exception:
        return []


def _save_queue(queue: list):
    QUEUE_PATH.parent.mkdir(parents=True, exist_ok=True)
    QUEUE_PATH.write_text(json.dumps(queue[-MAX_QUEUE_SIZE:],
                                      indent=2, ensure_ascii=False, default=str))


def _load_bot_token() -> str:
    config = json.loads(CONFIG_PATH.read_text())
    return config["channels"]["telegram"]["botToken"]


def _tg_request(method: str, payload: dict, token: str) -> dict | None:
    url = f"https://api.telegram.org/bot{token}/{method}"
    body = json.dumps(payload).encode()
    req = Request(url, data=body, headers={"Content-Type": "application/json"})
    try:
        with urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except (HTTPError, URLError, OSError) as e:
        _log({"action": "tg_api_error", "method": method, "error": str(e)})
        return None


def _generate_id(action: str, target: str) -> str:
    return hashlib.sha256(f"{action}:{target}:{time.time()}".encode()).hexdigest()[:12]


# ── Public API ──────────────────────────────────────────────────

def enqueue(action_name: str, target: str = "",
            context: str = "", risk_level: str = "medium",
            incident_id: str = "", confidence: float = 0.0) -> str:
    """Add an action to the approval queue and notify via Telegram.

    Returns: approval_id for status polling.
    """
    queue = _load_queue()
    _expire_old(queue)

    # Dedup: don't re-queue same action+target if already pending
    for entry in queue:
        if (entry.get("action_name") == action_name
                and entry.get("target") == target
                and entry.get("status") == "pending"):
            _log({"action": "enqueue_dedup", "existing_id": entry["id"],
                  "action_name": action_name, "target": target})
            return entry["id"]

    approval_id = _generate_id(action_name, target)
    now = time.time()

    entry = {
        "id": approval_id,
        "action_name": action_name,
        "target": target,
        "context": context[:500],
        "risk_level": risk_level,
        "incident_id": incident_id,
        "confidence": confidence,
        "status": "pending",
        "created_at": now,
        "expires_at": now + EXPIRATION_SECONDS,
        "message_id": None,
    }

    queue.append(entry)
    _save_queue(queue)

    # Send Telegram notification with inline buttons
    msg_id = _send_approval_message(entry)
    if msg_id:
        entry["message_id"] = msg_id
        _save_queue(queue)

    _log({"action": "enqueue", "id": approval_id, "action_name": action_name,
          "target": target, "risk_level": risk_level})
    return approval_id


def _send_approval_message(entry: dict) -> int | None:
    """Send Telegram message with approval buttons. Returns message_id."""
    try:
        token = _load_bot_token()
    except Exception:
        return None

    risk_icon = {"safe": "", "low": "", "medium": "", "high": ""}
    icon = risk_icon.get(entry["risk_level"], "")

    now_str = datetime.now().strftime("%H:%M")
    target_str = f" → {entry['target']}" if entry["target"] else ""
    text = (
        f"{icon} Aprobacion requerida — {now_str}\n\n"
        f"Accion: {entry['action_name']}{target_str}\n"
        f"Riesgo: {entry['risk_level'].upper()}\n"
    )
    if entry.get("context"):
        text += f"Contexto: {entry['context'][:200]}\n"
    if entry.get("confidence"):
        text += f"Confianza: {entry['confidence']:.0%}\n"
    text += f"\nExpira en {EXPIRATION_SECONDS // 60} min"

    keyboard = {
        "inline_keyboard": [
            [
                {"text": "Aprobar", "callback_data": f"aq_approve:{entry['id']}"},
                {"text": "Rechazar", "callback_data": f"aq_reject:{entry['id']}"},
            ],
            [
                {"text": "Posponer 10min", "callback_data": f"aq_postpone:{entry['id']}"},
            ],
        ]
    }

    result = _tg_request("sendMessage", {
        "chat_id": CHAT_ID,
        "text": text,
        "reply_markup": keyboard,
    }, token)

    if result and result.get("ok"):
        return result["result"]["message_id"]
    return None


def handle_callback(callback_query: dict) -> dict:
    """Process a Telegram callback from approval buttons.

    Called by telegram_router when it receives a callback with aq_ prefix.
    Returns: {"action": "approved"|"rejected"|"postponed"|"expired", ...}
    """
    try:
        token = _load_bot_token()
    except Exception:
        return {"action": "error", "reason": "no_token"}

    callback_id = callback_query.get("id", "")
    data = callback_query.get("data", "")
    message = callback_query.get("message", {})
    chat_id = message.get("chat", {}).get("id", "")
    message_id = message.get("message_id", "")

    if ":" not in data:
        return {"action": "unknown"}

    action_type, approval_id = data.split(":", 1)

    queue = _load_queue()
    _expire_old(queue)
    entry = _find_entry(queue, approval_id)

    if not entry or entry["status"] != "pending":
        _tg_request("answerCallbackQuery", {
            "callback_query_id": callback_id,
            "text": "Expirado o ya procesado",
        }, token)
        _remove_buttons(chat_id, message_id, token)
        return {"action": "expired", "id": approval_id}

    if action_type == "aq_approve":
        entry["status"] = "approved"
        entry["resolved_at"] = time.time()
        _save_queue(queue)
        _tg_request("answerCallbackQuery", {
            "callback_query_id": callback_id, "text": "Aprobado"}, token)
        _tg_request("editMessageText", {
            "chat_id": chat_id, "message_id": message_id,
            "text": f"Aprobado — {entry['action_name']} → {entry.get('target', '')}",
            "reply_markup": {"inline_keyboard": []},
        }, token)
        _log({"action": "approved", "id": approval_id})
        return {"action": "approved", "id": approval_id, "entry": entry}

    elif action_type == "aq_reject":
        entry["status"] = "rejected"
        entry["resolved_at"] = time.time()
        _save_queue(queue)
        _tg_request("answerCallbackQuery", {
            "callback_query_id": callback_id, "text": "Rechazado"}, token)
        _tg_request("editMessageText", {
            "chat_id": chat_id, "message_id": message_id,
            "text": f"Rechazado — {entry['action_name']} → {entry.get('target', '')}",
            "reply_markup": {"inline_keyboard": []},
        }, token)
        _log({"action": "rejected", "id": approval_id})
        return {"action": "rejected", "id": approval_id}

    elif action_type == "aq_postpone":
        entry["expires_at"] = time.time() + POSTPONE_SECONDS
        _save_queue(queue)
        _tg_request("answerCallbackQuery", {
            "callback_query_id": callback_id,
            "text": f"Pospuesto {POSTPONE_SECONDS // 60} min"}, token)
        _log({"action": "postponed", "id": approval_id})
        return {"action": "postponed", "id": approval_id}

    return {"action": "unknown"}


def check_status(approval_id: str) -> str:
    """Non-blocking status check.

    Returns: "pending" | "approved" | "rejected" | "postponed" | "expired" | "not_found"
    """
    queue = _load_queue()
    entry = _find_entry(queue, approval_id)
    if not entry:
        return "not_found"
    if entry["status"] == "pending" and time.time() > entry.get("expires_at", 0):
        return "expired"
    return entry["status"]


def approve_by_text(text: str) -> str | None:
    """Approve a pending item by partial text match on action_name or target.

    Returns approval_id if found and approved, None otherwise.
    Supports: "aprobá sonarr", "approve restart", etc.
    """
    queue = _load_queue()
    _expire_old(queue)
    text_lower = text.lower()

    for entry in queue:
        if entry["status"] != "pending":
            continue
        if (text_lower in entry.get("action_name", "").lower()
                or text_lower in entry.get("target", "").lower()):
            entry["status"] = "approved"
            entry["resolved_at"] = time.time()
            _save_queue(queue)
            _log({"action": "approved_by_text", "id": entry["id"], "text": text})
            return entry["id"]
    return None


def get_pending() -> list[dict]:
    """Get all pending approvals (not expired)."""
    queue = _load_queue()
    now = time.time()
    return [e for e in queue
            if e["status"] == "pending" and now < e.get("expires_at", 0)]


def get_all(limit: int = 20) -> list[dict]:
    """Get recent queue entries."""
    return _load_queue()[-limit:]


# ── Internal helpers ────────────────────────────────────────────

def _find_entry(queue: list, approval_id: str) -> dict | None:
    for e in queue:
        if e["id"] == approval_id:
            return e
    return None


def _expire_old(queue: list):
    """Mark expired entries in-place."""
    now = time.time()
    changed = False
    for e in queue:
        if e["status"] == "pending" and now > e.get("expires_at", 0):
            e["status"] = "expired"
            changed = True
    if changed:
        _save_queue(queue)


def _remove_buttons(chat_id, message_id, token: str):
    _tg_request("editMessageReplyMarkup", {
        "chat_id": chat_id,
        "message_id": message_id,
        "reply_markup": {"inline_keyboard": []},
    }, token)
