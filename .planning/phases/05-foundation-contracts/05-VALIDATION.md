---
phase: 5
slug: foundation-contracts
status: draft
nyquist_compliant: true
wave_0_complete: false
created: 2026-04-28
---

# Phase 5 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | unittest (Python stdlib) |
| **Config file** | none — existing test infrastructure |
| **Quick run command** | `docker exec openclaw python3 -m pytest /home/tenazas/tests/test_sprint9.py -x -q` |
| **Full suite command** | `docker exec openclaw python3 /home/tenazas/tests/test_sprint9.py` |
| **Estimated runtime** | ~15 seconds |

---

## Sampling Rate

- **After every task commit:** Run quick command
- **After every plan wave:** Run full suite command
- **Before `/gsd-verify-work`:** Full suite must be green
- **Max feedback latency:** 15 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Threat Ref | Secure Behavior | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|------------|-----------------|-----------|-------------------|-------------|--------|
| 05-01-01 | 01 | 1 | FRMK-01 | — | N/A | unit | `python3 -c "from agent_protocol import Observation, Recommendation"` | W0 | pending |
| 05-01-02 | 01 | 1 | FRMK-01 | — | N/A | unit | `python3 -c "from agent_protocol import Observation; o = Observation('test','infra','s',0.8,{}); d = o.to_dict()"` | W0 | pending |
| 05-02-01 | 02 | 1 | FRMK-02 | — | N/A | unit | `python3 tests/test_sprint9.py TestAgentRegistry` | W0 | pending |
| 05-02-02 | 02 | 1 | FRMK-03 | — | N/A | unit | `python3 tests/test_sprint9.py TestErrorIsolation` | W0 | pending |
| 05-03-01 | 03 | 2 | INTG-02 | T-05-13 | Concurrent writes never corrupt JSON | unit | `python3 tests/test_sprint9.py TestLockedState TestRegistryLockedWrites` | W0 | pending |
| 05-03-02 | 03 | 2 | FRMK-04 | — | N/A | unit | `python3 tests/test_sprint9.py TestPreferredRole` | W0 | pending |

*Status: pending / green / red / flaky*

---

## Wave 0 Requirements

- [ ] `tests/test_sprint9.py` — update existing tests to expect Observation/Recommendation types
- [ ] Test stubs for agent_protocol.py and agent_registry.py

*Existing test infrastructure (unittest + mock) covers framework needs.*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| fcntl locking under concurrent Docker processes | INTG-02 | Requires two concurrent docker exec processes | Run two `python3 -c "..."` scripts writing to same agent state file simultaneously; verify no corruption |

---

## Validation Sign-Off

- [x] All tasks have `<automated>` verify or Wave 0 dependencies
- [x] Sampling continuity: no 3 consecutive tasks without automated verify
- [x] Wave 0 covers all MISSING references
- [x] No watch-mode flags
- [x] Feedback latency < 15s
- [x] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
