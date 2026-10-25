"""One scheduling owner for personal summaries, with durable per-slot deduplication."""

import re
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from assistant_jobs import Jobs, ROOT
from runtime_config import get_default_telegram_chat_id
from runtime_utils import StateLock, load_json, write_atomic

SETTINGS = ROOT / "assistant/settings.json"
TZ = ZoneInfo("America/Argentina/Buenos_Aires")


def settings():
    data = load_json(SETTINGS, {})
    if not data:
        data = {"created_at": time.time(), "daily": "09:00", "weekly": "18:00", "weekday": 4,
                "daily_enabled": True, "weekly_enabled": True}
        write_atomic(SETTINGS, data)
    return data


def configure(kind, clock, weekday=None):
    if clock != "off" and not re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", clock):
        raise ValueError("Horario inválido: usá HH:MM u off")
    with StateLock(SETTINGS):
        data = settings()
        data[kind + "_enabled"] = clock != "off"
        if clock != "off":
            data[kind] = clock
        if weekday is not None:
            data["weekday"] = weekday
        write_atomic(SETTINGS, data)
    return data


def enqueue_daily(moment=None):
    data = settings()
    if not data["daily_enabled"]:
        return None
    moment = moment or datetime.now(TZ)
    hour, minute = map(int, data["daily"].split(":"))
    slot = moment.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if slot.timestamp() < data["created_at"]:
        return None
    chat = get_default_telegram_chat_id()
    if not chat:
        return None
    return Jobs().create(chat, "daily", schedule_key=f"daily:{chat}:{slot.date()}", run_after=slot.timestamp())


def tick(moment=None):
    moment = moment or datetime.now(TZ)
    data = settings()
    from application_tracker import Applications
    Applications().schedule(moment)
    hour, minute = map(int, data["daily"].split(":"))
    if moment.hour * 60 + moment.minute >= hour * 60 + minute:
        enqueue_daily(moment)
    if not data["weekly_enabled"]:
        return
    day = moment - timedelta(days=(moment.weekday() - data["weekday"]) % 7)
    hour, minute = map(int, data["weekly"].split(":"))
    slot = day.replace(hour=hour, minute=minute, second=0, microsecond=0)
    chat = get_default_telegram_chat_id()
    if chat and moment >= slot and slot.timestamp() >= data["created_at"]:
        Jobs().create(chat, "weekly", schedule_key=f"weekly:{chat}:{slot.date()}")
