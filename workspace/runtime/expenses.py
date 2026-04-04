#!/usr/bin/env python3
"""
Expenses — Quick daily expense capture for Tenazas.

Ultra-fast front-end for registering expenses from Telegram with minimal
friction. Shares storage with finance.py (runtime/finance/transactions.json)
so both modules see the same data.

Key differences from finance.py:
  - Accepts ultra-short input ("uber 4200", "almuerzo 8500")
  - Parses informal amounts ("12k", "15 lucas", "12.000")
  - Handles relative dates ("ayer", "anteayer")
  - Preserves raw input text for audit
  - Prioritizes capture speed over precision

Persistence: runtime/finance/transactions.json (shared with finance.py)
Logs: runtime/logs/expenses.log

Usage:
  python3 /home/tenazas/runtime/expenses.py --add "gasto 12k en nafta"
  python3 /home/tenazas/runtime/expenses.py --add "almuerzo 8500"
  python3 /home/tenazas/runtime/expenses.py --add "ayer uber 4200"
  python3 /home/tenazas/runtime/expenses.py --today
  python3 /home/tenazas/runtime/expenses.py --week
  python3 /home/tenazas/runtime/expenses.py --reclassify ID transporte
  python3 /home/tenazas/runtime/expenses.py --alerts
"""

import json
import re
import uuid
from datetime import datetime, timedelta
from pathlib import Path

# ── Config (shared storage with finance.py) ─────────────────────

BASE_DIR = Path(__file__).parent
FINANCE_DIR = BASE_DIR / "finance"
TRANSACTIONS_FILE = FINANCE_DIR / "transactions.json"
LOG_FILE = BASE_DIR / "logs" / "expenses.log"

# ── Categories (same as finance.py) ─────────────────────────────

CATEGORIES = {
    "supermercado", "transporte", "servicios", "alquiler", "salud",
    "educacion", "ocio", "impuestos", "trabajo", "ingresos", "otros",
}

# Extended keyword map optimized for quick capture
_CATEGORY_MAP = {
    "supermercado": ["super", "mercado", "carrefour", "coto", "dia", "chino",
                     "almacen", "verduleria", "carniceria", "comida", "compras",
                     "fiambre", "pan", "verdura", "fruta"],
    "transporte":   ["uber", "taxi", "remis", "sube", "nafta", "combustible",
                     "estacionamiento", "peaje", "cabify", "colectivo", "subte",
                     "didi", "gnc", "garage"],
    "servicios":    ["internet", "luz", "gas", "agua", "telefono", "celular",
                     "cable", "streaming", "spotify", "netflix", "flow",
                     "personal", "claro", "movistar"],
    "alquiler":     ["alquiler", "expensas", "inmobiliaria"],
    "salud":        ["medico", "farmacia", "remedio", "obra social", "prepaga",
                     "hospital", "clinica", "turno", "medicamento"],
    "educacion":    ["curso", "universidad", "libro", "capacitacion", "clase",
                     "udemy", "platzi"],
    "ocio":         ["cine", "teatro", "restaurant", "bar", "cerveza", "salida",
                     "juego", "steam", "play", "viaje", "vacaciones", "cafe",
                     "helado", "delivery", "rappi", "pedidosya", "almuerzo",
                     "cena", "birra", "pizza", "hamburgues", "sushi"],
    "impuestos":    ["impuesto", "monotributo", "afip", "iibb", "ganancias",
                     "iva", "arba", "agip", "patente"],
}

# Single-word shortcuts that ARE the category (no description needed)
_SHORTCUT_WORDS = {
    "uber": "transporte", "taxi": "transporte", "remis": "transporte",
    "cabify": "transporte", "subte": "transporte", "sube": "transporte",
    "nafta": "transporte", "gnc": "transporte",
    "super": "supermercado", "mercado": "supermercado",
    "almuerzo": "ocio", "cena": "ocio", "cafe": "ocio",
    "delivery": "ocio", "rappi": "ocio", "pedidosya": "ocio",
    "farmacia": "salud", "medico": "salud",
    "alquiler": "alquiler", "expensas": "alquiler",
    "spotify": "servicios", "netflix": "servicios",
}

