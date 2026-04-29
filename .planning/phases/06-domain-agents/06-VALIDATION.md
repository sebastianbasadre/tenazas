---
phase: 6
slug: domain-agents
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-04-29
---

# Phase 6 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest (unittest) |
| **Config file** | none — existing test infrastructure |
| **Quick run command** | `docker exec openclaw python3 -m pytest /home/tenazas/tests/test_sprint9.py -x -q --tb=short` |
| **Full suite command** | `docker exec openclaw python3 -m pytest /home/tenazas/tests/test_sprint9.py -q --tb=short` |
| **Estimated runtime** | ~10 seconds |

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
| 06-01-01 | 01 | 1 | AGNT-01 | — | N/A | unit | `python3 -m pytest tests/test_sprint9.py -k "agent_infra" -q` | ⬜ W0 | ⬜ pending |
| 06-02-01 | 02 | 1 | AGNT-02 | — | N/A | unit | `python3 -m pytest tests/test_sprint9.py -k "agent_personal" -q` | ⬜ W0 | ⬜ pending |
| 06-03-01 | 03 | 1 | AGNT-03 | — | N/A | unit | `python3 -m pytest tests/test_sprint9.py -k "agent_finance" -q` | ⬜ W0 | ⬜ pending |
| 06-04-01 | 04 | 1 | AGNT-04 | — | N/A | unit | `python3 -m pytest tests/test_sprint9.py -k "agent_research" -q` | ⬜ W0 | ⬜ pending |
| 06-05-01 | 05 | 1 | AGNT-05 | — | N/A | unit | `python3 -m pytest tests/test_sprint9.py -k "agent_self_improve" -q` | ⬜ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- Existing infrastructure covers all phase requirements (test_sprint9.py already has agent test classes).

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Telegram integration query-response | ALL | Requires live Telegram bot + Ollama | Send test queries via Telegram, verify agent routes |

---

## Validation Sign-Off

- [ ] All tasks have automated verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 15s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
