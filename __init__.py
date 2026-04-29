"""
Proposal executor registry.

Add new deterministic executors here. Each executor must:
  - expose TYPE (str)
  - implement can_handle(proposal) and apply(...)
"""
from __future__ import annotations

from typing import Dict, Iterable, List, Optional

from .base import (
    ApplyOutcome,
    ExecutionMode,
    ExecutionStatus,
    Proposal,
    ResultCode,
    SandboxProtocol,
    DEFAULT_PROTECTED_FILES,
    path_is_protected,
)
from .network_retry_policy import NetworkRetryPolicyExecutor
from .runtime_name_error_fix import RuntimeNameErrorFixExecutor
from .sandbox_adapter import (
    GitSandbox,
    InMemorySandbox,
    get_import_error,
    get_sandbox,
)
from .threshold_change import ThresholdChangeExecutor

__all__ = [
    "ApplyOutcome",
    "ExecutionMode",
    "ExecutionStatus",
    "Proposal",
    "ResultCode",
    "SandboxProtocol",
    "DEFAULT_PROTECTED_FILES",
    "path_is_protected",
    "get_sandbox",
    "get_import_error",
    "GitSandbox",
    "InMemorySandbox",
    "ThresholdChangeExecutor",
    "NetworkRetryPolicyExecutor",
    "RuntimeNameErrorFixExecutor",
    "EXECUTORS",
    "find_executor",
    "SUPPORTED_TYPES",
]


# Registered executors. Order matters only for tie-breaking; in practice
# types are disjoint.
EXECUTORS: List[object] = [
    ThresholdChangeExecutor(),
    NetworkRetryPolicyExecutor(),
    RuntimeNameErrorFixExecutor(),
]


SUPPORTED_TYPES = {
    "threshold_change",
    "guardrail_tuning",   # handled by ThresholdChangeExecutor
    "network_retry_policy",
    "runtime_name_error_fix",
}


def find_executor(proposal: Proposal):
    for ex in EXECUTORS:
        if ex.can_handle(proposal):
            return ex
    return None
