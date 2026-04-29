"""
Base types for proposal executors.

An executor takes a Proposal and a Sandbox, produces an ExecutionResult.
Executors MUST be deterministic. LLM-based changes belong in a separate
path (currently degraded to manual_only).

Design invariants:
  - No executor writes to main/master directly.
  - No executor touches protected files.
  - No executor runs arbitrary shell from proposal data.
  - No executor retries on its own; the orchestrator owns retry policy.
"""
from __future__ import annotations

import dataclasses
import enum
import json
import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Protocol


class ExecutionMode(str, enum.Enum):
    DETERMINISTIC = "deterministic"
    SANDBOX_LLM = "sandbox_llm"      # reserved; currently always degraded
    MANUAL_ONLY = "manual_only"


class ExecutionStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    MERGED = "merged"
    ROLLED_BACK = "rolled_back"
    FAILED = "failed"
    SKIPPED = "skipped"
    UNSUPPORTED = "unsupported"


class ResultCode(str, enum.Enum):
    OK = "ok"
    CLASSIFIED_MANUAL = "classified_manual"
    PROTECTED_FILE = "protected_file"
    ALREADY_EXECUTED = "already_executed"
    RATE_LIMITED = "rate_limited"
    KILL_SWITCH = "kill_switch"
    PATCH_FAILED = "patch_failed"
    DIFF_TOO_LARGE = "diff_too_large"
    TEST_FAILED = "test_failed"
    MERGE_FAILED = "merge_failed"
    UNKNOWN_TYPE = "unknown_type"
    ROLLED_BACK = "rolled_back"
    LOCK_HELD = "lock_held"
    INTERNAL_ERROR = "internal_error"


@dataclass
class Proposal:
    """
    Canonical proposal representation.

    Required:
      id, type, status

    Optional (execution-time metadata; orchestrator fills missing ones):
      execution_mode, target_files, patch_spec, test_command,
      executed_at, execution_status, execution_result, execution_attempts
    """
    id: str
    type: str
    status: str
    description: str = ""
    raw: Dict[str, Any] = field(default_factory=dict)

    # Execution metadata
    execution_mode: Optional[str] = None
    target_files: List[str] = field(default_factory=list)
    patch_spec: Dict[str, Any] = field(default_factory=dict)
    test_command: Optional[str] = None
    executed_at: Optional[str] = None
    execution_status: Optional[str] = None
    execution_result: Optional[Dict[str, Any]] = None
    execution_attempts: int = 0

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Proposal":
        return cls(
            id=str(data.get("id") or data.get("proposal_id") or ""),
            type=str(data.get("type") or ""),
            status=str(data.get("status") or "proposed"),
            description=str(data.get("description") or data.get("proposal") or ""),
            raw=dict(data),
            execution_mode=data.get("execution_mode"),
            target_files=list(data.get("target_files") or []),
            patch_spec=dict(data.get("patch_spec") or {}),
            test_command=data.get("test_command"),
            executed_at=data.get("executed_at"),
            execution_status=data.get("execution_status"),
            execution_result=data.get("execution_result"),
            execution_attempts=int(data.get("execution_attempts") or 0),
        )

    def to_dict(self) -> Dict[str, Any]:
        # Merge back onto raw so we don't lose unknown fields.
        out = dict(self.raw)
        out.update({
            "id": self.id,
            "type": self.type,
            "status": self.status,
            "description": self.description,
            "execution_mode": self.execution_mode,
            "target_files": list(self.target_files),
            "patch_spec": dict(self.patch_spec),
            "test_command": self.test_command,
            "executed_at": self.executed_at,
            "execution_status": self.execution_status,
            "execution_result": self.execution_result,
            "execution_attempts": self.execution_attempts,
        })
        # Drop Nones to keep JSON clean, but keep empty lists/dicts.
        return {k: v for k, v in out.items() if v is not None}


