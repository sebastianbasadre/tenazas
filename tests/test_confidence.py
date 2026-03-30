#!/usr/bin/env python3
"""
Tests para runtime/confidence_engine.py, action_mapper.py, infra_diagnostics.py
Corre: python3 tests/test_confidence.py
"""

import sys
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, "/home/tenazas")
from runtime import confidence_engine as ce
from runtime import action_mapper as am
from runtime import infra_diagnostics as diag


# ── Confidence Engine ────────────────────────────────────────────


class TestSourceClassification(unittest.TestCase):

    def test_skill_classified(self):
        self.assertEqual(ce._classify_source("skills/rag/SKILL.md"), "skill")

    def test_core_doc(self):
        self.assertEqual(ce._classify_source("SOUL.md"), "core_doc")
        self.assertEqual(ce._classify_source("AGENTS.md"), "core_doc")

    def test_working_memory(self):
        self.assertEqual(ce._classify_source("working_memory"), "working")

    def test_recent_memory(self):
        today = datetime.now().strftime("%Y-%m-%d")
        self.assertEqual(ce._classify_source(f"memory/{today}-session.md"), "memory_recent")

    def test_old_memory(self):
        self.assertEqual(ce._classify_source("memory/2024-01-01-old.md"), "memory_old")

    def test_unknown(self):
        self.assertEqual(ce._classify_source("random/file.py"), "unknown")


