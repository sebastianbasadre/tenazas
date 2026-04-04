#!/usr/bin/env python3
"""
Tests for personal_supervisor.py — Unified personal domain coordinator.

Covers:
- Intent classification (single domain, multi-domain, overview, default)
- Domain handlers (finance, expense, jobs, improvement — success + failure)
- Request handling (delegation, combination, missing data)
- Global status
- Format output
- Edge cases

Corre: python3 tests/test_personal_supervisor.py
"""

import sys
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, "/home/tenazas")
from runtime import personal_supervisor as ps


class _TempDirMixin:
    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self._orig_log = ps.LOG_FILE
        ps.LOG_FILE = self.tmpdir / "logs" / "personal_supervisor.log"

    def tearDown(self):
        ps.LOG_FILE = self._orig_log


# ── Intent Classification ───────────────────────────────────────


class TestClassifyIntent(unittest.TestCase):

    def test_finance_keywords(self):
        self.assertIn("finance", ps.classify_intent("cuál es mi balance este mes"))

    def test_expense_keywords(self):
        self.assertIn("expense", ps.classify_intent("en qué se me está yendo la plata"))

    def test_expense_daily(self):
        self.assertIn("expense", ps.classify_intent("resumen de hoy"))

    def test_jobs_keywords(self):
        self.assertIn("jobs", ps.classify_intent("qué empresas tengo activas"))

    def test_jobs_postulacion(self):
        self.assertIn("jobs", ps.classify_intent("cómo van mis postulaciones"))

    def test_improvement_keywords(self):
        self.assertIn("improvement", ps.classify_intent("qué mejora propone el sistema"))

    def test_overview_como_vengo(self):
        domains = ps.classify_intent("cómo vengo este mes")
        self.assertIn("overview", domains)
        # Overview expands to include sub-domains
        self.assertIn("finance", domains)
        self.assertIn("expense", domains)

    def test_overview_priorizar(self):
        domains = ps.classify_intent("qué debería priorizar hoy")
        self.assertIn("overview", domains)

    def test_default_is_overview(self):
        domains = ps.classify_intent("hola")
        self.assertIn("overview", domains)

    def test_multi_domain(self):
        """'cuánto gasté y cómo van las postulaciones' → expense + jobs"""
        domains = ps.classify_intent("cuánto gasté y cómo van las postulaciones")
        self.assertIn("expense", domains)
        self.assertIn("jobs", domains)

    def test_returns_list(self):
        self.assertIsInstance(ps.classify_intent("test"), list)

    def test_at_least_one_domain(self):
        self.assertGreaterEqual(len(ps.classify_intent("")), 1)


# ── Domain Handlers ─────────────────────────────────────────────


class TestHandleFinance(unittest.TestCase):

    @patch("runtime.finance.summary")
    @patch("runtime.finance.format_summary")
    def test_returns_data(self, mock_fmt, mock_sum):
        mock_sum.return_value = {"total_income": 100000, "total_expenses": 50000,
                                  "balance": 50000, "transaction_count": 5, "by_category": {}}
        mock_fmt.return_value = "Resumen mensual: ..."
        data, text = ps._handle_finance()
        self.assertIsNotNone(data)
        self.assertIn("Resumen", text)

    @patch("runtime.finance.summary")
    def test_no_transactions_returns_none(self, mock_sum):
        mock_sum.return_value = {"transaction_count": 0}
        data, text = ps._handle_finance()
        self.assertIsNone(data)

    @patch("runtime.finance.summary", side_effect=ImportError)
    def test_import_error_returns_none(self, mock_sum):
        data, text = ps._handle_finance()
        self.assertIsNone(data)


class TestHandleExpense(unittest.TestCase):

    @patch("runtime.expenses.check_threshold_alerts", return_value=[])
    @patch("runtime.expenses.format_weekly")
    @patch("runtime.expenses.format_daily")
    @patch("runtime.expenses.weekly_summary")
    @patch("runtime.expenses.daily_summary")
    def test_returns_data(self, mock_ds, mock_ws, mock_fd, mock_fw, mock_alerts):
        mock_ds.return_value = {"total": 5000, "count": 2, "by_category": {}}
        mock_ws.return_value = {"total": 20000, "count": 10, "by_category": {}}
        mock_fd.return_value = "Hoy: $5,000"
        mock_fw.return_value = "Semana: $20,000"
        data, text = ps._handle_expense()
        self.assertIsNotNone(data)
        self.assertIn("5,000", text)

    @patch("runtime.expenses.check_threshold_alerts", return_value=[])
    @patch("runtime.expenses.weekly_summary")
    @patch("runtime.expenses.daily_summary")
    def test_no_data_returns_none(self, mock_ds, mock_ws, mock_alerts):
        mock_ds.return_value = {"total": 0, "count": 0, "by_category": {}}
        mock_ws.return_value = {"total": 0, "count": 0, "by_category": {}}
        data, text = ps._handle_expense()
        self.assertIsNone(data)


class TestHandleJobs(unittest.TestCase):

    @patch("runtime.jobs.pipeline_summary")
    @patch("runtime.jobs.format_pipeline")
    def test_returns_data(self, mock_fmt, mock_ps):
        mock_ps.return_value = {"total": 3, "active": 2, "by_status": {"applied": 2}, "recent": []}
        mock_fmt.return_value = "Pipeline: 3 total"
        data, text = ps._handle_jobs()
        self.assertIsNotNone(data)

    @patch("runtime.jobs.pipeline_summary")
    def test_no_jobs_returns_none(self, mock_ps):
        mock_ps.return_value = {"total": 0, "active": 0, "by_status": {}, "recent": []}
        data, text = ps._handle_jobs()
        self.assertIsNone(data)


