---
phase: 7
slug: arbitrator-cross-agent
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-04-29
---

# Phase 7 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest (unittest) |
| **Config file** | none — existing test infrastructure |
| **Quick run command** | `docker exec openclaw python3 -m pytest /home/tenazas/tests/test_sprint9.py -x -q --tb=short` |
| **Full suite command** | `docker exec openclaw python3 -m pytest /home/tenazas/tests/test_sprint9.py -q --tb=short` |
| **Estimated runtime** | ~15 seconds |

---

## Sampling Rate

- **After every task commit:** Run quick command
- **After every plan wave:** Run full suite command
- **Before `/gsd-verify-work`:** Full suite must be green
- **Max feedback latency:** 15 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Status |
|---------|------|------|-------------|-----------|--------|
| 07-01-01 | 01 | 1 | ARBT-01, ARBT-02 | unit | ⬜ pending |
| 07-01-02 | 01 | 1 | ARBT-03 | unit | ⬜ pending |
| 07-02-01 | 02 | 1 | AGNT-06, AGNT-07 | unit | ⬜ pending |
| 07-03-01 | 03 | 2 | ALL | unit | ⬜ pending |

---

## Wave 0 Requirements

- Existing infrastructure covers all phase requirements (test_sprint9.py already has arbitrator test classes).

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Telegram escalation buttons | ARBT-03 | Requires live Telegram bot | Send conflicting query, verify inline keyboard appears |

---

## Validation Sign-Off

- [ ] All tasks have automated verify or Wave 0 dependencies
- [ ] Sampling continuity maintained
- [ ] Feedback latency < 15s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
