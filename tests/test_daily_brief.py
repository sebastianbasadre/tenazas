#!/usr/bin/env python3
"""
Tests for daily_brief.py — Daily assistant brief for Telegram.

Covers:
- Section gatherers (finance, expenses, jobs, improvement, alerts)
- Graceful degradation (missing modules, empty data)
- Brief assembly (all sections, partial, empty)
- Format output (Telegram-ready text)
- Action suggestion logic
- Helpers (top categories, top expense category)

Corre: python3 tests/test_daily_brief.py
"""

import sys
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, "/home/tenazas")
from runtime import daily_brief as db


class _TempDirMixin:
    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self._orig = {
            "LOG_FILE": db.LOG_FILE,
            "TRANSACTIONS_FILE": db.TRANSACTIONS_FILE,
            "JOBS_FILE": db.JOBS_FILE,
            "DAILY_IMPROVEMENT_FILE": db.DAILY_IMPROVEMENT_FILE,
        }
        db.LOG_FILE = self.tmpdir / "logs" / "daily_brief.log"
        db.TRANSACTIONS_FILE = self.tmpdir / "finance" / "transactions.json"
        db.JOBS_FILE = self.tmpdir / "jobs" / "opportunities.json"
        db.DAILY_IMPROVEMENT_FILE = self.tmpdir / "learning" / "daily_improvement.json"

    def tearDown(self):
        for k, v in self._orig.items():
            setattr(db, k, v)


# ── Gatherers ───────────────────────────────────────────────────


class TestGatherFinance(unittest.TestCase):

    @patch("runtime.finance.summary")
    def test_returns_data(self, mock_summary):
        mock_summary.return_value = {
            "total_income": 100000, "total_expenses": 50000,
            "balance": 50000, "transaction_count": 10,
            "by_category": {"supermercado": {"expenses": 20000, "income": 0, "count": 5}},
        }
        result = db.gather_finance()
        self.assertIsNotNone(result)
        self.assertEqual(result["income"], 100000)
        self.assertEqual(result["balance"], 50000)

    @patch("runtime.finance.summary")
    def test_no_transactions_returns_none(self, mock_summary):
        mock_summary.return_value = {
            "total_income": 0, "total_expenses": 0,
            "balance": 0, "transaction_count": 0, "by_category": {},
        }
        self.assertIsNone(db.gather_finance())

    @patch("runtime.finance.summary", side_effect=ImportError)
    def test_import_error_returns_none(self, mock_summary):
        self.assertIsNone(db.gather_finance())


class TestGatherExpenses(unittest.TestCase):

    @patch("runtime.expenses.daily_summary")
    def test_returns_data(self, mock_ds):
        mock_ds.return_value = {
            "total": 15000, "count": 3,
            "by_category": {"transporte": 8000, "ocio": 7000},
        }
        result = db.gather_expenses_today()
        self.assertIsNotNone(result)
        self.assertEqual(result["total"], 15000)
        self.assertEqual(result["top_category"], "transporte")

    @patch("runtime.expenses.daily_summary")
    def test_empty_day_returns_none(self, mock_ds):
        mock_ds.return_value = {"total": 0, "count": 0, "by_category": {}}
        self.assertIsNone(db.gather_expenses_today())

    @patch("runtime.expenses.daily_summary", side_effect=Exception("boom"))
    def test_error_returns_none(self, mock_ds):
        self.assertIsNone(db.gather_expenses_today())


class TestGatherJobs(unittest.TestCase):

    @patch("runtime.jobs.pipeline_summary")
    def test_returns_data(self, mock_ps):
        mock_ps.return_value = {
            "total": 5, "active": 3,
            "by_status": {"applied": 2, "interview": 1},
            "recent": [{"company": "TIVIT", "role": "TAM", "status": "applied", "id": "abc"}],
        }
        result = db.gather_jobs()
        self.assertIsNotNone(result)
        self.assertEqual(result["active"], 3)
        self.assertEqual(result["recent"][0]["company"], "TIVIT")

    @patch("runtime.jobs.pipeline_summary")
    def test_no_jobs_returns_none(self, mock_ps):
        mock_ps.return_value = {"total": 0, "active": 0, "by_status": {}, "recent": []}
        self.assertIsNone(db.gather_jobs())

    @patch("runtime.jobs.pipeline_summary", side_effect=ImportError)
    def test_import_error_returns_none(self, mock_ps):
        self.assertIsNone(db.gather_jobs())


