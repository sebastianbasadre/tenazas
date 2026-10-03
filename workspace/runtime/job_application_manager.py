#!/usr/bin/env python3
"""Supervised job-application queue and Telegram approval flow."""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from runtime_config import get_default_telegram_chat_id, get_telegram_bot_token
from telegram_transport import telegram_request

BASE_DIR = Path(__file__).parent
STATE_FILE = BASE_DIR / "jobs" / "application_queue.json"
LOG_FILE = BASE_DIR / "logs" / "job_application_manager.log"
LINKEDIN_BRIDGE_URL = os.getenv("LINKEDIN_BRIDGE_URL", "http://192.168.2.73:9224")
LINKEDIN_BRIDGE_TOKEN_FILE = Path("/home/tenazas/.openclaw/linkedin_bridge_token")
SENSITIVE_TOPICS = (
    "salary", "compensation", "visa", "sponsorship", "work authorization",
    "gender", "race", "ethnicity", "disability", "veteran", "criminal",
)
STATUS_RANK = {
    "submitted": 5,
    "approved": 4,
    "pending_approval": 3,
    "manual_action_required": 2,
    "rejected": 1,
}


def _log(entry: dict) -> None:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    entry["ts"] = time.time()
    with open(LOG_FILE, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _load() -> list[dict]:
    try:
        data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (OSError, json.JSONDecodeError):
        return []


def _save(items: list[dict]) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(items, indent=2, ensure_ascii=False), encoding="utf-8")


def _id(opportunity_id: str) -> str:
    return hashlib.sha256(f"{opportunity_id}:{time.time()}".encode()).hexdigest()[:12]


def _portal(url: str) -> str:
    host = urlsplit(url).netloc.lower()
    if "linkedin.com" in host:
        return "linkedin"
    if "greenhouse.io" in host:
        return "greenhouse"
    if "lever.co" in host:
        return "lever"
    return "other"


def _canonical_url(url: str, portal: str | None = None) -> str:
    cleaned = urlsplit(url.strip())
    path = cleaned.path.rstrip("/")
    portal = portal or _portal(url)
    if portal == "linkedin" and "/jobs/view/" in path:
        path = re.sub(r"-\d+$", "", path)
    return f"{cleaned.scheme.lower()}://{cleaned.netloc.lower()}{path}"


def _fingerprint(opportunity: dict) -> str:
    url = str(opportunity.get("url", "")).strip()
    portal = str(opportunity.get("portal", "")).strip() or _portal(url)
    if url:
        return f"{portal}:{_canonical_url(url, portal)}"
    title = str(opportunity.get("title", "")).strip().lower()
    company = str(opportunity.get("company", "")).strip().lower()
    return f"{portal}:{title}:{company}"


def _dedupe_queue(items: list[dict]) -> list[dict]:
    grouped: dict[str, dict] = {}
    for item in items:
        key = item.get("fingerprint") or _fingerprint(item)
        item["fingerprint"] = key
        current = grouped.get(key)
        if current is None:
            grouped[key] = item
            continue
        current_rank = STATUS_RANK.get(str(current.get("status", "")), 0)
        item_rank = STATUS_RANK.get(str(item.get("status", "")), 0)
        if (item_rank, float(item.get("created_at", 0))) >= (current_rank, float(current.get("created_at", 0))):
            grouped[key] = item
    return list(grouped.values())


def migrate_queue() -> int:
    queue = _load()
    deduped = _dedupe_queue(queue)
    if len(deduped) != len(queue):
        _save(deduped)
    return len(queue) - len(deduped)


def stage(opportunity: dict, cv_result: dict) -> dict:
    """Create one reviewable application proposal per opportunity."""
    queue = _dedupe_queue(_load())
    opportunity_key = _fingerprint(opportunity)
    for entry in queue:
        if entry.get("fingerprint") == opportunity_key:
            return {**entry, "created": False}
    entry = {
        "id": _id(opportunity["id"]),
        "opportunity_id": opportunity["id"],
        "fingerprint": opportunity_key,
        "title": opportunity.get("title", ""),
        "company": opportunity.get("company", ""),
        "url": opportunity.get("url", ""),
        "portal": _portal(opportunity.get("url", "")),
        "score": opportunity.get("match_score", 0),
        "cv_path": cv_result.get("path", ""),
        "cv_filename": cv_result.get("filename", ""),
        "strengths": cv_result.get("strengths", []),
        "gaps": cv_result.get("gaps", []),
        "proposed_answers": {},
        "sensitive_questions": [],
        "status": "pending_approval",
        "created_at": time.time(),
    }
    queue.append(entry)
    _save(_dedupe_queue(queue))
    _log({"action": "staged", "id": entry["id"], "opportunity_id": opportunity["id"]})
    return {**entry, "created": True}


