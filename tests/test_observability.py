#!/usr/bin/env python3
"""
Tests for freeze_detector and metrics_exporter.
Corre: python3 tests/test_observability.py
"""

import sys
import json
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, "/home/tenazas")
from runtime import freeze_detector as fd
from runtime import metrics_exporter as me


class TestFreezeDetector(unittest.TestCase):

    def setUp(self):
        self.detector = fd.FreezeDetector(threshold=0.5)

    def test_not_frozen_when_inactive(self):
        status = self.detector.check()
        self.assertFalse(status["frozen"])
        self.assertEqual(status["status"], "inactive")

    def test_not_frozen_after_heartbeat(self):
        self.detector.start()
        self.detector.heartbeat("step1")
        status = self.detector.check()
        self.assertFalse(status["frozen"])
        self.assertEqual(status["last_step"], "step1")
        self.detector.stop()

    def test_frozen_after_threshold(self):
        self.detector = fd.FreezeDetector(threshold=0.05)
        self.detector.start()
        self.detector.heartbeat("slow_step")
        time.sleep(0.1)  # Exceed 0.05s threshold
        status = self.detector.check()
        self.assertTrue(status["frozen"])
        self.assertEqual(status["status"], "frozen")
        self.assertEqual(status["last_step"], "slow_step")
        self.detector.stop()

    def test_heartbeat_resets_timer(self):
        self.detector = fd.FreezeDetector(threshold=0.1)
        self.detector.start()
        self.detector.heartbeat("step1")
        time.sleep(0.05)
        self.detector.heartbeat("step2")  # Reset
        status = self.detector.check()
        self.assertFalse(status["frozen"])
        self.detector.stop()

    def test_step_durations_tracked(self):
        self.detector.start()
        self.detector.heartbeat("step_a")
        time.sleep(0.01)
        self.detector.heartbeat("step_b")
        durations = self.detector.get_step_durations()
        self.assertIn("step_a", durations)
        self.assertGreater(durations["step_a"], 0)
        self.detector.stop()

    def test_freeze_count_increments(self):
        self.detector = fd.FreezeDetector(threshold=0.01)
        self.detector.start()
        self.detector.heartbeat("x")
        time.sleep(0.05)
        self.detector.check()  # frozen
        self.assertEqual(self.detector.freeze_count, 1)
        self.detector.stop()

    def test_stop_makes_inactive(self):
        self.detector.start()
        self.detector.heartbeat("x")
        self.detector.stop()
        status = self.detector.check()
        self.assertEqual(status["status"], "inactive")

    def test_global_singleton(self):
        detector = fd.get_detector()
        self.assertIsNotNone(detector)
        self.assertIsInstance(detector, fd.FreezeDetector)

    def test_convenience_functions(self):
        fd.start()
        fd.heartbeat("test")
        status = fd.check()
        self.assertIn("status", status)
        fd.stop()


class TestMetricsExporter(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self._orig = me.METRICS_LOG
        me.METRICS_LOG = Path(self.tmpdir) / "metrics.log"

    def tearDown(self):
        me.METRICS_LOG = self._orig

    def test_record_run(self):
        me.record_run(5.2)
        content = me.METRICS_LOG.read_text()
        self.assertIn("runtime_run", content)

    def test_record_failure(self):
        me.record_failure()
        content = me.METRICS_LOG.read_text()
        self.assertIn("runtime_failure", content)

    def test_record_step(self):
        me.record_step("update_state", 1.5)
        content = me.METRICS_LOG.read_text()
        self.assertIn("step_duration", content)
        self.assertIn("update_state", content)

    def test_record_container_restart(self):
        me.record_container_restart()
        content = me.METRICS_LOG.read_text()
        self.assertIn("container_restart", content)

    def test_record_vpn_disconnect(self):
        me.record_vpn_disconnect()
        content = me.METRICS_LOG.read_text()
        self.assertIn("vpn_disconnect", content)

    def test_record_freeze(self):
        me.record_freeze()
        content = me.METRICS_LOG.read_text()
        self.assertIn("freeze_detected", content)

    def test_record_watchdog(self):
        me.record_watchdog()
        content = me.METRICS_LOG.read_text()
        self.assertIn("watchdog_triggered", content)

    def test_metrics_are_json(self):
        me.record_run(1.0)
        me.record_step("test", 0.5)
        for line in me.METRICS_LOG.read_text().strip().split("\n"):
            parsed = json.loads(line)
            self.assertIn("ts", parsed)
            self.assertIn("metric", parsed)

    def test_rag_query_low_confidence(self):
        me.record_rag_query(0.5, 0.4)
        content = me.METRICS_LOG.read_text()
        self.assertIn("rag_query", content)


class TestHealthScore(unittest.TestCase):

    def test_perfect_score(self):
        score = me.calculate_health(5.0, {"step1": 1.0, "step2": 2.0})
        self.assertGreaterEqual(score, 90)

    def test_slow_cycle_penalized(self):
        score = me.calculate_health(65.0, {"step1": 30.0})
        self.assertLess(score, 60)

    def test_freeze_heavily_penalized(self):
        score = me.calculate_health(5.0, {}, freeze_count=2)
        self.assertLess(score, 70)

    def test_restart_loop_penalized(self):
        score = me.calculate_health(5.0, {}, restart_count=5)
        self.assertLess(score, 85)

    def test_score_never_negative(self):
        score = me.calculate_health(200, {"x": 60}, freeze_count=10, restart_count=10)
        self.assertGreaterEqual(score, 0)

    def test_score_max_100(self):
        score = me.calculate_health(1.0, {})
        self.assertLessEqual(score, 100)


if __name__ == "__main__":
    unittest.main(verbosity=2)
