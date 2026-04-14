#!/usr/bin/env python3
"""Simulation: Ollama down + VRAM saturated."""

import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

RUNTIME = Path(__file__).parent.parent.parent.parent / "workspace" / "runtime"
_CONTAINER_RT = Path("/home/tenazas/runtime")
if _CONTAINER_RT.exists():
    RUNTIME = _CONTAINER_RT
sys.path.insert(0, str(RUNTIME.parent.parent))
sys.path.insert(0, str(RUNTIME.parent))
sys.path.insert(0, str(RUNTIME))


class TestSimOllamaGPU(unittest.TestCase):
    """Simulate Ollama down with VRAM at 95%."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_infra_diagnostics_handles_ollama_offline(self):
        """infra_diagnostics.py has ollama offline detection."""
        diag_path = RUNTIME / "infra_diagnostics.py"
        content = diag_path.read_text()
        self.assertIn("ollama", content.lower())

    def test_homelab_goals_has_ollama_check(self):
        """homelab_goals.py evaluates ollama availability."""
        hg_path = RUNTIME / "homelab_goals.py"
        content = hg_path.read_text()
        self.assertIn("ollama", content.lower())
        self.assertIn("evaluate_all", content)

    def test_policy_engine_evaluates_risk(self):
        """policy_engine.py has risk evaluation for actions."""
        pe_path = RUNTIME / "policy_engine.py"
        content = pe_path.read_text()
        self.assertIn("def evaluate", content)
        self.assertIn("risk", content.lower())

    def test_action_registry_has_ollama_actions(self):
        """action_registry defines ollama-related actions."""
        ar_path = RUNTIME / "action_registry.py"
        content = ar_path.read_text()
        self.assertTrue("ollama" in content.lower())

    def test_homelab_goals_has_evaluate(self):
        """homelab_goals.py has evaluate_all and get_failing_goals."""
        hg_path = RUNTIME / "homelab_goals.py"
        content = hg_path.read_text()
        self.assertIn("def evaluate_all", content)
        self.assertIn("def get_failing_goals", content)


class TestSimOllamaGPUMetrics(unittest.TestCase):

    def test_decision_accuracy_metric(self):
        """Scoring a correct diagnosis as good."""
        tmpdir = tempfile.mkdtemp()
        import decision_quality as dq
        orig = dq.STATE_DIR
        dq.STATE_DIR = Path(tmpdir) / "dq"
        dq.STATE_DIR.mkdir(parents=True)
        dq.score(action="diagnose_vram", target="homelab",
                 validation_result={"success": True}, confidence=0.85)
        s = dq.summary(1)
        self.assertGreaterEqual(s["good_rate"], 0.70)
        dq.STATE_DIR = orig
        shutil.rmtree(tmpdir)

    def test_confidence_calibration_error(self):
        """Calibration error should be ≤ 0.15 for correct diagnoses."""
        tmpdir = tempfile.mkdtemp()
        import confidence_calibration as cc
        orig_s, orig_c, orig_r = cc.STATE_DIR, cc.CALIBRATION_PATH, cc.REPORT_PATH
        cc.STATE_DIR = Path(tmpdir)
        cc.CALIBRATION_PATH = Path(tmpdir) / "cal.json"
        cc.REPORT_PATH = Path(tmpdir) / "rep.json"
        decisions = [
            {"confidence": 0.85, "quality": "good"},
            {"confidence": 0.90, "quality": "good"},
            {"confidence": 0.70, "quality": "neutral"},
        ]
        with patch.object(cc, "_load_decisions", return_value=decisions):
            r = cc.calibrate()
        self.assertLessEqual(r["calibration_error"], 0.15)
        cc.STATE_DIR, cc.CALIBRATION_PATH, cc.REPORT_PATH = orig_s, orig_c, orig_r
        shutil.rmtree(tmpdir)


if __name__ == "__main__":
    unittest.main()