# Weekly thresholds per category (default alerts)
DEFAULT_WEEKLY_THRESHOLDS = {
    "supermercado": 80000,
    "transporte": 40000,
    "ocio": 50000,
    "servicios": 60000,
}


def _now():
    return datetime.now().astimezone()


def _log(entry: dict):
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    entry["ts"] = _now().isoformat()
    with open(LOG_FILE, "a") as f:
        f.write(json.dumps(entry, default=str) + "\n")


# ── Shared Persistence ──────────────────────────────────────────


def _load_transactions() -> list:
    try:
        with open(TRANSACTIONS_FILE) as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def _save_transactions(txns: list):
    FINANCE_DIR.mkdir(parents=True, exist_ok=True)
    with open(TRANSACTIONS_FILE, "w") as f:
        json.dump(txns, f, indent=2, ensure_ascii=False, default=str)


# ── Informal Amount Parser ──────────────────────────────────────


def parse_amount(raw: str) -> float | None:
    """Parse informal Argentine amount expressions.

    Handles:
      "12k" → 12000
      "12K" → 12000
      "15 lucas" → 15000
      "12.000" → 12000
      "12,5k" → 12500
      "8500" → 8500
      "$8500" → 8500
      "1.200.000" → 1200000

    Returns:
        float or None if unparseable.
    """
    raw = raw.strip().replace("$", "").replace(" ", "")

    # "12k" or "12K" or "12,5k"
    m = re.match(r"^([0-9]+(?:[.,][0-9]+)?)\s*[kK]$", raw)
    if m:
        val = m.group(1).replace(",", ".")
        result = float(val) * 1000
        return result if result > 0 else None

    # "15 lucas" / "15lucas"
    raw_no_space = raw.replace(" ", "")
    m = re.match(r"^([0-9]+(?:[.,][0-9]+)?)\s*lucas?$", raw_no_space, re.I)
    if m:
        val = m.group(1).replace(",", ".")
        result = float(val) * 1000
        return result if result > 0 else None

    # Standard number parsing (handles dots as thousand separators)
    clean = raw
    if "." in clean and "," in clean:
        clean = clean.replace(".", "").replace(",", ".")
    elif "," in clean:
        parts = clean.split(",")
        if len(parts[-1]) <= 2:
            clean = clean.replace(",", ".")
        else:
            clean = clean.replace(",", "")
    elif "." in clean:
        parts = clean.split(".")
        if len(parts[-1]) == 3 and len(parts) > 1:
            clean = clean.replace(".", "")

    try:
        val = float(clean)
        return val if val > 0 else None
    except ValueError:
        return None


# ── Relative Date Parser ────────────────────────────────────────


