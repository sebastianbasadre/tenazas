#!/usr/bin/env python3
"""
Finance — Personal finance tracker for Tenazas.

Registers income/expenses, categorizes transactions, generates summaries,
and detects spending anomalies. All input via natural language (Spanish).

Persistence: runtime/finance/transactions.json
Logs: runtime/logs/finance.log

Security:
  - No bank API integrations
  - No payment execution
  - No sensitive data inference
  - Read/write only within runtime/finance/

Usage:
  docker exec openclaw python3 /home/tenazas/runtime/finance.py --summary
  docker exec openclaw python3 /home/tenazas/runtime/finance.py --add "gasté 18500 en supermercado"
  docker exec openclaw python3 /home/tenazas/runtime/finance.py --month 2026-04
  docker exec openclaw python3 /home/tenazas/runtime/finance.py --alerts
"""

import json
import re
import uuid
from datetime import datetime, timedelta
from pathlib import Path

# ── Config ──────────────────────────────────────────────────────

BASE_DIR = Path(__file__).parent
FINANCE_DIR = BASE_DIR / "finance"
TRANSACTIONS_FILE = FINANCE_DIR / "transactions.json"
LOG_FILE = BASE_DIR / "logs" / "finance.log"

# ── Categories ──────────────────────────────────────────────────

CATEGORIES = {
    "supermercado", "transporte", "servicios", "alquiler", "salud",
    "educacion", "ocio", "impuestos", "trabajo", "ingresos", "otros",
}

# Keywords → category (Spanish, rioplatense)
CATEGORY_KEYWORDS = {
    "supermercado": ["super", "mercado", "carrefour", "coto", "dia", "chino",
                     "almacen", "verduleria", "carniceria", "comida", "compras"],
    "transporte":   ["uber", "taxi", "remis", "sube", "nafta", "combustible",
                     "estacionamiento", "peaje", "cabify", "colectivo", "subte"],
    "servicios":    ["internet", "luz", "gas", "agua", "telefono", "celular",
                     "cable", "streaming", "spotify", "netflix", "flow"],
    "alquiler":     ["alquiler", "expensas", "inmobiliaria"],
    "salud":        ["medico", "farmacia", "remedio", "obra social", "prepaga",
                     "hospital", "clinica", "turno", "medicamento"],
    "educacion":    ["curso", "universidad", "libro", "capacitacion", "clase",
                     "udemy", "platzi"],
    "ocio":         ["cine", "teatro", "restaurant", "bar", "cerveza", "salida",
                     "juego", "steam", "play", "viaje", "vacaciones"],
    "impuestos":    ["impuesto", "monotributo", "afip", "iibb", "ganancias",
                     "iva", "arba", "agip", "patente"],
    "trabajo":      ["sueldo", "salario", "freelance", "honorarios", "factura",
                     "cobro", "proyecto"],
    "ingresos":     ["cobre", "ingreso", "deposito", "transferencia recibida"],
}

# Alert thresholds
ALERT_HIGH_EXPENSE = 50000       # Single expense > 50k ARS
ALERT_DAILY_LIMIT = 100000       # Daily total > 100k ARS
ALERT_CATEGORY_DEVIATION = 2.0   # Category spending > 2x monthly average


def _now():
    return datetime.now().astimezone()


def _log(entry: dict):
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    entry["ts"] = _now().isoformat()
    with open(LOG_FILE, "a") as f:
        f.write(json.dumps(entry, default=str) + "\n")


# ── Persistence ─────────────────────────────────────────────────


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


# ── NLP Parsing (Spanish rioplatense) ───────────────────────────

