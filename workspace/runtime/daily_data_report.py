#!/usr/bin/env python3
"""
daily_data_report.py — Daily data collection report for Tenazas.

Runs metrics_report + extract stats, sends summary to Telegram.
Designed to run as a daily cron (e.g., 23:00).

Usage:
    python3 daily_data_report.py          # run + send Telegram
    python3 daily_data_report.py --dry-run # print only
"""

import json
import sys
from datetime import date
from pathlib import Path
from urllib.request import Request, urlopen

try:
    from .runtime_config import get_default_telegram_chat_id
except ImportError:
    from runtime_config import get_default_telegram_chat_id

RUNTIME = Path(__file__).parent
CONFIG_PATH = Path("/home/tenazas/.openclaw/openclaw.json")
CHAT_ID = get_default_telegram_chat_id()


def _load_token() -> str:
    try:
        cfg = json.loads(CONFIG_PATH.read_text())
        return cfg["channels"]["telegram"]["botToken"]
    except Exception:
        return ""


def _send_telegram(text: str) -> bool:
    token = _load_token()
    if not token:
        return False
    try:
        payload = json.dumps({
            "chat_id": CHAT_ID,
            "text": text,
            "parse_mode": "Markdown",
        }).encode()
        req = Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        urlopen(req, timeout=10)
        return True
    except Exception:
        # Retry without markdown
        try:
            payload = json.dumps({
                "chat_id": CHAT_ID, "text": text,
            }).encode()
            req = Request(
                f"https://api.telegram.org/bot{token}/sendMessage",
                data=payload,
                headers={"Content-Type": "application/json"},
            )
            urlopen(req, timeout=10)
            return True
        except Exception:
            return False


def build_report() -> str:
    lines = [f"*Tenazas Data Report* — {date.today().isoformat()}"]
    lines.append("━" * 20)

    # Metrics report
    try:
        sys.path.insert(0, str(RUNTIME))
        from metrics_report import generate_report
        report = generate_report(date.today())
        total = report.get("total", 0)

        if total == 0:
            lines.append("Sin interacciones hoy.")
        else:
            paths = report.get("paths", {})
            lines.append(f"Mensajes: {total}")
            lines.append(
                f"Composer: {paths.get('composer', {}).get('pct', 0)}% | "
                f"LLM: {paths.get('llm_chat', {}).get('pct', 0)}% | "
                f"Guardrail: {paths.get('guardrail', {}).get('pct', 0)}%"
            )

            lat = report.get("latency", {})
            lines.append(f"Latencia: avg {lat.get('avg_ms', 0)}ms, p95 {lat.get('p95_ms', 0)}ms")

            top_skills = report.get("top_skills", [])[:3]
            if top_skills:
                sk = ", ".join(f"{s[0]}({s[1]})" for s in top_skills)
                lines.append(f"Top: {sk}")

            failures = report.get("failures", [])
            if failures:
                fl = ", ".join(f"{f[0]}({f[1]})" for f in failures[:3])
                lines.append(f"Failures: {fl}")

            errors = report.get("errors", {})
            if errors.get("count", 0) > 0:
                lines.append(f"Errors: {errors['count']} ({errors['rate_pct']}%)")
    except Exception as e:
        lines.append(f"Metrics: error — {e}")

    lines.append("")

    # Dataset extraction stats
    try:
        from extract_dataset_candidates import extract_chat_candidates, extract_failure_candidates
        chat = extract_chat_candidates()
        fails = extract_failure_candidates()
        lines.append(f"Dataset acumulado: {len(chat)} chat + {len(fails)} failures")

        # Check if ready for curation
        if len(chat) >= 200:
            lines.append(">> Listo para extraer dataset v2")
        elif len(chat) >= 100:
            lines.append(f">> {200 - len(chat)} más para dataset v2")
        else:
            lines.append(f">> Acumulando datos ({len(chat)}/200)")
    except Exception as e:
        lines.append(f"Dataset: error — {e}")

    return "\n".join(lines)


if __name__ == "__main__":
    report = build_report()

    if "--dry-run" in sys.argv:
        print(report)
    else:
        print(report)
        if _send_telegram(report):
            print("\nSent to Telegram.")
        else:
            print("\nFailed to send to Telegram.")
