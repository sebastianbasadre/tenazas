#!/usr/bin/env python3
"""
Tests for finance.py — Personal finance tracker.

Covers:
- Amount parsing (dots, commas, thousand separators, decimals)
- NLP parsing (gasté, pagué, cobré, compré — Spanish rioplatense)
- Auto-categorization by keywords
- Transaction CRUD (add, list, filter)
- Summaries (day, week, month)
- Alerts (high expense, daily limit, category deviation)
- Edge cases (empty data, invalid input, boundary values)

Corre: python3 tests/test_finance.py
"""

import sys
import json
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, "/home/tenazas")
from runtime import finance as fin


class _TempDirMixin:
    """Isolate finance file I/O to temp directory."""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self._orig = {
            "FINANCE_DIR": fin.FINANCE_DIR,
            "TRANSACTIONS_FILE": fin.TRANSACTIONS_FILE,
            "LOG_FILE": fin.LOG_FILE,
        }
        fin.FINANCE_DIR = self.tmpdir / "finance"
        fin.TRANSACTIONS_FILE = fin.FINANCE_DIR / "transactions.json"
        fin.LOG_FILE = self.tmpdir / "logs" / "finance.log"

    def tearDown(self):
        for k, v in self._orig.items():
            setattr(fin, k, v)


# ── Amount Parsing ──────────────────────────────────────────────


class TestParseAmount(unittest.TestCase):

    def test_plain_integer(self):
        self.assertEqual(fin._parse_amount("18500"), 18500.0)

    def test_with_dollar_sign(self):
        self.assertEqual(fin._parse_amount("$18500"), 18500.0)

    def test_dot_thousand_separator(self):
        self.assertEqual(fin._parse_amount("18.500"), 18500.0)

    def test_comma_thousand_separator(self):
        self.assertEqual(fin._parse_amount("18,500"), 18500.0)

    def test_comma_decimal(self):
        self.assertEqual(fin._parse_amount("18500,50"), 18500.50)

    def test_dot_decimal(self):
        self.assertEqual(fin._parse_amount("18500.50"), 18500.50)

    def test_dot_thousand_comma_decimal(self):
        self.assertEqual(fin._parse_amount("18.500,50"), 18500.50)

    def test_zero_returns_none(self):
        self.assertIsNone(fin._parse_amount("0"))

    def test_negative_returns_none(self):
        self.assertIsNone(fin._parse_amount("-100"))

    def test_garbage_returns_none(self):
        self.assertIsNone(fin._parse_amount("abc"))

    def test_with_spaces(self):
        self.assertEqual(fin._parse_amount(" 18500 "), 18500.0)

    def test_millions(self):
        self.assertEqual(fin._parse_amount("1200000"), 1200000.0)


# ── NLP Parsing ─────────────────────────────────────────────────


class TestParseTransaction(unittest.TestCase):

    def test_gaste_en(self):
        r = fin.parse_transaction("gasté 18500 en supermercado")
        self.assertIsNotNone(r)
        self.assertEqual(r["type"], "expense")
        self.assertEqual(r["amount"], 18500.0)
        self.assertEqual(r["category"], "supermercado")

    def test_pague_de(self):
        r = fin.parse_transaction("pagué 42000 de internet")
        self.assertIsNotNone(r)
        self.assertEqual(r["type"], "expense")
        self.assertEqual(r["amount"], 42000.0)
        self.assertEqual(r["category"], "servicios")

    def test_cobre_de(self):
        r = fin.parse_transaction("cobré 1200000 de sueldo")
        self.assertIsNotNone(r)
        self.assertEqual(r["type"], "income")
        self.assertEqual(r["amount"], 1200000.0)
        self.assertIn(r["category"], ("trabajo", "ingresos"))

    def test_compre_por(self):
        r = fin.parse_transaction("compré remedio por 5000")
        self.assertIsNotNone(r)
        self.assertEqual(r["type"], "expense")
        self.assertEqual(r["amount"], 5000.0)
        self.assertEqual(r["category"], "salud")

    def test_ingreso_de(self):
        r = fin.parse_transaction("ingreso de 500000 por freelance")
        self.assertIsNotNone(r)
        self.assertEqual(r["type"], "income")
        self.assertEqual(r["amount"], 500000.0)

    def test_me_pagaron(self):
        r = fin.parse_transaction("me pagaron 80000")
        self.assertIsNotNone(r)
        self.assertEqual(r["type"], "income")
        self.assertEqual(r["amount"], 80000.0)

    def test_gaste_with_dot_separator(self):
        r = fin.parse_transaction("gasté 18.500 en super")
        self.assertIsNotNone(r)
        self.assertEqual(r["amount"], 18500.0)

    def test_unparseable_returns_none(self):
        self.assertIsNone(fin.parse_transaction("hola que tal"))
        self.assertIsNone(fin.parse_transaction(""))

    def test_gaste_without_accent(self):
        r = fin.parse_transaction("gaste 5000 en uber")
        self.assertIsNotNone(r)
        self.assertEqual(r["type"], "expense")
        self.assertEqual(r["category"], "transporte")


