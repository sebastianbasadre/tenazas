"""
skill_feedback.py
Collects explicit (thumbs) and implicit (repeat) feedback on skill executions.
Feeds into auto_training_pipeline via feedback_log.jsonl.
"""

import json
import time
from datetime import datetime
from pathlib import Path
from urllib.request import Request, urlopen

FEEDBACK_LOG = Path(__file__).parent / "learning" / "feedback_log.jsonl"
RECENT_SKILLS = {}  # chat_id → {query, category, ts}

CONFIG_PATH = Path("/home/tenazas/.openclaw/openclaw.json")
CHAT_ID = "1565794551"


def _load_token() -> str:
    try:
        return json.loads(CONFIG_PATH.read_text())["channels"]["telegram"]["botToken"]
    except Exception:
        return ""


def _tg(token: str, method: str, data: dict):
    try:
        payload = json.dumps(data).encode()
        urlopen(Request(
            f"https://api.telegram.org/bot{token}/{method}",
            data=payload, headers={"Content-Type": "application/json"},
        ), timeout=10)
    except Exception:
        pass


def _log_feedback(entry: dict):
    FEEDBACK_LOG.parent.mkdir(parents=True, exist_ok=True)
    entry["ts"] = datetime.now().astimezone().isoformat()
    with open(FEEDBACK_LOG, "a") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


# ── Explicit feedback: buttons after skill response ─────────────

def send_with_feedback(token: str, chat_id: str, text: str,
                       query: str, category: str) -> dict:
    """Send skill response with thumbs up/down buttons.

    Returns the sent message dict (for tracking).
    """
    feedback_id = f"{int(time.time())}_{hash(query) % 10000:04d}"

    keyboard = {
        "inline_keyboard": [[
            {"text": "OK", "callback_data": f"fb_ok:{feedback_id}"},
            {"text": "Mal", "callback_data": f"fb_bad:{feedback_id}"},
        ]]
    }

    # Store context for when button is pressed
    RECENT_SKILLS[feedback_id] = {
        "query": query,
        "category": category,
        "response": text[:200],
        "chat_id": chat_id,
        "ts": time.time(),
    }

    # Cleanup old entries (>10 min)
    _cleanup_recent()

    # Send with buttons
    if len(text) > 4096:
        text = text[:4090] + "\n..."

    result = _tg_send(token, chat_id, text, keyboard)
    return result or {}


def _tg_send(token, chat_id, text, reply_markup):
    try:
        payload = json.dumps({
            "chat_id": chat_id,
            "text": text,
            "reply_markup": reply_markup,
        }).encode()
        resp = urlopen(Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=payload, headers={"Content-Type": "application/json"},
        ), timeout=10)
        return json.loads(resp.read())
    except Exception:
        # Fallback: send without buttons
        try:
            payload = json.dumps({"chat_id": chat_id, "text": text}).encode()
            urlopen(Request(
                f"https://api.telegram.org/bot{token}/sendMessage",
                data=payload, headers={"Content-Type": "application/json"},
            ), timeout=10)
        except Exception:
            pass
    return None


# ── Handle feedback callback ────────────────────────────────────

def handle_feedback_callback(callback_query: dict, token: str) -> dict:
    """Process a feedback button tap. Returns result dict."""
    data = callback_query.get("data", "")
    cb_id = callback_query.get("id", "")
    message = callback_query.get("message", {})
    msg_id = message.get("message_id", "")
    chat_id = str(message.get("chat", {}).get("id", ""))

    if not data.startswith("fb_"):
        return {"handled": False}

    action, feedback_id = data.split(":", 1) if ":" in data else (data, "")
    context = RECENT_SKILLS.pop(feedback_id, None)

    if action == "fb_ok":
        # Positive feedback
        _log_feedback({
            "type": "explicit",
            "feedback": "positive",
            "query": context.get("query", "") if context else "",
            "category": context.get("category", "") if context else "",
            "response_preview": context.get("response", "") if context else "",
        })
        # Answer callback + remove buttons
        _tg(token, "answerCallbackQuery", {
            "callback_query_id": cb_id, "text": "Gracias!",
        })
        _remove_buttons(token, chat_id, msg_id)
        return {"handled": True, "feedback": "positive"}

    elif action == "fb_bad":
        # Negative feedback
        _log_feedback({
            "type": "explicit",
            "feedback": "negative",
            "query": context.get("query", "") if context else "",
            "category": context.get("category", "") if context else "",
            "response_preview": context.get("response", "") if context else "",
        })
        _tg(token, "answerCallbackQuery", {
            "callback_query_id": cb_id, "text": "Anotado. Voy a mejorar.",
        })
        _remove_buttons(token, chat_id, msg_id)
        return {"handled": True, "feedback": "negative"}

    return {"handled": False}


def _remove_buttons(token, chat_id, message_id):
    """Remove inline keyboard from a message."""
    try:
        _tg(token, "editMessageReplyMarkup", {
            "chat_id": chat_id,
            "message_id": message_id,
            "reply_markup": {"inline_keyboard": []},
        })
    except Exception:
        pass


# ── Implicit feedback: repeat detection ─────────────────────────

def check_repeat(chat_id: str, query: str, category: str) -> bool:
    """Check if this query was asked recently (within 2 min) — implicit negative."""
    key = f"{chat_id}:{category}"
    now = time.time()

    if key in RECENT_SKILLS:
        prev = RECENT_SKILLS[key]
        if now - prev.get("ts", 0) < 120:  # 2 minutes
            _log_feedback({
                "type": "implicit",
                "feedback": "negative_repeat",
                "query": query,
                "category": category,
                "previous_query": prev.get("query", ""),
                "seconds_since": round(now - prev["ts"]),
            })
            return True

    # Track for next time
    RECENT_SKILLS[key] = {"query": query, "category": category, "ts": now}
    return False


def _cleanup_recent():
    """Remove entries older than 10 minutes."""
    now = time.time()
    expired = [k for k, v in RECENT_SKILLS.items() if now - v.get("ts", 0) > 600]
    for k in expired:
        del RECENT_SKILLS[k]


# ── Stats ────────────────────────────────────────────────────────

def get_feedback_stats() -> dict:
    """Read feedback_log and return summary."""
    if not FEEDBACK_LOG.exists():
        return {"total": 0}

    positive = negative = implicit_neg = 0
    by_category = {}

    for line in FEEDBACK_LOG.read_text().splitlines():
        try:
            entry = json.loads(line)
            fb = entry.get("feedback", "")
            cat = entry.get("category", "unknown")

            if fb == "positive":
                positive += 1
            elif fb == "negative":
                negative += 1
            elif fb == "negative_repeat":
                implicit_neg += 1

            by_category.setdefault(cat, {"pos": 0, "neg": 0})
            if "positive" in fb:
                by_category[cat]["pos"] += 1
            else:
                by_category[cat]["neg"] += 1
        except Exception:
            continue

    return {
        "total": positive + negative + implicit_neg,
        "positive": positive,
        "negative": negative,
        "implicit_negative": implicit_neg,
        "by_category": by_category,
    }
