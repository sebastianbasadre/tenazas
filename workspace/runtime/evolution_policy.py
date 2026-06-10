#!/usr/bin/env python3
"""
evolution_policy.py
Policy gate for OpenClaw/Tenazas self-evolution.

The proposal executor owns sandboxing, tests, and merges. This module owns the
decision about whether a proposal is eligible for autonomous execution at all.

Autonomy levels:
  L0 observe only
  L1 propose only
  L2 branch/test only
  L3 auto-merge safe/test/docs/observability changes
  L4 auto-merge low-risk code fixes
  L5 broader autonomy, still blocked by protected boundaries
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

try:
    from proposal_executors import DEFAULT_PROTECTED_FILES, Proposal, path_is_protected
except ImportError:
    from .proposal_executors import DEFAULT_PROTECTED_FILES, Proposal, path_is_protected


RUNTIME = Path(__file__).parent
DEFAULT_OUTCOME_LEDGER = RUNTIME / "learning" / "evolution_outcomes.jsonl"

AUTONOMY_OBSERVE = 0
AUTONOMY_PROPOSE = 1
AUTONOMY_BRANCH_TEST = 2
AUTONOMY_AUTO_SAFE = 3
AUTONOMY_AUTO_LOW = 4
AUTONOMY_BROAD = 5

DEFAULT_AUTONOMY_LEVEL = AUTONOMY_AUTO_SAFE

SAFE_CHANGE_TYPES = {
    "test",
    "docs",
    "runbook",
    "observability",
    "alert_wording",
}

LOW_CHANGE_TYPES = {
    "bugfix",
    "retry_threshold",
    "noncritical_prompt",
    "read_only_diagnostic",
}

MANUAL_CHANGE_TYPES = {
    "security",
    "broker_config",
    "finance_policy",
    "trading",
    "routing_adjustment",
    "classification_fix",
    "policy",
    "container_control",
    "secrets",
}

PROTECTED_PATHS = sorted(set(DEFAULT_PROTECTED_FILES + [
    "runtime/policy_engine.py",
    "runtime/action_registry.py",
    "runtime/autonomy_contracts.py",
    "runtime/evolution_policy.py",
    "runtime/approval_queue.py",
    "runtime/exec_approvals.py",
    "runtime/model_router.py",
    "runtime/finance_daily.py",
    "runtime/finance_exporter.py",
    "runtime/agent_finance.py",
    "runtime/trading_observer.py",
    "runtime/trading_paper.py",
    "runtime/trading_scorer.py",
    "tools/iol_refresh.py",
    "tools/iol_init_token.py",
    "skills/iol_broker/",
    ".env",
    ".env.example",
]))

AGENT_CONTRACTS: dict[str, dict[str, Any]] = {
    "self_debugger": {
        "autonomy_level": AUTONOMY_AUTO_LOW,
        "allowed_change_types": sorted(SAFE_CHANGE_TYPES | {"bugfix"}),
        "max_files": 3,
        "max_diff_lines": 120,
        "requires_tests": True,
    },
    "self_dev_engine": {
        "autonomy_level": AUTONOMY_BRANCH_TEST,
        "allowed_change_types": sorted(SAFE_CHANGE_TYPES | LOW_CHANGE_TYPES),
        "max_files": 3,
        "max_diff_lines": 120,
        "requires_tests": True,
    },
    "daily_improvement": {
        "autonomy_level": AUTONOMY_AUTO_SAFE,
        "allowed_change_types": sorted(SAFE_CHANGE_TYPES | {"retry_threshold"}),
        "max_files": 2,
        "max_diff_lines": 80,
        "requires_tests": True,
    },
    "improvement_loop": {
        "autonomy_level": AUTONOMY_AUTO_SAFE,
        "allowed_change_types": sorted(SAFE_CHANGE_TYPES),
        "max_files": 2,
        "max_diff_lines": 80,
        "requires_tests": True,
    },
}


@dataclass
class EvolutionDecision:
    allowed: bool
    auto_merge_allowed: bool
    requires_approval: bool
    autonomy_level: int
    change_type: str
    risk: str
    reason: str
    required_tests: list[str] = field(default_factory=list)
    protected_files: list[str] = field(default_factory=list)
    evidence_count: int = 0


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def proposal_source(proposal: Proposal) -> str:
    return str(proposal.raw.get("source") or proposal.raw.get("agent") or "").strip()


def proposal_change_type(proposal: Proposal) -> str:
    value = proposal.raw.get("change_type") or proposal.type or "unknown"
    return str(value).strip().lower()


def proposal_risk(proposal: Proposal) -> str:
    value = proposal.raw.get("risk") or proposal.raw.get("risk_level") or "low"
    return str(value).strip().lower()


def proposal_targets(proposal: Proposal) -> list[str]:
    targets = list(proposal.target_files or [])
    if not targets and isinstance(proposal.patch_spec, dict):
        file_target = proposal.patch_spec.get("file")
        if file_target:
            targets.append(str(file_target))
    return targets


def is_self_evolution_proposal(proposal: Proposal) -> bool:
    if _truthy(proposal.raw.get("autonomy_eligible")):
        return True
    source = proposal_source(proposal)
    return source in AGENT_CONTRACTS


def _contract_for(proposal: Proposal) -> dict[str, Any]:
    source = proposal_source(proposal)
    return AGENT_CONTRACTS.get(source, {
        "autonomy_level": DEFAULT_AUTONOMY_LEVEL,
        "allowed_change_types": sorted(SAFE_CHANGE_TYPES),
        "max_files": 3,
        "max_diff_lines": 120,
        "requires_tests": True,
    })


def _required_level(change_type: str, risk: str) -> int:
    if change_type in SAFE_CHANGE_TYPES and risk in {"safe", "low"}:
        return AUTONOMY_AUTO_SAFE
    if change_type in LOW_CHANGE_TYPES and risk in {"safe", "low"}:
        return AUTONOMY_AUTO_LOW
    return AUTONOMY_BROAD + 1


def evaluate_proposal(
    proposal: Proposal,
    *,
    diff_lines: int = 0,
    strict: bool | None = None,
) -> EvolutionDecision:
    """Evaluate a proposal against self-evolution policy.

    strict=None means strict only for self-evolution/autonomy-eligible proposals.
    Legacy manually accepted proposals remain governed by proposal_executor's
    existing deterministic safeguards.
    """
    strict_mode = is_self_evolution_proposal(proposal) if strict is None else strict
    change_type = proposal_change_type(proposal)
    risk = proposal_risk(proposal)
    targets = proposal_targets(proposal)
    contract = _contract_for(proposal)
    level = int(contract.get("autonomy_level", DEFAULT_AUTONOMY_LEVEL))
    max_files = int(contract.get("max_files", 3))
    max_diff = int(contract.get("max_diff_lines", 120))
    evidence = _as_list(proposal.raw.get("evidence"))
    required_tests = _as_list(proposal.raw.get("expected_tests"))
    if proposal.test_command:
        required_tests.append(proposal.test_command)
    required_tests = [str(t) for t in required_tests if str(t).strip()]

    protected = [t for t in targets if path_is_protected(t, PROTECTED_PATHS)]
    if protected:
        return EvolutionDecision(
            allowed=False,
            auto_merge_allowed=False,
            requires_approval=True,
            autonomy_level=level,
            change_type=change_type,
            risk=risk,
            reason=f"protected_files:{','.join(protected)}",
            protected_files=protected,
            evidence_count=len(evidence),
        )

    if change_type in MANUAL_CHANGE_TYPES or risk in {"high", "critical"}:
        return EvolutionDecision(
            allowed=False,
            auto_merge_allowed=False,
            requires_approval=True,
            autonomy_level=level,
            change_type=change_type,
            risk=risk,
            reason=f"manual_boundary:{change_type}:{risk}",
            evidence_count=len(evidence),
        )

    if len(targets) > max_files:
        return EvolutionDecision(
            allowed=False,
            auto_merge_allowed=False,
            requires_approval=True,
            autonomy_level=level,
            change_type=change_type,
            risk=risk,
            reason=f"too_many_files:{len(targets)}>{max_files}",
            evidence_count=len(evidence),
        )

    if diff_lines and diff_lines > max_diff:
        return EvolutionDecision(
            allowed=False,
            auto_merge_allowed=False,
            requires_approval=True,
            autonomy_level=level,
            change_type=change_type,
            risk=risk,
            reason=f"diff_too_large:{diff_lines}>{max_diff}",
            evidence_count=len(evidence),
        )

    allowed_types = set(contract.get("allowed_change_types") or [])
    if strict_mode and change_type not in allowed_types:
        return EvolutionDecision(
            allowed=False,
            auto_merge_allowed=False,
            requires_approval=True,
            autonomy_level=level,
            change_type=change_type,
            risk=risk,
            reason=f"change_type_not_allowed:{change_type}",
            evidence_count=len(evidence),
        )

    if strict_mode and not evidence:
        return EvolutionDecision(
            allowed=False,
            auto_merge_allowed=False,
            requires_approval=True,
            autonomy_level=level,
            change_type=change_type,
            risk=risk,
            reason="missing_evidence",
            required_tests=required_tests,
            evidence_count=0,
        )

    if strict_mode and contract.get("requires_tests", True) and not required_tests:
        return EvolutionDecision(
            allowed=True,
            auto_merge_allowed=False,
            requires_approval=True,
            autonomy_level=level,
            change_type=change_type,
            risk=risk,
            reason="missing_expected_tests",
            evidence_count=len(evidence),
        )

    required_level = _required_level(change_type, risk)
    auto_merge = level >= required_level and required_level <= AUTONOMY_AUTO_LOW
    if level < AUTONOMY_BRANCH_TEST:
        return EvolutionDecision(
            allowed=False,
            auto_merge_allowed=False,
            requires_approval=True,
            autonomy_level=level,
            change_type=change_type,
            risk=risk,
            reason="autonomy_level_observe_or_propose_only",
            required_tests=required_tests,
            evidence_count=len(evidence),
        )

    if level == AUTONOMY_BRANCH_TEST:
        return EvolutionDecision(
            allowed=True,
            auto_merge_allowed=False,
            requires_approval=True,
            autonomy_level=level,
            change_type=change_type,
            risk=risk,
            reason="branch_test_only",
            required_tests=required_tests,
            evidence_count=len(evidence),
        )

    return EvolutionDecision(
        allowed=True,
        auto_merge_allowed=auto_merge,
        requires_approval=not auto_merge,
        autonomy_level=level,
        change_type=change_type,
        risk=risk,
        reason="auto_merge_allowed" if auto_merge else "approval_required_by_level",
        required_tests=required_tests,
        evidence_count=len(evidence),
    )


def record_outcome(
    proposal: Proposal,
    result: Any,
    *,
    ledger_path: str | os.PathLike[str] | None = None,
    decision: EvolutionDecision | None = None,
) -> None:
    """Append self-evolution outcome memory for later policy learning."""
    path = Path(ledger_path) if ledger_path else DEFAULT_OUTCOME_LEDGER
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "proposal_id": proposal.id,
        "source": proposal_source(proposal),
        "type": proposal.type,
        "change_type": proposal_change_type(proposal),
        "risk": proposal_risk(proposal),
        "status": getattr(getattr(result, "status", None), "value", getattr(result, "status", None)),
        "code": getattr(getattr(result, "code", None), "value", getattr(result, "code", None)),
        "tests_passed": getattr(result, "tests_passed", None),
        "rolled_back": getattr(result, "rolled_back", None),
        "commit_sha": getattr(result, "commit_sha", None),
        "files_changed": list(getattr(result, "files_changed", []) or []),
        "diff_lines": getattr(result, "diff_lines", 0),
    }
    if decision:
        entry["decision"] = asdict(decision)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, sort_keys=True, default=str) + "\n")


def status() -> dict[str, Any]:
    return {
        "default_autonomy_level": DEFAULT_AUTONOMY_LEVEL,
        "contracts": AGENT_CONTRACTS,
        "safe_change_types": sorted(SAFE_CHANGE_TYPES),
        "low_change_types": sorted(LOW_CHANGE_TYPES),
        "manual_change_types": sorted(MANUAL_CHANGE_TYPES),
        "protected_paths": PROTECTED_PATHS,
    }
