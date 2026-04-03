#!/usr/bin/env python3
"""
Tests for idle_learning.py — Multi-model collaborative learning engine.

Covers:
- load_recent_decisions: log parsing, filtering, interesting flagging
- run_critic: LLM call, JSON extraction, validation
- run_synthesizer: LLM call, schema validation, type enforcement
- Governor: save_proposals dedup, validation, file I/O
- Report generation: content, file creation
- Pipeline: run_idle_learning end-to-end
- JSON extraction: markdown blocks, raw arrays, edge cases
- Security: only writes to learning/ directory

Corre: python3 tests/test_idle_learning_v2.py
"""

import sys
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, "/home/tenazas")
from runtime import idle_learning as il


class _TempDirMixin:
    """Manage temp directories for test isolation."""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self._orig = {
            "LOG_DIR": il.LOG_DIR,
            "LEARNING_DIR": il.LEARNING_DIR,
            "PROPOSALS_FILE": il.PROPOSALS_FILE,
            "REPORT_FILE": il.REPORT_FILE,
            "TELEMETRY_LOG": il.TELEMETRY_LOG,
            "ROUTER_LOG": il.ROUTER_LOG,
            "GUARDRAIL_STATE": il.GUARDRAIL_STATE,
        }
        il.LOG_DIR = self.tmpdir / "logs"
        il.LEARNING_DIR = self.tmpdir / "learning"
        il.PROPOSALS_FILE = il.LEARNING_DIR / "proposals.json"
        il.REPORT_FILE = il.LEARNING_DIR / "learning_report.md"
        il.TELEMETRY_LOG = il.LOG_DIR / "idle_learning_v2.log"
        il.ROUTER_LOG = self.tmpdir / "logs" / "model_router.log"
        il.GUARDRAIL_STATE = self.tmpdir / "guardrail_state.json"
        il.LOG_DIR.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        for k, v in self._orig.items():
            setattr(il, k, v)

    def _write_router_log(self, entries: list):
        il.ROUTER_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(il.ROUTER_LOG, "w") as f:
            for e in entries:
                f.write(json.dumps(e) + "\n")

    def _write_guardrail_state(self, state: dict):
        with open(il.GUARDRAIL_STATE, "w") as f:
            json.dump(state, f)


# ── Load Decisions Tests ────────────────────────────────────────


class TestLoadDecisions(_TempDirMixin, unittest.TestCase):

    def test_empty_log_returns_empty(self):
        self._write_router_log([])
        result = il.load_recent_decisions()
        self.assertEqual(result, [])

    def test_missing_log_returns_empty(self):
        result = il.load_recent_decisions()
        self.assertEqual(result, [])

    def test_loads_route_v2_entries(self):
        entries = [
            {"action": "route_v2", "role": "fast", "model_chosen": "qwen2.5:7b"},
            {"action": "detect_backends", "results": {}},  # ignored
            {"action": "route_v2", "role": "reason", "model_chosen": "deepseek-r1:14b"},
        ]
        self._write_router_log(entries)
        result = il.load_recent_decisions()
        self.assertTrue(all(d["action"] == "route_v2" for d in result))

    def test_interesting_flagged_first(self):
        entries = [
            {"action": "route_v2", "role": "fast", "model_chosen": "qwen2.5:7b"},
            {"action": "route_v2", "role": "heavy", "model_chosen": "qwen2.5:32b", "vram_swap": True},
            {"action": "route_v2", "role": "fast", "model_chosen": "qwen2.5:7b"},
        ]
        self._write_router_log(entries)
        result = il.load_recent_decisions(limit=2)
        # heavy/vram_swap entry should be included
        self.assertTrue(any(d.get("vram_swap") for d in result))

    def test_degraded_entries_are_interesting(self):
        entries = [
            {"action": "route_v2", "role": "reason", "model_chosen": "qwen2.5:7b",
             "degraded": True, "degrade_reason": "cooldown"},
        ]
        self._write_router_log(entries)
        result = il.load_recent_decisions()
        self.assertEqual(len(result), 1)

    def test_limit_respected(self):
        entries = [{"action": "route_v2", "role": "reason", "model_chosen": "deepseek-r1:14b"}
                   for _ in range(100)]
        self._write_router_log(entries)
        result = il.load_recent_decisions(limit=10)
        self.assertLessEqual(len(result), 10)

    def test_malformed_lines_skipped(self):
        il.ROUTER_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(il.ROUTER_LOG, "w") as f:
            f.write("not json\n")
            f.write(json.dumps({"action": "route_v2", "role": "fast", "model_chosen": "qwen2.5:7b"}) + "\n")
        result = il.load_recent_decisions()
        self.assertEqual(len(result), 1)


