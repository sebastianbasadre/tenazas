#!/usr/bin/env python3
"""
Tests for advanced intelligence modules:
context_manager, decision_trace, agent_conflict, retrieval_quality,
adaptive_router, prompt_optimizer, knowledge_graph, failure_simulator,
skill_analytics, experiment_engine.

Corre: python3 tests/test_intelligence.py
"""

import sys
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, "/home/tenazas")
from runtime import context_manager as cm
from runtime import decision_trace as dt
from runtime import agent_conflict as ac
from runtime import retrieval_quality as rq
from runtime import adaptive_router as ar
from runtime import knowledge_graph as kg
from runtime import failure_simulator as fs
from runtime import experiment_engine as ee


# ── Context Manager ──────────────────────────────────────────────


class TestContextManager(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self._orig = cm.TELEMETRY_LOG
        cm.TELEMETRY_LOG = Path(self.tmpdir) / "log"

    def tearDown(self):
        cm.TELEMETRY_LOG = self._orig

    def test_budget_allocation(self):
        budget = cm.allocate_context_budget(8192)
        self.assertIn("rag_context", budget)
        self.assertIn("working_memory", budget)
        total_pct = sum(b["percentage"] for b in budget.values())
        self.assertAlmostEqual(total_pct, 1.0, places=2)

    def test_prune_low_value(self):
        items = [
            {"text": "high quality content about VPN", "score": 0.9},
            {"text": "medium quality", "score": 0.6},
            {"text": "low quality junk", "score": 0.3},
        ]
        pruned = cm.prune_low_value_context(items, budget_chars=1000, min_score=0.5)
        self.assertEqual(len(pruned), 2)
        self.assertEqual(pruned[0]["score"], 0.9)

    def test_prune_respects_budget(self):
        items = [
            {"text": "x" * 500, "score": 0.9},
            {"text": "y" * 500, "score": 0.8},
            {"text": "z" * 500, "score": 0.7},
        ]
        pruned = cm.prune_low_value_context(items, budget_chars=600)
        total = sum(len(i["text"]) for i in pruned)
        self.assertLessEqual(total, 604)  # budget + "..."

    def test_prioritize_sources(self):
        rag = [{"text": "rag result", "score": 0.8}]
        working = [{"text": "working mem", "score": 0.5}]
        agent = [{"text": "agent output", "score": 0.7}]
        result = cm.prioritize_sources(rag, working, agent)
        self.assertIn("rag", result)
        self.assertIn("stats", result)
        self.assertGreater(result["stats"]["total_chars"], 0)


# ── Decision Trace ───────────────────────────────────────────────


class TestDecisionTrace(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self._orig = dt.DECISIONS_LOG
        dt.DECISIONS_LOG = Path(self.tmpdir) / "decisions.jsonl"

    def tearDown(self):
        dt.DECISIONS_LOG = self._orig

    def test_trace_creates_entry(self):
        dt.trace("test query", classification="chat", pipeline="direct",
                 model="gemini-flash", confidence=0.9, latency_ms=100)
        self.assertTrue(dt.DECISIONS_LOG.exists())
        entries = dt.get_recent(10)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0]["classification"], "chat")

    def test_multiple_traces(self):
        for i in range(5):
            dt.trace(f"query {i}", classification="chat")
        entries = dt.get_recent(10)
        self.assertEqual(len(entries), 5)

    def test_get_by_classification(self):
        dt.trace("q1", classification="chat")
        dt.trace("q2", classification="knowledge_query")
        dt.trace("q3", classification="chat")
        chats = dt.get_by_classification("chat")
        self.assertEqual(len(chats), 2)

    def test_get_escalations(self):
        dt.trace("q1", escalated=False)
        dt.trace("q2", escalated=True, escalation_reason="low_confidence")
        escalations = dt.get_escalations()
        self.assertEqual(len(escalations), 1)

    def test_summary(self):
        dt.trace("q1", classification="chat", confidence=0.9, latency_ms=100)
        dt.trace("q2", classification="infra", confidence=0.5, latency_ms=500)
        s = dt.summary(hours=1)
        self.assertEqual(s["total"], 2)
        self.assertGreater(s["avg_confidence"], 0)


# ── Agent Conflict ───────────────────────────────────────────────


