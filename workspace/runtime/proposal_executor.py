#!/usr/bin/env python3
"""
proposal_executor.py — Closes the self-improvement loop.

Reads learning/proposals.json, selects proposals with status="accepted",
classifies each as deterministic / manual_only, and executes the
deterministic ones inside a sandboxed git branch. On success, merges to
main. On any failure, rolls back.

Usage:
    python3 proposal_executor.py                    # run all accepted
    python3 proposal_executor.py --dry-run          # classify, don't execute
    python3 proposal_executor.py --id prop_abc      # run one specific
    python3 proposal_executor.py --limit 3          # cap per-run
    python3 proposal_executor.py --json             # machine-readable output
    python3 proposal_executor.py --once             # alias; single pass
    python3 proposal_executor.py --status           # print executor state

Safety invariants (hard, always enforced):
  - Never writes to main/master directly. All edits go through a branch.
  - Never touches a file in DEFAULT_PROTECTED_FILES.
  - Never executes a proposal whose execution_status is already merged/failed
    (idempotent per proposal).
  - Single-process enforced via lockfile (stale after 600s).
  - Kill switch file (state/PROPOSAL_EXECUTOR_DISABLED) stops all execution.
  - Rate limits: max 5 merges per 24h, max 3 attempts per proposal.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import logging
import os
import random
import re
import string
import sys
import time
import traceback
from dataclasses import asdict
from typing import Any, Dict, List, Optional, Tuple

# Import the package we just built.
# When running from /home/tenazas/runtime/ this works because the package
# lives in the sibling directory proposal_executors/.
try:
    from proposal_executors import (  # type: ignore
        DEFAULT_PROTECTED_FILES,
        EXECUTORS,
        ExecutionStatus,
        InMemorySandbox,
        Proposal,
        ResultCode,
        SUPPORTED_TYPES,
        find_executor,
        get_import_error,
        get_sandbox,
        get_sandbox_info,
        path_is_protected,
    )
    from proposal_executors.base import ExecutionResult, _iso_now  # type: ignore
except ImportError:
    # Fallback when imported as a package: `from runtime.proposal_executor ...`
    from .proposal_executors import (  # type: ignore
        DEFAULT_PROTECTED_FILES,
        EXECUTORS,
        ExecutionStatus,
        InMemorySandbox,
        Proposal,
        ResultCode,
        SUPPORTED_TYPES,
        find_executor,
        get_import_error,
        get_sandbox,
        get_sandbox_info,
        path_is_protected,
    )
    from .proposal_executors.base import ExecutionResult, _iso_now  # type: ignore


# ---- Paths and defaults ---------------------------------------------------

DEFAULT_REPO_ROOT = os.environ.get(
    "TENAZAS_REPO_ROOT", "/home/tenazas"
)
DEFAULT_PROPOSALS_PATH = os.environ.get(
    "TENAZAS_PROPOSALS_PATH", "learning/proposals.json"
)
DEFAULT_EXEC_LOG_PATH = os.environ.get(
    "TENAZAS_EXEC_LOG_PATH", "learning/execution_log.jsonl"
)
DEFAULT_STATE_PATH = os.environ.get(
    "TENAZAS_EXECUTOR_STATE_PATH", "state/proposal_executor_state.json"
)
DEFAULT_LOCK_PATH = os.environ.get(
    "TENAZAS_EXECUTOR_LOCK_PATH", "/tmp/tenazas_proposal_executor.lock"
)
DEFAULT_KILL_SWITCH = os.environ.get(
    "TENAZAS_EXECUTOR_KILL_SWITCH", "state/PROPOSAL_EXECUTOR_DISABLED"
)

# Hard limits.
MAX_DIFF_LINES = 200           # refuse patches > this many diff lines
MAX_FILES_PER_PROPOSAL = 3
MAX_MERGES_PER_24H = 5
MAX_ATTEMPTS_PER_PROPOSAL = 3
LOCKFILE_STALE_SECONDS = 600
DEFAULT_TEST_TIMEOUT = 300

# Proposal types that are NEVER auto-executable, even if accepted.
ALWAYS_MANUAL_TYPES = {
    "security",
    "broker_config",
    "finance_policy",
    "routing_adjustment",       # routing changes can have cascade effects
    "classification_fix",       # usually touches orchestrator classification
}


# ---- Logging --------------------------------------------------------------


def _build_logger(repo_root: str) -> logging.Logger:
    logger = logging.getLogger("proposal_executor")
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    log_dir = os.path.join(repo_root, "logs")
    try:
        os.makedirs(log_dir, exist_ok=True)
        fh = logging.FileHandler(os.path.join(log_dir, "proposal_executor.log"))
    except OSError:
        fh = logging.StreamHandler()
    fh.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s %(message)s"
    ))
    logger.addHandler(fh)
    return logger


# ---- State persistence ----------------------------------------------------


def _load_state(path: str) -> Dict[str, Any]:
    if not os.path.exists(path):
        return {"merges_24h": [], "attempts": {}}
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        # Defensive defaults.
        data.setdefault("merges_24h", [])
        data.setdefault("attempts", {})
        return data
    except (OSError, json.JSONDecodeError):
        return {"merges_24h": [], "attempts": {}}


def _save_state(path: str, state: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, sort_keys=True)
    os.replace(tmp, path)


def _prune_merge_window(state: Dict[str, Any]) -> None:
    cutoff = time.time() - 86_400
    state["merges_24h"] = [t for t in state.get("merges_24h", []) if t >= cutoff]


# ---- Lockfile -------------------------------------------------------------


class _LockHeld(Exception):
    pass


class Lock:
    """
    File lock. Uses flock; falls back to content check for stale detection.
    """

    def __init__(self, path: str):
        self.path = path
        self.fh = None
        self.acquired = False

    def acquire(self) -> None:
        self.fh = open(self.path, "a+")
        try:
            fcntl.flock(self.fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError):
            # Check staleness by mtime.
            try:
                age = time.time() - os.path.getmtime(self.path)
            except OSError:
                age = 0
            if age > LOCKFILE_STALE_SECONDS:
                # Force reacquire; re-open fresh.
                self.fh.close()
                self.fh = open(self.path, "w")
                try:
                    fcntl.flock(self.fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except (OSError, BlockingIOError):
                    raise _LockHeld(f"lock held at {self.path}")
            else:
                raise _LockHeld(f"lock held at {self.path}")
        self.fh.seek(0)
        self.fh.truncate()
        self.fh.write(json.dumps({
            "pid": os.getpid(),
            "ts": _iso_now(),
        }))
        self.fh.flush()
        os.fsync(self.fh.fileno())
        self.acquired = True

    def release(self) -> None:
        if not self.fh:
            return
        try:
            fcntl.flock(self.fh.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        try:
            self.fh.close()
        except OSError:
            pass
        try:
            os.remove(self.path)
        except OSError:
            pass
        self.acquired = False

    def __enter__(self) -> "Lock":
        self.acquire()
        return self

    def __exit__(self, *args) -> None:
        self.release()


# ---- Kill switch ----------------------------------------------------------


def kill_switch_active(repo_root: str, path: str) -> bool:
    full = path if os.path.isabs(path) else os.path.join(repo_root, path)
    return os.path.exists(full)


# ---- Proposal IO ----------------------------------------------------------


def load_proposals(path: str) -> List[Dict[str, Any]]:
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    # Be tolerant of either a list or a {'proposals': [...]} wrapping.
    if isinstance(data, dict):
        data = data.get("proposals", [])
    if not isinstance(data, list):
        return []
    return data


def save_proposals(path: str, proposals: List[Dict[str, Any]]) -> None:
    """
    Atomic-ish write via tmp+rename. Preserves any top-level structure by
    writing back as a plain list (matching load_proposals tolerance).
    """
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(proposals, f, indent=2, sort_keys=False)
    os.replace(tmp, path)


def select_pending_accepted_proposals(
    raw_proposals: List[Dict[str, Any]],
    state: Dict[str, Any],
    only_id: Optional[str] = None,
) -> List[Proposal]:
    """
    Return proposals with status='accepted' that are not already terminally
    executed. Optionally filter to a specific id.
    """
    out: List[Proposal] = []
    terminal = {
        ExecutionStatus.MERGED.value,
        ExecutionStatus.FAILED.value,
        ExecutionStatus.ROLLED_BACK.value,
        ExecutionStatus.UNSUPPORTED.value,
    }
    for raw in raw_proposals:
        if not isinstance(raw, dict):
            continue
        if raw.get("status") != "accepted":
            continue
        if raw.get("execution_status") in terminal:
            continue
        if only_id and str(raw.get("id") or raw.get("proposal_id") or "") != only_id:
            continue

        # Rate limit by attempts.
        pid = str(raw.get("id") or raw.get("proposal_id") or "")
        attempts = int(state.get("attempts", {}).get(pid, 0))
        if attempts >= MAX_ATTEMPTS_PER_PROPOSAL:
            # Leave it marked so next pass also skips.
            continue

        out.append(Proposal.from_dict(raw))
    return out


# ---- Classification -------------------------------------------------------


def classify_execution_mode(proposal: Proposal) -> Tuple[str, Optional[str]]:
    """
    Determine execution_mode for a proposal.

    Returns (mode, reason) where:
      mode in {'deterministic', 'manual_only', 'sandbox_llm'}
      reason explains the decision (useful for logs / UI).
    """
    # Explicit overrides win.
    explicit = (proposal.execution_mode or "").strip()
    if explicit == "manual_only":
        return "manual_only", "explicit_manual"
    if explicit == "sandbox_llm":
        # sandbox_llm is reserved; degrade to manual_only for now.
        return "manual_only", "sandbox_llm_not_yet_supported"
    if explicit and explicit != "deterministic":
        return "manual_only", f"unknown_execution_mode:{explicit}"

    # Categories that are always manual.
    if proposal.type in ALWAYS_MANUAL_TYPES:
        return "manual_only", f"type_in_always_manual:{proposal.type}"

    # Unknown type -> manual.
    if proposal.type not in SUPPORTED_TYPES:
        return "manual_only", f"unsupported_type:{proposal.type}"

    # Must have an executor.
    if find_executor(proposal) is None:
        return "manual_only", "no_executor_registered"

    # Must have a patch_spec (we are deterministic, not creative).
    if not isinstance(proposal.patch_spec, dict) or not proposal.patch_spec:
        return "manual_only", "missing_patch_spec"

    # Target files must not be protected and must be small in number.
    targets = proposal.target_files or []
    if not targets and "file" in proposal.patch_spec:
        targets = [proposal.patch_spec["file"]]
    if not targets:
        return "manual_only", "no_target_files"
    if len(targets) > MAX_FILES_PER_PROPOSAL:
        return "manual_only", f"too_many_files:{len(targets)}"
    for t in targets:
        if path_is_protected(t, DEFAULT_PROTECTED_FILES):
            return "manual_only", f"protected_file:{t}"

    return "deterministic", None


# ---- Execution ------------------------------------------------------------


def _branch_name(proposal: Proposal) -> str:
    # Short, deterministic, unique-per-attempt.
    h = hashlib.sha256(
        f"{proposal.id}:{proposal.type}:{time.time()}".encode()
    ).hexdigest()[:10]
    safe_id = re.sub(r"[^a-zA-Z0-9_-]", "-", proposal.id or "prop")[:24]
    return f"tenazas/auto-exec/{safe_id}-{h}"


def _default_test_command(proposal: Proposal, repo_root: str) -> Optional[str]:
    """
    If proposal.test_command is set, prefer it. Otherwise derive a sensible
    default by mapping target file -> test file. If we can't find a natural
    test file, return None (the orchestrator will refuse to execute without
    a test).
    """
    if proposal.test_command:
        # Refuse obvious injection shapes. We split with shlex later, but we
        # also reject anything with shell meta-chars out of abundance.
        tc = proposal.test_command
        if any(c in tc for c in (";", "&&", "||", "`", "$(")):
            return None
        return tc
    # Derive from first target file.
    targets = proposal.target_files or []
    if not targets and proposal.patch_spec.get("file"):
        targets = [proposal.patch_spec["file"]]
    if not targets:
        return None
    target = targets[0]
    # runtime/foo.py -> tests/test_foo.py
    base = os.path.basename(target).replace(".py", "")
    test_candidate = os.path.join("tests", f"test_{base}.py")
    if os.path.exists(os.path.join(repo_root, test_candidate)):
        return f"python3 {test_candidate}"
    return None


def execute_proposal(
    proposal: Proposal,
    *,
    repo_root: str = DEFAULT_REPO_ROOT,
    sandbox_kind: str = "auto",
    sandbox: Optional[Any] = None,
    dry_run: bool = False,
    state: Optional[Dict[str, Any]] = None,
    logger: Optional[logging.Logger] = None,
) -> ExecutionResult:
    """
    Execute a single proposal. Idempotent per proposal given stable id.

    On any failure: roll back branch, return failure. Caller is responsible
    for persisting the result and log entry.
    """
    logger = logger or logging.getLogger("proposal_executor")
    state = state if state is not None else {}

    started = time.time()
    mode, reason = classify_execution_mode(proposal)

    result = ExecutionResult(
        proposal_id=proposal.id,
        proposal_type=proposal.type,
        execution_mode=mode,
        status=ExecutionStatus.PENDING,
        code=ResultCode.OK,
        started_at=started,
    )
    result.extra["classify_reason"] = reason

    if mode != "deterministic":
        result.status = ExecutionStatus.UNSUPPORTED
        result.code = ResultCode.CLASSIFIED_MANUAL
        result.error = reason or "not deterministic"
        result.ended_at = time.time()
        result.duration_ms = int((result.ended_at - started) * 1000)
        return result

    if dry_run:
        result.status = ExecutionStatus.SKIPPED
        result.code = ResultCode.OK
        result.extra["dry_run"] = True
        result.ended_at = time.time()
        result.duration_ms = int((result.ended_at - started) * 1000)
        return result

    # Rate limit: merges per 24h.
    if state is not None:
        _prune_merge_window(state)
        if len(state.get("merges_24h", [])) >= MAX_MERGES_PER_24H:
            result.status = ExecutionStatus.SKIPPED
            result.code = ResultCode.RATE_LIMITED
            result.error = f"exceeded {MAX_MERGES_PER_24H} merges in 24h"
            result.ended_at = time.time()
            result.duration_ms = int((result.ended_at - started) * 1000)
            return result

    # Derive test command before touching anything.
    test_command = _default_test_command(proposal, repo_root)
    if not test_command:
        result.status = ExecutionStatus.UNSUPPORTED
        result.code = ResultCode.CLASSIFIED_MANUAL
        result.error = "no test_command and no derivable test file"
        result.ended_at = time.time()
        result.duration_ms = int((result.ended_at - started) * 1000)
        return result

    # Pick executor.
    executor = find_executor(proposal)
    if executor is None:
        result.status = ExecutionStatus.UNSUPPORTED
        result.code = ResultCode.UNKNOWN_TYPE
        result.error = f"no executor for type {proposal.type}"
        result.ended_at = time.time()
        result.duration_ms = int((result.ended_at - started) * 1000)
        return result

    # Get sandbox.
    sb = sandbox or get_sandbox(repo_root, prefer=sandbox_kind)
    branch = _branch_name(proposal)
    result.branch = branch
    result.status = ExecutionStatus.RUNNING

    try:
        sb.create_branch(branch)
    except Exception as e:
        result.status = ExecutionStatus.FAILED
        result.code = ResultCode.INTERNAL_ERROR
        result.error = f"create_branch failed: {e!r}"
        result.ended_at = time.time()
        result.duration_ms = int((result.ended_at - started) * 1000)
        logger.warning("branch_create_failed: %s", result.error)
        return result

    try:
        outcome = executor.apply(proposal, sb, branch, repo_root)
        if not outcome.ok:
            result.status = ExecutionStatus.FAILED
            result.code = ResultCode.PATCH_FAILED
            result.error = outcome.error or "patch failed"
            _safe_rollback(sb, branch, result, logger)
            return _finalize(result, started)

        result.files_changed = outcome.files_changed
        result.extra["executor_details"] = outcome.details

        # Diff validation.
        diff_err = sb.validate_diff(branch, MAX_DIFF_LINES, DEFAULT_PROTECTED_FILES)
        result.diff_lines = sb.diff_line_count(branch)
        if diff_err:
            result.status = ExecutionStatus.FAILED
            if "protected" in diff_err.lower():
                result.code = ResultCode.PROTECTED_FILE
            elif "too large" in diff_err.lower():
                result.code = ResultCode.DIFF_TOO_LARGE
            else:
                result.code = ResultCode.PATCH_FAILED
            result.error = diff_err
            _safe_rollback(sb, branch, result, logger)
            return _finalize(result, started)

        # Run tests.
        test_res = sb.run_tests(test_command, cwd=repo_root)
        result.tests_passed = bool(test_res.get("passed"))
        result.extra["test_result"] = {
            "passed": result.tests_passed,
            "returncode": test_res.get("returncode"),
            "stdout_tail": (test_res.get("stdout") or "")[-800:],
            "stderr_tail": (test_res.get("stderr") or "")[-800:],
            "skipped": bool(test_res.get("skipped")),
        }
        if not result.tests_passed:
            result.status = ExecutionStatus.FAILED
            result.code = ResultCode.TEST_FAILED
            result.error = f"tests failed: {test_command}"
            _safe_rollback(sb, branch, result, logger)
            return _finalize(result, started)

        # Merge.
        try:
            sha = sb.merge_to_main(
                branch,
                f"tenazas-exec: {proposal.type} {proposal.id}",
            )
        except Exception as e:
            result.status = ExecutionStatus.FAILED
            result.code = ResultCode.MERGE_FAILED
            result.error = f"merge failed: {e!r}"
            _safe_rollback(sb, branch, result, logger)
            return _finalize(result, started)

        result.commit_sha = sha
        result.status = ExecutionStatus.MERGED
        result.code = ResultCode.OK
        if state is not None:
            state.setdefault("merges_24h", []).append(time.time())
        return _finalize(result, started)

    except Exception as e:  # pragma: no cover - defensive
        result.status = ExecutionStatus.FAILED
        result.code = ResultCode.INTERNAL_ERROR
        result.error = f"unexpected: {e!r}\n{traceback.format_exc()[-1500:]}"
        _safe_rollback(sb, branch, result, logger)
        return _finalize(result, started)


def _safe_rollback(sb, branch, result: ExecutionResult, logger: logging.Logger) -> None:
    try:
        sb.rollback(branch)
        result.rolled_back = True
    except Exception as e:
        logger.warning("rollback_failed: branch=%s err=%r", branch, e)


def _finalize(result: ExecutionResult, started: float) -> ExecutionResult:
    result.ended_at = time.time()
    result.duration_ms = int((result.ended_at - started) * 1000)
    return result


# ---- Result persistence ---------------------------------------------------


def persist_execution_result(
    proposal: Proposal,
    result: ExecutionResult,
    *,
    proposals_path: str,
    exec_log_path: str,
    state_path: str,
    state: Dict[str, Any],
) -> None:
    """
    Writes back:
      - the proposal record with execution_* fields updated
      - an append-only line in the execution log
      - the executor state (attempts, merges_24h)
    """
    # Update proposal in place.
    raw_list = load_proposals(proposals_path)
    changed = False
    for i, raw in enumerate(raw_list):
        if not isinstance(raw, dict):
            continue
        if str(raw.get("id") or raw.get("proposal_id") or "") != proposal.id:
            continue
        raw["executed_at"] = _iso_now()
        raw["execution_status"] = result.status.value
        raw["execution_mode"] = result.execution_mode
        raw["execution_attempts"] = int(raw.get("execution_attempts") or 0) + 1
        raw["execution_result"] = {
            "code": result.code.value,
            "branch": result.branch,
            "commit_sha": result.commit_sha,
            "tests_passed": result.tests_passed,
            "rolled_back": result.rolled_back,
            "duration_ms": result.duration_ms,
            "error": result.error,
            "files_changed": result.files_changed,
            "diff_lines": result.diff_lines,
            "classify_reason": result.extra.get("classify_reason"),
        }
        raw_list[i] = raw
        changed = True
        break
    if changed:
        save_proposals(proposals_path, raw_list)

    # Append log.
    os.makedirs(os.path.dirname(exec_log_path) or ".", exist_ok=True)
    with open(exec_log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(result.to_log_entry(), sort_keys=True) + "\n")

    # Attempts counter: bump on any non-skip terminal outcome.
    if result.status in (
        ExecutionStatus.FAILED,
        ExecutionStatus.MERGED,
        ExecutionStatus.ROLLED_BACK,
    ):
        state.setdefault("attempts", {})
        state["attempts"][proposal.id] = (
            int(state["attempts"].get(proposal.id, 0)) + 1
        )
    _save_state(state_path, state)


# ---- Main runner ----------------------------------------------------------


def run(
    *,
    repo_root: str = DEFAULT_REPO_ROOT,
    proposals_path: Optional[str] = None,
    exec_log_path: Optional[str] = None,
    state_path: Optional[str] = None,
    lock_path: str = DEFAULT_LOCK_PATH,
    kill_switch: str = DEFAULT_KILL_SWITCH,
    only_id: Optional[str] = None,
    limit: Optional[int] = None,
    dry_run: bool = False,
    sandbox_kind: str = "auto",
    sandbox: Optional[Any] = None,     # for tests
    skip_lock: bool = False,           # for tests
) -> Dict[str, Any]:
    """
    One pass over accepted proposals. Returns a summary dict.
    """
    proposals_path = proposals_path or os.path.join(repo_root, DEFAULT_PROPOSALS_PATH)
    exec_log_path = exec_log_path or os.path.join(repo_root, DEFAULT_EXEC_LOG_PATH)
    state_path = state_path or os.path.join(repo_root, DEFAULT_STATE_PATH)

    logger = _build_logger(repo_root)

    if kill_switch_active(repo_root, kill_switch):
        logger.warning("kill_switch_active")
        return {
            "ok": False,
            "code": ResultCode.KILL_SWITCH.value,
            "message": "kill switch active",
            "results": [],
        }

    lock = None
    try:
        if not skip_lock:
            lock = Lock(lock_path)
            lock.acquire()

        state = _load_state(state_path)
        _prune_merge_window(state)

        raw = load_proposals(proposals_path)
        selected = select_pending_accepted_proposals(raw, state, only_id=only_id)
        if limit is not None:
            selected = selected[: max(0, limit)]

        out_results: List[Dict[str, Any]] = []
        for prop in selected:
            logger.info(
                "processing proposal id=%s type=%s", prop.id, prop.type,
            )
            res = execute_proposal(
                prop,
                repo_root=repo_root,
                sandbox_kind=sandbox_kind,
                sandbox=sandbox,
                dry_run=dry_run,
                state=state,
                logger=logger,
            )
            if not dry_run:
                persist_execution_result(
                    prop, res,
                    proposals_path=proposals_path,
                    exec_log_path=exec_log_path,
                    state_path=state_path,
                    state=state,
                )
            out_results.append({
                "id": prop.id,
                "type": prop.type,
                "status": res.status.value,
                "code": res.code.value,
                "branch": res.branch,
                "commit_sha": res.commit_sha,
                "tests_passed": res.tests_passed,
                "rolled_back": res.rolled_back,
                "duration_ms": res.duration_ms,
                "error": res.error,
                "execution_mode": res.execution_mode,
                "classify_reason": res.extra.get("classify_reason"),
                "files_changed": res.files_changed,
            })
            logger.info(
                "done id=%s status=%s code=%s duration_ms=%s",
                prop.id, res.status.value, res.code.value, res.duration_ms,
            )

        return {
            "ok": True,
            "considered": len(selected),
            "results": out_results,
            "merges_24h": len(state.get("merges_24h", [])),
        }
    except _LockHeld as e:
        logger.warning("lock held: %s", e)
        return {
            "ok": False,
            "code": ResultCode.LOCK_HELD.value,
            "message": str(e),
            "results": [],
        }
    finally:
        if lock is not None:
            lock.release()


# ---- CLI ------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Execute accepted proposals from learning/proposals.json",
    )
    p.add_argument("--repo-root", default=DEFAULT_REPO_ROOT,
                   help="Repo root (default: $TENAZAS_REPO_ROOT or /home/tenazas)")
    p.add_argument("--proposals", default=None,
                   help="Override proposals.json path")
    p.add_argument("--exec-log", default=None,
                   help="Override execution_log.jsonl path")
    p.add_argument("--state", default=None,
                   help="Override executor state path")
    p.add_argument("--lock", default=DEFAULT_LOCK_PATH,
                   help="Lockfile path")
    p.add_argument("--kill-switch", default=DEFAULT_KILL_SWITCH,
                   help="Kill switch file path (relative to repo_root or absolute)")
    p.add_argument("--id", dest="only_id", default=None,
                   help="Only process this proposal id")
    p.add_argument("--limit", type=int, default=None,
                   help="Max proposals to process this run")
    p.add_argument("--dry-run", action="store_true",
                   help="Classify but do not execute")
    p.add_argument("--once", action="store_true",
                   help="Single pass (this is the default; flag kept for clarity)")
    p.add_argument("--json", dest="json_out", action="store_true",
                   help="Emit machine-readable JSON on stdout")
    p.add_argument("--status", action="store_true",
                   help="Print executor state and exit")
    p.add_argument("--sandbox", default="auto",
                   choices=("auto", "autofix", "git", "memory"),
                   help="Sandbox backend")
    return p


def _print_status(repo_root: str, state_path: str, kill_switch: str, json_out: bool) -> None:
    state = _load_state(state_path if os.path.isabs(state_path)
                       else os.path.join(repo_root, state_path))
    _prune_merge_window(state)
    info = {
        "repo_root": repo_root,
        "state_path": state_path,
        "kill_switch_active": kill_switch_active(repo_root, kill_switch),
        "autofix_module_import_error": get_import_error(),
        "sandbox": get_sandbox_info(repo_root),
        "merges_last_24h": len(state.get("merges_24h", [])),
        "max_merges_per_24h": MAX_MERGES_PER_24H,
        "attempt_counts": state.get("attempts", {}),
        "supported_types": sorted(SUPPORTED_TYPES),
        "always_manual_types": sorted(ALWAYS_MANUAL_TYPES),
        "max_diff_lines": MAX_DIFF_LINES,
        "max_files_per_proposal": MAX_FILES_PER_PROPOSAL,
        "max_attempts_per_proposal": MAX_ATTEMPTS_PER_PROPOSAL,
        "lockfile_stale_seconds": LOCKFILE_STALE_SECONDS,
    }
    if json_out:
        print(json.dumps(info, indent=2, sort_keys=True))
    else:
        print("proposal_executor status")
        for k, v in info.items():
            print(f"  {k}: {v}")


def main(argv: Optional[List[str]] = None) -> int:
    args = _build_parser().parse_args(argv)

    if args.status:
        state_path = args.state or os.path.join(args.repo_root, DEFAULT_STATE_PATH)
        _print_status(args.repo_root, state_path, args.kill_switch, args.json_out)
        return 0

    summary = run(
        repo_root=args.repo_root,
        proposals_path=args.proposals,
        exec_log_path=args.exec_log,
        state_path=args.state,
        lock_path=args.lock,
        kill_switch=args.kill_switch,
        only_id=args.only_id,
        limit=args.limit,
        dry_run=args.dry_run,
        sandbox_kind=args.sandbox,
    )
    if args.json_out:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"considered: {summary.get('considered', 0)}")
        for r in summary.get("results", []):
            line = (
                f"  [{r['id']}] type={r['type']} "
                f"status={r['status']} code={r['code']}"
            )
            if r.get("commit_sha"):
                line += f" sha={r['commit_sha'][:12]}"
            if r.get("error"):
                line += f" err={r['error'][:80]}"
            print(line)
    return 0 if summary.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