# Patterns for natural language input
_EXPENSE_PATTERNS = [
    # "gasté 18500 en supermercado"
    re.compile(r"gast[eé]\s+\$?([0-9.,]+)\s+(?:en\s+)?(.+)", re.I),
    # "pagué 42000 de internet"
    re.compile(r"pagu[eé]\s+\$?([0-9.,]+)\s+(?:de|por)\s+(.+)", re.I),
    # "compré algo por 5000"
    re.compile(r"compr[eé]\s+(.+?)\s+(?:por|a)\s+\$?([0-9.,]+)", re.I),
    # "gasto: supermercado 18500"
    re.compile(r"gasto:?\s+(.+?)\s+\$?([0-9.,]+)", re.I),
]

_INCOME_PATTERNS = [
    # "cobré 1200000 de sueldo"
    re.compile(r"cobr[eé]\s+\$?([0-9.,]+)\s+(?:de|por)\s+(.+)", re.I),
    # "ingreso de 500000 por freelance"
    re.compile(r"ingreso\s+(?:de\s+)?\$?([0-9.,]+)\s+(?:de|por)\s+(.+)", re.I),
    # "me pagaron 80000"
    re.compile(r"me\s+pagaron\s+\$?([0-9.,]+)(?:\s+(?:de|por)\s+(.+))?", re.I),
]


def _parse_amount(raw: str) -> float | None:
    """Parse an amount string to float. Handles dots and commas."""
    raw = raw.strip().replace("$", "").replace(" ", "")
    # "18.500" or "18,500" → 18500 (thousand separators)
    # "18500.50" or "18500,50" → 18500.50 (decimal)
    if "." in raw and "," in raw:
        # "18.500,50" → 18500.50
        raw = raw.replace(".", "").replace(",", ".")
    elif "," in raw:
        parts = raw.split(",")
        if len(parts[-1]) <= 2:
            raw = raw.replace(",", ".")
        else:
            raw = raw.replace(",", "")
    elif "." in raw:
        parts = raw.split(".")
        if len(parts[-1]) == 3 and len(parts) > 1:
            raw = raw.replace(".", "")  # thousand separator
    try:
        val = float(raw)
        return val if val > 0 else None
    except ValueError:
        return None


def parse_transaction(text: str) -> dict | None:
    """Parse a natural language transaction from Spanish input.

    Returns:
        dict with: type, amount, description, category (or None if unparseable)
    """
    text = text.strip()

    # Try income patterns first
    for pattern in _INCOME_PATTERNS:
        m = pattern.search(text)
        if m:
            groups = m.groups()
            amount_raw = groups[0]
            desc = (groups[1] or "ingreso").strip() if len(groups) > 1 else "ingreso"
            amount = _parse_amount(amount_raw)
            if amount:
                category = auto_categorize(desc, "income")
                return {
                    "type": "income",
                    "amount": amount,
                    "description": desc,
                    "category": category,
                }

    # Try expense patterns
    for pattern in _EXPENSE_PATTERNS:
        m = pattern.search(text)
        if m:
            groups = m.groups()
            # Some patterns have amount first, some have description first
            if pattern == _EXPENSE_PATTERNS[2]:  # "compré X por N"
                desc = groups[0].strip()
                amount_raw = groups[1]
            elif pattern == _EXPENSE_PATTERNS[3]:  # "gasto: X N"
                desc = groups[0].strip()
                amount_raw = groups[1]
            else:
                amount_raw = groups[0]
                desc = groups[1].strip() if len(groups) > 1 else "gasto"
            amount = _parse_amount(amount_raw)
            if amount:
                category = auto_categorize(desc, "expense")
                return {
                    "type": "expense",
                    "amount": amount,
                    "description": desc,
                    "category": category,
                }

    return None


# ── Categorization ──────────────────────────────────────────────


def auto_categorize(description: str, txn_type: str = "expense") -> str:
    """Auto-categorize a transaction by keyword matching.

    Args:
        description: transaction description text
        txn_type: "income" or "expense"

    Returns:
        Category string from CATEGORIES.
    """
    if txn_type == "income":
        desc_lower = description.lower()
        for cat, keywords in CATEGORY_KEYWORDS.items():
            if cat in ("trabajo", "ingresos"):
                for kw in keywords:
                    if kw in desc_lower:
                        return cat
        return "ingresos"

    desc_lower = description.lower()
    for cat, keywords in CATEGORY_KEYWORDS.items():
        for kw in keywords:
            if kw in desc_lower:
                return cat
    return "otros"