class TestAgentConflict(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self._orig = ac.TELEMETRY_LOG
        ac.TELEMETRY_LOG = Path(self.tmpdir) / "log"

    def tearDown(self):
        ac.TELEMETRY_LOG = self._orig

    def test_agreement(self):
        result = ac.compute_disagreement(
            "network issue detected, latency high",
            "confirmed network latency problem",
        )
        self.assertLess(result["score"], 0.5)
        self.assertFalse(result["should_escalate"])

    def test_disagreement(self):
        result = ac.compute_disagreement(
            "this is a network connectivity issue",
            "this is a container crash problem",
        )
        self.assertGreater(result["score"], 0)
        self.assertGreater(result["category_divergence"], 0)

    def test_contradiction_detected(self):
        result = ac.compute_disagreement(
            "system is estable and normal",
            "system is inestable and failing",
        )
        self.assertTrue(result["contradiction_detected"])

    def test_empty_inputs(self):
        result = ac.compute_disagreement("", "")
        self.assertEqual(result["score"], 0.0)

    def test_check_multi_agent_result(self):
        reasoning = {
            "analysis": "VPN leak detected, gluetun needs restart",
            "verification": "Confirmed VPN issue",
        }
        result = ac.check_multi_agent_result(reasoning)
        self.assertIn("score", result)
        self.assertFalse(result["should_escalate"])


# ── Retrieval Quality ────────────────────────────────────────────


class TestRetrievalQuality(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self._orig = rq.TELEMETRY_LOG
        rq.TELEMETRY_LOG = Path(self.tmpdir) / "log"

    def tearDown(self):
        rq.TELEMETRY_LOG = self._orig

    def test_good_retrieval(self):
        results = [
            {"text": "gluetun VPN proxy config", "source": "skills/infra/SKILL.md", "score": 0.9},
            {"text": "network diagnostic info", "source": "SOUL.md", "score": 0.85},
            {"text": "container management", "source": "skills/restart/SKILL.md", "score": 0.8},
        ]
        quality = rq.evaluate(results)
        self.assertGreater(quality["quality_score"], 0.6)
        self.assertFalse(quality["should_expand"])

    def test_poor_retrieval(self):
        results = [
            {"text": "some vague content", "source": "memory/2024-01-01.md", "score": 0.3},
        ]
        quality = rq.evaluate(results)
        self.assertLess(quality["quality_score"], 0.6)
        self.assertTrue(quality["should_expand"])

    def test_empty_results(self):
        quality = rq.evaluate([])
        self.assertLess(quality["quality_score"], 0.6)
        self.assertTrue(quality["should_expand"])

    def test_diverse_sources_score_higher(self):
        diverse = [
            {"text": "skill info", "source": "skills/x/SKILL.md", "score": 0.8},
            {"text": "soul info", "source": "SOUL.md", "score": 0.8},
            {"text": "memory info", "source": "memory/2026-03-29.md", "score": 0.8},
        ]
        single = [
            {"text": "info a", "source": "memory/a.md", "score": 0.8},
            {"text": "info b", "source": "memory/b.md", "score": 0.8},
            {"text": "info c", "source": "memory/c.md", "score": 0.8},
        ]
        q_diverse = rq.evaluate(diverse)
        q_single = rq.evaluate(single)
        self.assertGreater(q_diverse["diversity"], q_single["diversity"])


# ── Knowledge Graph ──────────────────────────────────────────────


class TestKnowledgeGraph(unittest.TestCase):

    def test_get_node(self):
        node = kg.get_node("gluetun")
        self.assertIsNotNone(node)
        self.assertEqual(node["type"], "container")
        self.assertEqual(node["role"], "vpn_gateway")

    def test_get_nodes_by_type(self):
        containers = kg.get_nodes_by_type("container")
        self.assertGreater(len(containers), 5)
        self.assertIn("sonarr", containers)

    def test_dependencies(self):
        deps = kg.get_dependencies("sonarr")
        self.assertIn("gluetun", deps)
        self.assertIn("prowlarr", deps)

    def test_dependents(self):
        dependents = kg.get_dependents("gluetun")
        self.assertIn("qbittorrent", dependents)
        self.assertIn("sonarr", dependents)

    def test_impact_analysis(self):
        impact = kg.get_impact("gluetun")
        self.assertTrue(impact["is_critical"])
        self.assertGreater(len(impact["direct_dependents"]), 3)

    def test_build_graph(self):
        tmpdir = tempfile.mkdtemp()
        orig = kg.GRAPH_FILE
        kg.GRAPH_FILE = Path(tmpdir) / "graph.json"
        kg.TELEMETRY_LOG = Path(tmpdir) / "log"
        graph = kg.build_graph()
        self.assertGreater(graph["node_count"], 10)
        self.assertGreater(graph["edge_count"], 10)
        kg.GRAPH_FILE = orig


# ── Failure Simulator ────────────────────────────────────────────


class TestFailureSimulator(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self._orig = fs.TELEMETRY_LOG
        fs.TELEMETRY_LOG = Path(self.tmpdir) / "log"

    def tearDown(self):
        fs.TELEMETRY_LOG = self._orig

    def test_crash_loop_scenario(self):
        sim = fs.simulate_crash_loop("sonarr")
        self.assertFalse(any(
            c["up"] for c in sim["state"]["containers"]
            if c["name"] == "sonarr"
        ))

    def test_network_outage(self):
        sim = fs.simulate_network_outage()
        self.assertFalse(sim["state"]["internet"]["ok"])

    def test_vpn_disconnect(self):
        sim = fs.simulate_vpn_disconnect()
        self.assertFalse(sim["state"]["vpn"]["ok"])

    def test_run_scenario(self):
        result = fs.run_scenario("network_outage")
        self.assertGreater(result["issues_count"], 0)
        self.assertGreater(result["critical"], 0)

    def test_unknown_scenario(self):
        result = fs.run_scenario("nonexistent")
        self.assertIn("error", result)

    def test_list_scenarios(self):
        scenarios = fs.list_scenarios()
        self.assertIn("crash_loop", scenarios)
        self.assertIn("vpn_disconnect", scenarios)


# ── Experiment Engine ────────────────────────────────────────────


class TestExperimentEngine(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self._orig_dir = ee.EXPERIMENTS_DIR
        self._orig_log = ee.TELEMETRY_LOG
        ee.EXPERIMENTS_DIR = Path(self.tmpdir) / "experiments"
        ee.TELEMETRY_LOG = Path(self.tmpdir) / "log"

    def tearDown(self):
        ee.EXPERIMENTS_DIR = self._orig_dir
        ee.TELEMETRY_LOG = self._orig_log

    def test_create_experiment(self):
        exp = ee.create_experiment(
            "test_exp",
            variants=[{"name": "A"}, {"name": "B"}],
            metric="confidence",
        )
        self.assertIn("id", exp)
        self.assertEqual(exp["status"], "created")

    def test_record_and_analyze(self):
        exp = ee.create_experiment("ab_test", [{"name": "A"}, {"name": "B"}])
        for _ in range(3):
            ee.record_result(exp["id"], "A", {"confidence": 0.9})
            ee.record_result(exp["id"], "B", {"confidence": 0.7})

        analysis = ee.analyze_experiment(exp["id"])
        self.assertIsNotNone(analysis)
        self.assertEqual(analysis["winner"], "A")

    def test_list_experiments(self):
        ee.create_experiment("exp1", [{"name": "X"}])
        ee.create_experiment("exp2", [{"name": "Y"}])
        exps = ee.list_experiments()
        self.assertEqual(len(exps), 2)


# ── Dreaming (REM memory consolidation) ─────────────────────────

from runtime import dreaming as dr


class TestDreaming(unittest.TestCase):

    def test_content_hash_deterministic(self):
        h1 = dr._content_hash("Sebastián prefiere respuestas cortas")
        h2 = dr._content_hash("Sebastián prefiere respuestas cortas")
        self.assertEqual(h1, h2)

    def test_content_hash_strips_whitespace(self):
        h1 = dr._content_hash("  truth  ")
        h2 = dr._content_hash("truth")
        self.assertEqual(h1, h2)

    def test_content_hash_case_insensitive(self):
        h1 = dr._content_hash("Some Truth")
        h2 = dr._content_hash("some truth")
        self.assertEqual(h1, h2)

    def test_content_hash_different_texts(self):
        h1 = dr._content_hash("truth one")
        h2 = dr._content_hash("truth two")
        self.assertNotEqual(h1, h2)

    def test_content_hash_length(self):
        h = dr._content_hash("test")
        self.assertEqual(len(h), 16)

    def test_write_to_dreams_no_duplicates(self):
        """Writing same truth twice should deduplicate."""
        existing = set()
        # Mock DREAMS_FILE to temp
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            tmp = Path(f.name)
        orig = dr.DREAMS_FILE
        try:
            dr.DREAMS_FILE = tmp
            new1 = dr._write_to_dreams(["test truth alpha"], existing)
            new2 = dr._write_to_dreams(["test truth alpha"], existing)
            self.assertEqual(len(new1), 1)
            self.assertEqual(len(new2), 0)
            # Verify file has content
            content = tmp.read_text()
            self.assertIn("test truth alpha", content)
            self.assertIn("<!-- hash:", content)
        finally:
            dr.DREAMS_FILE = orig
            tmp.unlink(missing_ok=True)

    def test_write_to_dreams_multiple_truths(self):
        existing = set()
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            tmp = Path(f.name)
        orig = dr.DREAMS_FILE
        try:
            dr.DREAMS_FILE = tmp
            new = dr._write_to_dreams(["truth A", "truth B", "truth C"], existing)
            self.assertEqual(len(new), 3)
            self.assertEqual(len(existing), 3)
        finally:
            dr.DREAMS_FILE = orig
            tmp.unlink(missing_ok=True)

    def test_load_existing_hashes_empty(self):
        orig = dr.DREAMS_FILE
        try:
            dr.DREAMS_FILE = Path("/tmp/nonexistent_dreams_test.md")
            hashes = dr._load_existing_hashes()
            self.assertEqual(hashes, set())
        finally:
            dr.DREAMS_FILE = orig

    def test_load_existing_hashes_parses_file(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=False) as f:
            f.write("## REM Session\n<!-- hash:abc123 -->\n- truth\n<!-- hash:def456 -->\n- truth2\n")
            tmp = Path(f.name)
        orig = dr.DREAMS_FILE
        try:
            dr.DREAMS_FILE = tmp
            hashes = dr._load_existing_hashes()
            self.assertIn("abc123", hashes)
            self.assertIn("def456", hashes)
            self.assertEqual(len(hashes), 2)
        finally:
            dr.DREAMS_FILE = orig
            tmp.unlink(missing_ok=True)

    def test_load_recent_episodic_missing_file(self):
        orig_log = dr.MEMORY_LOG_FILE
        orig_shared = dr.SHARED_MEMORY_FILE
        try:
            dr.MEMORY_LOG_FILE = Path("/tmp/nonexistent_mem.json")
            dr.SHARED_MEMORY_FILE = Path("/tmp/nonexistent_shared.json")
            entries = dr._load_recent_episodic(10)
            self.assertEqual(entries, [])
        finally:
            dr.MEMORY_LOG_FILE = orig_log
            dr.SHARED_MEMORY_FILE = orig_shared

    def test_load_recent_episodic_parses_events(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump([{
                "ts": "2026-04-08T10:00:00",
                "events": [{"type": "container_down", "target": "sonarr"}],
                "results": [{"action": "restart", "target": "sonarr", "ok": True}],
            }], f)
            tmp = Path(f.name)
        orig_log = dr.MEMORY_LOG_FILE
        orig_shared = dr.SHARED_MEMORY_FILE
        try:
            dr.MEMORY_LOG_FILE = tmp
            dr.SHARED_MEMORY_FILE = Path("/tmp/nonexistent_shared.json")
            entries = dr._load_recent_episodic(10)
            self.assertTrue(len(entries) >= 1)
            self.assertTrue(any("container_down" in e for e in entries))
        finally:
            dr.MEMORY_LOG_FILE = orig_log
            dr.SHARED_MEMORY_FILE = orig_shared
            tmp.unlink(missing_ok=True)

    def test_log_session_creates_file(self):
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
            tmp = Path(f.name)
        tmp.unlink()  # start fresh
        orig = dr.DREAM_LOG
        try:
            dr.DREAM_LOG = tmp
            dr._log_session(3, 2, 5.5)
            self.assertTrue(tmp.exists())
            data = json.loads(tmp.read_text())
            self.assertEqual(len(data), 1)
            self.assertEqual(data[0]["truths_found"], 3)
            self.assertEqual(data[0]["new_truths"], 2)
        finally:
            dr.DREAM_LOG = orig
            tmp.unlink(missing_ok=True)

    def test_log_session_caps_at_100(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            existing = [{"timestamp": "t", "truths_found": 0, "new_truths": 0,
                         "duration_seconds": 1} for _ in range(105)]
            json.dump(existing, f)
            tmp = Path(f.name)
        orig = dr.DREAM_LOG
        try:
            dr.DREAM_LOG = tmp
            dr._log_session(1, 1, 2.0)
            data = json.loads(tmp.read_text())
            self.assertEqual(len(data), 100)
        finally:
            dr.DREAM_LOG = orig
            tmp.unlink(missing_ok=True)

    def test_run_rem_no_episodic(self):
        """Should return skip if no episodic data."""
        orig_log = dr.MEMORY_LOG_FILE
        orig_shared = dr.SHARED_MEMORY_FILE
        try:
            dr.MEMORY_LOG_FILE = Path("/tmp/nonexistent_mem.json")
            dr.SHARED_MEMORY_FILE = Path("/tmp/nonexistent_shared.json")
            result = dr.run_rem_session(force=True)
            self.assertEqual(result["status"], "skip")
            self.assertEqual(result["reason"], "no_episodic_data")
        finally:
            dr.MEMORY_LOG_FILE = orig_log
            dr.SHARED_MEMORY_FILE = orig_shared


if __name__ == "__main__":
    unittest.main(verbosity=2)
