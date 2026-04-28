#!/usr/bin/env python3
"""
Daily Brief — Tenazas personal assistant morning brief.

Aggregates data from finance, expenses, jobs, daily improvement, and
system health into a concise, actionable brief for Telegram.

Each data source is isolated: if a module fails or has no data, that
section is skipped gracefully — the brief is never blocked.

Usage:
  docker exec openclaw python3 /home/tenazas/runtime/daily_brief.py
  docker exec openclaw python3 /home/tenazas/runtime/daily_brief.py --json

Output: prints brief to stdout (for Telegram capture) + logs to daily_brief.log
"""

import json
from datetime import datetime
from pathlib import Path
from runtime_utils import now

BASE_DIR = Path(__file__).parent
LOG_FILE = BASE_DIR / "logs" / "daily_brief.log"

# Data source paths (for direct file reads when imports fail)
FINANCE_DIR = BASE_DIR / "finance"
TRANSACTIONS_FILE = FINANCE_DIR / "transactions.json"
JOBS_FILE = BASE_DIR / "jobs" / "opportunities.json"
DAILY_IMPROVEMENT_FILE = BASE_DIR / "learning" / "daily_improvement.json"



def _log(entry: dict):
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    entry["ts"] = now().isoformat()
    with open(LOG_FILE, "a") as f:
        f.write(json.dumps(entry, default=str) + "\n")


# ── Section Gatherers (each isolated, never raises) ────────────


def gather_finance() -> dict | None:
    """Gather monthly finance summary. Returns None on failure."""
    try:
        from runtime.finance import summary
        s = summary("month")
        if s.get("transaction_count", 0) == 0:
            return None
        return {
            "income": s["total_income"],
            "expenses": s["total_expenses"],
            "balance": s["balance"],
            "transactions": s["transaction_count"],
            "top_categories": _top_categories(s.get("by_category", {})),
        }
    except Exception:
        return None


def gather_expenses_today() -> dict | None:
    """Gather today's expense summary. Returns None on failure."""
    try:
        from runtime.expenses import daily_summary
        s = daily_summary()
        if s.get("count", 0) == 0:
            return None
        return {
            "total": s["total"],
            "count": s["count"],
            "top_category": _top_expense_category(s.get("by_category", {})),
        }
    except Exception:
        return None


def gather_expense_alerts() -> list:
    """Gather active expense alerts. Returns empty list on failure."""
    try:
        from runtime.expenses import check_threshold_alerts
        return check_threshold_alerts()
    except Exception:
        return []


def gather_finance_alerts() -> list:
    """Gather finance alerts. Returns empty list on failure."""
    try:
        from runtime.finance import check_alerts
        return check_alerts()
    except Exception:
        return []


def gather_jobs() -> dict | None:
    """Gather jobs pipeline summary. Returns None on failure."""
    try:
        from runtime.jobs import pipeline_summary, ACTIVE_STATUSES
        ps = pipeline_summary()
        if ps.get("total", 0) == 0:
            return None
        return {
            "total": ps["total"],
            "active": ps["active"],
            "by_status": ps.get("by_status", {}),
            "recent": [
                {"company": o.get("company", "?"), "role": o.get("role", "?"),
                 "status": o.get("status", "?")}
                for o in ps.get("recent", [])[:3]
            ],
        }
    except Exception:
        return None


def gather_daily_improvement() -> dict | None:
    """Gather the latest daily improvement proposal. Returns None on failure."""
    try:
        with open(DAILY_IMPROVEMENT_FILE) as f:
            history = json.load(f)
        if not isinstance(history, list) or not history:
            return None
        latest = history[-1]
        return {
            "date": latest.get("date", ""),
            "area": latest.get("area", ""),
            "proposal": latest.get("proposal", ""),
            "impact": latest.get("impact", ""),
        }
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        return None


# ── Helpers ─────────────────────────────────────────────────────


def _top_categories(by_cat: dict, limit: int = 3) -> list:
    """Extract top expense categories sorted by amount."""
    items = []
    for cat, data in by_cat.items():
        amount = data.get("expenses", 0) if isinstance(data, dict) else 0
        if amount > 0:
            items.append({"category": cat, "amount": amount})
    items.sort(key=lambda x: -x["amount"])
    return items[:limit]


def _top_expense_category(by_cat: dict) -> str | None:
    """Get the highest expense category name."""
    if not by_cat:
        return None
    return max(by_cat, key=by_cat.get)


