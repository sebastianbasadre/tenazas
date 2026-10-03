#!/usr/bin/env python3
"""Periodic job discovery with Telegram notifications for new qualified matches."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import agent_job_search
from runtime_config import (
    get_default_telegram_chat_id,
    get_telegram_bot_token,
    load_config,
)
from telegram_transport import send_document, send_message
from worker_heartbeat import heartbeat

INTERVAL_SECONDS = int(os.getenv("JOB_SEARCH_INTERVAL_SECONDS", "600"))
LOG_FILE = Path(__file__).parent / "logs" / "job_search_watcher.log"


def _log(entry: dict) -> None:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    entry["ts"] = time.time()
    with open(LOG_FILE, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _format_notification(items: list[dict]) -> str:
    lines = [f"💼 {len(items)} vacante(s) nueva(s) calificada(s):"]
    for index, item in enumerate(items[:10], 1):
        lines.append(
            f"\n{index}. {item.get('title', 'Sin título')} — "
            f"{item.get('match_score', 0)}%\n"
            f"{item.get('company', '')}\n"
            f"Publicada: {item.get('posted_date', 'sin fecha')}\n"
            f"{item.get('url', '')}"
        )
    return "\n".join(lines)


def _format_tailored_caption(item: dict, result: dict) -> str:
    return (
        f"CV tailor-made: {item.get('title', 'Vacante')} — {result.get('score', 0)}%\n"
        f"{item.get('company', '')}\n"
        f"Aplicar: {item.get('url', '')}\n"
        "Revisalo antes de enviarlo."
    )


def run_once(*, notify: bool = True) -> dict:
    heartbeat("job_search_watcher", "searching")
    before = {item.get("id") for item in agent_job_search._load_opportunities()}
    discovery = agent_job_search.discover()
    opportunities = agent_job_search._load_opportunities()
    new_items = [item for item in opportunities if item.get("id") not in before]
    new_items.sort(key=lambda item: item.get("match_score", 0), reverse=True)

    notified = False
    if notify and new_items:
        config = load_config()
        token = get_telegram_bot_token(config)
        chat_id = get_default_telegram_chat_id(config)
        if token and chat_id:
            send_message(token, chat_id, _format_notification(new_items))
            for item in new_items:
                if item.get("match_score", 0) <= 70:
                    continue
                result = agent_job_search.prepare_tailored_cv_for_id(item["id"])
                if result.get("error"):
                    send_message(
                        token,
                        chat_id,
                        f"No pude preparar el CV para {item.get('title')}: {result['error']}\n"
                        f"Aplicar: {item.get('url', '')}",
                    )
                    continue
                document_response = send_document(
                    token,
                    chat_id,
                    result["path"],
                    caption=f"CV tailor-made adjunto: {result['filename']}",
                )
                document = (document_response or {}).get("result", {}).get("document", {})
                if not (document_response or {}).get("ok") or not document.get("file_id"):
                    send_message(
                        token,
                        chat_id,
                        f"Falló el envío del archivo {result['filename']}.\n"
                        f"Aplicar: {item.get('url', '')}",
                    )
                    _log(
                        {
                            "action": "document_send_failed",
                            "id": item.get("id"),
                            "response": document_response,
                        }
                )
                continue
                send_message(token, chat_id, _format_tailored_caption(item, result))
                from job_application_manager import send_review, stage

                application = stage(item, result)
                if application.get("created", True):
                    send_review(application)
                _log(
                    {
                        "action": "document_sent",
                        "id": item.get("id"),
                        "filename": result["filename"],
                        "telegram_file_id": document["file_id"],
                    }
                )
            notified = True

    result = {
        "new": len(new_items),
        "notified": notified,
        "active": len(opportunities),
        "discovery": discovery,
    }
    heartbeat("job_search_watcher", f"idle:new={len(new_items)}")
    _log(result)
    return result


def main() -> None:
    _log({"action": "startup", "interval_seconds": INTERVAL_SECONDS})
    while True:
        try:
            run_once()
        except Exception as exc:
            _log({"action": "cycle_error", "error": str(exc)})
            heartbeat("job_search_watcher", "error")
        deadline = time.monotonic() + INTERVAL_SECONDS
        while time.monotonic() < deadline:
            heartbeat("job_search_watcher", "waiting")
            time.sleep(min(30, max(1, deadline - time.monotonic())))


if __name__ == "__main__":
    main()