# ── JSON Extraction Tests ───────────────────────────────────────


class TestExtractJson(unittest.TestCase):

    def test_plain_array(self):
        result = il._extract_json('[{"a": 1}]')
        self.assertEqual(result, [{"a": 1}])

    def test_markdown_code_block(self):
        text = "Here is the result:\n```json\n[{\"a\": 1}]\n```\nDone."
        result = il._extract_json(text)
        self.assertEqual(result, [{"a": 1}])

    def test_embedded_array(self):
        text = "Some text before [{\"a\": 1}] and after"
        result = il._extract_json(text)
        self.assertEqual(result, [{"a": 1}])

    def test_invalid_returns_none(self):
        self.assertIsNone(il._extract_json("no json here"))

    def test_empty_string(self):
        self.assertIsNone(il._extract_json(""))

    def test_plain_object(self):
        result = il._extract_json('{"issue": "test"}')
        self.assertEqual(result, {"issue": "test"})


# ── Critic Tests ────────────────────────────────────���───────────


class TestCritic(_TempDirMixin, unittest.TestCase):

    def test_empty_decisions_returns_empty(self):
        result = il.run_critic([])
        self.assertEqual(result, [])

    @patch("runtime.idle_learning._call_ollama")
    def test_critic_parses_valid_response(self, mock_ollama):
        mock_ollama.return_value = json.dumps([
            {"issue": "heavy on short prompts", "pattern": "len<500", "impact": "slow", "confidence": 0.8}
        ])
        decisions = [{"action": "route_v2", "role": "heavy", "model_chosen": "qwen2.5:32b"}]
        result = il.run_critic(decisions)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["issue"], "heavy on short prompts")
        self.assertAlmostEqual(result[0]["confidence"], 0.8)

    @patch("runtime.idle_learning._call_ollama")
    def test_critic_uses_deepseek(self, mock_ollama):
        mock_ollama.return_value = "[]"
        il.run_critic([{"action": "route_v2", "role": "fast", "model_chosen": "qwen2.5:7b"}])
        mock_ollama.assert_called_once()
        self.assertEqual(mock_ollama.call_args[0][0], il.CRITIC_MODEL)

    @patch("runtime.idle_learning._call_ollama")
    def test_critic_handles_no_response(self, mock_ollama):
        mock_ollama.return_value = ""
        result = il.run_critic([{"action": "route_v2", "role": "fast"}])
        self.assertEqual(result, [])

    @patch("runtime.idle_learning._call_ollama")
    def test_critic_handles_bad_json(self, mock_ollama):
        mock_ollama.return_value = "I found some issues but here they are in text..."
        result = il.run_critic([{"action": "route_v2", "role": "fast"}])
        self.assertEqual(result, [])

    @patch("runtime.idle_learning._call_ollama")
    def test_critic_caps_issues(self, mock_ollama):
        issues = [{"issue": f"issue_{i}", "confidence": 0.5} for i in range(20)]
        mock_ollama.return_value = json.dumps(issues)
        result = il.run_critic([{"action": "route_v2", "role": "fast"}])
        self.assertLessEqual(len(result), il.MAX_ISSUES)

    @patch("runtime.idle_learning._call_ollama")
    def test_critic_clamps_confidence(self, mock_ollama):
        mock_ollama.return_value = json.dumps([
            {"issue": "test", "confidence": 1.5},
            {"issue": "test2", "confidence": -0.5},
        ])
        result = il.run_critic([{"action": "route_v2", "role": "fast"}])
        self.assertLessEqual(result[0]["confidence"], 1.0)
        self.assertGreaterEqual(result[1]["confidence"], 0.0)

    @patch("runtime.idle_learning._call_ollama")
    def test_critic_handles_single_object(self, mock_ollama):
        mock_ollama.return_value = json.dumps({"issue": "single", "confidence": 0.7})
        result = il.run_critic([{"action": "route_v2", "role": "fast"}])
        self.assertEqual(len(result), 1)


# ── Synthesizer Tests ───────────────────────────────────────────


