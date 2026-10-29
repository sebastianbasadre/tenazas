# Tenazas documentation

Reviewed **2026-10-28** against repository code. Historical reports and inventory
notes do not establish the current production state.

## Setup and development

| Guide | Contents |
|---|---|
| [Overview](../README.md) | Scope and components |
| [Setup](setup.md) | Requirements, configuration, mounts, and startup |
| [Architecture](architecture.md) | Deployment, request paths, scheduling, and state |
| [Development](development.md) | Python environment, checks, and contribution workflow |
| [Infrastructure](../INFRA.md) | Services, ports, telemetry, and external dependencies |
| [Models](../MODELS.md) | Catalog and fallback chains in code |
| [Routing contract](../workspace/runtime/INTERFACE_CONTRACT.md) | Canonical labels and implemented APIs |

## Operations

| Guide | Contents |
|---|---|
| [HomeLab Ollama](homelab-ollama-runbook.md) | Windows startup and model configuration differences |
| [Platform migration](platform-migration-plan.md) | Target split and proposed rollout phases |
| [n8n jobs](n8n-phase1-jobs.md) | Allowlist, timeouts, and reference schedules |
| [n8n cutover](n8n-cutover-runbook.md) | Connectivity, gradual activation, and rollback |
| [DSM checklist](dsm-task-checklist.md) | Scheduled tasks and wrappers |
| [n8n directory](../n8n/README.md) | Workflows and persistence |
| [Job search](job-search-platform.md) | Profile, opportunities, matching, and CVs |
| [Improvement plan](improvement-execution-plan.md) | Hardening and remaining work |

## Context and history

- [Workspace](../workspace/README.md) and [current context](../workspace/TENAZAS-CONTEXT.md).
- [Staging](../workspace/staging/README.md): candidates and review boundaries.
- [Contributor instructions](../CLAUDE.md): repository rules and operational lessons.
- [Schema notes](../tools/SCHEMA_NOTES.md): observations from May 2026.
- [2026-05-21 report](../tools/REPORT_20260521.md): changes recorded on that date.
- [Planning state](../.planning/STATE.md): milestone history.
- [Documentation review](documentation-review-20261028.md): scope and validation limits.

Milestone plans and dated results retain their historical meaning. Use the current
guides and linked source files for installation, model selection, and routing decisions.
Executable identifiers, log messages, and user-facing command examples retain their
original spelling where changing them would break matching or behavior.