class TestGatherImprovement(_TempDirMixin, unittest.TestCase):

    def test_returns_latest(self):
        db.DAILY_IMPROVEMENT_FILE.parent.mkdir(parents=True, exist_ok=True)
        history = [
            {"date": "2026-04-03", "area": "routing", "proposal": "old one", "impact": "low"},
            {"date": "2026-04-04", "area": "guardrails", "proposal": "reduce cooldown", "impact": "medium"},
        ]
        with open(db.DAILY_IMPROVEMENT_FILE, "w") as f:
            json.dump(history, f)
        result = db.gather_daily_improvement()
        self.assertIsNotNone(result)
        self.assertEqual(result["proposal"], "reduce cooldown")

    def test_missing_file_returns_none(self):
        self.assertIsNone(db.gather_daily_improvement())

    def test_empty_history_returns_none(self):
        db.DAILY_IMPROVEMENT_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(db.DAILY_IMPROVEMENT_FILE, "w") as f:
            json.dump([], f)
        self.assertIsNone(db.gather_daily_improvement())


class TestGatherAlerts(unittest.TestCase):

    @patch("runtime.expenses.check_threshold_alerts")
    def test_returns_alerts(self, mock_alerts):
        mock_alerts.return_value = [{"type": "threshold_exceeded", "message": "transporte alto"}]
        result = db.gather_expense_alerts()
        self.assertEqual(len(result), 1)

    @patch("runtime.expenses.check_threshold_alerts", side_effect=ImportError)
    def test_error_returns_empty(self, mock_alerts):
        self.assertEqual(db.gather_expense_alerts(), [])


# ── Helpers ─────────────────────────────────────────────────────


class TestHelpers(unittest.TestCase):

    def test_top_categories(self):
        by_cat = {
            "supermercado": {"expenses": 20000, "income": 0},
            "transporte": {"expenses": 8000, "income": 0},
            "ocio": {"expenses": 15000, "income": 0},
        }
        tops = db._top_categories(by_cat, 2)
        self.assertEqual(len(tops), 2)
        self.assertEqual(tops[0]["category"], "supermercado")

    def test_top_categories_empty(self):
        self.assertEqual(db._top_categories({}), [])

    def test_top_expense_category(self):
        self.assertEqual(db._top_expense_category({"a": 100, "b": 200}), "b")

    def test_top_expense_category_empty(self):
        self.assertIsNone(db._top_expense_category({}))


# ── Action Suggestion ───────────────────────────────────────────


class TestActionSuggestion(unittest.TestCase):

    def test_alerts_take_priority(self):
        alerts = [{"message": "gasto alto"}]
        action = db._action_suggestion(None, None, None, None, alerts)
        self.assertIn("gasto alto", action)

    def test_stale_jobs_second(self):
        jobs = {"by_status": {"detected": 3}, "active": 3}
        action = db._action_suggestion(None, None, jobs, None, [])
        self.assertIn("oportunidades", action)

    def test_high_expenses_third(self):
        expenses = {"total": 60000, "top_category": "ocio"}
        action = db._action_suggestion(None, expenses, None, None, [])
        self.assertIn("gasto alto", action.lower())

    def test_improvement_fourth(self):
        imp = {"proposal": "reducir cooldown de guardrails"}
        action = db._action_suggestion(None, None, None, imp, [])
        self.assertIn("reducir cooldown", action)

    def test_default_message(self):
        action = db._action_suggestion(None, None, None, None, [])
        self.assertIn("pipeline laboral", action)


# ── Brief Assembly ──────────────────────────────────────────────