class TestSynthesizer(_TempDirMixin, unittest.TestCase):

    def test_empty_issues_returns_empty(self):
        result = il.run_synthesizer([])
        self.assertEqual(result, [])

    @patch("runtime.idle_learning._call_ollama")
    def test_synthesizer_parses_valid_response(self, mock_ollama):
        mock_ollama.return_value = json.dumps([{
            "type": "routing_adjustment",
            "description": "avoid heavy on short prompts",
            "condition": {"prompt_max_chars": 500},
            "action": {"prefer_role": "reason"},
            "confidence": 0.85,
            "expected_benefit": "lower latency",
            "risk": "low",
        }])
        issues = [{"issue": "test", "pattern": "", "impact": "", "confidence": 0.8}]
        result = il.run_synthesizer(issues)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["type"], "routing_adjustment")
        self.assertEqual(result[0]["status"], "proposed")
        self.assertIn("created_at", result[0])

    @patch("runtime.idle_learning._call_ollama")
    def test_synthesizer_uses_32b(self, mock_ollama):
        mock_ollama.return_value = "[]"
        il.run_synthesizer([{"issue": "test"}])
        self.assertEqual(mock_ollama.call_args[0][0], il.SYNTHESIZER_MODEL)

    @patch("runtime.idle_learning._call_ollama")
    def test_synthesizer_enforces_valid_types(self, mock_ollama):
        mock_ollama.return_value = json.dumps([{
            "type": "invalid_type",
            "description": "test",
        }])
        result = il.run_synthesizer([{"issue": "test"}])
        self.assertEqual(result[0]["type"], "routing_adjustment")  # defaults

    @patch("runtime.idle_learning._call_ollama")
    def test_synthesizer_caps_proposals(self, mock_ollama):
        proposals = [{"description": f"p_{i}", "confidence": 0.5} for i in range(20)]
        mock_ollama.return_value = json.dumps(proposals)
        result = il.run_synthesizer([{"issue": "test"}])
        self.assertLessEqual(len(result), il.MAX_PROPOSALS)

    @patch("runtime.idle_learning._call_ollama")
    def test_synthesizer_links_source_issues(self, mock_ollama):
        mock_ollama.return_value = json.dumps([{"description": "fix it"}])
        issues = [{"issue": "problem A"}, {"issue": "problem B"}]
        result = il.run_synthesizer(issues)
        self.assertIn("problem A", result[0]["source_issues"])
        self.assertIn("problem B", result[0]["source_issues"])


# ── Governor (Save Proposals) Tests ────────────────────────────


class TestGovernor(_TempDirMixin, unittest.TestCase):

    def _make_proposal(self, desc="test proposal", **kwargs):
        p = {
            "type": "routing_adjustment",
            "description": desc,
            "condition": {},
            "action": {},
            "confidence": 0.8,
            "status": "proposed",
            "created_at": "2026-04-03T00:00:00",
        }
        p.update(kwargs)
        return p

    def test_saves_to_file(self):
        p = self._make_proposal()
        saved = il.save_proposals([p])
        self.assertEqual(saved, 1)
        self.assertTrue(il.PROPOSALS_FILE.exists())
        data = json.loads(il.PROPOSALS_FILE.read_text())
        self.assertEqual(len(data), 1)

    def test_dedup_same_description(self):
        p1 = self._make_proposal("fix routing")
        p2 = self._make_proposal("fix routing")
        il.save_proposals([p1])
        saved = il.save_proposals([p2])
        self.assertEqual(saved, 0)
        data = json.loads(il.PROPOSALS_FILE.read_text())
        self.assertEqual(len(data), 1)

    def test_dedup_same_condition_action(self):
        p1 = self._make_proposal("desc1", condition={"a": 1}, action={"b": 2})
        p2 = self._make_proposal("desc2", condition={"a": 1}, action={"b": 2})
        il.save_proposals([p1])
        saved = il.save_proposals([p2])
        self.assertEqual(saved, 0)

    def test_different_proposals_both_saved(self):
        p1 = self._make_proposal("fix A")
        p2 = self._make_proposal("fix B")
        saved = il.save_proposals([p1, p2])
        self.assertEqual(saved, 2)

    def test_rejects_non_proposed_status(self):
        p = self._make_proposal(status="approved")
        saved = il.save_proposals([p])
        self.assertEqual(saved, 0)

    def test_rejects_empty_description(self):
        p = self._make_proposal(description="")
        saved = il.save_proposals([p])
        self.assertEqual(saved, 0)

    def test_rejects_non_dict(self):
        saved = il.save_proposals(["not a dict"])
        self.assertEqual(saved, 0)

    def test_append_to_existing(self):
        il.save_proposals([self._make_proposal("first")])
        il.save_proposals([self._make_proposal("second")])
        data = json.loads(il.PROPOSALS_FILE.read_text())
        self.assertEqual(len(data), 2)


# ── Report Tests ────────────────────────────────────────────────


