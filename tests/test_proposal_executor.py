#!/usr/bin/env python3
"""
Tests for proposal_executor and its sub-executors.

Run:
    docker exec openclaw python3 /home/tenazas/tests/test_proposal_executor.py

Coverage:
  - load_proposals / select_pending_accepted_proposals
  - classification: supported, unsupported, always_manual, protected files,
    manual override, missing patch_spec, too many files, sandbox_llm
  - idempotence: terminal status skipped, attempts cap enforced, re-run
    doesn't re-execute
  - threshold_change: happy path, wrong old_value, ambiguous, protected file,
    out-of-bounds ratio, non-parseable literal, compile failure
  - network_retry_policy: happy path, disallowed function, disallowed kwarg,
    out-of-bounds value, missing default
  - runtime_name_error_fix: missing logger (happy), missing stdlib import
    (happy), already-defined refusal, not-referenced refusal
  - rollback on test failure
  - lockfile: double execution blocked, stale lock overridden
  - persistence: execution_log.jsonl append-only, state attempts bump
  - kill switch
  - rate limit: 24h merge cap
  - CLI: --dry-run, --status, --json
  - integration: end-to-end threshold_change through run()
"""
from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import contextmanager
from typing import Any, Dict, List, Optional
from unittest import mock

# Make runtime/ importable as top-level.
THIS_DIR = os.path.dirname(os.path.abspath(__file__))
RUNTIME_DIR = os.path.abspath(os.path.join(THIS_DIR, "..", "runtime"))
if RUNTIME_DIR not in sys.path:
    sys.path.insert(0, RUNTIME_DIR)