# ── Auto-Categorization ────────────────────────────────────────


class TestCategorize(unittest.TestCase):

    def test_supermercado(self):
        self.assertEqual(fin.auto_categorize("compras en carrefour"), "supermercado")

    def test_transporte(self):
        self.assertEqual(fin.auto_categorize("uber al centro"), "transporte")

    def test_servicios(self):
        self.assertEqual(fin.auto_categorize("pago de luz"), "servicios")

    def test_salud(self):
        self.assertEqual(fin.auto_categorize("turno medico"), "salud")

    def test_ocio(self):
        self.assertEqual(fin.auto_categorize("salida al cine"), "ocio")

    def test_unknown_is_otros(self):
        self.assertEqual(fin.auto_categorize("algo random"), "otros")

    def test_income_sueldo(self):
        self.assertEqual(fin.auto_categorize("sueldo mensual", "income"), "trabajo")

    def test_income_default(self):
        self.assertEqual(fin.auto_categorize("random income", "income"), "ingresos")


# ── Transaction CRUD ────────────────────────────────────────────


class TestAddTransaction(_TempDirMixin, unittest.TestCase):

    def test_add_expense(self):
        txn = fin.add_transaction("expense", 18500, "supermercado")
        self.assertEqual(txn["type"], "expense")
        self.assertEqual(txn["amount"], 18500)
        self.assertIn("id", txn)
        self.assertIn("date", txn)

    def test_add_income(self):
        txn = fin.add_transaction("income", 1200000, "sueldo", "trabajo")
        self.assertEqual(txn["type"], "income")
        self.assertEqual(txn["category"], "trabajo")

    def test_persists_to_file(self):
        fin.add_transaction("expense", 1000, "test")
        txns = fin._load_transactions()
        self.assertEqual(len(txns), 1)

    def test_multiple_transactions(self):
        fin.add_transaction("expense", 1000, "a")
        fin.add_transaction("expense", 2000, "b")
        fin.add_transaction("income", 5000, "c")
        txns = fin._load_transactions()
        self.assertEqual(len(txns), 3)

    def test_invalid_type_raises(self):
        with self.assertRaises(ValueError):
            fin.add_transaction("refund", 100, "test")

    def test_zero_amount_raises(self):
        with self.assertRaises(ValueError):
            fin.add_transaction("expense", 0, "test")

    def test_negative_amount_raises(self):
        with self.assertRaises(ValueError):
            fin.add_transaction("expense", -100, "test")

    def test_invalid_category_defaults_to_otros(self):
        txn = fin.add_transaction("expense", 100, "test", "nonexistent_category")
        self.assertEqual(txn["category"], "otros")

    def test_auto_categorize_on_add(self):
        txn = fin.add_transaction("expense", 5000, "uber al aeropuerto")
        self.assertEqual(txn["category"], "transporte")

    def test_custom_date(self):
        txn = fin.add_transaction("expense", 100, "test", date="2026-01-15T10:00:00")
        self.assertEqual(txn["date"], "2026-01-15T10:00:00")


class TestAddFromText(_TempDirMixin, unittest.TestCase):

    def test_add_from_text_expense(self):
        txn = fin.add_from_text("gasté 18500 en supermercado")
        self.assertIsNotNone(txn)
        self.assertEqual(txn["type"], "expense")
        self.assertEqual(txn["amount"], 18500)

    def test_add_from_text_income(self):
        txn = fin.add_from_text("cobré 1200000 de sueldo")
        self.assertIsNotNone(txn)
        self.assertEqual(txn["type"], "income")

    def test_add_from_text_unparseable(self):
        self.assertIsNone(fin.add_from_text("hola mundo"))

    def test_add_from_text_persists(self):
        fin.add_from_text("gasté 5000 en nafta")
        txns = fin._load_transactions()
        self.assertEqual(len(txns), 1)


class TestListTransactions(_TempDirMixin, unittest.TestCase):

    def test_list_empty(self):
        self.assertEqual(fin.list_transactions(), [])

    def test_list_all(self):
        fin.add_transaction("expense", 1000, "a", date="2026-04-01T10:00:00")
        fin.add_transaction("expense", 2000, "b", date="2026-04-02T10:00:00")
        self.assertEqual(len(fin.list_transactions()), 2)

    def test_list_filtered_by_category(self):
        fin.add_transaction("expense", 1000, "a", "supermercado")
        fin.add_transaction("expense", 2000, "b", "transporte")
        result = fin.list_transactions(category="supermercado")
        self.assertEqual(len(result), 1)

    def test_list_filtered_by_type(self):
        fin.add_transaction("expense", 1000, "a")
        fin.add_transaction("income", 5000, "b")
        result = fin.list_transactions(txn_type="income")
        self.assertEqual(len(result), 1)

    def test_list_sorted_newest_first(self):
        fin.add_transaction("expense", 1000, "old", date="2026-04-01T10:00:00")
        fin.add_transaction("expense", 2000, "new", date="2026-04-03T10:00:00")
        result = fin.list_transactions()
        self.assertEqual(result[0]["description"], "new")


