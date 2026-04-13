"""
smoking.py
Registra cigarrillos fumados con timestamp.
Storage: /home/tenazas/runtime/smoking/log.json
"""

import json
import sys
from datetime import datetime, date, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/Argentina/Buenos_Aires")
LOG_PATH = Path(__file__).parent / "smoking" / "log.json"
METRICS_PATH = Path(__file__).parent / "smoking" / "metrics.json"


def _load() -> list:
    if not LOG_PATH.exists():
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        return []
    try:
        return json.loads(LOG_PATH.read_text())
    except Exception:
        return []


def _save(entries: list):
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    LOG_PATH.write_text(json.dumps(entries, indent=2, ensure_ascii=False))


def _update_metrics(entries: list):
    """Escribe metricas para Prometheus."""
    today = date.today().isoformat()
    week_ago = (date.today() - timedelta(days=7)).isoformat()

    today_count = sum(1 for e in entries if e["date"] == today)
    week_count = sum(1 for e in entries if e["date"] >= week_ago)
    total_count = len(entries)

    by_hour = {}
    for e in entries:
        if e["date"] == today:
            h = e["hour"]
            by_hour[h] = by_hour.get(h, 0) + 1

    metrics = {
        "today": today_count,
        "week": week_count,
        "total": total_count,
        "by_hour": by_hour,
        "updated": datetime.now(TZ).isoformat(),
    }
    METRICS_PATH.parent.mkdir(parents=True, exist_ok=True)
    METRICS_PATH.write_text(json.dumps(metrics, indent=2))


def log_cigarette(note: str = "") -> dict:
    """Registra un cigarrillo. Retorna stats del dia."""
    now = datetime.now(TZ)
    entries = _load()

    entry = {
        "ts": now.isoformat(),
        "date": now.date().isoformat(),
        "hour": now.hour,
        "time": now.strftime("%H:%M"),
        "note": note,
    }
    entries.append(entry)
    _save(entries)
    _update_metrics(entries)

    today = now.date().isoformat()
    today_count = sum(1 for e in entries if e["date"] == today)

    return {
        "logged": True,
        "time": now.strftime("%H:%M"),
        "today_count": today_count,
        "entry": entry,
    }


def get_stats() -> dict:
    """Retorna estadisticas completas."""
    entries = _load()
    if not entries:
        return {"total": 0, "today": 0, "week": 0, "message": "Sin registros aun."}

    today = date.today().isoformat()
    week_ago = (date.today() - timedelta(days=7)).isoformat()

    today_entries = [e for e in entries if e["date"] == today]
    week_entries = [e for e in entries if e["date"] >= week_ago]

    days_with_data = len(set(e["date"] for e in week_entries))
    avg = round(len(week_entries) / max(days_with_data, 1), 1)

    by_hour = {}
    for e in entries:
        by_hour[e["hour"]] = by_hour.get(e["hour"], 0) + 1
    peak_hour = max(by_hour, key=by_hour.get) if by_hour else None

    by_day = {}
    for e in week_entries:
        by_day[e["date"]] = by_day.get(e["date"], 0) + 1

    return {
        "total": len(entries),
        "today": len(today_entries),
        "week": len(week_entries),
        "avg_daily": avg,
        "peak_hour": peak_hour,
        "by_day": dict(sorted(by_day.items())),
        "today_times": [e["time"] for e in today_entries],
    }


def format_response(action: str = "log", note: str = "") -> str:
    """Retorna mensaje formateado para Telegram."""
    if action == "log":
        result = log_cigarette(note)
        count = result["today_count"]
        time = result["time"]

        msg = f"Registrado — {time}\n"
        msg += f"Hoy: {count} cigarrillo{'s' if count != 1 else ''}\n"

        if count == 1:
            msg += "Primer cigarrillo del dia."
        elif count <= 5:
            msg += "Vas bien, trata de espaciarlos."
        elif count <= 10:
            msg += f"Ya van {count}. Queres hacer una pausa?"
        else:
            msg += f"Ya van {count} hoy. Considera cortar."

        return msg

    elif action == "stats":
        s = get_stats()
        if s["total"] == 0:
            return "Sin registros de cigarrillos aun."

        msg = "Estadisticas de tabaco\n"
        msg += "━━━━━━━━━━━━━━━\n"
        msg += f"Hoy: {s['today']}\n"
        msg += f"Esta semana: {s['week']}\n"
        msg += f"Promedio diario: {s['avg_daily']}\n"
        msg += f"Total historico: {s['total']}\n"

        if s["peak_hour"] is not None:
            msg += f"Hora pico: {s['peak_hour']}:00\n"

        if s["today_times"]:
            msg += f"\nHoy fumaste a: {', '.join(s['today_times'])}"

        if s["by_day"]:
            msg += "\n\nUltimos dias:\n"
            for d, c in list(s["by_day"].items())[-5:]:
                bar = "▪" * min(c, 20)
                msg += f"  {d}: {bar} {c}\n"

        return msg

    return "Usa: log (registrar) o stats (estadisticas)"


if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else "log"
    note = sys.argv[2] if len(sys.argv) > 2 else ""
    print(format_response(action, note))