class TestGenerateBrief(_TempDirMixin, unittest.TestCase):

    @patch("runtime.daily_brief.gather_finance", return_value=None)
    @patch("runtime.daily_brief.gather_expenses_today", return_value=None)
    @patch("runtime.daily_brief.gather_jobs", return_value=None)
    @patch("runtime.daily_brief.gather_daily_improvement", return_value=None)
    @patch("runtime.daily_brief.gather_expense_alerts", return_value=[])
    @patch("runtime.daily_brief.gather_finance_alerts", return_value=[])
    def test_empty_brief(self, *mocks):
        brief = db.generate_brief()
        self.assertIn("date", brief)
        self.assertEqual(brief["sections"], {})
        self.assertEqual(len(brief["sections_missing"]), 4)

    @patch("runtime.daily_brief.gather_finance")
    @patch("runtime.daily_brief.gather_expenses_today", return_value=None)
    @patch("runtime.daily_brief.gather_jobs", return_value=None)
    @patch("runtime.daily_brief.gather_daily_improvement", return_value=None)
    @patch("runtime.daily_brief.gather_expense_alerts", return_value=[])
    @patch("runtime.daily_brief.gather_finance_alerts", return_value=[])
    def test_partial_brief(self, mock_fa, mock_ea, mock_imp, mock_jobs, mock_exp, mock_fin):
        mock_fin.return_value = {"income": 100000, "expenses": 50000,
                                  "balance": 50000, "transactions": 10, "top_categories": []}
        brief = db.generate_brief()
        self.assertIn("finance", brief["sections"])
        self.assertNotIn("jobs", brief["sections"])
        self.assertIn("finance", brief["sections_available"])
        self.assertIn("jobs", brief["sections_missing"])

    @patch("runtime.daily_brief.gather_finance")
    @patch("runtime.daily_brief.gather_expenses_today")
    @patch("runtime.daily_brief.gather_jobs")
    @patch("runtime.daily_brief.gather_daily_improvement")
    @patch("runtime.daily_brief.gather_expense_alerts", return_value=[])
    @patch("runtime.daily_brief.gather_finance_alerts", return_value=[])
    def test_full_brief(self, mock_fa, mock_ea, mock_imp, mock_jobs, mock_exp, mock_fin):
        mock_fin.return_value = {"income": 100000, "expenses": 50000,
                                  "balance": 50000, "transactions": 10,
                                  "top_categories": [{"category": "super", "amount": 20000}]}
        mock_exp.return_value = {"total": 8000, "count": 2, "top_category": "transporte"}
        mock_jobs.return_value = {"total": 5, "active": 3,
                                   "by_status": {"applied": 2}, "recent": []}
        mock_imp.return_value = {"date": "2026-04-04", "area": "routing",
                                  "proposal": "test improvement", "impact": "medium"}
        brief = db.generate_brief()
        self.assertEqual(len(brief["sections"]), 4)
        self.assertEqual(brief["sections_missing"], [])

    def test_brief_has_required_keys(self):
        with patch("runtime.daily_brief.gather_finance", return_value=None), \
             patch("runtime.daily_brief.gather_expenses_today", return_value=None), \
             patch("runtime.daily_brief.gather_jobs", return_value=None), \
             patch("runtime.daily_brief.gather_daily_improvement", return_value=None), \
             patch("runtime.daily_brief.gather_expense_alerts", return_value=[]), \
             patch("runtime.daily_brief.gather_finance_alerts", return_value=[]):
            brief = db.generate_brief()
        for key in ("date", "time", "sections", "alerts", "action",
                     "sections_available", "sections_missing"):
            self.assertIn(key, brief, f"Missing key: {key}")


# ── Format ──────────────────────────────────────────────────────


class TestFormatBrief(unittest.TestCase):

    def test_format_with_finance(self):
        brief = {
            "date": "2026-04-04", "time": "08:00",
            "sections": {
                "finance": {"income": 100000, "expenses": 50000,
                            "balance": 50000, "transactions": 10,
                            "top_categories": [{"category": "super", "amount": 20000}]},
            },
            "alerts": [], "action": "Sin acciones urgentes",
            "sections_available": ["finance"], "sections_missing": ["expenses_today", "jobs", "improvement"],
        }
        text = db.format_brief(brief)
        self.assertIn("Buenos dias", text)
        self.assertIn("100,000", text)
        self.assertIn("balance", text.lower())

    def test_format_with_jobs(self):
        brief = {
            "date": "2026-04-04", "time": "08:00",
            "sections": {
                "jobs": {"total": 5, "active": 3,
                         "by_status": {"applied": 2, "interview": 1},
                         "recent": [{"company": "TIVIT", "role": "TAM", "status": "applied"}]},
            },
            "alerts": [], "action": "Revisar pipeline",
            "sections_available": ["jobs"], "sections_missing": [],
        }
        text = db.format_brief(brief)
        self.assertIn("TIVIT", text)
        self.assertIn("applied", text)

    def test_format_with_alerts(self):
        brief = {
            "date": "2026-04-04", "time": "08:00",
            "sections": {},
            "alerts": [{"message": "transporte excedido"}],
            "action": "Revisar alerta",
            "sections_available": [], "sections_missing": [],
        }
        text = db.format_brief(brief)
        self.assertIn("transporte excedido", text)

    def test_format_with_improvement(self):
        brief = {
            "date": "2026-04-04", "time": "08:00",
            "sections": {
                "improvement": {"area": "guardrails", "proposal": "bajar cooldown",
                                "date": "2026-04-04", "impact": "medium"},
            },
            "alerts": [], "action": "test",
            "sections_available": ["improvement"], "sections_missing": [],
        }
        text = db.format_brief(brief)
        self.assertIn("bajar cooldown", text)
        self.assertIn("guardrails", text)

    def test_format_empty_brief(self):
        brief = {
            "date": "2026-04-04", "time": "08:00",
            "sections": {},
            "alerts": [], "action": "Sin acciones urgentes",
            "sections_available": [],
            "sections_missing": ["finance", "expenses_today", "jobs", "improvement"],
        }
        text = db.format_brief(brief)
        self.assertIn("Buenos dias", text)
        self.assertIn("sin datos", text.lower())

    def test_format_shows_action(self):
        brief = {
            "date": "2026-04-04", "time": "08:00",
            "sections": {},
            "alerts": [], "action": "Revisar pipeline laboral",
            "sections_available": [], "sections_missing": [],
        }
        text = db.format_brief(brief)
        self.assertIn(">> Revisar pipeline laboral", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