# ── Transaction CRUD ────────────────────────────────────────────


def add_transaction(
    txn_type: str,
    amount: float,
    description: str,
    category: str | None = None,
    date: str | None = None,
) -> dict:
    """Add a transaction to the ledger.

    Args:
        txn_type: "income" or "expense"
        amount: positive number
        description: what it was for
        category: override auto-categorization
        date: ISO format string, or None for now

    Returns:
        The saved transaction dict.
    """
    if txn_type not in ("income", "expense"):
        raise ValueError(f"Invalid type: {txn_type}")
    if amount <= 0:
        raise ValueError(f"Amount must be positive: {amount}")

    if category is None:
        category = auto_categorize(description, txn_type)
    if category not in CATEGORIES:
        category = "otros"

    txn = {
        "id": uuid.uuid4().hex[:8],
        "type": txn_type,
        "amount": round(amount, 2),
        "description": description,
        "category": category,
        "date": date or _now().isoformat(),
        "created_at": _now().isoformat(),
    }

    txns = _load_transactions()
    txns.append(txn)
    _save_transactions(txns)

    _log({"action": "add_transaction", "id": txn["id"], "type": txn_type,
          "amount": txn["amount"], "category": category})

    return txn


def add_from_text(text: str) -> dict | None:
    """Parse and add a transaction from natural language.

    Args:
        text: Spanish natural language input (e.g., "gasté 18500 en super")

    Returns:
        Saved transaction dict, or None if unparseable.
    """
    parsed = parse_transaction(text)
    if not parsed:
        return None
    return add_transaction(
        txn_type=parsed["type"],
        amount=parsed["amount"],
        description=parsed["description"],
        category=parsed["category"],
    )


def list_transactions(
    start_date: str | None = None,
    end_date: str | None = None,
    category: str | None = None,
    txn_type: str | None = None,
) -> list:
    """List transactions with optional filters.

    Args:
        start_date: ISO date string (inclusive)
        end_date: ISO date string (inclusive)
        category: filter by category
        txn_type: "income" or "expense"

    Returns:
        Filtered list of transaction dicts, newest first.
    """
    txns = _load_transactions()

    if start_date:
        txns = [t for t in txns if t.get("date", "") >= start_date]
    if end_date:
        # Include the full end day
        end_next = end_date + "T23:59:59" if "T" not in end_date else end_date
        txns = [t for t in txns if t.get("date", "") <= end_next]
    if category:
        txns = [t for t in txns if t.get("category") == category]
    if txn_type:
        txns = [t for t in txns if t.get("type") == txn_type]

    txns.sort(key=lambda t: t.get("date", ""), reverse=True)
    return txns


# ── Summaries ───────────────────────────────────────────────────


def _date_range(period: str) -> tuple:
    """Get (start, end) ISO date strings for a period."""
    now = _now()
    if period == "day":
        start = now.strftime("%Y-%m-%d")
        end = start
    elif period == "week":
        monday = now - timedelta(days=now.weekday())
        start = monday.strftime("%Y-%m-%d")
        end = now.strftime("%Y-%m-%d")
    elif period == "month":
        start = now.strftime("%Y-%m-01")
        end = now.strftime("%Y-%m-%d")
    else:
        start = now.strftime("%Y-01-01")
        end = now.strftime("%Y-%m-%d")
    return start, end