class TestHandleImprovement(_TempDirMixin, unittest.TestCase):

    def setUp(self):
        super().setUp()
        self._orig_imp = ps.IMPROVEMENT_FILE
        ps.IMPROVEMENT_FILE = self.tmpdir / "learning" / "daily_improvement.json"

    def tearDown(self):
        ps.IMPROVEMENT_FILE = self._orig_imp
        super().tearDown()

    def test_returns_latest(self):
        ps.IMPROVEMENT_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(ps.IMPROVEMENT_FILE, "w") as f:
            json.dump([{"area": "routing", "proposal": "reduce cooldown",
                        "impact": "medium", "risk": "bajo"}], f)
        data, text = ps._handle_improvement()
        self.assertIsNotNone(data)
        self.assertIn("reduce cooldown", text)

    def test_missing_file_returns_none(self):
        data, text = ps._handle_improvement()
        self.assertIsNone(data)


# ── Request Handling ────────────────────────────────────────────


class TestHandleRequest(_TempDirMixin, unittest.TestCase):

    def _mock_handlers(self, fin=None, exp=None, jobs=None, imp=None):
        """Replace HANDLERS dict entries with mock callables."""
        none_tuple = (None, None)
        self._saved_handlers = dict(ps.HANDLERS)
        ps.HANDLERS["finance"] = lambda: fin if fin else none_tuple
        ps.HANDLERS["expense"] = lambda: exp if exp else none_tuple
        ps.HANDLERS["jobs"] = lambda: jobs if jobs else none_tuple
        ps.HANDLERS["improvement"] = lambda: imp if imp else none_tuple

    def _restore_handlers(self):
        ps.HANDLERS.update(self._saved_handlers)

    def test_single_domain(self):
        self._mock_handlers(fin=({"balance": 50000}, "Balance: $50,000"))
        try:
            result = ps.handle_request("cuál es mi balance")
            self.assertIn("finance", result["domains"])
            self.assertIn("50,000", result["response"])
        finally:
            self._restore_handlers()

    def test_multi_domain(self):
        self._mock_handlers(
            fin=({"balance": 50000}, "Balance: $50,000"),
            exp=({"total": 5000}, "Hoy: $5,000"),
        )
        try:
            result = ps.handle_request("cómo vengo este mes")
            self.assertIn("50,000", result["response"])
            self.assertIn("5,000", result["response"])
        finally:
            self._restore_handlers()

    def test_no_data(self):
        self._mock_handlers()
        try:
            result = ps.handle_request("hola")
            self.assertIn("Sin datos", result["response"])
        finally:
            self._restore_handlers()

    def test_result_has_required_keys(self):
        self._mock_handlers()
        try:
            result = ps.handle_request("test")
            for key in ("query", "domains", "results", "response", "domains_with_data", "missing"):
                self.assertIn(key, result, f"Missing key: {key}")
        finally:
            self._restore_handlers()

    def test_missing_domains_noted(self):
        self._mock_handlers(fin=({"balance": 50000}, "Balance OK"))
        try:
            result = ps.handle_request("cómo vengo")
            self.assertIn("expense", result["missing"])
        finally:
            self._restore_handlers()


# ── Global Status ───────────────────────────────────────────────


class TestGlobalStatus(_TempDirMixin, unittest.TestCase):

    def _mock_handlers(self, fin=None, exp=None, jobs=None, imp=None):
        none_tuple = (None, None)
        self._saved = dict(ps.HANDLERS)
        ps.HANDLERS["finance"] = lambda: fin if fin else none_tuple
        ps.HANDLERS["expense"] = lambda: exp if exp else none_tuple
        ps.HANDLERS["jobs"] = lambda: jobs if jobs else none_tuple
        ps.HANDLERS["improvement"] = lambda: imp if imp else none_tuple

    def _restore(self):
        ps.HANDLERS.update(self._saved)

    def test_status_per_domain(self):
        self._mock_handlers(
            fin=({"balance": 50000}, "Balance OK"),
            jobs=({"total": 3}, "Pipeline: 3"),
        )
        try:
            status = ps.global_status()
            self.assertTrue(status["finance"]["available"])
            self.assertFalse(status["expense"]["available"])
            self.assertTrue(status["jobs"]["available"])
        finally:
            self._restore()

    def test_format_status(self):
        self._mock_handlers(fin=({"balance": 50000}, "Balance OK"))
        try:
            status = ps.global_status()
            text = ps.format_status(status)
            self.assertIn("finance: OK", text)
            self.assertIn("sin datos", text)
        finally:
            self._restore()


# ── Edge Cases ──────────────────────────────────────────────────


class TestEdgeCases(unittest.TestCase):

    def test_domains_constant(self):
        for d in ("finance", "expense", "jobs", "improvement", "overview"):
            self.assertIn(d, ps.DOMAINS)

    def test_all_handlers_registered(self):
        for d in ("finance", "expense", "jobs", "improvement"):
            self.assertIn(d, ps.HANDLERS)

    def test_classify_empty_string(self):
        domains = ps.classify_intent("")
        self.assertGreaterEqual(len(domains), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