class TestConfidenceScoring(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self._orig = ce.DIAGNOSTICS_LOG
        ce.DIAGNOSTICS_LOG = Path(self.tmpdir) / "diagnostics.log"

    def tearDown(self):
        ce.DIAGNOSTICS_LOG = self._orig

    def test_high_confidence_with_good_results(self):
        results = [
            {"source": "skills/infra-monitor/SKILL.md", "vector_score": 0.9, "score": 0.9},
            {"source": "SOUL.md", "vector_score": 0.85, "score": 0.85},
            {"source": "skills/rag/SKILL.md", "vector_score": 0.8, "score": 0.8},
        ]
        conf = ce.compute_confidence(results)
        self.assertGreaterEqual(conf["score"], 0.8)
        self.assertEqual(conf["level"], "high")
        self.assertEqual(conf["recommendation"], "answer_normally")

    def test_low_confidence_with_poor_results(self):
        results = [
            {"source": "memory/2024-01-01-old.md", "vector_score": 0.3, "score": 0.3},
        ]
        conf = ce.compute_confidence(results)
        self.assertLess(conf["score"], 0.6)
        self.assertEqual(conf["level"], "low")

    def test_empty_results_low_confidence(self):
        conf = ce.compute_confidence([])
        self.assertLess(conf["score"], 0.6)
        self.assertEqual(conf["level"], "low")

    def test_medium_confidence(self):
        results = [
            {"source": "memory/2026-03-28-notes.md", "vector_score": 0.7, "score": 0.7},
        ]
        conf = ce.compute_confidence(results)
        self.assertGreaterEqual(conf["score"], 0.4)
        self.assertLess(conf["score"], 0.8)

    def test_components_present(self):
        results = [{"source": "SOUL.md", "vector_score": 0.8, "score": 0.8}]
        conf = ce.compute_confidence(results)
        for key in ("similarity", "agreement", "recency", "source_quality"):
            self.assertIn(key, conf["components"])

    def test_score_normalized(self):
        results = [
            {"source": "skills/x/SKILL.md", "vector_score": 1.0, "score": 1.0},
            {"source": "SOUL.md", "vector_score": 1.0, "score": 1.0},
        ]
        conf = ce.compute_confidence(results)
        self.assertLessEqual(conf["score"], 1.0)
        self.assertGreaterEqual(conf["score"], 0.0)


class TestSelfCheck(unittest.TestCase):

    def test_high_confidence_answers_normally(self):
        conf = {"score": 0.9, "level": "high", "components": {
            "similarity": 0.9, "agreement": 0.9, "recency": 0.9, "source_quality": 0.9,
        }}
        check = ce.self_check("query", [{"source": "x"}], conf)
        self.assertEqual(check["strategy"], "answer")
        self.assertIsNone(check["caveat"])

    def test_medium_confidence_adds_caveat(self):
        conf = {"score": 0.7, "level": "medium", "components": {
            "similarity": 0.5, "agreement": 0.8, "recency": 0.8, "source_quality": 0.8,
        }}
        check = ce.self_check("query", [{"source": "x"}], conf)
        self.assertEqual(check["strategy"], "answer_with_caveat")
        self.assertIsNotNone(check["caveat"])

    def test_low_confidence_expands(self):
        conf = {"score": 0.3, "level": "low", "components": {
            "similarity": 0.3, "agreement": 0.3, "recency": 0.3, "source_quality": 0.3,
        }}
        check = ce.self_check("query", [{"source": "x"}], conf)
        self.assertEqual(check["strategy"], "expand")
        self.assertIsNotNone(check["expand_query"])

    def test_low_confidence_no_results_clarifies(self):
        conf = {"score": 0.0, "level": "low", "components": {
            "similarity": 0, "agreement": 0, "recency": 0, "source_quality": 0,
        }}
        check = ce.self_check("query", [], conf)
        self.assertEqual(check["strategy"], "clarify")


# ── Action Mapper ────────────────────────────────────────────────


class TestActionMapping(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self._orig = am.DIAGNOSTICS_LOG
        am.DIAGNOSTICS_LOG = Path(self.tmpdir) / "diagnostics.log"

    def tearDown(self):
        am.DIAGNOSTICS_LOG = self._orig

    def test_vpn_down_matches(self):
        actions = am.match_actions("vpn_down detected")
        self.assertTrue(any(a["target"] == "gluetun" for a in actions))

    def test_vpn_leak_matches(self):
        actions = am.match_actions("vpn_leak ip argentina AR")
        self.assertTrue(any(a["target"] == "gluetun" for a in actions))

    def test_container_down_matches(self):
        actions = am.match_actions("sonarr container is down and crashed")
        self.assertTrue(any(a["target"] == "sonarr" for a in actions))

    def test_internet_slow_matches(self):
        actions = am.match_actions("internet is slow and latency is high")
        self.assertTrue(any(a["skill"] == "internet-health" for a in actions))

    def test_ollama_offline_matches(self):
        actions = am.match_actions("ollama notebook is offline")
        self.assertTrue(any(a["skill"] == "model-manager" for a in actions))

    def test_no_match_returns_empty(self):
        actions = am.match_actions("hola como estas")
        self.assertEqual(actions, [])

    def test_priority_sorting(self):
        actions = am.match_actions("vpn_down and internet slow network problem")
        if len(actions) >= 2:
            self.assertLessEqual(actions[0]["priority"], actions[1]["priority"])

    def test_all_actions_are_safe(self):
        actions = am.match_actions("container_down vpn_down internet_lost")
        for a in actions:
            self.assertTrue(a["safe"])


class TestEventToActionMapping(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self._orig = am.DIAGNOSTICS_LOG
        am.DIAGNOSTICS_LOG = Path(self.tmpdir) / "diagnostics.log"

    def tearDown(self):
        am.DIAGNOSTICS_LOG = self._orig

    def test_maps_runtime_events(self):
        events = [
            {"type": "container_down", "container": "sonarr"},
            {"type": "vpn_leak", "ip": "190.1.2.3", "country": "AR"},
        ]
        actions = am.map_events_to_actions(events)
        targets = [a["target"] for a in actions]
        self.assertIn("gluetun", targets)

    def test_deduplicates(self):
        events = [
            {"type": "vpn_down"},
            {"type": "vpn_leak", "ip": "190.1.2.3"},
        ]
        actions = am.map_events_to_actions(events)
        gluetun_count = sum(1 for a in actions if a["target"] == "gluetun")
        self.assertEqual(gluetun_count, 1)


class TestFormatSuggestions(unittest.TestCase):

    def test_empty_actions(self):
        self.assertEqual(am.format_suggestions([]), "")

    def test_formats_actions(self):
        actions = [
            {"action": "docker.restart", "target": "gluetun",
             "skill": "container-restart", "description": "Reiniciar gluetun",
             "priority": 1, "safe": True},
        ]
        output = am.format_suggestions(actions)
        self.assertIn("Reiniciar gluetun", output)
        self.assertIn("automáticamente", output)


# ── Infra Diagnostics ────────────────────────────────────────────


class TestInfraDiagnostics(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self._orig_log = diag.DIAGNOSTICS_LOG
        self._orig_mem = diag.MEMORY_FILE
        diag.DIAGNOSTICS_LOG = Path(self.tmpdir) / "diagnostics.log"
        diag.MEMORY_FILE = Path(self.tmpdir) / "memory_log.json"

    def tearDown(self):
        diag.DIAGNOSTICS_LOG = self._orig_log
        diag.MEMORY_FILE = self._orig_mem

    def _make_state(self, **overrides):
        base = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "containers": [
                {"name": "sonarr", "status": "Up 2h", "up": True},
                {"name": "gluetun", "status": "Up 1h (healthy)", "up": True},
            ],
            "internet": {"ok": True, "latency_ms": 15},
            "vpn": {"ip": "82.140.1.2", "ok": True, "country": "ES"},
            "ollama_notebook": {"ok": True},
            "ollama_nas": {"ok": False},
            "network_devices": {"count": 12},
        }
        base.update(overrides)
        return base

    def test_healthy_system_no_issues(self):
        state = self._make_state()
        issues = diag.run_diagnostics(state)
        # Only ollama_nas is down but that's INFO level, not critical
        critical = [i for i in issues if i["severity"] == "critical"]
        self.assertEqual(len(critical), 0)

    def test_container_down_detected(self):
        state = self._make_state(containers=[
            {"name": "sonarr", "status": "Exited (1)", "up": False},
            {"name": "gluetun", "status": "Up 1h", "up": True},
        ])
        issues = diag.run_diagnostics(state)
        problems = [i["problem"] for i in issues]
        self.assertTrue(any("sonarr" in p for p in problems))

    def test_internet_down_critical(self):
        state = self._make_state(internet={"ok": False, "latency_ms": None})
        issues = diag.run_diagnostics(state)
        critical = [i for i in issues if i["severity"] == "critical"]
        self.assertTrue(any("internet" in i["problem"].lower() for i in critical))

    def test_vpn_leak_critical(self):
        state = self._make_state(vpn={"ip": "190.1.2.3", "ok": True, "country": "AR"})
        issues = diag.run_diagnostics(state)
        critical = [i for i in issues if i["severity"] == "critical"]
        self.assertTrue(any("leak" in i["problem"].lower() for i in critical))

    def test_vpn_down_critical(self):
        state = self._make_state(vpn={"ip": None, "ok": False})
        issues = diag.run_diagnostics(state)
        critical = [i for i in issues if i["severity"] == "critical"]
        self.assertTrue(any("vpn" in i["problem"].lower() for i in critical))

    def test_all_ollama_down_critical(self):
        state = self._make_state(
            ollama_notebook={"ok": False}, ollama_nas={"ok": False}
        )
        issues = diag.run_diagnostics(state)
        critical = [i for i in issues if i["severity"] == "critical"]
        self.assertTrue(any("ollama" in i["problem"].lower() for i in critical))

    def test_high_latency_warning(self):
        state = self._make_state(internet={"ok": True, "latency_ms": 350})
        issues = diag.run_diagnostics(state)
        warnings = [i for i in issues if i["severity"] == "warning"]
        self.assertTrue(any("latencia" in i["problem"].lower() for i in warnings))

    def test_crash_loop_detection(self):
        # Simulate 4 restarts of sonarr in recent memory
        now = datetime.now(timezone.utc)
        memory = []
        for i in range(4):
            memory.append({
                "ts": (now - timedelta(minutes=i * 10)).isoformat(),
                "events": [],
                "tasks_run": 1,
                "results": [{"action": "docker.restart", "target": "sonarr", "ok": True}],
            })
        with open(diag.MEMORY_FILE, "w") as f:
            json.dump(memory, f)

        state = self._make_state()
        issues = diag.run_diagnostics(state)
        critical = [i for i in issues if i["severity"] == "critical"]
        self.assertTrue(any("crash loop" in i["problem"].lower() for i in critical))

    def test_format_report_healthy(self):
        report = diag.format_diagnostic_report([])
        self.assertIn("saludable", report.lower())

    def test_format_report_with_issues(self):
        issues = [{
            "problem": "VPN leak", "cause": "gluetun",
            "severity": "critical", "suggested_action": "restart",
        }]
        report = diag.format_diagnostic_report(issues)
        self.assertIn("VPN leak", report)
        self.assertIn("CRIT", report)

    def test_quick_health_check(self):
        with patch.object(diag, "STATE_FILE", Path(self.tmpdir) / "state.json"):
            # Write a healthy state
            state = self._make_state()
            with open(Path(self.tmpdir) / "state.json", "w") as f:
                json.dump(state, f)
            health = diag.quick_health_check()
            self.assertTrue(health["healthy"])
            self.assertEqual(health["critical_count"], 0)

    def test_sorted_by_severity(self):
        state = self._make_state(
            internet={"ok": False, "latency_ms": None},  # critical
            ollama_notebook={"ok": False},                # info
        )
        issues = diag.run_diagnostics(state)
        if len(issues) >= 2:
            severity_order = {"critical": 0, "warning": 1, "info": 2}
            for i in range(len(issues) - 1):
                self.assertLessEqual(
                    severity_order.get(issues[i]["severity"], 9),
                    severity_order.get(issues[i + 1]["severity"], 9),
                )


if __name__ == "__main__":
    unittest.main(verbosity=2)