def send_review(entry: dict) -> dict | None:
    token = get_telegram_bot_token()
    chat_id = get_default_telegram_chat_id()
    if not token or not chat_id:
        return None
    sensitive = entry.get("sensitive_questions", [])
    text = (
        f"Postulación lista para revisión\n\n"
        f"{entry['title']} — {entry['company']} ({entry['score']}%)\n"
        f"Portal: {entry['portal']}\n"
        f"CV: {entry['cv_filename']}\n"
        f"Fortalezas: {', '.join(entry.get('strengths', [])[:5]) or 'sin datos'}\n"
        f"Brechas: {', '.join(entry.get('gaps', [])[:5]) or 'sin datos'}\n"
        f"Preguntas sensibles: {len(sensitive)}\n"
        f"Aplicar: {entry['url']}\n\n"
        "Aprobar autoriza el intento de envío. CAPTCHA, login o preguntas sensibles "
        "detendrán el proceso."
    )
    keyboard = {
        "inline_keyboard": [[
            {"text": "Aprobar postulación", "callback_data": f"job_apply_approve:{entry['id']}"},
            {"text": "Rechazar", "callback_data": f"job_apply_reject:{entry['id']}"},
        ]]
    }
    result = telegram_request(
        token,
        "sendMessage",
        {"chat_id": chat_id, "text": text, "reply_markup": json.dumps(keyboard)},
    )
    if result and result.get("ok"):
        queue = _load()
        for item in queue:
            if item["id"] == entry["id"]:
                item["message_id"] = result["result"]["message_id"]
        _save(queue)
    return result


def _find(application_id: str) -> tuple[list[dict], dict | None]:
    queue = _dedupe_queue(_load())
    return queue, next((item for item in queue if item.get("id") == application_id), None)


def submit(application_id: str) -> dict:
    """Submit only through a configured real adapter; never report a false success."""
    queue, entry = _find(application_id)
    if not entry:
        return {"ok": False, "status": "not_found", "message": "Postulación no encontrada."}
    if entry["status"] not in {"approved", "pending_approval"}:
        return {"ok": False, "status": entry["status"], "message": "Ya fue procesada."}

    portal = entry["portal"]
    if portal == "linkedin":
        try:
            token = LINKEDIN_BRIDGE_TOKEN_FILE.read_text(encoding="utf-8").strip()
            payload = json.dumps({"url": entry["url"], "cv_path": entry["cv_path"]}).encode()
            request = Request(
                f"{LINKEDIN_BRIDGE_URL}/apply",
                data=payload,
                headers={
                    "Content-Type": "application/json",
                    "X-LinkedIn-Bridge-Token": token,
                },
            )
            try:
                response = json.loads(urlopen(request, timeout=120).read())
            except HTTPError as exc:
                response = json.loads(exc.read() or b"{}")
            status = str(response.get("status", "bridge_error"))
            message = str(response.get("message", response.get("error", "Error del bridge.")))
            submitted = bool(response.get("ok") and status == "submitted")
        except (OSError, URLError, json.JSONDecodeError) as exc:
            status = "bridge_unavailable"
            message = f"No se pudo conectar con la sesión local de LinkedIn: {exc}"
            submitted = False
    elif portal in {"greenhouse", "lever"}:
        status = "adapter_not_configured"
        message = (
            f"El adaptador real de {portal.title()} aún no está configurado para este formulario. "
            "No se envió la postulación."
        )
    else:
        status = "unsupported_portal"
        message = "Portal no soportado. No se envió la postulación."

    entry["status"] = status
    entry["resolved_at"] = time.time()
    entry["result_message"] = message
    _save(_dedupe_queue(queue))
    _log({"action": "submit_blocked", "id": entry["id"], "status": status})
    return {"ok": submitted if portal == "linkedin" else False, "status": status, "message": message, "entry": entry}


def handle_callback(callback_query: dict, token: str) -> dict:
    data = callback_query.get("data", "")
    callback_id = callback_query.get("id", "")
    message = callback_query.get("message", {})
    chat_id = str(message.get("chat", {}).get("id", ""))
    message_id = message.get("message_id", "")
    action, application_id = data.split(":", 1)
    queue, entry = _find(application_id)
    if not entry or entry.get("status") != "pending_approval":
        telegram_request(token, "answerCallbackQuery", {
            "callback_query_id": callback_id, "text": "Ya procesada o inexistente",
        })
        return {"action": "expired"}

    if action == "job_apply_reject":
        entry["status"] = "rejected"
        entry["resolved_at"] = time.time()
        _save(_dedupe_queue(queue))
        result = {"ok": False, "status": "rejected", "message": "Postulación rechazada."}
    else:
        entry["status"] = "approved"
        entry["approved_at"] = time.time()
        _save(_dedupe_queue(queue))
        result = submit(application_id)

    telegram_request(token, "answerCallbackQuery", {
        "callback_query_id": callback_id,
        "text": "Procesado",
    })
    telegram_request(token, "editMessageText", {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": (
            f"{entry['title']} — {entry['company']}\n"
            f"Estado: {result['status']}\n{result['message']}\n{entry['url']}"
        ),
        "reply_markup": json.dumps({"inline_keyboard": []}),
    })
    return {"action": result["status"], "result": result}
