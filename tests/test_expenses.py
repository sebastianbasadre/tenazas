#!/usr/bin/env python3
"""
Tests for expenses.py — Quick daily expense capture.

Covers:
- Informal amount parsing (12k, lucas, dots, dollars, plain)
- Relative date parsing (ayer, anteayer, hoy)
- Quick expense parsing (all input formats)
- Auto-categorization (shortcuts, keywords, unknown)
- Quick add (save, persist, source tag)
- Reclassify (valid, invalid category, not found)
- Daily/weekly summaries
- Threshold alerts
- Edge cases
- Shared storage with finance.py

Corre: python3 tests/test_expenses.py
"""

import sys
import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, "/home/tenazas")
from runtime import expenses as exp


class _TempDirMixin:
    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self._orig = {
            "FINANCE_DIR": exp.FINANCE_DIR,
            "TRANSACTIONS_FILE": exp.TRANSACTIONS_FILE,
            "LOG_FILE": exp.LOG_FILE,
        }
        exp.FINANCE_DIR = self.tmpdir / "finance"
        exp.TRANSACTIONS_FILE = exp.FINANCE_DIR / "transactions.json"
        exp.LOG_FILE = self.tmpdir / "logs" / "expenses.log"

    def tearDown(self):
        for k, v in self._orig.items():
            setattr(exp, k, v)


# ── Amount Parsing ──────────────────────────────────────────────


class TestParseAmount(unittest.TestCase):

    def test_plain_integer(self):
        self.assertEqual(exp.parse_amount("8500"), 8500.0)

    def test_with_dollar(self):
        self.assertEqual(exp.parse_amount("$8500"), 8500.0)

    def test_k_suffix(self):
        self.assertEqual(exp.parse_amount("12k"), 12000.0)

    def test_K_upper(self):
        self.assertEqual(exp.parse_amount("12K"), 12000.0)

    def test_k_with_decimal(self):
        self.assertEqual(exp.parse_amount("12,5k"), 12500.0)

    def test_lucas(self):
        self.assertEqual(exp.parse_amount("15lucas"), 15000.0)

    def test_lucas_with_space(self):
        self.assertEqual(exp.parse_amount("15 lucas"), 15000.0)

    def test_luca_singular(self):
        self.assertEqual(exp.parse_amount("1luca"), 1000.0)

    def test_dot_thousand(self):
        self.assertEqual(exp.parse_amount("12.000"), 12000.0)

    def test_dot_thousand_comma_decimal(self):
        self.assertEqual(exp.parse_amount("12.500,50"), 12500.50)

    def test_millions(self):
        self.assertEqual(exp.parse_amount("1.200.000"), 1200000.0)

    def test_zero_returns_none(self):
        self.assertIsNone(exp.parse_amount("0"))

    def test_garbage_returns_none(self):
        self.assertIsNone(exp.parse_amount("abc"))

    def test_empty_returns_none(self):
        self.assertIsNone(exp.parse_amount(""))

    def test_with_spaces(self):
        self.assertEqual(exp.parse_amount(" 8500 "), 8500.0)


# ── Relative Date Parsing ──────────────────────────────────────