def summary(period: str = "month") -> dict:
    """Generate a financial summary for a period.

    Args:
        period: "day", "week", "month", or "year"

    Returns:
        dict with: period, start, end, total_income, total_expenses,
        balance, by_category, transaction_count
    """
    start, end = _date_range(period)
    txns = list_transactions(start_date=start, end_date=end)

    total_income = sum(t["amount"] for t in txns if t["type"] == "income")
    total_expenses = sum(t["amount"] for t in txns if t["type"] == "expense")

    by_category = {}
    for t in txns:
        cat = t.get("category", "otros")
        if cat not in by_category:
            by_category[cat] = {"income": 0, "expenses": 0, "count": 0}
        if t["type"] == "income":
            by_category[cat]["income"] += t["amount"]
        else:
            by_category[cat]["expenses"] += t["amount"]
        by_category[cat]["count"] += 1

    result = {
        "period": period,
        "start": start,
        "end": end,
        "total_income": round(total_income, 2),
        "total_expenses": round(total_expenses, 2),
        "balance": round(total_income - total_expenses, 2),
        "by_category": by_category,
        "transaction_count": len(txns),
    }

    _log({"action": "summary", "period": period, "income": total_income,
          "expenses": total_expenses, "balance": result["balance"]})

    return result


def format_summary(s: dict) -> str:
    """Format a summary dict as human-readable text (for Telegram)."""
    lines = [
        f"Resumen {s['period']} ({s['start']} → {s['end']})",
        f"  Ingresos:  ${s['total_income']:,.0f}",
        f"  Gastos:    ${s['total_expenses']:,.0f}",
        f"  Balance:   ${s['balance']:,.0f}",
        f"  Movimientos: {s['transaction_count']}",
    ]

    if s["by_category"]:
        lines.append("")
        lines.append("Por categoria:")
        # Sort by expenses descending
        sorted_cats = sorted(
            s["by_category"].items(),
            key=lambda x: x[1]["expenses"],
            reverse=True,
        )
        for cat, data in sorted_cats:
            if data["expenses"] > 0:
                lines.append(f"  {cat}: ${data['expenses']:,.0f} ({data['count']} mov)")
            elif data["income"] > 0:
                lines.append(f"  {cat}: +${data['income']:,.0f} ({data['count']} mov)")

    return "\n".join(lines)


# ── Alerts ──────────────────────────────────────────────────────


def check_alerts() -> list:
    """Check for spending anomalies and generate alerts.

    Checks:
      1. Single high expense (> ALERT_HIGH_EXPENSE)
      2. Daily total over limit (> ALERT_DAILY_LIMIT)
      3. Category spending deviation (> 2x monthly average for category)

    Returns:
        List of alert dicts: [{type, message, amount, details}, ...]
    """
    alerts = []
    today = _now().strftime("%Y-%m-%d")
    month_start = _now().strftime("%Y-%m-01")

    # All transactions this month
    month_txns = list_transactions(start_date=month_start, txn_type="expense")
    today_txns = [t for t in month_txns if t.get("date", "").startswith(today)]

    # 1. High single expense (today)
    for t in today_txns:
        if t["amount"] > ALERT_HIGH_EXPENSE:
            alerts.append({
                "type": "high_expense",
                "message": f"Gasto alto: ${t['amount']:,.0f} en {t.get('description', '?')}",
                "amount": t["amount"],
                "details": t,
            })

    # 2. Daily total over limit
    daily_total = sum(t["amount"] for t in today_txns)
    if daily_total > ALERT_DAILY_LIMIT:
        alerts.append({
            "type": "daily_limit",
            "message": f"Gasto diario alto: ${daily_total:,.0f} (limite: ${ALERT_DAILY_LIMIT:,.0f})",
            "amount": daily_total,
            "details": {"date": today, "count": len(today_txns)},
        })

    # 3. Category deviation (compare this month vs average)
    days_in_month = max(1, _now().day)
    cat_totals = {}
    for t in month_txns:
        cat = t.get("category", "otros")
        cat_totals[cat] = cat_totals.get(cat, 0) + t["amount"]

    # Load previous months for baseline (look at last 3 months)
    monthly_avgs = _calculate_monthly_averages(months=3)
    for cat, total in cat_totals.items():
        avg = monthly_avgs.get(cat, 0)
        if avg > 0 and total > avg * ALERT_CATEGORY_DEVIATION:
            alerts.append({
                "type": "category_deviation",
                "message": f"{cat}: ${total:,.0f} este mes (promedio: ${avg:,.0f})",
                "amount": total,
                "details": {"category": cat, "average": avg, "ratio": round(total / avg, 1)},
            })

    if alerts:
        _log({"action": "alerts", "count": len(alerts),
              "types": [a["type"] for a in alerts]})

    return alerts