@dataclass
class ExecutionResult:
    """
    Outcome of one execute() call. Always populated, even on failure.
    """
    proposal_id: str
    proposal_type: str
    execution_mode: str
    status: ExecutionStatus
    code: ResultCode
    branch: Optional[str] = None
    commit_sha: Optional[str] = None
    tests_passed: Optional[bool] = None
    rolled_back: bool = False
    duration_ms: int = 0
    error: Optional[str] = None
    files_changed: List[str] = field(default_factory=list)
    diff_lines: int = 0
    started_at: float = 0.0
    ended_at: float = 0.0
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_log_entry(self) -> Dict[str, Any]:
        return {
            "ts": _iso_now(),
            "proposal_id": self.proposal_id,
            "proposal_type": self.proposal_type,
            "execution_mode": self.execution_mode,
            "status": self.status.value,
            "code": self.code.value,
            "branch": self.branch,
            "commit_sha": self.commit_sha,
            "tests_passed": self.tests_passed,
            "rolled_back": self.rolled_back,
            "duration_ms": self.duration_ms,
            "error": self.error,
            "files_changed": list(self.files_changed),
            "diff_lines": self.diff_lines,
            "extra": dict(self.extra),
        }


def _iso_now() -> str:
    # UTC ISO-8601 without microseconds; matches existing Tenazas log format.
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class SandboxProtocol(Protocol):
    """
    Minimal sandbox surface. Concrete impl lives in sandbox_adapter.py
    and wraps autofix_sandbox.py when present.
    """

    def create_branch(self, name: str) -> str: ...

    def apply_file_change(self, branch: str, path: str, new_content: str) -> None: ...

    def diff_for_branch(self, branch: str) -> str: ...

    def diff_line_count(self, branch: str) -> int: ...

    def files_changed(self, branch: str) -> List[str]: ...

    def validate_diff(
        self, branch: str, max_lines: int, protected_files: List[str]
    ) -> Optional[str]:
        """Return error string if invalid, else None."""
        ...

    def run_tests(self, test_command: str, cwd: Optional[str] = None) -> Dict[str, Any]:
        """Return {'passed': bool, 'returncode': int, 'stdout': str, 'stderr': str}."""
        ...

    def merge_to_main(self, branch: str, message: str) -> str:
        """Return commit sha on main."""
        ...

    def rollback(self, branch: str) -> None: ...


class ProposalExecutor(Protocol):
    """
    Contract for a deterministic per-type executor.

    An executor:
      1. Validates the proposal's patch_spec matches what it expects.
      2. Applies file changes through the sandbox (NEVER directly).
      3. Returns (files_changed, error_or_None).

    It does NOT run tests, merge, or rollback. The orchestrator does that.
    """

    TYPE: str

    def can_handle(self, proposal: Proposal) -> bool: ...

    def apply(
        self,
        proposal: Proposal,
        sandbox: SandboxProtocol,
        branch: str,
        repo_root: str,
    ) -> "ApplyOutcome": ...


@dataclass
class ApplyOutcome:
    ok: bool
    files_changed: List[str] = field(default_factory=list)
    error: Optional[str] = None
    details: Dict[str, Any] = field(default_factory=dict)


# --- Safety helpers used across executors ----------------------------------

DEFAULT_PROTECTED_FILES: List[str] = [
    # Mirrors the documented autofix protected list.
    "runtime/orchestrator.py",
    "runtime/safety_guard.py",
    "runtime/docker_wrapper.py",
    "runtime/runtime.py",
    "runtime/autofix_agent.py",
    "runtime/autofix_sandbox.py",
    "runtime/proposal_executor.py",
    # Financial / broker boundary — never auto-touch.
    "runtime/iol_handler.py",
    "runtime/finance.py",
    "runtime/expenses.py",
    "skills/iol_broker/",
    # Secrets.
    "state/openclaw.json",
    "openclaw.json",
]


def path_is_protected(path: str, protected: Optional[List[str]] = None) -> bool:
    """
    Normalize + compare. A path is protected if:
      - it equals a protected path, or
      - a protected path ending in '/' is a prefix of it.
    """
    if not path:
        return True
    protected = protected or DEFAULT_PROTECTED_FILES
    norm = os.path.normpath(path).lstrip("./")
    for p in protected:
        pn = os.path.normpath(p).lstrip("./")
        if p.endswith("/"):
            if norm.startswith(pn.rstrip("/") + os.sep) or norm == pn.rstrip("/"):
                return True
        else:
            if norm == pn:
                return True
    return False


def read_file(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def safe_join(repo_root: str, rel_path: str) -> str:
    """
    Join + reject path traversal. Returns absolute path inside repo_root.
    Raises ValueError if the resulting path escapes the repo.
    """
    abs_root = os.path.abspath(repo_root)
    abs_path = os.path.abspath(os.path.join(abs_root, rel_path))
    if not (abs_path == abs_root or abs_path.startswith(abs_root + os.sep)):
        raise ValueError(f"path escapes repo_root: {rel_path}")
    return abs_path