def _action_suggestion(finance, expenses, jobs, improvement, alerts) -> str:
    """Generate a concrete action recommendation based on available data."""
    # Priority: alerts > stale jobs > high expenses > improvement
    if alerts:
        return f"Revisar alerta: {alerts[0].get('message', alerts[0].get('type', 'alerta activa'))}"

    if jobs and jobs.get("by_status", {}).get("detected", 0) > 2:
        return "Revisar oportunidades detectadas y decidir cuales postular"

    if expenses and expenses["total"] > 50000:
        cat = expenses.get("top_category", "gastos")
        return f"Dia de gasto alto (${expenses['total']:,.0f}) — revisar {cat}"

    if improvement and improvement.get("proposal"):
        return f"Mejora propuesta: {improvement['proposal'][:60]}"

    return "Sin acciones urgentes — buen dia para revisar pipeline laboral"


# ── Brief Assembly ──────────────────────────────────────────────


def generate_brief() -> dict:
    """Generate the daily brief by gathering all sections.

    Each section is independent — failures are logged but don't block others.

    Returns:
        dict with: date, sections (dict of gathered data), brief_text, action
    """
    _now = now()
    sections = {}
    errors = []

    # Gather all sections
    for name, fn in [
        ("finance", gather_finance),
        ("expenses_today", gather_expenses_today),
        ("jobs", gather_jobs),
        ("improvement", gather_daily_improvement),
    ]:
        try:
            result = fn()
            if result is not None:
                sections[name] = result
        except Exception as e:
            errors.append(f"{name}: {e}")

    # Alerts (merged)
    all_alerts = gather_expense_alerts() + gather_finance_alerts()

    # Action suggestion
    action = _action_suggestion(
        sections.get("finance"),
        sections.get("expenses_today"),
        sections.get("jobs"),
        sections.get("improvement"),
        all_alerts,
    )

    brief = {
        "date": _now.strftime("%Y-%m-%d"),
        "time": _now.strftime("%H:%M"),
        "sections": sections,
        "alerts": all_alerts,
        "action": action,
        "sections_available": list(sections.keys()),
        "sections_missing": [s for s in ("finance", "expenses_today", "jobs", "improvement")
                             if s not in sections],
    }

    if errors:
        brief["errors"] = errors

    _log({"action": "brief_generated",
          "sections": list(sections.keys()),
          "alerts": len(all_alerts),
          "errors": len(errors)})

    return brief


def format_brief(brief: dict) -> str:
    """Format brief as concise Telegram-ready text."""
    _now = now()
    lines = [f"Buenos dias Sebas — {_now.strftime('%A %d/%m')}"]
    lines.append("")

    sections = brief.get("sections", {})

    # 1. Finance
    fin = sections.get("finance")
    if fin:
        lines.append(f"Finanzas del mes: ${fin['income']:,.0f} ingreso, "
                      f"${fin['expenses']:,.0f} gasto, "
                      f"balance ${fin['balance']:,.0f}")
        if fin.get("top_categories"):
            tops = ", ".join(f"{c['category']}(${c['amount']:,.0f})"
                            for c in fin["top_categories"][:2])
            lines.append(f"  Top: {tops}")

    # 2. Expenses today
    exp = sections.get("expenses_today")
    if exp:
        lines.append(f"Hoy: ${exp['total']:,.0f} en {exp['count']} gastos"
                      + (f" — mayor: {exp['top_category']}" if exp.get("top_category") else ""))

    # 3. Alerts
    alerts = brief.get("alerts", [])
    if alerts:
        lines.append("")
        for a in alerts[:3]:
            msg = a.get("message", a.get("type", "alerta"))
            lines.append(f"  ! {msg}")

    # 4. Jobs
    jobs = sections.get("jobs")
    if jobs:
        lines.append("")
        status_parts = []
        for s in ("applied", "screening", "interview", "offer"):
            count = jobs["by_status"].get(s, 0)
            if count:
                status_parts.append(f"{count} {s}")
        if status_parts:
            lines.append(f"Busqueda: {', '.join(status_parts)}")
        elif jobs["active"]:
            lines.append(f"Busqueda: {jobs['active']} activas")
        if jobs.get("recent"):
            r = jobs["recent"][0]
            lines.append(f"  Reciente: {r['company']} — {r['role']} ({r['status']})")

    # 5. Daily improvement
    imp = sections.get("improvement")
    if imp and imp.get("proposal"):
        lines.append("")
        lines.append(f"Mejora [{imp.get('area', '?')}]: {imp['proposal'][:80]}")

    # 6. Action
    lines.append("")
    lines.append(f">> {brief.get('action', 'Sin acciones urgentes')}")

    # Missing data note (subtle)
    missing = brief.get("sections_missing", [])
    if missing:
        lines.append("")
        lines.append(f"(sin datos: {', '.join(missing)})")

    return "\n".join(lines)


# ── CLI ─────────────────────────────────────────────────────────


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Tenazas Daily Brief")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    brief = generate_brief()

    if args.json:
        print(json.dumps(brief, ensure_ascii=False, indent=2, default=str))
    else:
        print(format_brief(brief))
