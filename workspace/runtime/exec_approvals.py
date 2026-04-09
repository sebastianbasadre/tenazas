#!/usr/bin/env python3
"""
exec_approvals.py — Approval system via Telegram Inline Keyboard.

Replaces text-based "write YES to confirm" with interactive buttons.
Compatible with Tenazas' "never auto-exec" model.

Flow:
  1. Action mapper or runtime detects an action that needs approval
  2. send_approval_request() sends message with inline buttons
  3. User taps Aprobar / Cancelar / Ver detalles
  4. handle_callback_query() processes the button tap
  5. Caller checks status via get_approval_status() or poll_approval()

State: pending_approvals.json (in runtime dir, gitignored)
Log: logs/exec_approvals.log
"""

import json
import time
import hashlib
from datetime import datetime
from pathlib import Path
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

# ---------------------------------------------------------------------------
# Paths & Config
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).parent
PENDING_FILE = BASE_DIR / "pending_approvals.json"
LOG_FILE = BASE_DIR / "logs" / "exec_approvals.log"
CONFIG_FILE = Path("/home/tenazas/.openclaw/openclaw.json")

CHAT_ID = "1565794551"
APPROVAL_TIMEOUT_SECONDS = 300  # 5 minutes


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def _log(entry: dict):
    """Append a JSON line to exec_approvals.log."""
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    entry["ts"] = datetime.now().astimezone().isoformat()
    with open(LOG_FILE, "a") as f:
        f.write(json.dumps(entry, default=str, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# Telegram helpers
# ---------------------------------------------------------------------------

def _load_bot_token() -> str:
    """Read bot token from openclaw.json."""
    config = json.loads(CONFIG_FILE.read_text())
    return config["channels"]["telegram"]["botToken"]


def _tg_request(method: str, payload: dict, token: str) -> dict | None:
    """Call Telegram Bot API. Returns parsed JSON or None on error."""
    url = f"https://api.telegram.org/bot{token}/{method}"
    body = json.dumps(payload).encode()
    req = Request(url, data=body, headers={"Content-Type": "application/json"})
    try:
        with urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except (HTTPError, URLError, OSError) as e:
        _log({"action": "tg_api_error", "method": method, "error": str(e)})
        return None


# ---------------------------------------------------------------------------
# Pending approvals state
# ---------------------------------------------------------------------------

def _generate_approval_id(action: str) -> str:
    """Generate a short unique ID for an approval request."""
    ts = str(time.time())
    return hashlib.sha256(f"{action}{ts}".encode()).hexdigest()[:12]


def _load_pending() -> dict:
    """Load pending approvals from JSON file."""
    if PENDING_FILE.exists():
        try:
            return json.loads(PENDING_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save_pending(pending: dict):
    """Save pending approvals to JSON file."""
    PENDING_FILE.parent.mkdir(parents=True, exist_ok=True)
    PENDING_FILE.write_text(json.dumps(pending, indent=2, ensure_ascii=False))


def _cleanup_expired(pending: dict) -> dict:
    """Remove expired approval requests."""
    now = time.time()
    return {k: v for k, v in pending.items()
            if now - v.get("created_at", 0) < APPROVAL_TIMEOUT_SECONDS}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def send_approval_request(
    action_summary: str,
    action_details: str,
    skill_name: str,
    chat_id: str = CHAT_ID,
    token: str | None = None,
) -> str:
    """Send an approval request with inline keyboard to Telegram.

    Args:
        action_summary: short description (shown in the main message)
        action_details: full description (shown on "Ver detalles" tap)
        skill_name: skill that would be executed
        chat_id: Telegram chat ID
        token: bot token (reads from config if None)

    Returns:
        approval_id: string to poll for result
    """
    if token is None:
        token = _load_bot_token()

    approval_id = _generate_approval_id(action_summary)

    # Save to pending
    pending = _cleanup_expired(_load_pending())
    pending[approval_id] = {
        "status": "pending",
        "action_summary": action_summary,
        "action_details": action_details,
        "skill_name": skill_name,
        "chat_id": str(chat_id),
        "created_at": time.time(),
    }
    _save_pending(pending)

    # Build inline keyboard
    keyboard = {
        "inline_keyboard": [
            [
                {"text": "Aprobar", "callback_data": f"approve:{approval_id}"},
                {"text": "Cancelar", "callback_data": f"cancel:{approval_id}"},
            ],
            [
                {"text": "Ver detalles", "callback_data": f"details:{approval_id}"},
            ],
        ]
    }

    now_str = datetime.now().strftime("%H:%M")
    text = (
        f"Accion pendiente de aprobacion -- {now_str}\n\n"
        f"Skill: {skill_name}\n"
        f"Accion: {action_summary}\n\n"
        f"Expira en 5 minutos"
    )

    result = _tg_request("sendMessage", {
        "chat_id": chat_id,
        "text": text,
        "reply_markup": keyboard,
    }, token)

    _log({
        "action": "approval_sent",
        "approval_id": approval_id,
        "skill": skill_name,
        "summary": action_summary,
        "tg_ok": bool(result and result.get("ok")),
    })

    return approval_id


def handle_callback_query(callback_query: dict, token: str | None = None) -> dict:
    """Process a Telegram callback_query (button tap).

    Args:
        callback_query: the callback_query object from Telegram update
        token: bot token (reads from config if None)

    Returns:
        dict with action result
    """
    if token is None:
        token = _load_bot_token()

    callback_id = callback_query.get("id", "")
    data = callback_query.get("data", "")
    message = callback_query.get("message", {})
    chat_id = message.get("chat", {}).get("id", "")
    message_id = message.get("message_id", "")

    if ":" not in data:
        _tg_request("answerCallbackQuery", {
            "callback_query_id": callback_id,
            "text": "Accion no reconocida",
        }, token)
        return {"action": "unknown"}

    action_type, approval_id = data.split(":", 1)

    pending = _cleanup_expired(_load_pending())
    approval = pending.get(approval_id)

    if approval is None:
        _tg_request("answerCallbackQuery", {
            "callback_query_id": callback_id,
            "text": "Esta aprobacion expiro o ya fue procesada",
        }, token)
        # Remove buttons from expired message
        _tg_request("editMessageReplyMarkup", {
            "chat_id": chat_id,
            "message_id": message_id,
            "reply_markup": {"inline_keyboard": []},
        }, token)
        _log({"action": "callback_expired", "approval_id": approval_id,
              "action_type": action_type})
        return {"action": "expired", "approval_id": approval_id}

    if action_type == "details":
        # Show details without consuming the approval
        details_text = approval.get("action_details", "Sin detalles")[:200]
        _tg_request("answerCallbackQuery", {
            "callback_query_id": callback_id,
            "text": details_text,
            "show_alert": True,
        }, token)
        _log({"action": "callback_details", "approval_id": approval_id})
        return {"action": "details", "approval_id": approval_id}

    elif action_type == "approve":
        pending[approval_id]["status"] = "approved"
        pending[approval_id]["resolved_at"] = time.time()
        _save_pending(pending)

        _tg_request("answerCallbackQuery", {
            "callback_query_id": callback_id,
            "text": "Aprobado",
        }, token)
        _tg_request("editMessageText", {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": f"Aprobado -- {approval['action_summary']}\n{approval['skill_name']}",
            "reply_markup": {"inline_keyboard": []},
        }, token)
        _log({"action": "callback_approved", "approval_id": approval_id,
              "skill": approval.get("skill_name")})
        return {"action": "approved", "approval_id": approval_id, "approval": approval}

    elif action_type == "cancel":
        pending[approval_id]["status"] = "cancelled"
        pending[approval_id]["resolved_at"] = time.time()
        _save_pending(pending)

        _tg_request("answerCallbackQuery", {
            "callback_query_id": callback_id,
            "text": "Cancelado",
        }, token)
        _tg_request("editMessageText", {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": f"Cancelado -- {approval['action_summary']}",
            "reply_markup": {"inline_keyboard": []},
        }, token)
        _log({"action": "callback_cancelled", "approval_id": approval_id})
        return {"action": "cancelled", "approval_id": approval_id}

    return {"action": "unknown"}


def get_approval_status(approval_id: str) -> str:
    """Non-blocking check of approval status.

    Returns: "pending" | "approved" | "cancelled" | "expired" | "not_found"
    """
    pending = _load_pending()
    entry = pending.get(approval_id)
    if entry is None:
        return "not_found"
    if time.time() - entry.get("created_at", 0) > APPROVAL_TIMEOUT_SECONDS:
        return "expired"
    return entry.get("status", "pending")


def poll_approval(approval_id: str, timeout: float = 300.0) -> str:
    """Blocking poll for approval resolution. Use in a separate thread.

    Returns: "approved" | "cancelled" | "expired"
    """
    deadline = time.time() + min(timeout, APPROVAL_TIMEOUT_SECONDS)
    while time.time() < deadline:
        status = get_approval_status(approval_id)
        if status in ("approved", "cancelled", "expired", "not_found"):
            return status
        time.sleep(2)
    return "expired"