# ── Summary ─────────────────────────────────────────────────────


class TestSummary(_TempDirMixin, unittest.TestCase):

    def test_empty_summary(self):
        s = fin.summary("month")
        self.assertEqual(s["total_income"], 0)
        self.assertEqual(s["total_expenses"], 0)
        self.assertEqual(s["balance"], 0)
        self.assertEqual(s["transaction_count"], 0)

    def test_summary_with_data(self):
        now = fin.now().isoformat()
        fin.add_transaction("income", 100000, "sueldo", date=now)
        fin.add_transaction("expense", 20000, "super", date=now)
        fin.add_transaction("expense", 5000, "uber", date=now)
        s = fin.summary("month")
        self.assertEqual(s["total_income"], 100000)
        self.assertEqual(s["total_expenses"], 25000)
        self.assertEqual(s["balance"], 75000)
        self.assertEqual(s["transaction_count"], 3)

    def test_summary_by_category(self):
        now = fin.now().isoformat()
        fin.add_transaction("expense", 10000, "carrefour", "supermercado", date=now)
        fin.add_transaction("expense", 5000, "uber", "transporte", date=now)
        s = fin.summary("month")
        self.assertIn("supermercado", s["by_category"])
        self.assertEqual(s["by_category"]["supermercado"]["expenses"], 10000)

    def test_format_summary_readable(self):
        now = fin.now().isoformat()
        fin.add_transaction("income", 100000, "sueldo", date=now)
        fin.add_transaction("expense", 20000, "super", "supermercado", date=now)
        s = fin.summary("month")
        text = fin.format_summary(s)
        self.assertIn("Ingresos", text)
        self.assertIn("Gastos", text)
        self.assertIn("Balance", text)

    def test_summary_has_required_keys(self):
        s = fin.summary("day")
        for key in ("period", "start", "end", "total_income", "total_expenses",
                     "balance", "by_category", "transaction_count"):
            self.assertIn(key, s, f"Missing key: {key}")


# ── Alerts ──────────────────────────────────────────────────────


class TestAlerts(_TempDirMixin, unittest.TestCase):

    def test_no_alerts_when_empty(self):
        self.assertEqual(fin.check_alerts(), [])

    def test_high_expense_alert(self):
        now = fin.now().isoformat()
        fin.add_transaction("expense", 60000, "electrodomestico", date=now)
        alerts = fin.check_alerts()
        high = [a for a in alerts if a["type"] == "high_expense"]
        self.assertGreaterEqual(len(high), 1)

    def test_no_alert_under_threshold(self):
        now = fin.now().isoformat()
        fin.add_transaction("expense", 1000, "cafe", date=now)
        alerts = fin.check_alerts()
        high = [a for a in alerts if a["type"] == "high_expense"]
        self.assertEqual(len(high), 0)

    def test_daily_limit_alert(self):
        now = fin.now().isoformat()
        for _ in range(5):
            fin.add_transaction("expense", 25000, "varios", date=now)
        alerts = fin.check_alerts()
        daily = [a for a in alerts if a["type"] == "daily_limit"]
        self.assertGreaterEqual(len(daily), 1)

    def test_format_alerts_no_alerts(self):
        text = fin.format_alerts([])
        self.assertIn("Sin alertas", text)

    def test_format_alerts_with_alerts(self):
        alerts = [{"type": "high_expense", "message": "Gasto alto: $60,000"}]
        text = fin.format_alerts(alerts)
        self.assertIn("Gasto alto", text)


# ── Categories ──────────────────────────────────────────────────


class TestCategories(unittest.TestCase):

    def test_all_categories_valid(self):
        expected = {"supermercado", "transporte", "servicios", "alquiler", "salud",
                    "educacion", "ocio", "impuestos", "trabajo", "ingresos", "otros"}
        self.assertEqual(fin.CATEGORIES, expected)

    def test_category_keywords_map_to_valid_categories(self):
        for cat in fin.CATEGORY_KEYWORDS:
            self.assertIn(cat, fin.CATEGORIES, f"Keyword category '{cat}' not in CATEGORIES")


# ── Edge Cases ──────────────────────────────────────────────────


class TestEdgeCases(_TempDirMixin, unittest.TestCase):

    def test_load_empty_file(self):
        fin.FINANCE_DIR.mkdir(parents=True, exist_ok=True)
        fin.TRANSACTIONS_FILE.write_text("")
        self.assertEqual(fin._load_transactions(), [])

    def test_load_corrupt_json(self):
        fin.FINANCE_DIR.mkdir(parents=True, exist_ok=True)
        fin.TRANSACTIONS_FILE.write_text("{broken")
        self.assertEqual(fin._load_transactions(), [])

    def test_load_missing_file(self):
        self.assertEqual(fin._load_transactions(), [])

    def test_amount_rounding(self):
        txn = fin.add_transaction("expense", 18500.999, "test")
        self.assertEqual(txn["amount"], 18501.0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
