---
phase: 8
slug: coordinator-rewrite
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-04-29
---

# Phase 8 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | unittest (Python stdlib) |
| **Config file** | none — existing infrastructure |
| **Quick run command** | `docker exec openclaw python3 /home/tenazas/tests/test_sprint9.py -k "Coordinator"` |
| **Full suite command** | `docker exec openclaw python3 /home/tenazas/tests/test_sprint9.py` |
| **Estimated runtime** | ~15 seconds |

---

## Sampling Rate

- **After every task commit:** Run quick command (coordinator test classes only)
- **After every plan wave:** Run full sprint9 suite
- **Before `/gsd-verify-work`:** Full suite must be green
- **Max feedback latency:** 30 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Secure Behavior | Test Type | Automated Command | Status |
|---------|------|------|-------------|-----------------|-----------|-------------------|--------|
| 08-01-01 | 01 | 1 | CORD-01 | owns() returns False for unrelated queries | unit | `python3 tests/test_sprint9.py -k "TestCoordinatorOwns"` | ⬜ pending |
| 08-01-02 | 01 | 1 | CORD-01 | 0 claims → skill fallback, not crash | unit | `python3 tests/test_sprint9.py -k "TestCoordinatorFallback"` | ⬜ pending |
| 08-01-03 | 01 | 1 | CORD-03 | single claim → direct call, no ThreadPoolExecutor | unit | `python3 tests/test_sprint9.py -k "TestCoordinatorStrategy"` | ⬜ pending |
| 08-01-04 | 01 | 1 | CORD-04 | correlation_id present in all log entries | unit | `python3 tests/test_sprint9.py -k "TestCoordinatorCorrelation"` | ⬜ pending |
| 08-02-01 | 02 | 1 | CORD-02 | multi-domain response has emoji sections | unit | `python3 tests/test_sprint9.py -k "TestCoordinatorAggregation"` | ⬜ pending |
| 08-02-02 | 02 | 1 | CORD-03 | 2+ claims → ThreadPoolExecutor parallel run | unit | `python3 tests/test_sprint9.py -k "TestCoordinatorParallel"` | ⬜ pending |
| 08-02-03 | 02 | 1 | CORD-03 | agent timeout → omitted from result, not fatal | unit | `python3 tests/test_sprint9.py -k "TestCoordinatorTimeout"` | ⬜ pending |
| 08-03-01 | 03 | 2 | CORD-01-04 | all coordinator behaviors pass together | integration | `python3 tests/test_sprint9.py -k "TestCoordinator"` | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/test_sprint9.py` — add TestCoordinatorOwns, TestCoordinatorFallback, TestCoordinatorStrategy, TestCoordinatorCorrelation, TestCoordinatorAggregation, TestCoordinatorParallel, TestCoordinatorTimeout test classes

*Existing test infrastructure (unittest + docker exec) covers all Phase 8 requirements. Framework install not needed.*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| agent_coordinator module imports cleanly from container | CORD-01 | Requires live Docker container | `docker exec openclaw python3 -c "import agent_coordinator; print('ok')"` |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 30s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
