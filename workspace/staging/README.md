# Improvement staging

This directory holds candidates and drafts. Their presence does not establish
approval or deployment.

| Intended directory | Contents |
|---|---|
| `prompts/` | Prompt adjustments |
| `router_rules/` | Routing rule candidates |
| `runbooks/` | Remediation proposals |
| `tests/` | Proposed test cases |
| `datasets/` | Training material |

Directories and artifacts are created by their respective workflows. Review
`improvement_candidates.py`, `self_benchmark.py`, and approval handlers before
applying a candidate.

The runtime proposal workflow also uses `learning/proposals.json`.
`proposal_executor.py` selects `accepted` proposals, distinguishes deterministic
from manual-only work, and may execute deterministic work on a sandbox branch
with checks, validation, limits, and rollback. Staging files are not necessarily
direct inputs to that executor.

Human review must precede acceptance that enables execution. Record evidence,
validation, and the intended destination; candidate approval is separate from
verification of the resulting deployment.

[Architecture](../../docs/architecture.md) · [Development](../../docs/development.md)