class TestRelativeDate(unittest.TestCase):

    def test_ayer_at_start(self):
        date, rest = exp.parse_relative_date("ayer uber 4200")
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        self.assertTrue(date.startswith(yesterday))
        self.assertEqual(rest, "uber 4200")

    def test_ayer_at_end(self):
        date, rest = exp.parse_relative_date("gasté 5000 en super ayer")
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        self.assertTrue(date.startswith(yesterday))

    def test_anteayer(self):
        date, rest = exp.parse_relative_date("anteayer nafta 12k")
        two_days = (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d")
        self.assertTrue(date.startswith(two_days))
        self.assertEqual(rest, "nafta 12k")

    def test_hoy_explicit(self):
        date, rest = exp.parse_relative_date("hoy uber 4200")
        today = datetime.now().strftime("%Y-%m-%d")
        self.assertTrue(date.startswith(today))
        self.assertEqual(rest, "uber 4200")

    def test_no_date_defaults_today(self):
        date, rest = exp.parse_relative_date("uber 4200")
        today = datetime.now().strftime("%Y-%m-%d")
        self.assertTrue(date.startswith(today))
        self.assertEqual(rest, "uber 4200")


# ── Quick Expense Parsing ──────────────────────────────────────


class TestParseQuickExpense(unittest.TestCase):

    def test_word_then_amount(self):
        """'uber 4200' → amount=4200, desc=uber, cat=transporte"""
        r = exp.parse_quick_expense("uber 4200")
        self.assertIsNotNone(r)
        self.assertEqual(r["amount"], 4200.0)
        self.assertEqual(r["description"], "uber")
        self.assertEqual(r["category"], "transporte")

    def test_almuerzo(self):
        r = exp.parse_quick_expense("almuerzo 8500")
        self.assertIsNotNone(r)
        self.assertEqual(r["amount"], 8500.0)
        self.assertEqual(r["category"], "ocio")

    def test_amount_then_word(self):
        """'12000 super' → amount=12000, desc=super, cat=supermercado"""
        r = exp.parse_quick_expense("12000 super")
        self.assertIsNotNone(r)
        self.assertEqual(r["amount"], 12000.0)
        self.assertEqual(r["category"], "supermercado")

    def test_amount_en_word(self):
        """'12k en nafta' → amount=12000, desc=nafta, cat=transporte"""
        r = exp.parse_quick_expense("12k en nafta")
        self.assertIsNotNone(r)
        self.assertEqual(r["amount"], 12000.0)
        self.assertEqual(r["category"], "transporte")

    def test_gasto_prefix(self):
        """'gasto 12k en nafta' → strips prefix, parses normally"""
        r = exp.parse_quick_expense("gasto 12k en nafta")
        self.assertIsNotNone(r)
        self.assertEqual(r["amount"], 12000.0)

    def test_gaste_prefix(self):
        r = exp.parse_quick_expense("gasté 15 lucas en farmacia")
        self.assertIsNotNone(r)
        self.assertEqual(r["amount"], 15000.0)
        self.assertEqual(r["category"], "salud")

    def test_lucas_amount(self):
        r = exp.parse_quick_expense("nafta 12 lucas")
        self.assertIsNotNone(r)
        self.assertEqual(r["amount"], 12000.0)

    def test_ayer_date(self):
        r = exp.parse_quick_expense("ayer uber 4200")
        self.assertIsNotNone(r)
        yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        self.assertTrue(r["date"].startswith(yesterday))

    def test_preserves_raw_input(self):
        r = exp.parse_quick_expense("uber 4200")
        self.assertEqual(r["raw_input"], "uber 4200")

    def test_empty_returns_none(self):
        self.assertIsNone(exp.parse_quick_expense(""))

    def test_unparseable_returns_none(self):
        self.assertIsNone(exp.parse_quick_expense("hola mundo"))

    def test_k_suffix(self):
        r = exp.parse_quick_expense("nafta 12k")
        self.assertIsNotNone(r)
        self.assertEqual(r["amount"], 12000.0)

    def test_two_word_description(self):
        r = exp.parse_quick_expense("obra social 25000")
        self.assertIsNotNone(r)
        self.assertEqual(r["category"], "salud")


# ── Auto-Categorization ────────────────────────────────────────


class TestAutoCategorize(unittest.TestCase):

    def test_shortcut_uber(self):
        self.assertEqual(exp._auto_categorize("uber"), "transporte")

    def test_shortcut_almuerzo(self):
        self.assertEqual(exp._auto_categorize("almuerzo"), "ocio")

    def test_shortcut_super(self):
        self.assertEqual(exp._auto_categorize("super"), "supermercado")

    def test_keyword_nafta(self):
        self.assertEqual(exp._auto_categorize("nafta gnc"), "transporte")

    def test_keyword_farmacia(self):
        self.assertEqual(exp._auto_categorize("farmacia"), "salud")

    def test_keyword_spotify(self):
        self.assertEqual(exp._auto_categorize("spotify premium"), "servicios")

    def test_unknown(self):
        self.assertEqual(exp._auto_categorize("algo random"), "otros")


# ── Quick Add ───────────────────────────────────────────────────


class TestQuickAdd(_TempDirMixin, unittest.TestCase):

    def test_add_and_persist(self):
        txn = exp.quick_add("uber 4200")
        self.assertIsNotNone(txn)
        self.assertEqual(txn["amount"], 4200.0)
        self.assertEqual(txn["type"], "expense")
        self.assertEqual(txn["source"], "quick_add")
        self.assertIn("id", txn)

        saved = exp._load_transactions()
        self.assertEqual(len(saved), 1)

    def test_add_unparseable(self):
        self.assertIsNone(exp.quick_add("hello world"))

    def test_add_multiple(self):
        exp.quick_add("uber 4200")
        exp.quick_add("almuerzo 8500")
        exp.quick_add("nafta 12k")
        self.assertEqual(len(exp._load_transactions()), 3)

    def test_preserves_raw(self):
        txn = exp.quick_add("ayer 15 lucas en farmacia")
        self.assertEqual(txn["raw_input"], "ayer 15 lucas en farmacia")

    def test_amount_rounded(self):
        txn = exp.quick_add("uber 4200")
        self.assertEqual(txn["amount"], 4200.0)


# ── Reclassify ──────────────────────────────────────────────────


class TestReclassify(_TempDirMixin, unittest.TestCase):

    def test_reclassify_success(self):
        txn = exp.quick_add("uber 4200")
        self.assertTrue(exp.reclassify(txn["id"], "ocio"))
        updated = exp._load_transactions()
        self.assertEqual(updated[0]["category"], "ocio")
        self.assertEqual(updated[0]["reclassified_from"], "transporte")

    def test_reclassify_invalid_category(self):
        txn = exp.quick_add("uber 4200")
        self.assertFalse(exp.reclassify(txn["id"], "nonexistent"))

    def test_reclassify_not_found(self):
        self.assertFalse(exp.reclassify("nope", "ocio"))

    def test_reclassify_tracks_timestamp(self):
        txn = exp.quick_add("uber 4200")
        exp.reclassify(txn["id"], "ocio")
        updated = exp._load_transactions()[0]
        self.assertIn("reclassified_at", updated)


# ── Summaries ───────────────────────────────────────────────────


class TestDailySummary(_TempDirMixin, unittest.TestCase):

    def test_empty_day(self):
        s = exp.daily_summary()
        self.assertEqual(s["total"], 0)
        self.assertEqual(s["count"], 0)

    def test_with_expenses(self):
        now = exp.now().strftime("%Y-%m-%dT%H:%M:%S")
        txns = [
            {"id": "a", "type": "expense", "amount": 4200, "category": "transporte",
             "description": "uber", "date": now},
            {"id": "b", "type": "expense", "amount": 8500, "category": "ocio",
             "description": "almuerzo", "date": now},
        ]
        exp.FINANCE_DIR.mkdir(parents=True, exist_ok=True)
        exp._save_transactions(txns)
        s = exp.daily_summary()
        self.assertEqual(s["total"], 12700)
        self.assertEqual(s["count"], 2)
        self.assertIn("transporte", s["by_category"])

    def test_format_daily(self):
        now = exp.now().strftime("%Y-%m-%dT%H:%M:%S")
        txns = [{"id": "a", "type": "expense", "amount": 5000,
                 "category": "ocio", "description": "cafe", "date": now}]
        exp.FINANCE_DIR.mkdir(parents=True, exist_ok=True)
        exp._save_transactions(txns)
        text = exp.format_daily(exp.daily_summary())
        self.assertIn("5,000", text)

    def test_format_empty_day(self):
        text = exp.format_daily(exp.daily_summary())
        self.assertIn("Sin gastos", text)


class TestWeeklySummary(_TempDirMixin, unittest.TestCase):

    def test_empty_week(self):
        s = exp.weekly_summary()
        self.assertEqual(s["total"], 0)

    def test_with_filter(self):
        now = exp.now().strftime("%Y-%m-%dT%H:%M:%S")
        txns = [
            {"id": "a", "type": "expense", "amount": 4200, "category": "transporte",
             "description": "uber", "date": now},
            {"id": "b", "type": "expense", "amount": 8500, "category": "ocio",
             "description": "almuerzo", "date": now},
        ]
        exp.FINANCE_DIR.mkdir(parents=True, exist_ok=True)
        exp._save_transactions(txns)
        s = exp.weekly_summary(category="transporte")
        self.assertEqual(s["total"], 4200)
        self.assertEqual(s["filter_category"], "transporte")

    def test_weekly_has_required_keys(self):
        s = exp.weekly_summary()
        for key in ("start", "end", "total", "count", "by_category", "by_day"):
            self.assertIn(key, s)


# ── Threshold Alerts ────────────────────────────────────────────


class TestThresholdAlerts(_TempDirMixin, unittest.TestCase):

    def test_no_alerts_when_empty(self):
        self.assertEqual(exp.check_threshold_alerts(), [])

    def test_alert_triggered(self):
        now = exp.now().strftime("%Y-%m-%dT%H:%M:%S")
        txns = [{"id": f"t{i}", "type": "expense", "amount": 30000,
                 "category": "transporte", "description": "uber", "date": now}
                for i in range(3)]  # 90000 > 40000 threshold
        exp.FINANCE_DIR.mkdir(parents=True, exist_ok=True)
        exp._save_transactions(txns)
        alerts = exp.check_threshold_alerts()
        transport = [a for a in alerts if a["category"] == "transporte"]
        self.assertGreaterEqual(len(transport), 1)
        self.assertEqual(transport[0]["type"], "threshold_exceeded")

    def test_custom_thresholds(self):
        now = exp.now().strftime("%Y-%m-%dT%H:%M:%S")
        txns = [{"id": "a", "type": "expense", "amount": 5000,
                 "category": "ocio", "description": "bar", "date": now}]
        exp.FINANCE_DIR.mkdir(parents=True, exist_ok=True)
        exp._save_transactions(txns)
        alerts = exp.check_threshold_alerts({"ocio": 1000})
        self.assertGreaterEqual(len(alerts), 1)

    def test_format_no_alerts(self):
        self.assertIn("Sin alertas", exp.format_alerts([]))

    def test_format_with_alerts(self):
        alerts = [{"type": "threshold_exceeded", "category": "transporte",
                   "spent": 50000, "limit": 40000,
                   "message": "transporte: $50,000 (limite: $40,000)"}]
        text = exp.format_alerts(alerts)
        self.assertIn("transporte", text)


# ── Shared Storage ──────────────────────────────────────────────


class TestSharedStorage(_TempDirMixin, unittest.TestCase):

    def test_writes_to_finance_dir(self):
        exp.quick_add("uber 4200")
        self.assertTrue(exp.TRANSACTIONS_FILE.exists())
        # The file is in FINANCE_DIR (shared with finance.py)
        self.assertEqual(exp.TRANSACTIONS_FILE.parent, exp.FINANCE_DIR)

    def test_reads_existing_transactions(self):
        """Expenses module sees transactions created by finance module."""
        exp.FINANCE_DIR.mkdir(parents=True, exist_ok=True)
        existing = [{"id": "fin1", "type": "income", "amount": 100000,
                     "category": "trabajo", "description": "sueldo",
                     "date": exp.now().isoformat()}]
        exp._save_transactions(existing)
        exp.quick_add("uber 4200")
        all_txns = exp._load_transactions()
        self.assertEqual(len(all_txns), 2)  # 1 existing + 1 new


# ── Edge Cases ──────────────────────────────────────────────────


class TestEdgeCases(_TempDirMixin, unittest.TestCase):

    def test_load_missing_file(self):
        self.assertEqual(exp._load_transactions(), [])

    def test_load_corrupt(self):
        exp.FINANCE_DIR.mkdir(parents=True, exist_ok=True)
        exp.TRANSACTIONS_FILE.write_text("{broken")
        self.assertEqual(exp._load_transactions(), [])

    def test_categories_match_finance(self):
        """Categories should match finance.py's set."""
        from runtime import finance as fin
        self.assertEqual(exp.CATEGORIES, fin.CATEGORIES)

    def test_all_shortcuts_are_valid_categories(self):
        for word, cat in exp._SHORTCUT_WORDS.items():
            self.assertIn(cat, exp.CATEGORIES,
                          f"Shortcut '{word}' maps to invalid category '{cat}'")

    def test_all_keyword_categories_valid(self):
        for cat in exp._CATEGORY_MAP:
            self.assertIn(cat, exp.CATEGORIES,
                          f"Keyword category '{cat}' not in CATEGORIES")


if __name__ == "__main__":
    unittest.main(verbosity=2)
