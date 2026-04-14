#!/usr/bin/env python3
"""
test_sprint7.py — Strong Self-Improvement tests.

Covers:
  - improvement_loop: approved → experiment → validated → staging
  - meta_optimizer: frequency adjustment, signal deprecation
  - skill_evolution: phase transitions, shadow doesn't respond
  - benchmark_continuous: threshold alerts, noise_filter respect
"""

import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch, MagicMock

RUNTIME = Path(__file__).parent.parent / "workspace" / "runtime"
_C = Path("/home/tenazas/runtime")
if _C.exists():
    RUNTIME = _C
sys.path.insert(0, str(RUNTIME.parent))
sys.path.insert(0, str(RUNTIME))


# ══════════════════════════════════════════════════════════════
#  improvement_loop
# ══════════════════════════════════════════════════════════════

class TestImprovementLoop(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        import improvement_loop as il
        self._orig_cf = il.CANDIDATES_FILE
        self._orig_lf = il.LOOP_STATE_FILE
        il.CANDIDATES_FILE = Path(self.tmpdir) / "ic.json"
        il.LOOP_STATE_FILE = Path(self.tmpdir) / "loop.json"
        self.il = il

    def tearDown(self):
        self.il.CANDIDATES_FILE = self._orig_cf
        self.il.LOOP_STATE_FILE = self._orig_lf
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_get_approved_candidates(self):
        cands = [
            {"id": "a1", "type": "t", "title": "t", "status": "approved"},
            {"id": "a2", "type": "t", "title": "t", "status": "proposed"},
            {"id": "a3", "type": "t", "title": "t", "status": "rejected"},
        ]
        self.il.CANDIDATES_FILE.write_text(json.dumps(cands))
        result = self.il.get_approved_candidates()
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["id"], "a1")

    def test_already_processed_skipped(self):
        cands = [{"id": "done", "type": "t", "title": "t", "status": "approved"}]
        self.il.CANDIDATES_FILE.write_text(json.dumps(cands))
        self.il.LOOP_STATE_FILE.write_text(json.dumps({"processed": ["done"]}))
        result = self.il.get_approved_candidates()
        self.assertEqual(len(result), 0)

    @patch("improvement_loop.create_experiment_for", return_value={"id": "exp1"})
    @patch("improvement_loop.evaluate_experiment",
           return_value={"improved": True, "metric_delta": 0.05,
                         "control_avg": 0.80, "variant_avg": 0.85,
                         "control_n": 20, "variant_n": 10, "reason": "improved"})
    @patch("improvement_loop._notify")
    def test_validated_candidate_stays_in_staging(self, mock_notify, mock_eval, mock_exp):
        cands = [{"id": "v1", "type": "prompt_tweak", "title": "test", "status": "approved"}]
        self.il.CANDIDATES_FILE.write_text(json.dumps(cands))
        results = self.il.run()
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["action"], "validated")
        # Verify still in candidates file (not moved to runtime)
        updated = json.loads(self.il.CANDIDATES_FILE.read_text())
        self.assertEqual(updated[0]["status"], "approved")
        self.assertTrue(updated[0].get("validated"))

    @patch("improvement_loop.create_experiment_for", return_value={"id": "exp2"})
    @patch("improvement_loop.evaluate_experiment",
           return_value={"improved": False, "metric_delta": -0.01,
                         "reason": "no improvement"})
    @patch("improvement_loop._notify")
    @patch("improvement_loop.decision_quality", create=True)
    def test_failed_candidate_scored_bad(self, mock_dq, mock_notify, mock_eval, mock_exp):
        cands = [{"id": "f1", "type": "router_rule", "title": "test", "status": "approved"}]
        self.il.CANDIDATES_FILE.write_text(json.dumps(cands))
        results = self.il.run()
        self.assertEqual(results[0]["action"], "failed")

    def test_never_writes_to_runtime(self):
        """improvement_loop.py never writes to runtime/ directory."""
        content = (RUNTIME / "improvement_loop.py").read_text()
        self.assertNotIn("/runtime/orchestrator", content)
        self.assertNotIn("/runtime/model_router", content)

    def test_empty_candidates_no_crash(self):
        self.il.CANDIDATES_FILE.write_text("[]")
        results = self.il.run()
        self.assertEqual(results, [])

    def test_get_status(self):
        status = self.il.get_status()
        self.assertIn("pending", status)
        self.assertIn("processed_total", status)


# ══════════════════════════════════════════════════════════════
#  meta_optimizer
# ══════════════════════════════════════════════════════════════

