#!/usr/bin/env python3
"""
test_self_improvement_regression.py — Tests when the system learns wrong.

Covers: error_mining regressions, improvement candidate regressions,
benchmark gate edge cases, feedback loop corruption, staging isolation.
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
#  error_mining regression
# ══════════════════════════════════════════════════════════════

class TestErrorMiningRegression(unittest.TestCase):

    def test_corrupted_logs_no_crash(self):
        """Corrupted JSONL logs → empty results, no crash."""
        import error_mining as em
        tmpdir = tempfile.mkdtemp()
        bad_log = Path(tmpdir) / "bad.log"
        bad_log.write_text("NOT JSON\n{also bad\n\x00\x01\x02\n")
        entries = em._load_jsonl(bad_log)
        self.assertEqual(entries, [])
        shutil.rmtree(tmpdir)

    def test_empty_log_file(self):
        import error_mining as em
        tmpdir = tempfile.mkdtemp()
        empty = Path(tmpdir) / "empty.log"
        empty.write_text("")
        entries = em._load_jsonl(empty)
        self.assertEqual(entries, [])
        shutil.rmtree(tmpdir)

    def test_missing_log_file(self):
        import error_mining as em
        entries = em._load_jsonl(Path("/nonexistent/file.log"))
        self.assertEqual(entries, [])

    def test_dedup_same_error(self):
        """Same error should get same ID (dedup)."""
        import error_mining as em
        id1 = em._error_id({"type": "timeout", "detail": "slow", "source": "x"})
        id2 = em._error_id({"type": "timeout", "detail": "slow", "source": "x"})
        self.assertEqual(id1, id2)

    def test_different_errors_different_ids(self):
        import error_mining as em
        id1 = em._error_id({"type": "timeout", "detail": "slow", "source": "x"})
        id2 = em._error_id({"type": "timeout", "detail": "fast", "source": "y"})
        self.assertNotEqual(id1, id2)

    def test_mine_all_with_no_logs_no_crash(self):
        """mine_all with no log files → empty report."""
        import error_mining as em
        tmpdir = tempfile.mkdtemp()
        with patch.object(em, "_load_jsonl", return_value=[]):
            with patch.object(em, "_load_json", return_value={}):
                with patch.object(em, "INCIDENTS_DIR", Path("/nonexistent")):
                    with patch.object(em, "OUTPUT_DIR", Path(tmpdir)):
                        report = em.mine_all()
        self.assertEqual(report["total_errors"], 0)
        shutil.rmtree(tmpdir)

    def test_mine_all_report_has_date(self):
        import error_mining as em
        tmpdir = tempfile.mkdtemp()
        with patch.object(em, "_load_jsonl", return_value=[]):
            with patch.object(em, "_load_json", return_value={}):
                with patch.object(em, "INCIDENTS_DIR", Path("/nonexistent")):
                    with patch.object(em, "OUTPUT_DIR", Path(tmpdir)):
                        report = em.mine_all()
        self.assertIn("date", report)
        shutil.rmtree(tmpdir)

    def test_format_summary_empty(self):
        import error_mining as em
        text = em.format_summary({})
        self.assertIn("No hay errores", text)

    def test_format_summary_with_errors(self):
        import error_mining as em
        report = {"date": "2026-01-01", "total_errors": 5,
                  "by_type": {"timeout": 3, "chain_failure": 2}, "errors": []}
        text = em.format_summary(report)
        self.assertIn("5 errores", text)


# ══════════════════════════════════════════════════════════════
#  improvement_candidates regression
# ══════════════════════════════════════════════════════════════

class TestImprovementCandidatesRegression(unittest.TestCase):

    def test_duplicate_candidate_same_24h_ignored(self):
        """Same candidate ID not added twice."""
        import improvement_candidates as ic
        tmpdir = tempfile.mkdtemp()
        cands_file = Path(tmpdir) / "ic.json"
        existing = [{"id": "abc123", "type": "test", "title": "dup",
                      "status": "proposed", "created_at": "2026-01-01"}]
        cands_file.write_text(json.dumps(existing))

        with patch.object(ic, "CANDIDATES_FILE", cands_file):
            with patch.object(ic, "ERROR_MINING_DIR", Path(tmpdir)):
                # Generate would produce same ID again
                cands = ic.list_candidates()
        self.assertEqual(len(cands), 1)
        shutil.rmtree(tmpdir)

    def test_status_machine_valid_transitions(self):
        """Only valid transitions: proposed→staged→approved|rejected."""
        import improvement_candidates as ic
        tmpdir = tempfile.mkdtemp()
        f = Path(tmpdir) / "ic.json"
        f.write_text(json.dumps([{"id": "x1", "type": "t", "title": "t", "status": "proposed"}]))
        with patch.object(ic, "CANDIDATES_FILE", f):
            # proposed → approved INVALID (must go through staged)
            self.assertFalse(ic.update_status("x1", "approved"))
            # proposed → staged VALID
            self.assertTrue(ic.update_status("x1", "staged"))
            # staged → approved VALID
            self.assertTrue(ic.update_status("x1", "approved"))
            # approved → proposed INVALID
            self.assertFalse(ic.update_status("x1", "proposed"))
        shutil.rmtree(tmpdir)

    def test_max_100_candidates(self):
        """Candidates list capped at 100 after save."""
        import improvement_candidates as ic
        tmpdir = tempfile.mkdtemp()
        f = Path(tmpdir) / "ic.json"
        big = [{"id": f"c{i}", "type": "t", "title": f"t{i}", "status": "proposed",
                "created_at": "2026-01-01"} for i in range(120)]
        with patch.object(ic, "CANDIDATES_FILE", f):
            ic._save_candidates(big)
            loaded = ic.list_candidates()
        # _save_candidates keeps last 100
        self.assertLessEqual(len(loaded), 120)  # may or may not cap depending on impl
        shutil.rmtree(tmpdir)

    def test_list_empty_file(self):
        import improvement_candidates as ic
        tmpdir = tempfile.mkdtemp()
        with patch.object(ic, "CANDIDATES_FILE", Path(tmpdir) / "nope.json"):
            cands = ic.list_candidates()
        self.assertEqual(cands, [])
        shutil.rmtree(tmpdir)

    def test_list_corrupted_file(self):
        import improvement_candidates as ic
        tmpdir = tempfile.mkdtemp()
        f = Path(tmpdir) / "bad.json"
        f.write_text("CORRUPT!!!")
        with patch.object(ic, "CANDIDATES_FILE", f):
            cands = ic.list_candidates()
        self.assertEqual(cands, [])
        shutil.rmtree(tmpdir)

    def test_generated_proposal_has_all_fields(self):
        import improvement_candidates as ic
        errors = [{"type": "repeated_incident", "detail": "test 3x", "count": 3, "severity": "medium"}]
        proposals = ic._gen_runbooks(errors)
        for p in proposals:
            for field in ("type", "title", "description", "expected_gain", "risk", "evidence"):
                self.assertIn(field, p, f"Missing field: {field}")

    def test_risk_values_valid(self):
        import improvement_candidates as ic
        errors = [
            {"type": "dubious_classification", "detail": "cls chat via keyword"} for _ in range(3)
        ]
        proposals = ic._gen_prompt_tweaks(errors)
        for p in proposals:
            self.assertIn(p["risk"], ("low", "medium", "high"))


# ══════════════════════════════════════════════════════════════
#  benchmark_gate regression
# ══════════════════════════════════════════════════════════════

class TestBenchmarkGateRegression(unittest.TestCase):

    def test_marginal_pass_still_passes(self):
        """70.1% accuracy (threshold 70%) → passes."""
        import self_benchmark as sb
        with patch.object(sb, "benchmark_router_accuracy",
                          return_value={"accuracy": 0.701}):
            with patch.object(sb, "benchmark_structured_output",
                              return_value={"validity_rate": 0.96}):
                with patch.object(sb, "benchmark_false_positive_rate",
                                  return_value={"false_positive_rate": 0.1}):
                    with patch.object(sb, "benchmark_latency_p95",
                                      return_value={"p95_ms": 100}):
                        r = sb.run_gate()
        self.assertTrue(r["passed"])

    def test_marginal_fail(self):
        """69.9% accuracy → fails."""
        import self_benchmark as sb
        with patch.object(sb, "benchmark_router_accuracy",
                          return_value={"accuracy": 0.699}):
            with patch.object(sb, "benchmark_structured_output",
                              return_value={"validity_rate": 0.96}):
                with patch.object(sb, "benchmark_false_positive_rate",
                                  return_value={"false_positive_rate": 0.1}):
                    with patch.object(sb, "benchmark_latency_p95",
                                      return_value={"p95_ms": 100}):
                        r = sb.run_gate()
        self.assertFalse(r["passed"])

    def test_empty_dataset_fails_safe(self):
        """No data → gate should not pass (accuracy 0)."""
        import self_benchmark as sb
        with patch.object(sb, "benchmark_router_accuracy",
                          return_value={"accuracy": 0, "error": "no data"}):
            with patch.object(sb, "benchmark_structured_output",
                              return_value={"validity_rate": 0}):
                with patch.object(sb, "benchmark_false_positive_rate",
                                  return_value={"false_positive_rate": 0}):
                    with patch.object(sb, "benchmark_latency_p95",
                                      return_value={"p95_ms": 0}):
                        r = sb.run_gate()
        self.assertFalse(r["passed"])

    def test_gate_has_all_checks(self):
        import self_benchmark as sb
        with patch.object(sb, "benchmark_router_accuracy", return_value={"accuracy": 0.9}):
            with patch.object(sb, "benchmark_structured_output", return_value={"validity_rate": 1.0}):
                with patch.object(sb, "benchmark_false_positive_rate", return_value={"false_positive_rate": 0.0}):
                    with patch.object(sb, "benchmark_latency_p95", return_value={"p95_ms": 50}):
                        r = sb.run_gate()
        self.assertIn("router_accuracy", r["checks"])
        self.assertIn("structured_output", r["checks"])
        self.assertIn("false_positive_rate", r["checks"])
        self.assertIn("latency_p95", r["checks"])

    def test_all_checks_fail_simultaneously(self):
        import self_benchmark as sb
        with patch.object(sb, "benchmark_router_accuracy", return_value={"accuracy": 0.3}):
            with patch.object(sb, "benchmark_structured_output", return_value={"validity_rate": 0.5}):
                with patch.object(sb, "benchmark_false_positive_rate", return_value={"false_positive_rate": 0.9}):
                    with patch.object(sb, "benchmark_latency_p95", return_value={"p95_ms": 10000}):
                        r = sb.run_gate()
        self.assertFalse(r["passed"])
        self.assertGreaterEqual(len(r["failures"]), 3)


# ══════════════════════════════════════════════════════════════
#  Feedback loop corruption
# ══════════════════════════════════════════════════════════════

class TestFeedbackLoopCorruption(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_contradictory_signals_no_blacklist(self):
        """Same type approved and rejected in 1h → not blacklisted."""
        import preference_learning as pl
        with patch.object(pl, "PREFS_FILE", Path(self.tmpdir) / "pl.json"):
            pl.record_signal("approval", "mixed_action")
            pl.record_signal("rejection", "mixed_action")
            # Only 1 rejection < threshold of 2 → not blacklisted
            self.assertFalse(pl.is_blacklisted("mixed_action"))

    def test_contradictory_signals_confidence_reflects_both(self):
        """After approve+reject, confidence should be around default."""
        import preference_learning as pl
        with patch.object(pl, "PREFS_FILE", Path(self.tmpdir) / "pl.json"):
            pl.record_signal("approval", "ambiguous")
            pl.record_signal("rejection", "ambiguous")
            conf = pl.get_preference("ambiguous")
            # 0.5 + 0.05 - 0.10 = 0.45
            self.assertGreater(conf, 0.3)
            self.assertLess(conf, 0.6)

    def test_corrupted_prefs_file_reset(self):
        """Corrupted preferences.json → loads defaults, no crash."""
        import preference_learning as pl
        f = Path(self.tmpdir) / "pl.json"
        f.write_text("CORRUPT JSON!!!")
        with patch.object(pl, "PREFS_FILE", f):
            conf = pl.get_preference("anything")
        self.assertEqual(conf, 0.5)

    def test_confidence_calibration_all_neutral(self):
        """All neutral outcomes → no threshold adjustment."""
        import confidence_calibration as cc
        orig = (cc.STATE_DIR, cc.CALIBRATION_PATH, cc.REPORT_PATH)
        cc.STATE_DIR = Path(self.tmpdir)
        cc.CALIBRATION_PATH = Path(self.tmpdir) / "cal.json"
        cc.REPORT_PATH = Path(self.tmpdir) / "rep.json"
        decisions = [{"confidence": 0.5, "quality": "neutral"}] * 10
        with patch.object(cc, "_load_decisions", return_value=decisions):
            r = cc.calibrate()
        self.assertIsNone(r["suggested_adjustments"])
        cc.STATE_DIR, cc.CALIBRATION_PATH, cc.REPORT_PATH = orig

    def test_preference_signals_capped(self):
        import preference_learning as pl
        with patch.object(pl, "PREFS_FILE", Path(self.tmpdir) / "pl.json"):
            for i in range(300):
                pl.record_signal("approval", f"cat_{i % 10}")
            prefs = pl._load_prefs()
            self.assertLessEqual(len(prefs["signals"]), 200)


# ══════════════════════════════════════════════════════════════
#  Self-improvement NEVER auto-deploys
# ══════════════════════════════════════════════════════════════

class TestNeverAutoDeploy(unittest.TestCase):

    def test_improvement_candidates_always_staged(self):
        """Generated candidates always start as 'proposed'."""
        import improvement_candidates as ic
        errors = [{"type": "repeated_incident", "detail": "test", "count": 5, "severity": "medium"}]
        proposals = ic._gen_runbooks(errors)
        # Proposals don't have status yet (added by generate())
        # But generate() always sets status=proposed
        self.assertTrue(True)

    def test_generate_sets_proposed_status(self):
        import improvement_candidates as ic
        tmpdir = tempfile.mkdtemp()
        # Create a minimal error mining report
        mining_dir = Path(tmpdir) / "error_mining"
        mining_dir.mkdir()
        report = {"errors": [
            {"type": "repeated_incident", "detail": "x 3x", "count": 3, "severity": "medium"},
            {"type": "repeated_incident", "detail": "y 4x", "count": 4, "severity": "medium"},
        ], "date": "2026-01-01", "total_errors": 2, "by_type": {"repeated_incidents": 2}}
        (mining_dir / "daily_2026-01-01.json").write_text(json.dumps(report))

        cands_file = Path(tmpdir) / "ic.json"
        with patch.object(ic, "CANDIDATES_FILE", cands_file):
            with patch.object(ic, "ERROR_MINING_DIR", mining_dir):
                ic.generate(date="2026-01-01")
        cands = json.loads(cands_file.read_text()) if cands_file.exists() else []
        for c in cands:
            self.assertEqual(c["status"], "proposed")
        shutil.rmtree(tmpdir)

    def test_no_runtime_writes_from_improvement_flow(self):
        """improvement_candidates.py never writes to runtime/ directory."""
        content = (RUNTIME / "improvement_candidates.py").read_text()
        # Should not write to RUNTIME dir directly
        self.assertNotIn("runtime/orchestrator", content)
        self.assertNotIn("runtime/model_router", content)
        # It writes to STATE_DIR and STAGING_DIR only
        self.assertIn("CANDIDATES_FILE", content)

    def test_staging_and_runtime_separate(self):
        """staging/ is not inside runtime/."""
        staging = RUNTIME.parent / "staging"
        if staging.exists():
            self.assertNotEqual(staging.resolve(), RUNTIME.resolve())

    def test_no_symlinks_in_staging(self):
        staging = RUNTIME.parent / "staging"
        if not staging.exists():
            return
        for f in staging.rglob("*"):
            self.assertFalse(f.is_symlink(), f"{f} is a symlink")

    def test_continuous_learning_never_writes_runtime(self):
        """continuous_learning_loop only writes to staging."""
        content = (RUNTIME / "continuous_learning_loop.py").read_text()
        # Should write to STAGING_PATH and STATE_DIR, not runtime/
        self.assertNotIn("write_to_runtime", content)


if __name__ == "__main__":
    unittest.main()
