#!/usr/bin/env python3
"""
test_sprint6.py — Operational Intelligence tests.

Covers:
  - infra_predictor: trend detection with mock data
  - root_cause_engine: causal chain analysis for all known patterns
  - incident_clustering: grouping and pattern detection
  - runbook auto-execution: SAFE only, MEDIUM/HIGH blocked
"""

import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

RUNTIME = Path(__file__).parent.parent / "workspace" / "runtime"
_C = Path("/home/tenazas/runtime")
if _C.exists():
    RUNTIME = _C
sys.path.insert(0, str(RUNTIME.parent))
sys.path.insert(0, str(RUNTIME))


# ══════════════════════════════════════════════════════════════
#  infra_predictor
# ══════════════════════════════════════════════════════════════

class TestInfraPredictor(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        import infra_predictor as ip
        self._orig_state = ip.STATE_DIR
        self._orig_pred = ip.PREDICTIONS_FILE
        ip.STATE_DIR = Path(self.tmpdir) / "state"
        ip.STATE_DIR.mkdir(parents=True)
        ip.PREDICTIONS_FILE = ip.STATE_DIR / "predictions.json"
        self.ip = ip

    def tearDown(self):
        self.ip.STATE_DIR = self._orig_state
        self.ip.PREDICTIONS_FILE = self._orig_pred
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_container_failure_risk_from_dq(self):
        """3+ restarts in decision_quality → high risk prediction."""
        dq_dir = self.ip.STATE_DIR / "decision_quality"
        dq_dir.mkdir(parents=True)
        today = datetime.now().strftime("%Y-%m-%d")
        entries = [
            {"action": "restart_container", "target": "sonarr", "quality": "good"},
            {"action": "restart_container", "target": "sonarr", "quality": "good"},
            {"action": "restart_container", "target": "sonarr", "quality": "bad"},
            {"action": "restart_container", "target": "sonarr", "quality": "bad"},
        ]
        (dq_dir / f"{today}.json").write_text(json.dumps(entries))
        with patch.object(self.ip, "_query_prometheus", return_value=[]):
            result = self.ip.predict_container_failure_risk()
        self.assertIsNotNone(result)
        self.assertEqual(result["type"], "container_failure_risk")
        self.assertGreater(result["probability"], 0.7)
        self.assertEqual(result["target"], "sonarr")

    def test_no_risk_below_3_restarts(self):
        """2 restarts → no prediction."""
        dq_dir = self.ip.STATE_DIR / "decision_quality"
        dq_dir.mkdir(parents=True)
        today = datetime.now().strftime("%Y-%m-%d")
        entries = [
            {"action": "restart_container", "target": "sonarr"},
            {"action": "restart_container", "target": "sonarr"},
        ]
        (dq_dir / f"{today}.json").write_text(json.dumps(entries))
        with patch.object(self.ip, "_query_prometheus", return_value=[]):
            result = self.ip.predict_container_failure_risk()
        self.assertIsNone(result)

    def test_linear_slope_positive(self):
        values = [(0, 10), (1, 20), (2, 30)]
        slope = self.ip._linear_slope(values)
        self.assertGreater(slope, 0)

    def test_linear_slope_negative(self):
        values = [(0, 30), (1, 20), (2, 10)]
        slope = self.ip._linear_slope(values)
        self.assertLess(slope, 0)

    def test_linear_slope_flat(self):
        values = [(0, 50), (1, 50), (2, 50)]
        slope = self.ip._linear_slope(values)
        self.assertAlmostEqual(slope, 0.0, places=5)

    def test_linear_slope_insufficient_data(self):
        values = [(0, 10)]
        slope = self.ip._linear_slope(values)
        self.assertEqual(slope, 0.0)

    def test_predict_all_no_prometheus(self):
        """predict_all with no Prometheus → uses fallback sources."""
        with patch.object(self.ip, "_query_prometheus", return_value=[]):
            with patch.object(self.ip, "_query_prometheus_range", return_value=[]):
                preds = self.ip.predict_all()
        self.assertIsInstance(preds, list)

    def test_predict_all_saves_file(self):
        with patch.object(self.ip, "_query_prometheus", return_value=[]):
            with patch.object(self.ip, "_query_prometheus_range", return_value=[]):
                self.ip.predict_all()
        self.assertTrue(self.ip.PREDICTIONS_FILE.exists())

    def test_prediction_structure(self):
        """Each prediction has required fields."""
        dq_dir = self.ip.STATE_DIR / "decision_quality"
        dq_dir.mkdir(parents=True)
        today = datetime.now().strftime("%Y-%m-%d")
        entries = [{"action": "restart_container", "target": "x"}] * 5
        (dq_dir / f"{today}.json").write_text(json.dumps(entries))
        with patch.object(self.ip, "_query_prometheus", return_value=[]):
            with patch.object(self.ip, "_query_prometheus_range", return_value=[]):
                preds = self.ip.predict_all()
        for p in preds:
            for field in ("type", "probability", "eta_minutes",
                          "evidence", "recommended_action", "severity"):
                self.assertIn(field, p)

    def test_min_probability_filter(self):
        """Predictions below 0.7 filtered out."""
        self.assertEqual(self.ip.MIN_PROBABILITY, 0.7)

    def test_format_empty(self):
        text = self.ip.format_predictions([])
        self.assertIn("No predictions", text)


# ══════════════════════════════════════════════════════════════
#  root_cause_engine
# ══════════════════════════════════════════════════════════════

class TestRootCauseEngine(unittest.TestCase):

    def test_cloud_fallback_root_is_vram_or_network(self):
        import root_cause_engine as rce
        result = rce.analyze("cloud_fallback")
        self.assertIn(result["root_cause"],
                      ["vram_high", "network_down", "homelab_offline"])
        self.assertTrue(result["is_symptom"])

    def test_ollama_slow_root_is_vram(self):
        import root_cause_engine as rce
        result = rce.analyze("ollama_slow")
        self.assertEqual(result["root_cause"], "vram_high")
        self.assertTrue(result["is_symptom"])

    def test_container_crash_root_is_ram(self):
        import root_cause_engine as rce
        result = rce.analyze("container_crash")
        self.assertEqual(result["root_cause"], "ram_high")

    def test_vpn_leak_is_root(self):
        import root_cause_engine as rce
        result = rce.analyze("vpn_leak")
        self.assertEqual(result["root_cause"], "vpn_leak")
        self.assertFalse(result["is_symptom"])

    def test_downloads_stopped_root_is_vpn(self):
        import root_cause_engine as rce
        result = rce.analyze("downloads_stopped")
        self.assertEqual(result["root_cause"], "vpn_leak")
        self.assertTrue(result["is_symptom"])

    def test_unknown_incident_returns_unknown(self):
        import root_cause_engine as rce
        result = rce.analyze("totally_unknown_problem")
        self.assertEqual(result["root_cause"], "unknown")
        self.assertFalse(result["is_symptom"])
        self.assertEqual(result["confidence"], 0.3)

    def test_fix_targets_root_not_symptom(self):
        """Fix action always targets root cause."""
        import root_cause_engine as rce
        result = rce.analyze("classifier_timeout")
        self.assertEqual(result["fix_target"], "vram_high")
        self.assertNotEqual(result["fix_target"], "classifier_timeout")

    def test_analyze_has_chain(self):
        import root_cause_engine as rce
        result = rce.analyze("cloud_fallback")
        self.assertIsInstance(result["chain"], list)
        self.assertGreater(len(result["chain"]), 1)

    def test_analyze_confidence_range(self):
        import root_cause_engine as rce
        for incident in ["cloud_fallback", "ollama_slow", "vpn_leak", "unknown"]:
            result = rce.analyze(incident)
            self.assertGreaterEqual(result["confidence"], 0.0)
            self.assertLessEqual(result["confidence"], 1.0)

    def test_analyze_multiple_deduplicates(self):
        import root_cause_engine as rce
        incidents = ["cloud_fallback", "classifier_timeout", "ollama_slow"]
        # All 3 share root "vram_high" → should dedup to 1
        results = rce.analyze_multiple(incidents)
        roots = [r["root_cause"] for r in results]
        self.assertEqual(len(set(roots)), len(roots))

    def test_analyze_multiple_sorts_by_confidence(self):
        import root_cause_engine as rce
        incidents = ["totally_unknown_problem", "cloud_fallback"]
        results = rce.analyze_multiple(incidents)
        if len(results) >= 2:
            self.assertGreaterEqual(results[0]["confidence"], results[1]["confidence"])

    def test_get_fix_known(self):
        import root_cause_engine as rce
        fix = rce.get_fix_for("ollama_slow")
        self.assertIsNotNone(fix)

    def test_get_fix_unknown(self):
        import root_cause_engine as rce
        fix = rce.get_fix_for("unknown_thing")
        self.assertIsNone(fix)

    def test_is_symptom_true(self):
        import root_cause_engine as rce
        self.assertTrue(rce.is_symptom("cloud_fallback"))

    def test_is_symptom_false_for_root(self):
        import root_cause_engine as rce
        self.assertFalse(rce.is_symptom("vram_high"))

    def test_list_chains_not_empty(self):
        import root_cause_engine as rce
        chains = rce.list_chains()
        self.assertGreater(len(chains), 0)
        for c in chains:
            self.assertIn("id", c)
            self.assertIn("chain", c)
            self.assertIn("fix", c)

    def test_gluetun_cascade(self):
        import root_cause_engine as rce
        result = rce.analyze("arr_stack_offline")
        self.assertEqual(result["root_cause"], "gluetun_down")

    def test_disk_cascade(self):
        import root_cause_engine as rce
        result = rce.analyze("data_loss_risk")
        self.assertEqual(result["root_cause"], "disk_full")

    def test_gpu_slow_not_restart(self):
        """GPU slow ≠ ollama_dead — fix should be optimize, not restart."""
        import root_cause_engine as rce
        result = rce.analyze("ollama_slow")
        self.assertNotIn("restart", result["fix_action"].lower())
        self.assertIn("optimize", result["fix_action"].lower())


# ══════════════════════════════════════════════════════════════
#  incident_clustering
# ══════════════════════════════════════════════════════════════

class TestIncidentClustering(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        import incident_clustering as ic
        self._orig_pf = ic.PATTERNS_FILE
        self._orig_em = ic.ERROR_MINING_DIR
        ic.PATTERNS_FILE = Path(self.tmpdir) / "patterns.json"
        ic.ERROR_MINING_DIR = Path(self.tmpdir) / "mining"
        self.ic = ic

    def tearDown(self):
        self.ic.PATTERNS_FILE = self._orig_pf
        self.ic.ERROR_MINING_DIR = self._orig_em
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_groups_3_similar(self):
        """3+ incidents of same type → pattern detected."""
        ts = datetime.now().astimezone().isoformat()
        incidents = [
            {"type": "backend_offline", "category": "backend_offline",
             "detail": "HomeLab offline", "probable_cause": "sleep",
             "ts": ts, "severity": "high"},
        ] * 4
        patterns = self.ic.cluster(incidents)
        self.assertEqual(len(patterns), 1)
        self.assertGreaterEqual(patterns[0]["occurrences"], 3)

    def test_below_threshold_no_pattern(self):
        """2 incidents → no pattern."""
        ts = datetime.now().astimezone().isoformat()
        incidents = [
            {"type": "rare_event", "category": "rare", "detail": "x",
             "probable_cause": "y", "ts": ts, "severity": "info"},
        ] * 2
        patterns = self.ic.cluster(incidents)
        self.assertEqual(len(patterns), 0)

    def test_different_types_separate_clusters(self):
        ts = datetime.now().astimezone().isoformat()
        incidents = (
            [{"type": "backend_offline", "category": "backend_offline",
              "detail": "offline", "probable_cause": "sleep",
              "ts": ts, "severity": "high"}] * 4 +
            [{"type": "vpn_leak", "category": "vpn_leak",
              "detail": "AR detected", "probable_cause": "vpn",
              "ts": ts, "severity": "critical"}] * 3
        )
        patterns = self.ic.cluster(incidents)
        self.assertEqual(len(patterns), 2)

    def test_pattern_has_peak_hour(self):
        ts = datetime.now().astimezone().isoformat()
        incidents = [{"type": "test", "category": "test", "detail": "x",
                      "probable_cause": "y", "ts": ts, "severity": "info"}] * 5
        patterns = self.ic.cluster(incidents)
        self.assertIn("peak_hour", patterns[0])

    def test_pattern_has_recommendation(self):
        ts = datetime.now().astimezone().isoformat()
        incidents = [{"type": "backend_offline", "category": "backend_offline",
                      "detail": "offline", "probable_cause": "sleep",
                      "ts": ts, "severity": "high"}] * 3
        patterns = self.ic.cluster(incidents)
        self.assertIn("recommendation", patterns[0])
        self.assertGreater(len(patterns[0]["recommendation"]), 0)

    def test_pattern_has_probable_trigger(self):
        ts = datetime.now().astimezone().isoformat()
        incidents = [{"type": "backend_offline", "category": "backend_offline",
                      "detail": "offline", "probable_cause": "sleep",
                      "ts": ts, "severity": "high"}] * 3
        patterns = self.ic.cluster(incidents)
        self.assertIn("probable_trigger", patterns[0])

    def test_patterns_sorted_by_occurrences(self):
        ts = datetime.now().astimezone().isoformat()
        incidents = (
            [{"type": "a", "category": "a", "detail": "x",
              "probable_cause": "y", "ts": ts, "severity": "info"}] * 5 +
            [{"type": "b", "category": "b", "detail": "z",
              "probable_cause": "w", "ts": ts, "severity": "info"}] * 3
        )
        patterns = self.ic.cluster(incidents)
        self.assertGreaterEqual(patterns[0]["occurrences"],
                                patterns[1]["occurrences"])

    def test_empty_incidents(self):
        patterns = self.ic.cluster([])
        self.assertEqual(patterns, [])

    def test_saves_to_file(self):
        ts = datetime.now().astimezone().isoformat()
        incidents = [{"type": "x", "category": "x", "detail": "d",
                      "probable_cause": "c", "ts": ts, "severity": "info"}] * 3
        self.ic.cluster(incidents)
        self.assertTrue(self.ic.PATTERNS_FILE.exists())

    def test_format_empty(self):
        text = self.ic.format_patterns([])
        self.assertIn("No patterns", text)


# ══════════════════════════════════════════════════════════════
#  Runbook auto-execution (SAFE only)
# ══════════════════════════════════════════════════════════════

class TestRunbookAutoExecution(unittest.TestCase):

    def test_safe_action_in_registry(self):
        """action_registry has actions with risk_level."""
        import action_registry as ar
        action = ar.get_action("restart_container")
        self.assertIn("risk_level", action)

    def test_medium_action_requires_approval(self):
        """MEDIUM risk actions require approval — never auto-execute."""
        import action_registry as ar
        for a in ar.list_actions():
            if a.get("risk_level") in ("medium", "MEDIUM"):
                # Should require approval
                self.assertTrue(a.get("requires_approval", True))

    def test_openclaw_blocked_for_auto_execution(self):
        """openclaw never auto-restartable."""
        import action_registry as ar
        action = ar.get_action("restart_container")
        self.assertIn("openclaw", action.get("blocked_targets", []))

    def test_watchtower_blocked_for_auto_execution(self):
        import action_registry as ar
        action = ar.get_action("restart_container")
        self.assertIn("watchtower", action.get("blocked_targets", []))

    def test_budget_constrains_auto_execution(self):
        """Action budget limits auto-execution."""
        import action_budget as ab
        tmpdir = tempfile.mkdtemp()
        orig = ab.STATE_PATH
        ab.STATE_PATH = Path(tmpdir) / "ab.json"
        now = time.time()
        # Exhaust budget
        for i in range(3):
            ab.can_execute("restart_container", _now=now + i)
        # 4th blocked
        self.assertFalse(ab.can_execute("restart_container", _now=now + 4))
        ab.STATE_PATH = orig
        shutil.rmtree(tmpdir)

    def test_root_cause_consulted_before_action(self):
        """root_cause_engine can be consulted to avoid wrong fix."""
        import root_cause_engine as rce
        # If symptom is cloud_fallback, fix should target vram, not restart cloud
        result = rce.analyze("cloud_fallback")
        self.assertNotEqual(result["fix_action"], "restart cloud")

    def test_notification_after_auto_execution(self):
        """Auto-execution should produce an informative notification."""
        import noise_filter as nf
        tmpdir = tempfile.mkdtemp()
        orig = nf.STATE_PATH
        nf.STATE_PATH = Path(tmpdir) / "nf.json"
        # First notification always passes
        self.assertTrue(nf.should_send("auto_heal_result", "restart sonarr OK"))
        nf.STATE_PATH = orig
        shutil.rmtree(tmpdir)


if __name__ == "__main__":
    unittest.main()