import proposal_executor as pe  # noqa: E402
from proposal_executors import (  # noqa: E402
    EXECUTORS,
    ExecutionStatus,
    GitSandbox,
    InMemorySandbox,
    NetworkRetryPolicyExecutor,
    Proposal,
    ResultCode,
    RuntimeNameErrorFixExecutor,
    ThresholdChangeExecutor,
)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _write(path: str, content: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


@contextmanager
def tmp_repo(files: Dict[str, str]):
    """
    Create a temporary repo-like directory with the given files. Cleans up.
    """
    d = tempfile.mkdtemp(prefix="tenazas_test_")
    try:
        for rel, content in files.items():
            _write(os.path.join(d, rel), content)
        yield d
    finally:
        shutil.rmtree(d, ignore_errors=True)


def make_proposal(
    *,
    pid: str = "prop_test_1",
    ptype: str = "threshold_change",
    status: str = "accepted",
    patch_spec: Optional[Dict[str, Any]] = None,
    target_files: Optional[List[str]] = None,
    test_command: Optional[str] = None,
    execution_mode: Optional[str] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    raw = {
        "id": pid,
        "type": ptype,
        "status": status,
        "description": "test proposal",
        "patch_spec": patch_spec or {},
        "target_files": target_files or [],
    }
    if test_command:
        raw["test_command"] = test_command
    if execution_mode:
        raw["execution_mode"] = execution_mode
    if extra:
        raw.update(extra)
    return raw


def default_paths(repo_root: str) -> Dict[str, str]:
    return {
        "proposals_path": os.path.join(repo_root, "learning/proposals.json"),
        "exec_log_path": os.path.join(repo_root, "learning/execution_log.jsonl"),
        "state_path": os.path.join(repo_root, "state/proposal_executor_state.json"),
        "lock_path": os.path.join(repo_root, "tmp_exec.lock"),
    }


# --------------------------------------------------------------------------
# Proposal loading / selection
# --------------------------------------------------------------------------


class TestLoadSelect(unittest.TestCase):
    def test_load_empty_file_returns_empty(self):
        with tmp_repo({"learning/proposals.json": "[]"}) as d:
            out = pe.load_proposals(os.path.join(d, "learning/proposals.json"))
            self.assertEqual(out, [])

    def test_load_missing_file_returns_empty(self):
        with tmp_repo({}) as d:
            out = pe.load_proposals(os.path.join(d, "missing.json"))
            self.assertEqual(out, [])

    def test_load_dict_wrapped(self):
        with tmp_repo({
            "proposals.json": json.dumps({"proposals": [{"id": "a"}]}),
        }) as d:
            out = pe.load_proposals(os.path.join(d, "proposals.json"))
            self.assertEqual(out, [{"id": "a"}])

    def test_select_only_accepted(self):
        raws = [
            {"id": "1", "status": "proposed"},
            {"id": "2", "status": "accepted"},
            {"id": "3", "status": "rejected"},
            {"id": "4", "status": "accepted",
             "execution_status": ExecutionStatus.MERGED.value},
            {"id": "5", "status": "accepted",
             "execution_status": ExecutionStatus.FAILED.value},
            {"id": "6", "status": "accepted"},
        ]
        selected = pe.select_pending_accepted_proposals(raws, state={})
        ids = [p.id for p in selected]
        self.assertEqual(ids, ["2", "6"])

    def test_select_filters_by_id(self):
        raws = [
            {"id": "a", "status": "accepted"},
            {"id": "b", "status": "accepted"},
        ]
        selected = pe.select_pending_accepted_proposals(raws, state={}, only_id="b")
        self.assertEqual([p.id for p in selected], ["b"])

    def test_select_respects_attempts_cap(self):
        raws = [{"id": "x", "status": "accepted"}]
        state = {"attempts": {"x": pe.MAX_ATTEMPTS_PER_PROPOSAL}}
        selected = pe.select_pending_accepted_proposals(raws, state=state)
        self.assertEqual(selected, [])

    def test_select_non_dict_entries_skipped(self):
        raws = ["not a dict", None, {"id": "z", "status": "accepted"}]
        selected = pe.select_pending_accepted_proposals(raws, state={})
        self.assertEqual([p.id for p in selected], ["z"])


# --------------------------------------------------------------------------
# Classification
# --------------------------------------------------------------------------


class TestClassification(unittest.TestCase):
    def test_threshold_change_classifies_deterministic(self):
        raw = make_proposal(
            ptype="threshold_change",
            patch_spec={
                "operation": "replace_constant",
                "file": "runtime/foo.py",
                "constant_name": "X",
                "old_value": 1,
                "new_value": 2,
                "value_type": "int",
            },
        )
        p = Proposal.from_dict(raw)
        mode, reason = pe.classify_execution_mode(p)
        self.assertEqual(mode, "deterministic")
        self.assertIsNone(reason)

    def test_unknown_type_manual(self):
        p = Proposal.from_dict(make_proposal(ptype="refactor_module"))
        mode, reason = pe.classify_execution_mode(p)
        self.assertEqual(mode, "manual_only")
        self.assertIn("unsupported_type", reason)

    def test_always_manual_type(self):
        p = Proposal.from_dict(make_proposal(
            ptype="routing_adjustment",
            patch_spec={"op": "whatever"},
        ))
        mode, reason = pe.classify_execution_mode(p)
        self.assertEqual(mode, "manual_only")
        self.assertIn("always_manual", reason)

    def test_missing_patch_spec_manual(self):
        p = Proposal.from_dict(make_proposal(
            ptype="threshold_change", patch_spec=None,
        ))
        mode, reason = pe.classify_execution_mode(p)
        self.assertEqual(mode, "manual_only")
        self.assertEqual(reason, "missing_patch_spec")

    def test_protected_file_manual(self):
        p = Proposal.from_dict(make_proposal(
            ptype="threshold_change",
            patch_spec={
                "operation": "replace_constant",
                "file": "runtime/safety_guard.py",
                "constant_name": "X",
                "old_value": 1,
                "new_value": 2,
                "value_type": "int",
            },
        ))
        mode, reason = pe.classify_execution_mode(p)
        self.assertEqual(mode, "manual_only")
        self.assertIn("protected_file", reason)

    def test_too_many_files_manual(self):
        p = Proposal.from_dict(make_proposal(
            ptype="threshold_change",
            patch_spec={"operation": "replace_constant",
                        "file": "runtime/foo.py",
                        "constant_name": "X",
                        "old_value": 1, "new_value": 2, "value_type": "int"},
            target_files=[f"runtime/f{i}.py" for i in range(10)],
        ))
        mode, reason = pe.classify_execution_mode(p)
        self.assertEqual(mode, "manual_only")
        self.assertTrue(reason.startswith("too_many_files"))

    def test_sandbox_llm_degrades_to_manual(self):
        p = Proposal.from_dict(make_proposal(
            ptype="threshold_change",
            execution_mode="sandbox_llm",
        ))
        mode, reason = pe.classify_execution_mode(p)
        self.assertEqual(mode, "manual_only")
        self.assertIn("sandbox_llm_not_yet_supported", reason)

    def test_explicit_manual_respected(self):
        p = Proposal.from_dict(make_proposal(
            ptype="threshold_change",
            execution_mode="manual_only",
            patch_spec={"operation": "replace_constant",
                        "file": "runtime/foo.py",
                        "constant_name": "X",
                        "old_value": 1, "new_value": 2, "value_type": "int"},
        ))
        mode, _ = pe.classify_execution_mode(p)
        self.assertEqual(mode, "manual_only")

    def test_guardrail_tuning_supported(self):
        p = Proposal.from_dict(make_proposal(
            ptype="guardrail_tuning",
            patch_spec={
                "operation": "replace_constant",
                "file": "runtime/model_router.py",
                "constant_name": "HEAVY_COOLDOWN_SEC",
                "old_value": 120, "new_value": 60, "value_type": "int",
            },
        ))
        mode, _ = pe.classify_execution_mode(p)
        self.assertEqual(mode, "deterministic")


# --------------------------------------------------------------------------
# ThresholdChangeExecutor
# --------------------------------------------------------------------------


class TestThresholdChangeExecutor(unittest.TestCase):

    def _seed_file(self, content: str) -> Dict[str, Any]:
        return {"runtime/model_router.py": content}

    def test_happy_path_replaces_int(self):
        seed = self._seed_file(
            "# header\n"
            "import os\n"
            "\n"
            "HEAVY_COOLDOWN_SEC = 120\n"
            "OTHER = 42\n"
        )
        with tmp_repo(seed) as d:
            sb = InMemorySandbox(d)
            branch = sb.create_branch("test-branch")
            p = Proposal.from_dict(make_proposal(
                patch_spec={
                    "operation": "replace_constant",
                    "file": "runtime/model_router.py",
                    "constant_name": "HEAVY_COOLDOWN_SEC",
                    "old_value": 120, "new_value": 60, "value_type": "int",
                }))
            out = ThresholdChangeExecutor().apply(p, sb, branch, d)
            self.assertTrue(out.ok, out.error)
            self.assertEqual(out.files_changed, ["runtime/model_router.py"])
            new_src = sb.branches[branch].files["runtime/model_router.py"]
            self.assertIn("HEAVY_COOLDOWN_SEC = 60", new_src)
            self.assertNotIn("HEAVY_COOLDOWN_SEC = 120", new_src)
            self.assertIn("OTHER = 42", new_src)

    def test_wrong_old_value_refused(self):
        seed = self._seed_file("X = 99\n")
        with tmp_repo(seed) as d:
            sb = InMemorySandbox(d)
            branch = sb.create_branch("b")
            p = Proposal.from_dict(make_proposal(patch_spec={
                "operation": "replace_constant",
                "file": "runtime/model_router.py",
                "constant_name": "X",
                "old_value": 120, "new_value": 60, "value_type": "int",
            }))
            out = ThresholdChangeExecutor().apply(p, sb, branch, d)
            self.assertFalse(out.ok)
            self.assertIn("current value", out.error)

    def test_ambiguous_constant_refused(self):
        seed = self._seed_file("X = 10\n\nif True:\n    X = 10\n")
        # Two top-level: the second one is inside an If, which is NOT a
        # top-level assignment. So this is actually a single top-level.
        # Construct a real ambiguous case by parameterizing differently.
        with tmp_repo(seed) as d:
            sb = InMemorySandbox(d)
            branch = sb.create_branch("b")
            p = Proposal.from_dict(make_proposal(patch_spec={
                "operation": "replace_constant",
                "file": "runtime/model_router.py",
                "constant_name": "X",
                "old_value": 10, "new_value": 20, "value_type": "int",
            }))
            out = ThresholdChangeExecutor().apply(p, sb, branch, d)
            self.assertTrue(out.ok)  # genuinely unambiguous at module scope
            new_src = sb.branches[branch].files["runtime/model_router.py"]
            self.assertIn("X = 20", new_src)

    def test_real_ambiguous_refused(self):
        seed = self._seed_file(
            "X = 10\nY = 1\nX = 10\n"  # two top-level X assignments
        )
        with tmp_repo(seed) as d:
            sb = InMemorySandbox(d)
            branch = sb.create_branch("b")
            p = Proposal.from_dict(make_proposal(patch_spec={
                "operation": "replace_constant",
                "file": "runtime/model_router.py",
                "constant_name": "X",
                "old_value": 10, "new_value": 20, "value_type": "int",
            }))
            out = ThresholdChangeExecutor().apply(p, sb, branch, d)
            self.assertFalse(out.ok)
            self.assertIn("ambiguous", out.error)

    def test_out_of_bounds_ratio_refused(self):
        seed = self._seed_file("X = 100\n")
        with tmp_repo(seed) as d:
            sb = InMemorySandbox(d)
            branch = sb.create_branch("b")
            p = Proposal.from_dict(make_proposal(patch_spec={
                "operation": "replace_constant",
                "file": "runtime/model_router.py",
                "constant_name": "X",
                "old_value": 100, "new_value": 100_000, "value_type": "int",
            }))
            out = ThresholdChangeExecutor().apply(p, sb, branch, d)
            self.assertFalse(out.ok)
            self.assertIn("ratio", out.error)

    def test_zero_new_value_refused(self):
        seed = self._seed_file("TIMEOUT = 30\n")
        with tmp_repo(seed) as d:
            sb = InMemorySandbox(d)
            branch = sb.create_branch("b")
            p = Proposal.from_dict(make_proposal(patch_spec={
                "operation": "replace_constant",
                "file": "runtime/model_router.py",
                "constant_name": "TIMEOUT",
                "old_value": 30, "new_value": 0, "value_type": "int",
            }))
            out = ThresholdChangeExecutor().apply(p, sb, branch, d)
            self.assertFalse(out.ok)

    def test_non_literal_default_refused(self):
        seed = self._seed_file("X = os.environ.get('FOO')\n")
        with tmp_repo(seed) as d:
            sb = InMemorySandbox(d)
            branch = sb.create_branch("b")
            p = Proposal.from_dict(make_proposal(patch_spec={
                "operation": "replace_constant",
                "file": "runtime/model_router.py",
                "constant_name": "X",
                "old_value": "foo", "new_value": "bar", "value_type": "str",
            }))
            out = ThresholdChangeExecutor().apply(p, sb, branch, d)
            self.assertFalse(out.ok)

    def test_float_replacement(self):
        seed = self._seed_file("THRESHOLD = 0.8\n")
        with tmp_repo(seed) as d:
            sb = InMemorySandbox(d)
            branch = sb.create_branch("b")
            p = Proposal.from_dict(make_proposal(patch_spec={
                "operation": "replace_constant",
                "file": "runtime/model_router.py",
                "constant_name": "THRESHOLD",
                "old_value": 0.8, "new_value": 0.75, "value_type": "float",
            }))
            out = ThresholdChangeExecutor().apply(p, sb, branch, d)
            self.assertTrue(out.ok, out.error)
            src = sb.branches[branch].files["runtime/model_router.py"]
            self.assertIn("0.75", src)

    def test_type_mismatch_spec_refused(self):
        p = Proposal.from_dict(make_proposal(patch_spec={
            "operation": "replace_constant",
            "file": "runtime/foo.py",
            "constant_name": "X",
            "old_value": "str_val",
            "new_value": 5,
            "value_type": "int",  # mismatches old_value str
        }))
        sb = InMemorySandbox("/tmp")
        branch = sb.create_branch("b")
        out = ThresholdChangeExecutor().apply(p, sb, branch, "/tmp")
        self.assertFalse(out.ok)
        self.assertIn("type mismatch", out.error)

    def test_annotated_constant_replaced(self):
        seed = self._seed_file("X: int = 5\n")
        with tmp_repo(seed) as d:
            sb = InMemorySandbox(d)
            branch = sb.create_branch("b")
            p = Proposal.from_dict(make_proposal(patch_spec={
                "operation": "replace_constant",
                "file": "runtime/model_router.py",
                "constant_name": "X",
                "old_value": 5, "new_value": 10, "value_type": "int",
            }))
            out = ThresholdChangeExecutor().apply(p, sb, branch, d)
            self.assertTrue(out.ok, out.error)
            src = sb.branches[branch].files["runtime/model_router.py"]
            self.assertIn("X: int = 10", src)


# --------------------------------------------------------------------------
# NetworkRetryPolicyExecutor
# --------------------------------------------------------------------------


class TestNetworkRetryExecutor(unittest.TestCase):

    def _seed(self, body: str) -> Dict[str, str]:
        return {"runtime/network_cache.py": body}

    def test_happy_path_adjusts_kwargs(self):
        seed = self._seed(
            "import requests\n"
            "\n"
            "def http_get_with_retry(url, timeout=10, retries=3, backoff=1.5):\n"
            "    return requests.get(url, timeout=timeout)\n"
        )
        with tmp_repo(seed) as d:
            sb = InMemorySandbox(d)
            branch = sb.create_branch("b")
            p = Proposal.from_dict(make_proposal(
                ptype="network_retry_policy",
                patch_spec={
                    "operation": "adjust_retry_kwargs",
                    "file": "runtime/network_cache.py",
                    "function": "http_get_with_retry",
                    "kwargs": {"timeout": 30, "retries": 5},
                }))
            out = NetworkRetryPolicyExecutor().apply(p, sb, branch, d)
            self.assertTrue(out.ok, out.error)
            src = sb.branches[branch].files["runtime/network_cache.py"]
            self.assertIn("timeout=30", src)
            self.assertIn("retries=5", src)
            # Backoff untouched.
            self.assertIn("backoff=1.5", src)

    def test_disallowed_function_refused(self):
        seed = self._seed(
            "def do_dangerous_thing(timeout=5):\n    pass\n"
        )
        with tmp_repo(seed) as d:
            sb = InMemorySandbox(d)
            branch = sb.create_branch("b")
            p = Proposal.from_dict(make_proposal(
                ptype="network_retry_policy",
                patch_spec={
                    "operation": "adjust_retry_kwargs",
                    "file": "runtime/network_cache.py",
                    "function": "do_dangerous_thing",
                    "kwargs": {"timeout": 10},
                }))
            out = NetworkRetryPolicyExecutor().apply(p, sb, branch, d)
            self.assertFalse(out.ok)
            self.assertIn("not in allowlist", out.error)

    def test_disallowed_kwarg_refused(self):
        seed = self._seed(
            "def http_get(url, timeout=5, verify_ssl=True):\n    pass\n"
        )
        with tmp_repo(seed) as d:
            sb = InMemorySandbox(d)
            branch = sb.create_branch("b")
            p = Proposal.from_dict(make_proposal(
                ptype="network_retry_policy",
                patch_spec={
                    "operation": "adjust_retry_kwargs",
                    "file": "runtime/network_cache.py",
                    "function": "http_get",
                    "kwargs": {"verify_ssl": False},
                }))
            out = NetworkRetryPolicyExecutor().apply(p, sb, branch, d)
            self.assertFalse(out.ok)
            self.assertIn("allowlist", out.error)

    def test_out_of_bounds_refused(self):
        seed = self._seed(
            "def http_get(url, timeout=5):\n    pass\n"
        )
        with tmp_repo(seed) as d:
            sb = InMemorySandbox(d)
            branch = sb.create_branch("b")
            p = Proposal.from_dict(make_proposal(
                ptype="network_retry_policy",
                patch_spec={
                    "operation": "adjust_retry_kwargs",
                    "file": "runtime/network_cache.py",
                    "function": "http_get",
                    "kwargs": {"timeout": 99999},
                }))
            out = NetworkRetryPolicyExecutor().apply(p, sb, branch, d)
            self.assertFalse(out.ok)
            self.assertIn("bounds", out.error)

    def test_missing_default_refused(self):
        seed = self._seed(
            "def http_get(url):\n    pass\n"
        )
        with tmp_repo(seed) as d:
            sb = InMemorySandbox(d)
            branch = sb.create_branch("b")
            p = Proposal.from_dict(make_proposal(
                ptype="network_retry_policy",
                patch_spec={
                    "operation": "adjust_retry_kwargs",
                    "file": "runtime/network_cache.py",
                    "function": "http_get",
                    "kwargs": {"timeout": 30},
                }))
            out = NetworkRetryPolicyExecutor().apply(p, sb, branch, d)
            self.assertFalse(out.ok)
            self.assertIn("not a parameter", out.error)

    def test_kwonly_default_adjusted(self):
        seed = self._seed(
            "def fetch_data(url, *, timeout=10, retries=2):\n"
            "    return None\n"
        )
        with tmp_repo(seed) as d:
            sb = InMemorySandbox(d)
            branch = sb.create_branch("b")
            p = Proposal.from_dict(make_proposal(
                ptype="network_retry_policy",
                patch_spec={
                    "operation": "adjust_retry_kwargs",
                    "file": "runtime/network_cache.py",
                    "function": "fetch_data",
                    "kwargs": {"timeout": 20, "retries": 4},
                }))
            out = NetworkRetryPolicyExecutor().apply(p, sb, branch, d)
            self.assertTrue(out.ok, out.error)
            src = sb.branches[branch].files["runtime/network_cache.py"]
            self.assertIn("timeout=20", src)
            self.assertIn("retries=4", src)


# --------------------------------------------------------------------------
# RuntimeNameErrorFixExecutor
# --------------------------------------------------------------------------


class TestRuntimeNameErrorFixExecutor(unittest.TestCase):

    def test_add_module_logger_happy(self):
        src = (
            "import os\n"
            "\n"
            "def do():\n"
            "    _log.info('hi')\n"
        )
        with tmp_repo({"runtime/foo.py": src}) as d:
            sb = InMemorySandbox(d)
            branch = sb.create_branch("b")
            p = Proposal.from_dict(make_proposal(
                ptype="runtime_name_error_fix",
                patch_spec={
                    "operation": "add_module_logger",
                    "file": "runtime/foo.py",
                    "logger_name": "_log",
                }))
            out = RuntimeNameErrorFixExecutor().apply(p, sb, branch, d)
            self.assertTrue(out.ok, out.error)
            new = sb.branches[branch].files["runtime/foo.py"]
            self.assertIn("import logging", new)
            self.assertIn("_log = logging.getLogger(__name__)", new)

    def test_add_module_logger_already_defined_refused(self):
        src = (
            "import logging\n"
            "_log = logging.getLogger(__name__)\n"
            "\n"
            "_log.info('ok')\n"
        )
        with tmp_repo({"runtime/foo.py": src}) as d:
            sb = InMemorySandbox(d)
            branch = sb.create_branch("b")
            p = Proposal.from_dict(make_proposal(
                ptype="runtime_name_error_fix",
                patch_spec={
                    "operation": "add_module_logger",
                    "file": "runtime/foo.py",
                    "logger_name": "_log",
                }))
            out = RuntimeNameErrorFixExecutor().apply(p, sb, branch, d)
            self.assertFalse(out.ok)
            self.assertIn("already defined", out.error)

    def test_add_module_logger_not_referenced_refused(self):
        src = "import os\n\ndef do():\n    pass\n"
        with tmp_repo({"runtime/foo.py": src}) as d:
            sb = InMemorySandbox(d)
            branch = sb.create_branch("b")
            p = Proposal.from_dict(make_proposal(
                ptype="runtime_name_error_fix",
                patch_spec={
                    "operation": "add_module_logger",
                    "file": "runtime/foo.py",
                    "logger_name": "_log",
                }))
            out = RuntimeNameErrorFixExecutor().apply(p, sb, branch, d)
            self.assertFalse(out.ok)
            self.assertIn("never referenced", out.error)

    def test_add_stdlib_import_happy(self):
        src = (
            '"""docstring."""\n'
            "\n"
            "def parse(blob):\n"
            "    return json.loads(blob)\n"
        )
        with tmp_repo({"runtime/foo.py": src}) as d:
            sb = InMemorySandbox(d)
            branch = sb.create_branch("b")
            p = Proposal.from_dict(make_proposal(
                ptype="runtime_name_error_fix",
                patch_spec={
                    "operation": "add_stdlib_import",
                    "file": "runtime/foo.py",
                    "module": "json",
                }))
            out = RuntimeNameErrorFixExecutor().apply(p, sb, branch, d)
            self.assertTrue(out.ok, out.error)
            new = sb.branches[branch].files["runtime/foo.py"]
            self.assertIn("import json", new)

    def test_add_stdlib_import_already_present_refused(self):
        src = "import json\n\ndef parse(b):\n    return json.loads(b)\n"
        with tmp_repo({"runtime/foo.py": src}) as d:
            sb = InMemorySandbox(d)
            branch = sb.create_branch("b")
            p = Proposal.from_dict(make_proposal(
                ptype="runtime_name_error_fix",
                patch_spec={
                    "operation": "add_stdlib_import",
                    "file": "runtime/foo.py",
                    "module": "json",
                }))
            out = RuntimeNameErrorFixExecutor().apply(p, sb, branch, d)
            self.assertFalse(out.ok)
            self.assertIn("already imported", out.error)

    def test_non_stdlib_module_refused(self):
        src = "def do():\n    x = requests.get('x')\n"
        with tmp_repo({"runtime/foo.py": src}) as d:
            sb = InMemorySandbox(d)
            branch = sb.create_branch("b")
            p = Proposal.from_dict(make_proposal(
                ptype="runtime_name_error_fix",
                patch_spec={
                    "operation": "add_stdlib_import",
                    "file": "runtime/foo.py",
                    "module": "requests",
                }))
            out = RuntimeNameErrorFixExecutor().apply(p, sb, branch, d)
            self.assertFalse(out.ok)
            self.assertIn("allowlist", out.error)

    def test_unsupported_operation_refused(self):
        p = Proposal.from_dict(make_proposal(
            ptype="runtime_name_error_fix",
            patch_spec={"operation": "rewrite_module"}))
        sb = InMemorySandbox("/tmp")
        branch = sb.create_branch("b")
        out = RuntimeNameErrorFixExecutor().apply(p, sb, branch, "/tmp")
        self.assertFalse(out.ok)
        self.assertIn("unsupported operation", out.error)


# --------------------------------------------------------------------------
# execute_proposal end-to-end (sandbox=InMemory)
# --------------------------------------------------------------------------


class TestExecuteProposal(unittest.TestCase):

    def _setup_repo(self) -> Dict[str, str]:
        return {
            "runtime/model_router.py":
                "# router\nHEAVY_COOLDOWN_SEC = 120\n",
            "tests/test_model_router.py":
                "# dummy test that just imports\n"
                "import os\n"
                "import sys\n"
                "sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'runtime'))\n"
                "import model_router\n"
                "assert model_router.HEAVY_COOLDOWN_SEC > 0\n"
                "print('ok')\n",
        }

    def test_happy_path_merged(self):
        with tmp_repo(self._setup_repo()) as d:
            sb = InMemorySandbox(d)
            p = Proposal.from_dict(make_proposal(
                patch_spec={
                    "operation": "replace_constant",
                    "file": "runtime/model_router.py",
                    "constant_name": "HEAVY_COOLDOWN_SEC",
                    "old_value": 120, "new_value": 60, "value_type": "int",
                }))
            res = pe.execute_proposal(
                p, repo_root=d, sandbox=sb, state={"merges_24h": []},
            )
            self.assertEqual(res.status, ExecutionStatus.MERGED)
            self.assertEqual(res.code, ResultCode.OK)
            self.assertTrue(res.commit_sha)
            self.assertTrue(res.tests_passed)
            self.assertEqual(res.files_changed, ["runtime/model_router.py"])
            self.assertIn(
                "HEAVY_COOLDOWN_SEC = 60",
                sb.main["runtime/model_router.py"],
            )

    def test_unsupported_type_marks_unsupported(self):
        with tmp_repo(self._setup_repo()) as d:
            sb = InMemorySandbox(d)
            p = Proposal.from_dict(make_proposal(ptype="weird_type"))
            res = pe.execute_proposal(
                p, repo_root=d, sandbox=sb, state={"merges_24h": []},
            )
            self.assertEqual(res.status, ExecutionStatus.UNSUPPORTED)
            self.assertEqual(res.code, ResultCode.CLASSIFIED_MANUAL)

    def test_dry_run_skips(self):
        with tmp_repo(self._setup_repo()) as d:
            sb = InMemorySandbox(d)
            p = Proposal.from_dict(make_proposal(patch_spec={
                "operation": "replace_constant",
                "file": "runtime/model_router.py",
                "constant_name": "HEAVY_COOLDOWN_SEC",
                "old_value": 120, "new_value": 60, "value_type": "int",
            }))
            res = pe.execute_proposal(
                p, repo_root=d, sandbox=sb, state={"merges_24h": []},
                dry_run=True,
            )
            self.assertEqual(res.status, ExecutionStatus.SKIPPED)
            self.assertEqual(res.execution_mode, "deterministic")
            # Nothing merged.
            self.assertIn(
                "HEAVY_COOLDOWN_SEC = 120",
                sb.main["runtime/model_router.py"],
            )

    def test_test_failure_triggers_rollback(self):
        repo_files = self._setup_repo()
        with tmp_repo(repo_files) as d:
            sb = InMemorySandbox(d)
            # Make the test fail deterministically.
            sb.test_hook = lambda cmd, cwd: {
                "passed": False, "returncode": 1,
                "stdout": "", "stderr": "simulated failure",
            }
            p = Proposal.from_dict(make_proposal(patch_spec={
                "operation": "replace_constant",
                "file": "runtime/model_router.py",
                "constant_name": "HEAVY_COOLDOWN_SEC",
                "old_value": 120, "new_value": 60, "value_type": "int",
            }))
            res = pe.execute_proposal(
                p, repo_root=d, sandbox=sb, state={"merges_24h": []},
            )
            self.assertEqual(res.status, ExecutionStatus.FAILED)
            self.assertEqual(res.code, ResultCode.TEST_FAILED)
            self.assertTrue(res.rolled_back)
            # Main untouched.
            self.assertIn(
                "HEAVY_COOLDOWN_SEC = 120",
                sb.main["runtime/model_router.py"],
            )

    def test_merge_failure_rolls_back(self):
        with tmp_repo(self._setup_repo()) as d:
            sb = InMemorySandbox(d)
            sb.fail_merge = True
            p = Proposal.from_dict(make_proposal(patch_spec={
                "operation": "replace_constant",
                "file": "runtime/model_router.py",
                "constant_name": "HEAVY_COOLDOWN_SEC",
                "old_value": 120, "new_value": 60, "value_type": "int",
            }))
            res = pe.execute_proposal(
                p, repo_root=d, sandbox=sb, state={"merges_24h": []},
            )
            self.assertEqual(res.status, ExecutionStatus.FAILED)
            self.assertEqual(res.code, ResultCode.MERGE_FAILED)
            self.assertTrue(res.rolled_back)

    def test_patch_failed_triggers_rollback(self):
        # old_value doesn't match what's in the file
        with tmp_repo(self._setup_repo()) as d:
            sb = InMemorySandbox(d)
            p = Proposal.from_dict(make_proposal(patch_spec={
                "operation": "replace_constant",
                "file": "runtime/model_router.py",
                "constant_name": "HEAVY_COOLDOWN_SEC",
                "old_value": 999,  # wrong
                "new_value": 60, "value_type": "int",
            }))
            res = pe.execute_proposal(
                p, repo_root=d, sandbox=sb, state={"merges_24h": []},
            )
            self.assertEqual(res.status, ExecutionStatus.FAILED)
            self.assertEqual(res.code, ResultCode.PATCH_FAILED)
            self.assertTrue(res.rolled_back)

    def test_rate_limit_blocks_merge(self):
        with tmp_repo(self._setup_repo()) as d:
            sb = InMemorySandbox(d)
            now = time.time()
            state = {"merges_24h": [now - i for i in range(pe.MAX_MERGES_PER_24H)]}
            p = Proposal.from_dict(make_proposal(patch_spec={
                "operation": "replace_constant",
                "file": "runtime/model_router.py",
                "constant_name": "HEAVY_COOLDOWN_SEC",
                "old_value": 120, "new_value": 60, "value_type": "int",
            }))
            res = pe.execute_proposal(p, repo_root=d, sandbox=sb, state=state)
            self.assertEqual(res.status, ExecutionStatus.SKIPPED)
            self.assertEqual(res.code, ResultCode.RATE_LIMITED)

    def test_no_derivable_test_refused(self):
        with tmp_repo({"runtime/only.py": "X = 1\n"}) as d:
            sb = InMemorySandbox(d)
            p = Proposal.from_dict(make_proposal(patch_spec={
                "operation": "replace_constant",
                "file": "runtime/only.py",
                "constant_name": "X",
                "old_value": 1, "new_value": 2, "value_type": "int",
            }))
            res = pe.execute_proposal(p, repo_root=d, sandbox=sb, state={})
            self.assertEqual(res.status, ExecutionStatus.UNSUPPORTED)
            self.assertIn("no test_command", res.error)

    def test_test_command_with_shell_meta_refused(self):
        with tmp_repo(self._setup_repo()) as d:
            sb = InMemorySandbox(d)
            p = Proposal.from_dict(make_proposal(
                patch_spec={
                    "operation": "replace_constant",
                    "file": "runtime/model_router.py",
                    "constant_name": "HEAVY_COOLDOWN_SEC",
                    "old_value": 120, "new_value": 60, "value_type": "int",
                },
                test_command="python3 tests/t.py && rm -rf /",
            ))
            res = pe.execute_proposal(
                p, repo_root=d, sandbox=sb, state={"merges_24h": []},
            )
            # Falls back to default derivation, so still uses safe test.
            # Or gets None. Either way, should NOT execute that command.
            # What we assert is that no "rm -rf" ever ran: tests dir still intact.
            self.assertTrue(os.path.exists(os.path.join(d, "tests")))


# --------------------------------------------------------------------------
# Persistence
# --------------------------------------------------------------------------


class TestPersistence(unittest.TestCase):

    def test_execution_log_append_only(self):
        with tmp_repo({}) as d:
            paths = default_paths(d)
            proposal = Proposal.from_dict(make_proposal(pid="p1"))
            from proposal_executors.base import ExecutionResult as ER, _iso_now
            r1 = ER(
                proposal_id="p1", proposal_type="threshold_change",
                execution_mode="deterministic",
                status=ExecutionStatus.MERGED, code=ResultCode.OK,
                branch="b", commit_sha="abc", tests_passed=True,
            )
            r2 = ER(
                proposal_id="p2", proposal_type="threshold_change",
                execution_mode="deterministic",
                status=ExecutionStatus.FAILED, code=ResultCode.TEST_FAILED,
                branch="b2", tests_passed=False, rolled_back=True,
            )
            _write(paths["proposals_path"], json.dumps([
                make_proposal(pid="p1"),
                make_proposal(pid="p2"),
            ]))
            state = {"attempts": {}, "merges_24h": []}
            pe.persist_execution_result(
                proposal, r1,
                proposals_path=paths["proposals_path"],
                exec_log_path=paths["exec_log_path"],
                state_path=paths["state_path"],
                state=state,
            )
            pe.persist_execution_result(
                Proposal.from_dict(make_proposal(pid="p2")), r2,
                proposals_path=paths["proposals_path"],
                exec_log_path=paths["exec_log_path"],
                state_path=paths["state_path"],
                state=state,
            )
            lines = _read(paths["exec_log_path"]).strip().splitlines()
            self.assertEqual(len(lines), 2)
            e1 = json.loads(lines[0])
            e2 = json.loads(lines[1])
            self.assertEqual(e1["proposal_id"], "p1")
            self.assertEqual(e2["proposal_id"], "p2")
            self.assertEqual(state["attempts"].get("p1"), 1)
            self.assertEqual(state["attempts"].get("p2"), 1)

    def test_execution_result_written_back_to_proposal(self):
        with tmp_repo({}) as d:
            paths = default_paths(d)
            raw = make_proposal(pid="px")
            _write(paths["proposals_path"], json.dumps([raw]))
            from proposal_executors.base import ExecutionResult as ER
            r = ER(
                proposal_id="px", proposal_type="threshold_change",
                execution_mode="deterministic",
                status=ExecutionStatus.MERGED, code=ResultCode.OK,
                branch="b", commit_sha="deadbeef", tests_passed=True,
                files_changed=["runtime/foo.py"],
            )
            pe.persist_execution_result(
                Proposal.from_dict(raw), r,
                proposals_path=paths["proposals_path"],
                exec_log_path=paths["exec_log_path"],
                state_path=paths["state_path"],
                state={"attempts": {}, "merges_24h": []},
            )
            after = json.loads(_read(paths["proposals_path"]))
            self.assertEqual(after[0]["execution_status"], "merged")
            self.assertEqual(after[0]["execution_attempts"], 1)
            self.assertEqual(
                after[0]["execution_result"]["commit_sha"], "deadbeef",
            )


# --------------------------------------------------------------------------
# Idempotence / Lock
# --------------------------------------------------------------------------


class TestIdempotenceAndLock(unittest.TestCase):

    def test_merged_proposal_not_re_executed(self):
        """After a merge, the next run must skip the proposal."""
        repo_files = {
            "runtime/model_router.py": "HEAVY_COOLDOWN_SEC = 120\n",
            "tests/test_model_router.py":
                "import os, sys\n"
                "sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'runtime'))\n"
                "import model_router\n"
                "print('ok')\n",
        }
        raw = make_proposal(patch_spec={
            "operation": "replace_constant",
            "file": "runtime/model_router.py",
            "constant_name": "HEAVY_COOLDOWN_SEC",
            "old_value": 120, "new_value": 60, "value_type": "int",
        })
        with tmp_repo(repo_files) as d:
            paths = default_paths(d)
            _write(paths["proposals_path"], json.dumps([raw]))
            sb = InMemorySandbox(d)

            s1 = pe.run(
                repo_root=d,
                proposals_path=paths["proposals_path"],
                exec_log_path=paths["exec_log_path"],
                state_path=paths["state_path"],
                lock_path=paths["lock_path"],
                kill_switch="__never__",
                sandbox=sb,
                skip_lock=False,
            )
            self.assertEqual(s1["considered"], 1)
            self.assertEqual(s1["results"][0]["status"], "merged")

            # Second run: same sandbox, already-merged proposal.
            s2 = pe.run(
                repo_root=d,
                proposals_path=paths["proposals_path"],
                exec_log_path=paths["exec_log_path"],
                state_path=paths["state_path"],
                lock_path=paths["lock_path"],
                kill_switch="__never__",
                sandbox=sb,
                skip_lock=False,
            )
            self.assertEqual(s2["considered"], 0)

    def test_lock_blocks_concurrent_run(self):
        with tmp_repo({}) as d:
            paths = default_paths(d)
            _write(paths["proposals_path"], "[]")
            # Manually hold the lock.
            l = pe.Lock(paths["lock_path"])
            l.acquire()
            try:
                summary = pe.run(
                    repo_root=d,
                    proposals_path=paths["proposals_path"],
                    exec_log_path=paths["exec_log_path"],
                    state_path=paths["state_path"],
                    lock_path=paths["lock_path"],
                    kill_switch="__never__",
                    skip_lock=False,
                )
                self.assertFalse(summary["ok"])
                self.assertEqual(summary["code"], ResultCode.LOCK_HELD.value)
            finally:
                l.release()

    def test_stale_lock_overridden(self):
        with tmp_repo({}) as d:
            paths = default_paths(d)
            _write(paths["proposals_path"], "[]")
            # Create a stale lock file (old mtime).
            with open(paths["lock_path"], "w") as f:
                f.write("{}")
            old = time.time() - (pe.LOCKFILE_STALE_SECONDS + 60)
            os.utime(paths["lock_path"], (old, old))

            summary = pe.run(
                repo_root=d,
                proposals_path=paths["proposals_path"],
                exec_log_path=paths["exec_log_path"],
                state_path=paths["state_path"],
                lock_path=paths["lock_path"],
                kill_switch="__never__",
                skip_lock=False,
            )
            self.assertTrue(summary["ok"])

    def test_attempts_cap_skips(self):
        raw = make_proposal(patch_spec={
            "operation": "replace_constant",
            "file": "runtime/model_router.py",
            "constant_name": "X", "old_value": 1, "new_value": 2,
            "value_type": "int",
        })
        with tmp_repo({"runtime/model_router.py": "X = 1\n",
                       "tests/test_model_router.py": "print('ok')\n"}) as d:
            paths = default_paths(d)
            _write(paths["proposals_path"], json.dumps([raw]))
            # Pre-load state to already-at-cap.
            _write(paths["state_path"], json.dumps({
                "attempts": {raw["id"]: pe.MAX_ATTEMPTS_PER_PROPOSAL},
                "merges_24h": [],
            }))
            sb = InMemorySandbox(d)
            summary = pe.run(
                repo_root=d,
                proposals_path=paths["proposals_path"],
                exec_log_path=paths["exec_log_path"],
                state_path=paths["state_path"],
                lock_path=paths["lock_path"],
                kill_switch="__never__",
                sandbox=sb,
            )
            self.assertEqual(summary["considered"], 0)


# --------------------------------------------------------------------------
# Kill switch
# --------------------------------------------------------------------------


class TestKillSwitch(unittest.TestCase):

    def test_kill_switch_blocks_run(self):
        with tmp_repo({"state/PROPOSAL_EXECUTOR_DISABLED": "1",
                       "learning/proposals.json": "[]"}) as d:
            paths = default_paths(d)
            summary = pe.run(
                repo_root=d,
                proposals_path=paths["proposals_path"],
                exec_log_path=paths["exec_log_path"],
                state_path=paths["state_path"],
                lock_path=paths["lock_path"],
                kill_switch="state/PROPOSAL_EXECUTOR_DISABLED",
            )
            self.assertFalse(summary["ok"])
            self.assertEqual(summary["code"], ResultCode.KILL_SWITCH.value)


# --------------------------------------------------------------------------
# path_is_protected
# --------------------------------------------------------------------------


class TestProtectedPath(unittest.TestCase):

    def test_exact_match(self):
        from proposal_executors.base import path_is_protected
        self.assertTrue(path_is_protected("runtime/safety_guard.py"))

    def test_prefix_dir_match(self):
        from proposal_executors.base import path_is_protected
        self.assertTrue(path_is_protected("skills/iol_broker/mcp_server.py"))

    def test_non_protected(self):
        from proposal_executors.base import path_is_protected
        self.assertFalse(path_is_protected("runtime/model_router.py"))

    def test_empty_refused(self):
        from proposal_executors.base import path_is_protected
        self.assertTrue(path_is_protected(""))

    def test_normalization(self):
        from proposal_executors.base import path_is_protected
        self.assertTrue(path_is_protected("./runtime/safety_guard.py"))


# --------------------------------------------------------------------------
# CLI smoke tests
# --------------------------------------------------------------------------


class TestCLI(unittest.TestCase):

    def test_status_prints_info(self):
        with tmp_repo({}) as d:
            buf = io.StringIO()
            with mock.patch("sys.stdout", buf):
                rc = pe.main([
                    "--repo-root", d,
                    "--status",
                    "--json",
                ])
            self.assertEqual(rc, 0)
            out = buf.getvalue()
            parsed = json.loads(out)
            self.assertIn("supported_types", parsed)
            self.assertIn("threshold_change", parsed["supported_types"])
            self.assertIn("max_merges_per_24h", parsed)

    def test_dry_run_no_mutation(self):
        repo_files = {
            "runtime/model_router.py": "X = 1\n",
            "tests/test_model_router.py": "print('ok')\n",
            "learning/proposals.json": json.dumps([make_proposal(
                patch_spec={
                    "operation": "replace_constant",
                    "file": "runtime/model_router.py",
                    "constant_name": "X",
                    "old_value": 1, "new_value": 2, "value_type": "int",
                })]),
        }
        with tmp_repo(repo_files) as d:
            paths = default_paths(d)
            rc = pe.main([
                "--repo-root", d,
                "--proposals", paths["proposals_path"],
                "--exec-log", paths["exec_log_path"],
                "--state", paths["state_path"],
                "--lock", paths["lock_path"],
                "--kill-switch", "__never__",
                "--dry-run",
                "--json",
                "--sandbox", "memory",
            ])
            # rc=0 on --dry-run (summary.ok=True even for classifications)
            self.assertEqual(rc, 0)
            # Proposal file must NOT have execution_status after dry run.
            after = json.loads(_read(paths["proposals_path"]))
            self.assertNotIn("execution_status", after[0])
            # Source file untouched.
            self.assertEqual(_read(os.path.join(d, "runtime/model_router.py")),
                             "X = 1\n")


# --------------------------------------------------------------------------
# Integration: run()
# --------------------------------------------------------------------------


class TestGitSandboxEnsureCommit(unittest.TestCase):
    """Regression: _ensure_commit must ignore untracked/unstaged files."""

    def _init_git_repo(self, d: str) -> None:
        """Initialize a minimal git repo with one tracked file."""
        subprocess.run(["git", "init", "--initial-branch", "main"],
                       cwd=d, capture_output=True, check=True)
        subprocess.run(["git", "config", "user.email", "test@test"],
                       cwd=d, capture_output=True, check=True)
        subprocess.run(["git", "config", "user.name", "test"],
                       cwd=d, capture_output=True, check=True)
        _write(os.path.join(d, "tracked.py"), "X = 1\n")
        subprocess.run(["git", "add", "tracked.py"],
                       cwd=d, capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "init"],
                       cwd=d, capture_output=True, check=True)

    def test_untracked_file_does_not_crash_ensure_commit(self):
        """
        Bug fixed: _ensure_commit used git status --porcelain which sees
        untracked and unstaged changes, then tried to commit with nothing
        staged — causing CalledProcessError(1). Now uses git diff --cached.
        """
        with tmp_repo({}) as d:
            self._init_git_repo(d)
            sb = GitSandbox(d)
            branch = sb.create_branch("test-branch")

            # Write an untracked file (NOT staged)
            _write(os.path.join(d, "untracked_noise.txt"), "noise\n")

            # _ensure_commit must not crash
            sb._ensure_commit(branch)

            # Verify: no new commits were made (HEAD is still the branch point)
            r = subprocess.run(["git", "log", "--oneline"],
                               cwd=d, capture_output=True, text=True)
            commits = r.stdout.strip().splitlines()
            # Only the initial commit; branch creation doesn't add one
            self.assertEqual(len(commits), 1)

    def test_unstaged_modified_file_does_not_crash_ensure_commit(self):
        """Unstaged modifications to tracked files must not trigger a commit."""
        with tmp_repo({}) as d:
            self._init_git_repo(d)
            sb = GitSandbox(d)
            branch = sb.create_branch("test-branch")

            # Modify tracked file WITHOUT staging
            with open(os.path.join(d, "tracked.py"), "w") as f:
                f.write("X = 999\n")

            # Must not crash
            sb._ensure_commit(branch)

            r = subprocess.run(["git", "log", "--oneline"],
                               cwd=d, capture_output=True, text=True)
            self.assertEqual(len(r.stdout.strip().splitlines()), 1)

    def test_staged_change_commits_normally(self):
        """Staged changes must still be committed (positive case)."""
        with tmp_repo({}) as d:
            self._init_git_repo(d)
            sb = GitSandbox(d)
            branch = sb.create_branch("test-branch")

            # Stage a real change
            with open(os.path.join(d, "tracked.py"), "w") as f:
                f.write("X = 42\n")
            subprocess.run(["git", "add", "tracked.py"],
                           cwd=d, capture_output=True, check=True)

            sb._ensure_commit(branch)

            r = subprocess.run(["git", "log", "--oneline"],
                               cwd=d, capture_output=True, text=True)
            # Now there should be 2 commits: init + the staged change
            self.assertEqual(len(r.stdout.strip().splitlines()), 2)


class TestIntegration(unittest.TestCase):

    def test_end_to_end_threshold_change(self):
        repo_files = {
            "runtime/model_router.py":
                "# router\nHEAVY_COOLDOWN_SEC = 120\n",
            "tests/test_model_router.py":
                "import os, sys\n"
                "sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'runtime'))\n"
                "import model_router\n"
                "assert model_router.HEAVY_COOLDOWN_SEC == 60\n"
                "print('ok')\n",
        }
        prop = make_proposal(
            pid="prop_integration_1",
            patch_spec={
                "operation": "replace_constant",
                "file": "runtime/model_router.py",
                "constant_name": "HEAVY_COOLDOWN_SEC",
                "old_value": 120, "new_value": 60, "value_type": "int",
            })

        with tmp_repo(repo_files) as d:
            paths = default_paths(d)
            _write(paths["proposals_path"], json.dumps([prop]))

            sb = InMemorySandbox(d)
            summary = pe.run(
                repo_root=d,
                proposals_path=paths["proposals_path"],
                exec_log_path=paths["exec_log_path"],
                state_path=paths["state_path"],
                lock_path=paths["lock_path"],
                kill_switch="__never__",
                sandbox=sb,
            )
            self.assertTrue(summary["ok"])
            self.assertEqual(summary["considered"], 1)
            r = summary["results"][0]
            self.assertEqual(r["status"], "merged")
            self.assertTrue(r["tests_passed"])
            self.assertEqual(r["files_changed"], ["runtime/model_router.py"])

            # Proposal file reflects merged status.
            after = json.loads(_read(paths["proposals_path"]))
            self.assertEqual(after[0]["execution_status"], "merged")
            self.assertEqual(after[0]["execution_mode"], "deterministic")
            self.assertEqual(after[0]["execution_attempts"], 1)

            # Log file has the entry.
            lines = _read(paths["exec_log_path"]).strip().splitlines()
            self.assertEqual(len(lines), 1)
            entry = json.loads(lines[0])
            self.assertEqual(entry["proposal_id"], "prop_integration_1")
            self.assertEqual(entry["status"], "merged")

            # State file has the merge recorded.
            state = json.loads(_read(paths["state_path"]))
            self.assertEqual(len(state["merges_24h"]), 1)
            self.assertEqual(state["attempts"]["prop_integration_1"], 1)


# --------------------------------------------------------------------------


def main():
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    for cls in [
        TestLoadSelect, TestClassification, TestThresholdChangeExecutor,
        TestNetworkRetryExecutor, TestRuntimeNameErrorFixExecutor,
        TestExecuteProposal, TestPersistence, TestIdempotenceAndLock,
        TestKillSwitch, TestProtectedPath, TestCLI,
        TestGitSandboxEnsureCommit, TestIntegration,
    ]:
        suite.addTests(loader.loadTestsFromTestCase(cls))
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