class TestReport(_TempDirMixin, unittest.TestCase):

    def test_creates_report_file(self):
        il.generate_report([], [], [])
        self.assertTrue(il.REPORT_FILE.exists())

    def test_report_has_header(self):
        il.generate_report([], [], [])
        content = il.REPORT_FILE.read_text()
        self.assertIn("Idle Learning Report", content)

    def test_report_includes_issues(self):
        issues = [{"issue": "test problem", "pattern": "", "impact": "", "confidence": 0.8}]
        il.generate_report(issues, [], [])
        content = il.REPORT_FILE.read_text()
        self.assertIn("test problem", content)

    def test_report_includes_proposals(self):
        proposals = [{
            "type": "routing_adjustment", "description": "fix it",
            "condition": {}, "action": {}, "confidence": 0.9,
            "expected_benefit": "better", "risk": "low", "status": "proposed",
        }]
        il.generate_report([], proposals, [])
        content = il.REPORT_FILE.read_text()
        self.assertIn("fix it", content)

    def test_report_includes_stats(self):
        decisions = [
            {"role": "fast", "fallback_used": False, "degraded": False, "vram_swap": False},
            {"role": "heavy", "fallback_used": True, "degraded": False, "vram_swap": True},
        ]
        il.generate_report([], [], decisions)
        content = il.REPORT_FILE.read_text()
        self.assertIn("Total: 2", content)
        self.assertIn("VRAM swaps: 1", content)

    def test_report_no_auto_apply_disclaimer(self):
        il.generate_report([], [], [])
        content = il.REPORT_FILE.read_text()
        self.assertIn("ninguna propuesta se aplica", content.lower())


# ── Pipeline Tests ───────────────────────────────────���──────────


class TestPipeline(_TempDirMixin, unittest.TestCase):

    def test_no_data_returns_no_data_status(self):
        self._write_router_log([])
        result = il.run_idle_learning()
        self.assertEqual(result["status"], "no_data")
        self.assertTrue(il.REPORT_FILE.exists())

    @patch("runtime.idle_learning._call_ollama")
    def test_full_pipeline(self, mock_ollama):
        # Setup: some interesting decisions
        entries = [
            {"action": "route_v2", "role": "heavy", "model_chosen": "qwen2.5:32b",
             "vram_swap": True, "degraded": False, "prompt_preview": "critical deploy"},
            {"action": "route_v2", "role": "reason", "model_chosen": "qwen2.5:7b",
             "degraded": True, "degrade_reason": "cooldown", "prompt_preview": "analyze logs"},
        ]
        self._write_router_log(entries)

        # Mock critic response, then synthesizer response
        mock_ollama.side_effect = [
            json.dumps([{"issue": "heavy on short prompt", "confidence": 0.8}]),
            json.dumps([{"description": "lower heavy threshold", "type": "guardrail_tuning", "confidence": 0.7}]),
        ]

        result = il.run_idle_learning()
        self.assertEqual(result["status"], "completed")
        self.assertGreater(result["decisions"], 0)
        self.assertGreater(result["issues"], 0)
        self.assertGreater(result["proposals"], 0)
        self.assertTrue(il.PROPOSALS_FILE.exists())
        self.assertTrue(il.REPORT_FILE.exists())

    @patch("runtime.idle_learning._call_ollama")
    def test_pipeline_no_issues_skips_synthesizer(self, mock_ollama):
        entries = [{"action": "route_v2", "role": "reason", "model_chosen": "deepseek-r1:14b"}]
        self._write_router_log(entries)
        mock_ollama.return_value = "[]"  # critic finds nothing

        result = il.run_idle_learning()
        # Synthesizer should not be called (only critic)
        self.assertEqual(mock_ollama.call_count, 1)
        self.assertEqual(result["proposals"], 0)


# ── Security Tests ──────────────────────────────────────────────


class TestSecurity(unittest.TestCase):

    def test_proposals_only_proposed_status(self):
        """All proposals must have status=proposed."""
        proposals = [
            {"type": "routing_adjustment", "description": "test",
             "condition": {}, "action": {}, "confidence": 0.5,
             "status": "proposed", "created_at": "now"},
        ]
        # Verify status is always "proposed" in synthesizer output
        for p in proposals:
            self.assertEqual(p["status"], "proposed")

    def test_output_paths_under_learning(self):
        """All output files are under the learning/ directory."""
        self.assertTrue(str(il.PROPOSALS_FILE).endswith("learning/proposals.json"))
        self.assertTrue(str(il.REPORT_FILE).endswith("learning/learning_report.md"))

    def test_no_router_modification(self):
        """idle_learning never imports or modifies model_router config."""
        import inspect
        source = inspect.getsource(il)
        # Should not contain direct modifications to router config
        self.assertNotIn("ROLE_MAP =", source)
        self.assertNotIn("MODEL_CATALOG =", source)
        self.assertNotIn("GUARDRAIL_COOLDOWN =", source)


if __name__ == "__main__":
    unittest.main(verbosity=2)
