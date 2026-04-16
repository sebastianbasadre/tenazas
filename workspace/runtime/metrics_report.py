#!/usr/bin/env python3
"""
metrics_report.py — Daily metrics summary for Tenazas.

Reads router_decisions.jsonl and generates a human-readable report.

Usage:
    python3 metrics_report.py             # today's report
    python3 metrics_report.py --yesterday # yesterday's report
    python3 metrics_report.py --all       # all-time report
    python3 metrics_report.py --json      # JSON output
"""

import json
import sys
from collections import Counter
from datetime import datetime, date, timedelta
from pathlib import Path

LOG_DIR = Path(__file__).parent / "logs"
ROUTER_LOG = LOG_DIR / "router_decisions.jsonl"
SKILL_LOG = LOG_DIR / "skill_runs.jsonl"
FAIL_LOG = LOG_DIR / "chat_failures.jsonl"


def _load_jsonl(path: Path, date_filter: date | None = None) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            if date_filter:
                ts = row.get("ts", "")[:10]
                if ts != date_filter.isoformat():
                    continue
            rows.append(row)
        except json.JSONDecodeError:
            continue
    return rows


def generate_report(date_filter: date | None = None) -> dict:
    decisions = _load_jsonl(ROUTER_LOG, date_filter)
    failures = _load_jsonl(FAIL_LOG, date_filter)

    if not decisions:
        return {"total": 0, "message": "No data for this period."}

    total = len(decisions)

    # Path distribution
    paths = Counter(d.get("path", "unknown") for d in decisions)
    composer_pct = round(paths.get("composer", 0) / total * 100, 1)
    humanize_pct = round(paths.get("humanize", 0) / total * 100, 1)
    raw_pct = round(paths.get("raw", 0) / total * 100, 1)
    llm_pct = round(paths.get("llm_chat", 0) / total * 100, 1)
    guardrail_pct = round(paths.get("early_return", 0) / total * 100, 1)

    # Top skills
    skills = Counter(d.get("skill") for d in decisions if d.get("skill"))

    # Top routes
    routes = Counter(d.get("route", "unknown") for d in decisions)

    # Latency
    latencies = [d.get("latency_ms", 0) for d in decisions if d.get("latency_ms", 0) > 0]
    avg_latency = round(sum(latencies) / len(latencies)) if latencies else 0
    p95_latency = 0
    if latencies:
        sorted_lat = sorted(latencies)
        p95_idx = int(len(sorted_lat) * 0.95)
        p95_latency = sorted_lat[min(p95_idx, len(sorted_lat) - 1)]

    # Errors
    errors = sum(1 for d in decisions if not d.get("success", True))
    error_rate = round(errors / total * 100, 1)

    # Failure reasons
    fail_reasons = Counter(f.get("reason", "unknown") for f in failures)

    return {
        "total": total,
        "paths": {
            "composer": {"count": paths.get("composer", 0), "pct": composer_pct},
            "humanize": {"count": paths.get("humanize", 0), "pct": humanize_pct},
            "raw": {"count": paths.get("raw", 0), "pct": raw_pct},
            "llm_chat": {"count": paths.get("llm_chat", 0), "pct": llm_pct},
            "guardrail": {"count": paths.get("early_return", 0), "pct": guardrail_pct},
        },
        "top_skills": skills.most_common(10),
        "top_routes": routes.most_common(10),
        "latency": {"avg_ms": avg_latency, "p95_ms": p95_latency},
        "errors": {"count": errors, "rate_pct": error_rate},
        "failures": fail_reasons.most_common(10),
    }


def format_report(report: dict) -> str:
    if report.get("total", 0) == 0:
        return report.get("message", "No data.")

    lines = [
        "Tenazas daily metrics",
        "=" * 21,
        f"Total messages: {report['total']}",
        f"Composer: {report['paths']['composer']['pct']}% ({report['paths']['composer']['count']})",
        f"Humanize: {report['paths']['humanize']['pct']}% ({report['paths']['humanize']['count']})",
        f"Raw: {report['paths']['raw']['pct']}% ({report['paths']['raw']['count']})",
        f"LLM chat: {report['paths']['llm_chat']['pct']}% ({report['paths']['llm_chat']['count']})",
        f"Guardrail: {report['paths']['guardrail']['pct']}% ({report['paths']['guardrail']['count']})",
        "",
        "Top skills:",
    ]
    for skill, count in report["top_skills"][:7]:
        lines.append(f"  - {skill}: {count}")

    if report["failures"]:
        lines.append("")
        lines.append("Failures:")
        for reason, count in report["failures"][:5]:
            lines.append(f"  - {reason}: {count}")

    lines.extend([
        "",
        "Latency:",
        f"  - avg: {report['latency']['avg_ms']} ms",
        f"  - p95: {report['latency']['p95_ms']} ms",
        "",
        f"Errors: {report['errors']['count']} ({report['errors']['rate_pct']}%)",
    ])

    return "\n".join(lines)


if __name__ == "__main__":
    args = sys.argv[1:]
    if "--all" in args:
        df = None
    elif "--yesterday" in args:
        df = date.today() - timedelta(days=1)
    else:
        df = date.today()

    report = generate_report(df)

    if "--json" in args:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        print(format_report(report))
