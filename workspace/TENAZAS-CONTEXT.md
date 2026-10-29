# Tenazas context

Reviewed **2026-10-28**. Repository context for collaborators and coding assistants;
this document does not authorize actions against live infrastructure.

Tenazas is Sebastián's personal assistant, built on OpenClaw and hosted on a
Synology NAS. Telegram is its main interaction channel. It combines Python runtime
modules, HomeLab Ollama inference, cloud providers where configured, observability,
and personal tools. The assistant's user-facing style is concise Rioplatense Spanish;
maintained repository documentation is English.

## Current references

- [Overview](../README.md): scope and documentation entry points.
- [Architecture](../docs/architecture.md): deployment and request/scheduling diagrams.
- [Setup](../docs/setup.md): requirements and bootstrap limitations.
- [Infrastructure](../INFRA.md): services, ports, and external dependencies.
- [Models](../MODELS.md): independent selectors and fallback chains.
- [Routing contract](runtime/INTERFACE_CONTRACT.md): labels and APIs.
- [Development](../docs/development.md): environment and validation.

## Configuration boundaries

The coordinator is controlled by `USE_AGENT_COORDINATOR` or the Telegram setting
read by Tenazas; it defaults to disabled. Its implementation does not prove it is
active on the NAS.

n8n uses the Compose `migration` profile. DSM, internal loops, and workflows can
coexist in an existing deployment; inventory them before moving schedules.

The gateway enforces a strict configuration schema. Helper templates do not replace
a validated OpenClaw configuration. Credentials and chat IDs come from operator
state/environment rather than fixed documentation values.

Accepted proposals may be executed under the executor's controls. Candidates are
not evidence of approval or deployment; see [Staging](staging/README.md).

Milestone plans and dated reports preserve historical decisions and results.
They do not establish the current inventory or evaluation metrics.