class TestMetaOptimizer(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        import meta_optimizer as mo
        self._orig = {
            "CONFIG": mo.CONFIG_FILE,
            "REPORT": mo.REPORT_FILE,
            "CANDS": mo.CANDIDATES_FILE,
            "EXPS": mo.EXPERIMENTS_DIR,
            "MINING": mo.ERROR_MINING_DIR,
        }
        mo.CONFIG_FILE = Path(self.tmpdir) / "config.json"
        mo.REPORT_FILE = Path(self.tmpdir) / "report.json"
        mo.CANDIDATES_FILE = Path(self.tmpdir) / "ic.json"
        mo.EXPERIMENTS_DIR = Path(self.tmpdir) / "experiments"
        mo.ERROR_MINING_DIR = Path(self.tmpdir) / "mining"
        self.mo = mo

    def tearDown(self):
        self.mo.CONFIG_FILE = self._orig["CONFIG"]
        self.mo.REPORT_FILE = self._orig["REPORT"]
        self.mo.CANDIDATES_FILE = self._orig["CANDS"]
        self.mo.EXPERIMENTS_DIR = self._orig["EXPS"]
        self.mo.ERROR_MINING_DIR = self._orig["MINING"]
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_high_error_rate_increases_frequency(self):
        mining = Path(self.tmpdir) / "mining"
        mining.mkdir()
        for i in range(5):
            (mining / f"daily_2026-01-0{i+1}.json").write_text(
                json.dumps({"total_errors": 30}))
        result = self.mo.analyze_learning_frequency()
        self.assertLess(result["suggested_hours"], result["current_hours"])

    def test_low_error_rate_decreases_frequency(self):
        mining = Path(self.tmpdir) / "mining"
        mining.mkdir()
        for i in range(5):
            (mining / f"daily_2026-01-0{i+1}.json").write_text(
                json.dumps({"total_errors": 2}))
        result = self.mo.analyze_learning_frequency()
        self.assertGreater(result["suggested_hours"], result["current_hours"])

    def test_deprecates_low_quality_signals(self):
        cands = [{"type": "bad_type", "status": "rejected"}] * 25
        self.mo.CANDIDATES_FILE.write_text(json.dumps(cands))
        result = self.mo.analyze_signal_quality()
        deprecated = [s["type"] for s in result["deprecated"]]
        self.assertIn("bad_type", deprecated)

    def test_promotes_high_quality_signals(self):
        cands = [{"type": "good_type", "status": "approved"}] * 25
        self.mo.CANDIDATES_FILE.write_text(json.dumps(cands))
        result = self.mo.analyze_signal_quality()
        promoted = [s["type"] for s in result["promoted"]]
        self.assertIn("good_type", promoted)

    def test_below_20_samples_no_judgment(self):
        cands = [{"type": "new_type", "status": "rejected"}] * 10
        self.mo.CANDIDATES_FILE.write_text(json.dumps(cands))
        result = self.mo.analyze_signal_quality()
        self.assertEqual(len(result["deprecated"]), 0)

    def test_high_rollback_lowers_traffic(self):
        exp_dir = Path(self.tmpdir) / "experiments"
        exp_dir.mkdir()
        for i in range(5):
            (exp_dir / f"exp_{i}.json").write_text(json.dumps({
                "status": "stopped",
                "stop_reason": "auto-rollback: metric dropped 10%",
                "results": {"control": {"count": 10}, "variant": {"count": 5}},
            }))
        result = self.mo.analyze_experiment_calibration()
        self.assertLess(result["suggested_traffic_pct"], 10)

    def test_analyze_saves_config(self):
        self.mo.analyze()
        self.assertTrue(self.mo.CONFIG_FILE.exists())

    def test_analyze_saves_report(self):
        self.mo.analyze()
        self.assertTrue(self.mo.REPORT_FILE.exists())

    def test_default_config(self):
        config = self.mo.load_config()
        self.assertEqual(config["learning_frequency_hours"], 24)
        self.assertEqual(config["max_experiment_traffic_pct"], 10)


# ══════════════════════════════════════════════════════════════
#  skill_evolution
# ══════════════════════════════════════════════════════════════

class TestSkillEvolution(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        import skill_evolution as se
        self._orig = se.EVOLUTION_FILE
        se.EVOLUTION_FILE = Path(self.tmpdir) / "se.json"
        self.se = se

    def tearDown(self):
        self.se.EVOLUTION_FILE = self._orig
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_register_skill(self):
        s = self.se.register("s1", "test_skill", "a test")
        self.assertEqual(s["phase"], "proposed")
        self.assertEqual(s["id"], "s1")

    def test_duplicate_registration_rejected(self):
        self.se.register("s1", "test", "desc")
        result = self.se.register("s1", "test", "desc")
        self.assertIn("error", result)

    def test_proposed_to_shadow(self):
        self.se.register("s1", "test", "desc")
        result = self.se.advance_phase("s1")
        self.assertTrue(result["success"])
        self.assertEqual(result["skill"]["phase"], "shadow")

    def test_shadow_needs_50_queries(self):
        self.se.register("s1", "test", "desc")
        self.se.advance_phase("s1")  # → shadow
        # Try advance without queries
        result = self.se.advance_phase("s1")
        self.assertFalse(result["success"])
        self.assertIn("50", result["reason"])

    def test_shadow_needs_80pct_accuracy(self):
        self.se.register("s1", "test", "desc")
        self.se.advance_phase("s1")  # → shadow
        # Record 50 queries with 60% accuracy (below 80%)
        for i in range(50):
            self.se.record_shadow_query("s1", correct=(i < 30))
        result = self.se.advance_phase("s1")
        self.assertFalse(result["success"])
        self.assertIn("accuracy", result["reason"])

    def test_shadow_to_canary_with_good_accuracy(self):
        self.se.register("s1", "test", "desc")
        self.se.advance_phase("s1")  # → shadow
        for i in range(50):
            self.se.record_shadow_query("s1", correct=True)
        result = self.se.advance_phase("s1")
        self.assertTrue(result["success"])
        self.assertEqual(result["skill"]["phase"], "canary_10pct")

    def test_canary_to_active(self):
        self.se.register("s1", "test", "desc")
        self.se.advance_phase("s1")  # → shadow
        for _ in range(50):
            self.se.record_shadow_query("s1", correct=True)
        self.se.advance_phase("s1")  # → canary
        for _ in range(100):
            self.se.record_canary_query("s1", correct=True)
        result = self.se.advance_phase("s1")
        self.assertTrue(result["success"])
        self.assertEqual(result["skill"]["phase"], "active")

    def test_shadow_doesnt_respond(self):
        """Shadow skill should be detectable as shadow (no user response)."""
        self.se.register("s1", "test", "desc")
        self.se.advance_phase("s1")  # → shadow
        self.assertTrue(self.se.is_shadow("s1"))
        self.assertFalse(self.se.should_canary("s1"))

    def test_archive_skill(self):
        self.se.register("s1", "test", "desc")
        self.assertTrue(self.se.archive("s1", reason="bad accuracy"))
        skills = self.se.list_by_phase("archived")
        self.assertEqual(len(skills), 1)

    def test_get_evaluation_report(self):
        self.se.register("s1", "test", "desc")
        self.se.advance_phase("s1")
        for i in range(10):
            self.se.record_shadow_query("s1", correct=True)
        report = self.se.get_evaluation("s1")
        self.assertIn("shadow_accuracy", report)
        self.assertEqual(report["shadow_accuracy"], 1.0)

    def test_get_status(self):
        self.se.register("s1", "a", "b")
        self.se.register("s2", "c", "d")
        status = self.se.get_status()
        self.assertEqual(status["total"], 2)
        self.assertEqual(status["by_phase"]["proposed"], 2)

    def test_phase_history_tracked(self):
        self.se.register("s1", "test", "desc")
        self.se.advance_phase("s1")
        skill = self.se._get_skill("s1")
        self.assertEqual(len(skill["phase_history"]), 2)

    def test_cannot_advance_active(self):
        """Active skill cannot advance further."""
        self.se.register("s1", "test", "desc")
        self.se.advance_phase("s1")  # shadow
        for _ in range(50):
            self.se.record_shadow_query("s1", correct=True)
        self.se.advance_phase("s1")  # canary
        for _ in range(100):
            self.se.record_canary_query("s1", correct=True)
        self.se.advance_phase("s1")  # active
        result = self.se.advance_phase("s1")  # can't go further
        self.assertFalse(result["success"])


# ══════════════════════════════════════════════════════════════
#  benchmark_continuous
# ══════════════════════════════════════════════════════════════

class TestBenchmarkContinuous(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        import benchmark_continuous as bc
        self._orig_rf = bc.RESULTS_FILE
        self._orig_ms = bc.METRICS_STATE
        self._orig_sf = bc.STATE_FILE
        bc.RESULTS_FILE = Path(self.tmpdir) / "results.json"
        bc.METRICS_STATE = Path(self.tmpdir) / "metrics.json"
        bc.STATE_FILE = Path(self.tmpdir) / "state.json"
        self.bc = bc

    def tearDown(self):
        self.bc.RESULTS_FILE = self._orig_rf
        self.bc.METRICS_STATE = self._orig_ms
        self.bc.STATE_FILE = self._orig_sf
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _run_with_mocks(self, accuracy=0.90, uptime=1.0, precision=1.0, latency=100,
                         nf_allow=True):
        """Helper: run benchmark with mocked suites and noise_filter."""
        import noise_filter as nf_mod
        with patch.object(self.bc, "check_router_accuracy", return_value=accuracy):
            with patch.object(self.bc, "check_infra_uptime", return_value=uptime):
                with patch.object(self.bc, "check_alert_precision", return_value=precision):
                    with patch.object(self.bc, "check_latency_p95", return_value=latency):
                        with patch.object(nf_mod, "should_send", return_value=nf_allow):
                            with patch.object(self.bc, "_notify_alert"):
                                return self.bc.run()

    def test_low_accuracy_generates_alert(self):
        results = self._run_with_mocks(accuracy=0.60)
        self.assertTrue(len(results["alerts"]) > 0)
        self.assertEqual(results["alerts"][0]["metric"], "router_accuracy")

    def test_low_uptime_critical_alert(self):
        results = self._run_with_mocks(uptime=0.80)
        uptime_alerts = [a for a in results["alerts"] if a["metric"] == "infra_uptime"]
        self.assertTrue(len(uptime_alerts) > 0)
        self.assertEqual(uptime_alerts[0]["severity"], "critical")

    def test_all_good_no_alerts(self):
        results = self._run_with_mocks()
        self.assertEqual(len(results["alerts"]), 0)

    def test_respects_noise_filter(self):
        """Alert only sent if noise_filter allows — suppressed = no notify."""
        import noise_filter as nf_mod
        with patch.object(self.bc, "check_router_accuracy", return_value=0.50):
            with patch.object(self.bc, "check_infra_uptime", return_value=1.0):
                with patch.object(self.bc, "check_alert_precision", return_value=1.0):
                    with patch.object(self.bc, "check_latency_p95", return_value=100):
                        with patch.object(nf_mod, "should_send", return_value=False):
                            with patch.object(self.bc, "_notify_alert") as mock_notify:
                                self.bc.run()
        mock_notify.assert_not_called()

    def test_saves_results(self):
        with patch.object(self.bc, "check_router_accuracy", return_value=0.90):
            with patch.object(self.bc, "check_infra_uptime", return_value=1.0):
                with patch.object(self.bc, "check_alert_precision", return_value=1.0):
                    with patch.object(self.bc, "check_latency_p95", return_value=100):
                        self.bc.run()
        self.assertTrue(self.bc.RESULTS_FILE.exists())

    def test_updates_prometheus_metrics(self):
        with patch.object(self.bc, "check_router_accuracy", return_value=0.85):
            with patch.object(self.bc, "check_infra_uptime", return_value=0.95):
                with patch.object(self.bc, "check_alert_precision", return_value=0.90):
                    with patch.object(self.bc, "check_latency_p95", return_value=200):
                        self.bc.run()
        metrics = json.loads(self.bc.METRICS_STATE.read_text())
        self.assertIn("benchmark_continuous", metrics)
        self.assertAlmostEqual(metrics["benchmark_continuous"]["router_accuracy"], 0.85)

    def test_high_latency_alert(self):
        results = self._run_with_mocks(latency=6000)
        lat_alerts = [a for a in results["alerts"] if a["metric"] == "latency_p95"]
        self.assertTrue(len(lat_alerts) > 0)

    def test_thresholds_defined(self):
        for metric in ("router_accuracy", "infra_uptime", "alert_precision", "latency_p95"):
            self.assertIn(metric, self.bc.THRESHOLDS)

    def test_get_last_results(self):
        with patch.object(self.bc, "check_router_accuracy", return_value=0.90):
            with patch.object(self.bc, "check_infra_uptime", return_value=1.0):
                with patch.object(self.bc, "check_alert_precision", return_value=1.0):
                    with patch.object(self.bc, "check_latency_p95", return_value=100):
                        self.bc.run()
        results = self.bc.get_last_results()
        self.assertIn("router_accuracy", results)


if __name__ == "__main__":
    unittest.main()
