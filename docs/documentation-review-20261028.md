# Documentation review — 2026-10-28

Reviewed base: `19882c295c0ce4bd543ca2e6c6913ad14c62f14c`.

## Scope

Maintained documentation in the repository root, `docs/`, workspace, and n8n was
updated and standardized in English. Dated operational reports were translated
while retaining their original observations. Historical milestone plans and past
verification results were not rewritten.

The former monolithic contributor inventory was replaced with current English
instructions and links to focused guides. Existing RTK instructions and GSD
workflow/profile blocks were retained. Original inventory notes remain in Git history.

## Architecture design

Added a compact landing-page overview and separate detailed views of deployment,
request handling, and scheduled operations. Diagrams use Mermaid for GitHub
rendering and distinguish optional components, external services, telemetry,
network boundaries, and persistence.

## Technical review

Compared Compose, Dockerfile, startup, configuration, model catalogs/roles,
routing APIs, agents, runner, wrappers, exporters, and the test runner.
Corrected nonexistent paths, stale model chains, Docker health/restart claims,
Prometheus intervals, and n8n connectivity to a localhost-bound runner.

Validation covers local Markdown links, fenced blocks, whitespace, source-backed
model-role tables and canonical labels, and Mermaid rendering. No live NAS jobs,
broker actions, Telegram messages, training, or deployment changes were executed.

## Workflow availability

Repository GSD prompts were inspected, but their external engine was absent from
the checked local/global locations. This review is recorded separately without
changing completed historical milestone status.