def _calculate_monthly_averages(months: int = 3) -> dict:
    """Calculate average monthly spending per category over last N months."""
    now = _now()
    totals = {}
    months_counted = 0

    for i in range(1, months + 1):
        # Go back i months
        year = now.year
        month = now.month - i
        if month <= 0:
            month += 12
            year -= 1
        start = f"{year}-{month:02d}-01"
        # End of that month
        if month == 12:
            end = f"{year + 1}-01-01"
        else:
            end = f"{year}-{month + 1:02d}-01"

        txns = list_transactions(start_date=start, end_date=end, txn_type="expense")
        if txns:
            months_counted += 1
            for t in txns:
                cat = t.get("category", "otros")
                totals[cat] = totals.get(cat, 0) + t["amount"]

    if months_counted == 0:
        return {}

    return {cat: round(total / months_counted, 2) for cat, total in totals.items()}


def format_alerts(alerts: list) -> str:
    """Format alerts for Telegram output."""
    if not alerts:
        return "Sin alertas activas."
    lines = ["Alertas de finanzas:"]
    for a in alerts:
        lines.append(f"  - {a['message']}")
    return "\n".join(lines)


# ── CLI ─────────────────────────────────────────────────────────


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Tenazas Finance Tracker")
    parser.add_argument("--add", type=str, help="Add transaction from text")
    parser.add_argument("--summary", action="store_true", help="Monthly summary")
    parser.add_argument("--day", action="store_true", help="Daily summary")
    parser.add_argument("--week", action="store_true", help="Weekly summary")
    parser.add_argument("--month", type=str, nargs="?", const="current",
                        help="Monthly summary (YYYY-MM or 'current')")
    parser.add_argument("--alerts", action="store_true", help="Check alerts")
    parser.add_argument("--list", action="store_true", help="List recent transactions")
    parser.add_argument("--category", type=str, help="Filter by category")
    parser.add_argument("--json", action="store_true", help="Output as JSON")

    args = parser.parse_args()

    if args.add:
        result = add_from_text(args.add)
        if result:
            if args.json:
                print(json.dumps(result, ensure_ascii=False, default=str))
            else:
                print(f"Registrado: {result['type']} ${result['amount']:,.0f} "
                      f"[{result['category']}] {result['description']}")
        else:
            print("No pude entender el formato. Ejemplos:")
            print('  --add "gasté 18500 en supermercado"')
            print('  --add "cobré 1200000 de sueldo"')

    elif args.summary or args.day or args.week or args.month:
        period = "month"
        if args.day:
            period = "day"
        elif args.week:
            period = "week"
        s = summary(period)
        if args.json:
            print(json.dumps(s, ensure_ascii=False, default=str))
        else:
            print(format_summary(s))

    elif args.alerts:
        a = check_alerts()
        if args.json:
            print(json.dumps(a, ensure_ascii=False, default=str))
        else:
            print(format_alerts(a))

    elif args.list:
        txns = list_transactions(category=args.category)[:20]
        if args.json:
            print(json.dumps(txns, ensure_ascii=False, default=str))
        else:
            if not txns:
                print("Sin transacciones registradas.")
            else:
                for t in txns:
                    sign = "+" if t["type"] == "income" else "-"
                    print(f"{t['date'][:10]}  {sign}${t['amount']:,.0f}  "
                          f"[{t['category']}]  {t['description']}")

    else:
        parser.print_help()