def parse_relative_date(text: str) -> tuple:
    """Extract relative date from text and return (date_iso, remaining_text).

    Handles: hoy, ayer, anteayer, "el lunes", "el martes", etc.

    Returns:
        (date_iso_string, text_without_date_part)
    """
    text = text.strip()
    now = _now()

    # "ayer" at start or end
    m = re.match(r"^ayer\s+(.+)", text, re.I)
    if m:
        date = (now - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%S")
        return date, m.group(1).strip()
    m = re.match(r"(.+?)\s+ayer$", text, re.I)
    if m:
        date = (now - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%S")
        return date, m.group(1).strip()

    # "anteayer"
    m = re.match(r"^anteayer\s+(.+)", text, re.I)
    if m:
        date = (now - timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%S")
        return date, m.group(1).strip()

    # "hoy" (explicit, strip it)
    m = re.match(r"^hoy\s+(.+)", text, re.I)
    if m:
        return now.strftime("%Y-%m-%dT%H:%M:%S"), m.group(1).strip()

    # Default: today
    return now.strftime("%Y-%m-%dT%H:%M:%S"), text


# ── Quick-Capture Parser ────────────────────────────────────────


def _auto_categorize(text: str) -> str:
    """Categorize expense from description text."""
    text_lower = text.lower()

    # Check shortcuts first (single-word matches)
    words = text_lower.split()
    for word in words:
        if word in _SHORTCUT_WORDS:
            return _SHORTCUT_WORDS[word]

    # Check keyword map
    for cat, keywords in _CATEGORY_MAP.items():
        for kw in keywords:
            if kw in text_lower:
                return cat

    return "otros"


def parse_quick_expense(text: str) -> dict | None:
    """Parse ultra-short expense input.

    Formats supported:
      "gasto 12k en nafta"
      "almuerzo 8500"
      "uber 4200"
      "ayer gasté 15 lucas en farmacia"
      "nafta 12k"
      "12000 super"

    Returns:
        dict with: amount, description, category, date, raw_input
        or None if unparseable.
    """
    original = text.strip()
    if not original:
        return None

    # Step 1: Extract relative date
    date, text = parse_relative_date(original)

    # Step 2: Strip common prefixes
    text = re.sub(r"^(?:gast[oé]|pagu[eé]|gasto:?)\s*", "", text, flags=re.I).strip()

    # Step 3: Try various patterns

    # Pattern A: "WORD AMOUNT" (e.g., "uber 4200", "almuerzo 8500", "nafta 12k")
    m = re.match(r"^(\w+(?:\s+\w+)?)\s+(\$?[0-9][0-9.,]*\s*(?:[kK]|lucas?)?)\s*$", text)
    if m:
        desc = m.group(1).strip()
        amount = parse_amount(m.group(2))
        if amount:
            return {
                "amount": amount,
                "description": desc,
                "category": _auto_categorize(desc),
                "date": date,
                "raw_input": original,
            }

    # Pattern B: "AMOUNT WORD" (e.g., "12000 super", "8500 almuerzo")
    m = re.match(r"^(\$?[0-9][0-9.,]*\s*(?:[kK]|lucas?)?)\s+(?:en\s+)?(.+)$", text)
    if m:
        amount = parse_amount(m.group(1))
        desc = m.group(2).strip()
        if amount:
            return {
                "amount": amount,
                "description": desc,
                "category": _auto_categorize(desc),
                "date": date,
                "raw_input": original,
            }

    # Pattern C: "AMOUNT en/de WORD" (e.g., "12k en nafta")
    m = re.match(r"^(\$?[0-9][0-9.,]*\s*(?:[kK]|lucas?)?)\s+(?:en|de)\s+(.+)$", text)
    if m:
        amount = parse_amount(m.group(1))
        desc = m.group(2).strip()
        if amount:
            return {
                "amount": amount,
                "description": desc,
                "category": _auto_categorize(desc),
                "date": date,
                "raw_input": original,
            }

    # Pattern D: just "WORD AMOUNT en WORD" (e.g., "gasto 12k en nafta" after prefix strip)
    m = re.match(r"^(\$?[0-9][0-9.,]*\s*(?:[kK]|lucas?)?)\s+(?:en|de|por)\s+(.+)$", text)
    if m:
        amount = parse_amount(m.group(1))
        desc = m.group(2).strip()
        if amount:
            return {
                "amount": amount,
                "description": desc,
                "category": _auto_categorize(desc),
                "date": date,
                "raw_input": original,
            }

    return None


# ── Quick Add ───────────────────────────────────────────────────


def quick_add(text: str) -> dict | None:
    """Parse and save an expense from ultra-short input.

    Returns:
        Saved transaction dict with id, or None if unparseable.
    """
    parsed = parse_quick_expense(text)
    if not parsed:
        return None

    txn = {
        "id": uuid.uuid4().hex[:8],
        "type": "expense",
        "amount": round(parsed["amount"], 2),
        "description": parsed["description"],
        "category": parsed["category"],
        "date": parsed["date"],
        "created_at": _now().isoformat(),
        "raw_input": parsed["raw_input"],
        "source": "quick_add",
    }

    txns = _load_transactions()
    txns.append(txn)
    _save_transactions(txns)

    _log({"action": "quick_add", "id": txn["id"], "amount": txn["amount"],
          "category": txn["category"], "description": txn["description"]})

    return txn


# ── Reclassify ──────────────────────────────────────────────────


def reclassify(txn_id: str, new_category: str) -> bool:
    """Change the category of an existing transaction.

    Args:
        txn_id: transaction ID
        new_category: must be a valid category from CATEGORIES

    Returns:
        True if updated, False if not found or invalid category.
    """
    if new_category not in CATEGORIES:
        _log({"action": "reclassify", "id": txn_id,
              "error": f"invalid category: {new_category}"})
        return False

    txns = _load_transactions()
    for txn in txns:
        if txn.get("id") == txn_id:
            old = txn.get("category", "otros")
            txn["category"] = new_category
            txn["reclassified_from"] = old
            txn["reclassified_at"] = _now().isoformat()
            _save_transactions(txns)
            _log({"action": "reclassify", "id": txn_id,
                  "from": old, "to": new_category})
            return True

    _log({"action": "reclassify", "id": txn_id, "error": "not found"})
    return False


# ── Summaries ───────────────────────────────────────────────────


def daily_summary(date: str | None = None) -> dict:
    """Get expense summary for a specific day.

    Args:
        date: YYYY-MM-DD string, or None for today.

    Returns:
        dict with: date, total, count, by_category, transactions
    """
    if date is None:
        date = _now().strftime("%Y-%m-%d")

    txns = _load_transactions()
    day_txns = [t for t in txns
                if t.get("type") == "expense"
                and t.get("date", "").startswith(date)]

    total = sum(t["amount"] for t in day_txns)
    by_cat = {}
    for t in day_txns:
        cat = t.get("category", "otros")
        by_cat[cat] = by_cat.get(cat, 0) + t["amount"]

    return {
        "date": date,
        "total": round(total, 2),
        "count": len(day_txns),
        "by_category": {k: round(v, 2) for k, v in
                        sorted(by_cat.items(), key=lambda x: -x[1])},
        "transactions": sorted(day_txns, key=lambda t: t.get("date", ""), reverse=True),
    }


def weekly_summary(category: str | None = None) -> dict:
    """Get expense summary for the current week (Monday–today).

    Args:
        category: optional filter for a single category

    Returns:
        dict with: start, end, total, count, by_category, by_day
    """
    now = _now()
    monday = now - timedelta(days=now.weekday())
    start = monday.strftime("%Y-%m-%d")
    end = now.strftime("%Y-%m-%d")

    txns = _load_transactions()
    week_txns = [t for t in txns
                 if t.get("type") == "expense"
                 and t.get("date", "") >= start
                 and t.get("date", "") <= end + "T23:59:59"]

    if category:
        week_txns = [t for t in week_txns if t.get("category") == category]

    total = sum(t["amount"] for t in week_txns)
    by_cat = {}
    by_day = {}
    for t in week_txns:
        cat = t.get("category", "otros")
        by_cat[cat] = by_cat.get(cat, 0) + t["amount"]
        day = t.get("date", "")[:10]
        by_day[day] = by_day.get(day, 0) + t["amount"]

    return {
        "start": start,
        "end": end,
        "total": round(total, 2),
        "count": len(week_txns),
        "by_category": {k: round(v, 2) for k, v in
                        sorted(by_cat.items(), key=lambda x: -x[1])},
        "by_day": {k: round(v, 2) for k, v in sorted(by_day.items())},
        "filter_category": category,
    }


def format_daily(s: dict) -> str:
    """Format daily summary for Telegram."""
    lines = [f"Gastos {s['date']}: ${s['total']:,.0f} ({s['count']} mov)"]
    if s["by_category"]:
        for cat, total in s["by_category"].items():
            lines.append(f"  {cat}: ${total:,.0f}")
    if not s["count"]:
        lines = [f"Sin gastos registrados para {s['date']}"]
    return "\n".join(lines)


def format_weekly(s: dict) -> str:
    """Format weekly summary for Telegram."""
    header = f"Semana {s['start']} → {s['end']}"
    if s["filter_category"]:
        header += f" [{s['filter_category']}]"
    lines = [f"{header}: ${s['total']:,.0f} ({s['count']} mov)"]
    if s["by_category"]:
        lines.append("")
        for cat, total in s["by_category"].items():
            lines.append(f"  {cat}: ${total:,.0f}")
    if s["by_day"]:
        lines.append("")
        for day, total in s["by_day"].items():
            lines.append(f"  {day}: ${total:,.0f}")
    return "\n".join(lines)


# ── Threshold Alerts ────────────────────────────────────────────


def check_threshold_alerts(thresholds: dict | None = None) -> list:
    """Check if any category exceeds its weekly threshold.

    Args:
        thresholds: {category: max_amount} or None for defaults

    Returns:
        List of alert dicts.
    """
    if thresholds is None:
        thresholds = DEFAULT_WEEKLY_THRESHOLDS

    ws = weekly_summary()
    alerts = []

    for cat, limit in thresholds.items():
        spent = ws["by_category"].get(cat, 0)
        if spent > limit:
            alerts.append({
                "type": "threshold_exceeded",
                "category": cat,
                "spent": spent,
                "limit": limit,
                "message": f"{cat}: ${spent:,.0f} esta semana (limite: ${limit:,.0f})",
            })

    if alerts:
        _log({"action": "threshold_alerts", "count": len(alerts),
              "categories": [a["category"] for a in alerts]})

    return alerts


def format_alerts(alerts: list) -> str:
    """Format alerts for Telegram."""
    if not alerts:
        return "Sin alertas de gastos."
    lines = ["Alertas de gastos:"]
    for a in alerts:
        lines.append(f"  - {a['message']}")
    return "\n".join(lines)


# ── CLI ─────────────────────────────────────────────────────────


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Tenazas Quick Expense Tracker")
    parser.add_argument("--add", type=str, help="Quick-add expense from text")
    parser.add_argument("--today", action="store_true", help="Today's summary")
    parser.add_argument("--yesterday", action="store_true", help="Yesterday's summary")
    parser.add_argument("--week", action="store_true", help="Weekly summary")
    parser.add_argument("--category", type=str, help="Filter by category")
    parser.add_argument("--reclassify", nargs=2, metavar=("ID", "CAT"),
                        help="Change transaction category")
    parser.add_argument("--alerts", action="store_true", help="Check threshold alerts")
    parser.add_argument("--json", action="store_true", help="Output as JSON")

    args = parser.parse_args()

    if args.add:
        result = quick_add(args.add)
        if result:
            if args.json:
                print(json.dumps(result, ensure_ascii=False, default=str))
            else:
                print(f"${result['amount']:,.0f} [{result['category']}] "
                      f"{result['description']}")
        else:
            print("No pude parsear. Formatos validos:")
            print("  --add 'uber 4200'")
            print("  --add 'almuerzo 8500'")
            print("  --add '12k en nafta'")
            print("  --add 'ayer 15 lucas en farmacia'")

    elif args.today:
        s = daily_summary()
        print(json.dumps(s, ensure_ascii=False, default=str) if args.json
              else format_daily(s))

    elif args.yesterday:
        yesterday = (_now() - timedelta(days=1)).strftime("%Y-%m-%d")
        s = daily_summary(yesterday)
        print(json.dumps(s, ensure_ascii=False, default=str) if args.json
              else format_daily(s))

    elif args.week:
        s = weekly_summary(category=args.category)
        print(json.dumps(s, ensure_ascii=False, default=str) if args.json
              else format_weekly(s))

    elif args.reclassify:
        txn_id, new_cat = args.reclassify
        if reclassify(txn_id, new_cat):
            print(f"[{txn_id}] reclasificado → {new_cat}")
        else:
            print("No se pudo reclasificar. Verificar ID y categoria.")
            print(f"Categorias: {', '.join(sorted(CATEGORIES))}")

    elif args.alerts:
        a = check_threshold_alerts()
        print(json.dumps(a, ensure_ascii=False, default=str) if args.json
              else format_alerts(a))

    else:
        parser.print_help()
