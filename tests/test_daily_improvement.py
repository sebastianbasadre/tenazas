#!/usr/bin/env python3
"""
Tests for daily_improvement.py — Daily improvement proposal routine.

Covers:
- Signal scanning (routing, guardrails, finance, jobs, observability)
- Prioritization (impact-based sorting)
- Opportunity selection (dedup against recent history)
- Proposal generation (LLM + fallback template)
- Save (append, dedup by date+signal)
- Report generation
- Full pipeline
- Edge cases

Corre: python3 tests/test_daily_improvement.py
"""

import sys
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, "/home/tenazas")
from runtime import daily_improvement as di


class _TempDirMixin:
    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self._orig = {}
        for attr in ("LOG_DIR", "LEARNING_DIR", "DAILY_FILE", "DAILY_REPORT",
                      "TELEMETRY_LOG", "ROUTER_LOG", "ORCHESTRATOR_LOG",
                      "GUARDRAIL_STATE", "FINANCE_FILE", "JOBS_FILE", "EXPENSES_LOG"):
            self._orig[attr] = getattr(di, attr)

        di.LOG_DIR = self.tmpdir / "logs"
        di.LEARNING_DIR = self.tmpdir / "learning"
        di.DAILY_FILE = di.LEARNING_DIR / "daily_improvement.json"
        di.DAILY_REPORT = di.LEARNING_DIR / "daily_improvement.md"
        di.TELEMETRY_LOG = di.LOG_DIR / "daily_improvement.log"
        di.ROUTER_LOG = di.LOG_DIR / "model_router.log"
        di.ORCHESTRATOR_LOG = di.LOG_DIR / "orchestrator.log"
        di.GUARDRAIL_STATE = self.tmpdir / "guardrail_state.json"
        di.FINANCE_FILE = self.tmpdir / "finance" / "transactions.json"
        di.JOBS_FILE = self.tmpdir / "jobs" / "opportunities.json"
        di.EXPENSES_LOG = di.LOG_DIR / "expenses.log"
        di.LOG_DIR.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        for k, v in self._orig.items():
            setattr(di, k, v)

    def _write_jsonl(self, path: Path, entries: list):
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            for e in entries:
                f.write(json.dumps(e) + "\n")

    def _write_json(self, path: Path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(data, f)


# ── Signal Scanning ─────────────────────────────────────────────


class TestRoutingSignals(_TempDirMixin, unittest.TestCase):

    def test_no_log_returns_empty(self):
        self.assertEqual(di._scan_routing_signals(), [])

    def test_high_fallback_rate(self):
        entries = [{"action": "route_v2", "role": "fast", "fallback_used": True}
                   for _ in range(8)]
        entries += [{"action": "route_v2", "role": "fast", "fallback_used": False}
                    for _ in range(2)]
        self._write_jsonl(di.ROUTER_LOG, entries)
        signals = di._scan_routing_signals()
        fallback = [s for s in signals if s["signal"] == "high_fallback_rate"]
        self.assertEqual(len(fallback), 1)
        self.assertEqual(fallback[0]["impact"], "high")

    def test_no_signal_on_low_fallback(self):
        entries = [{"action": "route_v2", "role": "fast", "fallback_used": False}
                   for _ in range(10)]
        self._write_jsonl(di.ROUTER_LOG, entries)
        signals = di._scan_routing_signals()
        self.assertFalse(any(s["signal"] == "high_fallback_rate" for s in signals))

    def test_degradation_signal(self):
        entries = [{"action": "route_v2", "role": "heavy", "degraded": True}
                   for _ in range(5)]
        self._write_jsonl(di.ROUTER_LOG, entries)
        signals = di._scan_routing_signals()
        self.assertTrue(any(s["signal"] == "frequent_degradation" for s in signals))

    def test_vram_swap_signal(self):
        entries = [{"action": "route_v2", "role": "heavy", "vram_swap": True}
                   for _ in range(6)]
        self._write_jsonl(di.ROUTER_LOG, entries)
        signals = di._scan_routing_signals()
        self.assertTrue(any(s["signal"] == "frequent_vram_swaps" for s in signals))

    def test_role_imbalance(self):
        entries = [{"action": "route_v2", "role": "fast"} for _ in range(20)]
        self._write_jsonl(di.ROUTER_LOG, entries)
        signals = di._scan_routing_signals()
        self.assertTrue(any(s["signal"] == "role_imbalance" for s in signals))


class TestGuardrailSignals(_TempDirMixin, unittest.TestCase):

    def test_no_state_returns_empty(self):
        self.assertEqual(di._scan_guardrail_signals(), [])

    def test_cooldown_dominant(self):
        self._write_json(di.GUARDRAIL_STATE, {
            "degradations": {"cooldown": 8, "frequency": 1, "latency": 1, "vram_storm": 0}
        })
        signals = di._scan_guardrail_signals()
        self.assertTrue(any(s["signal"] == "cooldown_too_aggressive" for s in signals))

    def test_latency_guardrail(self):
        self._write_json(di.GUARDRAIL_STATE, {
            "degradations": {"cooldown": 0, "frequency": 0, "latency": 5, "vram_storm": 0}
        })
        signals = di._scan_guardrail_signals()
        self.assertTrue(any(s["signal"] == "heavy_model_latency" for s in signals))


class TestFinanceSignals(_TempDirMixin, unittest.TestCase):

    def test_no_transactions(self):
        signals = di._scan_finance_signals()
        # Missing file → no_transactions signal
        no_txn = [s for s in signals if s["signal"] == "no_transactions"]
        self.assertEqual(len(no_txn), 1)

    def test_high_uncategorized(self):
        txns = [{"category": "otros"} for _ in range(8)]
        txns += [{"category": "transporte"} for _ in range(2)]
        self._write_json(di.FINANCE_FILE, txns)
        signals = di._scan_finance_signals()
        self.assertTrue(any(s["signal"] == "high_uncategorized" for s in signals))


class TestJobsSignals(_TempDirMixin, unittest.TestCase):

    def test_no_opportunities(self):
        self.assertEqual(di._scan_jobs_signals(), [])

    def test_stale_opportunities(self):
        old_date = (di.now() - __import__("datetime").timedelta(days=10)).isoformat()
        opps = [{"status": "applied", "updated_at": old_date}]
        self._write_json(di.JOBS_FILE, opps)
        signals = di._scan_jobs_signals()
        self.assertTrue(any(s["signal"] == "stale_opportunities" for s in signals))

    def test_high_rejection(self):
        opps = [{"status": "rejected"} for _ in range(4)]
        opps += [{"status": "applied"}]
        self._write_json(di.JOBS_FILE, opps)
        signals = di._scan_jobs_signals()
        self.assertTrue(any(s["signal"] == "high_rejection_rate" for s in signals))


class TestObservabilitySignals(_TempDirMixin, unittest.TestCase):

    def test_no_orchestrator_log(self):
        signals = di._scan_observability_signals()
        self.assertTrue(any(s["signal"] == "no_orchestrator_logs" for s in signals))

    def test_cloud_fallbacks(self):
        entries = [{"type": "cloud_fallback"} for _ in range(5)]
        self._write_jsonl(di.ORCHESTRATOR_LOG, entries)
        signals = di._scan_observability_signals()
        self.assertTrue(any(s["signal"] == "cloud_fallbacks" for s in signals))


class TestScanAll(_TempDirMixin, unittest.TestCase):

    def test_returns_list(self):
        signals = di.scan_all_signals()
        self.assertIsInstance(signals, list)

    def test_combines_sources(self):
        # Write data that triggers signals from multiple sources
        self._write_jsonl(di.ROUTER_LOG, [
            {"action": "route_v2", "role": "heavy", "degraded": True} for _ in range(5)
        ])
        self._write_json(di.GUARDRAIL_STATE, {
            "degradations": {"cooldown": 0, "frequency": 0, "latency": 5, "vram_storm": 0}
        })
        signals = di.scan_all_signals()
        areas = set(s["area"] for s in signals)
        self.assertTrue(len(areas) >= 2)


# ── Prioritization ──────────────────────────────────────────────


class TestPrioritize(unittest.TestCase):

    def test_high_first(self):
        signals = [
            {"signal": "low", "impact": "low"},
            {"signal": "high", "impact": "high"},
            {"signal": "med", "impact": "medium"},
        ]
        ranked = di.prioritize_signals(signals)
        self.assertEqual(ranked[0]["signal"], "high")
        self.assertEqual(ranked[-1]["signal"], "low")


class TestSelectBest(_TempDirMixin, unittest.TestCase):

    def test_selects_highest_impact(self):
        signals = [
            {"signal": "low_one", "impact": "low", "area": "routing"},
            {"signal": "high_one", "impact": "high", "area": "routing"},
        ]
        best = di.select_best_opportunity(signals)
        self.assertEqual(best["signal"], "high_one")

    def test_empty_returns_none(self):
        self.assertIsNone(di.select_best_opportunity([]))

    def test_avoids_recent_repeats(self):
        # Save a recent proposal
        di.LEARNING_DIR.mkdir(parents=True, exist_ok=True)
        history = [{"date": di.now().strftime("%Y-%m-%d"),
                     "signal": "already_done", "area": "routing"}]
        with open(di.DAILY_FILE, "w") as f:
            json.dump(history, f)

        signals = [
            {"signal": "already_done", "impact": "high", "area": "routing"},
            {"signal": "new_one", "impact": "medium", "area": "guardrails"},
        ]
        best = di.select_best_opportunity(signals)
        self.assertEqual(best["signal"], "new_one")

    def test_falls_back_to_repeat_if_all_recent(self):
        di.LEARNING_DIR.mkdir(parents=True, exist_ok=True)
        history = [{"date": di.now().strftime("%Y-%m-%d"),
                     "signal": "only_one", "area": "routing"}]
        with open(di.DAILY_FILE, "w") as f:
            json.dump(history, f)

        signals = [{"signal": "only_one", "impact": "high", "area": "routing"}]
        best = di.select_best_opportunity(signals)
        self.assertIsNotNone(best)


# ── Proposal Generation ────────────────────────────────────────


class TestGenerateProposal(_TempDirMixin, unittest.TestCase):

    @patch("runtime.daily_improvement._call_ollama")
    def test_generates_from_llm(self, mock_ollama):
        mock_ollama.return_value = json.dumps({
            "proposal": "Reduce cooldown to 60s",
            "steps": ["Edit config", "Test"],
            "expected_benefit": "More heavy model usage",
            "risk": "bajo",
        })
        opp = {"area": "guardrails", "signal": "cooldown_too_aggressive",
               "detail": "Cooldown too high", "evidence": "data", "impact": "medium"}
        proposal = di.generate_proposal(opp)
        self.assertEqual(proposal["proposal"], "Reduce cooldown to 60s")
        self.assertEqual(proposal["area"], "guardrails")
        self.assertEqual(proposal["status"], "proposed")
        self.assertIn("date", proposal)

    @patch("runtime.daily_improvement._call_ollama")
    def test_fallback_on_no_response(self, mock_ollama):
        mock_ollama.return_value = ""
        opp = {"area": "routing", "signal": "test", "detail": "test detail",
               "evidence": "data", "impact": "low"}
        proposal = di.generate_proposal(opp)
        self.assertIn("routing", proposal["proposal"])
        self.assertEqual(proposal["status"], "proposed")
        self.assertGreater(len(proposal["steps"]), 0)

    def test_proposal_has_required_fields(self):
        opp = {"area": "routing", "signal": "test", "detail": "d",
               "evidence": "e", "impact": "low"}
        with patch("runtime.daily_improvement._call_ollama", return_value=""):
            proposal = di.generate_proposal(opp)
        for key in ("date", "area", "signal", "insight", "proposal",
                     "steps", "expected_benefit", "risk", "impact",
                     "evidence", "status", "generated_at", "model"):
            self.assertIn(key, proposal, f"Missing: {key}")


# ── Save & Report ──────────────────────────────────────────────


class TestSaveDaily(_TempDirMixin, unittest.TestCase):

    def test_saves_to_file(self):
        proposal = {"date": "2026-04-04", "signal": "test", "area": "routing",
                     "proposal": "do something", "status": "proposed"}
        di.save_daily_proposal(proposal)
        self.assertTrue(di.DAILY_FILE.exists())
        data = json.loads(di.DAILY_FILE.read_text())
        self.assertEqual(len(data), 1)

    def test_appends_to_history(self):
        p1 = {"date": "2026-04-04", "signal": "s1", "area": "routing",
              "proposal": "a", "status": "proposed"}
        p2 = {"date": "2026-04-05", "signal": "s2", "area": "finance",
              "proposal": "b", "status": "proposed"}
        di.save_daily_proposal(p1)
        di.save_daily_proposal(p2)
        data = json.loads(di.DAILY_FILE.read_text())
        self.assertEqual(len(data), 2)

    def test_dedup_same_date_signal(self):
        p1 = {"date": "2026-04-04", "signal": "same", "area": "routing",
              "proposal": "a", "status": "proposed"}
        di.save_daily_proposal(p1)
        di.save_daily_proposal(p1)
        data = json.loads(di.DAILY_FILE.read_text())
        self.assertEqual(len(data), 1)


class TestReport(_TempDirMixin, unittest.TestCase):

    def test_creates_report(self):
        proposal = {"date": "2026-04-04", "area": "routing", "signal": "test",
                     "proposal": "fix routing", "impact": "high", "risk": "bajo",
                     "evidence": "data", "status": "proposed", "steps": ["step 1"],
                     "expected_benefit": "better"}
        di.generate_daily_report(proposal, [])
        self.assertTrue(di.DAILY_REPORT.exists())

    def test_report_contains_proposal(self):
        proposal = {"date": "2026-04-04", "area": "routing", "signal": "test",
                     "proposal": "fix routing", "impact": "high", "risk": "bajo",
                     "evidence": "data", "status": "proposed", "steps": [],
                     "expected_benefit": "better"}
        di.generate_daily_report(proposal, [])
        content = di.DAILY_REPORT.read_text()
        self.assertIn("fix routing", content)
        self.assertIn("routing", content)

    def test_report_no_auto_apply(self):
        proposal = {"date": "2026-04-04", "area": "none", "signal": "none",
                     "proposal": "nada", "impact": "none", "risk": "none",
                     "evidence": "", "status": "info", "steps": [],
                     "expected_benefit": "N/A"}
        di.generate_daily_report(proposal, [])
        content = di.DAILY_REPORT.read_text()
        self.assertIn("revision humana", content)


# ── Pipeline ────────────────────────────────────────────────────


class TestPipeline(_TempDirMixin, unittest.TestCase):

    @patch("runtime.daily_improvement.scan_all_signals", return_value=[])
    def test_no_signals_returns_no_signals(self, mock_scan):
        result = di.run_daily_improvement()
        self.assertEqual(result["status"], "no_signals")
        self.assertTrue(di.DAILY_REPORT.exists())

    @patch("runtime.daily_improvement._call_ollama")
    def test_full_pipeline(self, mock_ollama):
        mock_ollama.return_value = json.dumps({
            "proposal": "Lower cooldown",
            "steps": ["Change config"],
            "expected_benefit": "More heavy usage",
            "risk": "bajo",
        })
        # Create signals
        self._write_jsonl(di.ROUTER_LOG, [
            {"action": "route_v2", "role": "heavy", "degraded": True} for _ in range(5)
        ])
        result = di.run_daily_improvement()
        self.assertEqual(result["status"], "completed")
        self.assertGreater(result["signals"], 0)
        self.assertIn(result["area"], di.AREAS + ("none",))
        self.assertTrue(di.DAILY_FILE.exists())
        self.assertTrue(di.DAILY_REPORT.exists())


# ── Security ────────────────────────────────────────────────────


class TestSecurity(unittest.TestCase):

    def test_output_under_learning(self):
        self.assertTrue(str(di.DAILY_FILE).endswith("learning/daily_improvement.json"))
        self.assertTrue(str(di.DAILY_REPORT).endswith("learning/daily_improvement.md"))

    def test_areas_constant(self):
        self.assertIn("routing", di.AREAS)
        self.assertIn("finance", di.AREAS)
        self.assertIn("jobs", di.AREAS)


if __name__ == "__main__":
    unittest.main(verbosity=2)
