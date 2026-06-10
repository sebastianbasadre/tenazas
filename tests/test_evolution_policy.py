#!/usr/bin/env python3
"""
Tests for self-evolution policy and proposal executor gating.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

RUNTIME = Path(__file__).parent.parent / "workspace" / "runtime"
sys.path.insert(0, str(RUNTIME.parent))
sys.path.insert(0, str(RUNTIME))

import evolution_policy as ep
import proposal_executor as pe
from proposal_executors import ExecutionStatus, InMemorySandbox, Proposal, ResultCode


def _proposal(**overrides):
    raw = {
        "id": "prop_evo_1",
        "type": "threshold_change",
        "status": "accepted",
        "source": "self_debugger",
        "change_type": "bugfix",
        "risk": "low",
        "autonomy_eligible": True,
        "description": "Fix threshold drift",
        "evidence": ["test failure in runtime foo"],
        "expected_tests": ["python3 tests/test_foo.py"],
        "target_files": ["runtime/foo.py"],
        "test_command": "python3 tests/test_foo.py",
        "patch_spec": {
            "operation": "replace_constant",
            "file": "runtime/foo.py",
            "constant_name": "LIMIT",
            "old_value": 1,
            "new_value": 2,
            "value_type": "int",
        },
    }
    raw.update(overrides)
    return Proposal.from_dict(raw)


class TestEvolutionPolicy(unittest.TestCase):

    def test_missing_evidence_blocks_autonomous_proposal(self):
        decision = ep.evaluate_proposal(_proposal(evidence=[]))
        self.assertFalse(decision.allowed)
        self.assertTrue(decision.requires_approval)
        self.assertEqual(decision.reason, "missing_evidence")

    def test_protected_file_blocks_even_with_evidence(self):
        decision = ep.evaluate_proposal(_proposal(
            target_files=["runtime/policy_engine.py"],
            patch_spec={
                "operation": "replace_constant",
                "file": "runtime/policy_engine.py",
                "constant_name": "X",
                "old_value": 1,
                "new_value": 2,
                "value_type": "int",
            },
        ))
        self.assertFalse(decision.allowed)
        self.assertIn("runtime/policy_engine.py", decision.protected_files)

    def test_safe_docs_change_allows_l3_auto_merge(self):
        decision = ep.evaluate_proposal(_proposal(
            source="daily_improvement",
            change_type="docs",
            risk="safe",
            target_files=["docs/runbook.md"],
            patch_spec={"operation": "replace_constant", "file": "docs/runbook.md"},
        ))
        self.assertTrue(decision.allowed)
        self.assertTrue(decision.auto_merge_allowed)

    def test_branch_test_only_agent_requires_approval_after_tests(self):
        decision = ep.evaluate_proposal(_proposal(source="self_dev_engine"))
        self.assertTrue(decision.allowed)
        self.assertFalse(decision.auto_merge_allowed)
        self.assertEqual(decision.reason, "branch_test_only")

    def test_high_risk_is_manual_boundary(self):
        decision = ep.evaluate_proposal(_proposal(risk="high"))
        self.assertFalse(decision.allowed)
        self.assertIn("manual_boundary", decision.reason)


class TestProposalExecutorEvolutionGate(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="tenazas_evo_")
        os.makedirs(os.path.join(self.tmpdir, "runtime"), exist_ok=True)
        os.makedirs(os.path.join(self.tmpdir, "tests"), exist_ok=True)
        with open(os.path.join(self.tmpdir, "runtime/foo.py"), "w", encoding="utf-8") as f:
            f.write("LIMIT = 1\n")
        with open(os.path.join(self.tmpdir, "tests/test_foo.py"), "w", encoding="utf-8") as f:
            f.write("print('ok')\n")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_executor_classifies_missing_evidence_manual(self):
        mode, reason = pe.classify_execution_mode(_proposal(evidence=[]))
        self.assertEqual(mode, "manual_only")
        self.assertIn("evolution_policy:missing_evidence", reason)

    def test_executor_branch_test_only_does_not_merge(self):
        sandbox = InMemorySandbox(self.tmpdir)
        sandbox.main["runtime/foo.py"] = "LIMIT = 1\n"
        proposal = _proposal(source="self_dev_engine")
        result = pe.execute_proposal(
            proposal,
            repo_root=self.tmpdir,
            sandbox=sandbox,
            state={},
        )
        self.assertEqual(result.status, ExecutionStatus.SKIPPED)
        self.assertEqual(result.code, ResultCode.CLASSIFIED_MANUAL)
        self.assertTrue(result.tests_passed)
        self.assertTrue(result.rolled_back)
        self.assertEqual(sandbox.main["runtime/foo.py"], "LIMIT = 1\n")

    def test_executor_auto_merges_low_risk_self_debugger_fix(self):
        sandbox = InMemorySandbox(self.tmpdir)
        sandbox.main["runtime/foo.py"] = "LIMIT = 1\n"
        result = pe.execute_proposal(
            _proposal(),
            repo_root=self.tmpdir,
            sandbox=sandbox,
            state={},
        )
        self.assertEqual(result.status, ExecutionStatus.MERGED)
        self.assertEqual(result.code, ResultCode.OK)
        self.assertIn("LIMIT = 2", sandbox.main["runtime/foo.py"])

    def test_persist_writes_evolution_outcome_ledger(self):
        proposals_path = os.path.join(self.tmpdir, "learning/proposals.json")
        exec_log_path = os.path.join(self.tmpdir, "learning/execution_log.jsonl")
        state_path = os.path.join(self.tmpdir, "state/executor_state.json")
        outcome_path = os.path.join(self.tmpdir, "learning/evolution_outcomes.jsonl")
        raw = _proposal().to_dict()
        os.makedirs(os.path.dirname(proposals_path), exist_ok=True)
        with open(proposals_path, "w", encoding="utf-8") as f:
            json.dump([raw], f)

        sandbox = InMemorySandbox(self.tmpdir)
        sandbox.main["runtime/foo.py"] = "LIMIT = 1\n"
        prop = Proposal.from_dict(raw)
        state = {}
        result = pe.execute_proposal(
            prop,
            repo_root=self.tmpdir,
            sandbox=sandbox,
            state=state,
        )
        pe.persist_execution_result(
            prop,
            result,
            proposals_path=proposals_path,
            exec_log_path=exec_log_path,
            state_path=state_path,
            state=state,
            evolution_outcome_path=outcome_path,
        )

        self.assertTrue(os.path.exists(outcome_path))
        with open(outcome_path, encoding="utf-8") as f:
            entry = json.loads(f.readline())
        self.assertEqual(entry["proposal_id"], "prop_evo_1")
        self.assertEqual(entry["source"], "self_debugger")
        self.assertEqual(entry["status"], "merged")


if __name__ == "__main__":
    unittest.main()
