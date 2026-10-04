# Security review of PR #3, feature/tenazas-readonly-mcp

Scope: complete PR diff against main/merge base `19882c295c0ce4bd543ca2e6c6913ad14c62f14c`,
including every local implementation commit. This review authorizes only the requested branch push
and PR metadata update. Production has not been changed.

## Findings and disposition

| Area | Review result |
| --- | --- |
| Secrets | No real credential is intentionally committed. Canary values in tests are synthetic. Configured production inventory/variants are checked against added diffs and outputs without persisting or printing values. Unknown credentials remain an inventory-completeness limitation. |
| Docker socket | No gateway/adapter/policy access or mount. Validation probes check socket absence; references in docs/tests are prohibitions/assertions. |
| SSH and execution | No SSH, shell, subprocess, eval/exec, dynamic imports or arbitrary URL fetch in the serving code. Operator-side SSH/Docker commands used for isolated validation are outside the MCP API and are not included in the gateway image. |
| Mutable operations | Exactly six read-only tools. Trusted producer writes only its operator-selected dedicated export, never imported by gateway/adapter. Test probes create ephemeral fixtures/credentials only. |
| Filesystem | Adapter contents restricted to dedicated state/infra/direct JSON incidents. Gateway additionally reads operator-configured private bearer/TLS files; cache policy inspects only fixed cache layout metadata. No client-supplied path or collector. Python library loading and TLS/socket I/O are normal runtime capabilities, not exposed tools. |
| Links and races | POSIX adapter pins no-follow directory handles, rejects nonregular files/hardlinks and checks inode identity. Bearer reads reuse pinned no-follow opens. Cache policy rejects links/unmanaged entries/unsafe ownership. RO mounts and private parent directories are still required; the trusted producer is not designed for hostile writable parent directories. |
| Permissions | Exporter 0700 directories/0600 files, including reused exports. Startup and every tool read enforce those modes and current UID ownership. NAS inherited ACLs and raw 0777 writer migration remain explicit production prerequisites, not assumed complete. |
| Authentication | All recognized HTTP methods require the dedicated bearer before handling. Missing/wrong/altered/duplicate credentials deny data; absent/invalid private credential file fails closed; replacing/removing the file revokes access. Unsupported/parser requests receive fixed generic errors, no tools/data. |
| Bypass | No alternate tool route, query-string token, cookie auth, client-selected backend or mutating tool. Browser Origin rejected. HTTP ambiguous framing and arbitrary tool arguments denied. |
| Logs/responses | Corrected stdlib HTTP parser error reflection. Audit contains status only, no request lines, headers, bodies or tool results. Tool backend errors are generic. Credentials redacted before truncation/export; output fields bounded/projected. |
| Transport | HTTPS mandatory in CLI; minimum TLS1.2, loopback bind, no published ports. Handshake timeouts/connection limits prevent one stalled TLS client blocking the listener. Global per-process rate limit is not distributed DoS protection. |
| Deployment | Added a network-none staging template with RO cache/root/private credentials, UID1026, dropped caps and no-new-privileges. Production compose untouched. Template must use an immutable reviewed image and a dedicated sanitized cache. |
| ChatGPT compatibility | Internal bearer is proven in staging; OAuth/tunnel proxy and end-to-end ChatGPT compatibility are proposed, not implemented. This does not claim production readiness. |

Two issues corrected during this review: HTTP error text could reflect user-controlled request
text, and generic rejected HTTP methods did not require auth. Added private-cache policy validation,
credential-directory link protection and ignored generated cache/private deployment files in Git.

## Linux/NAS validation

Python3.11.2 / pytest9.1.1; UID/GID1026; isolated disposable containers, root/cache/code RO,
network-none, no capabilities, no-new-privileges. No production code/config writes or restarts.

- Specific security tests: **143 passed**, 0 failed, 0 skipped (adapter70, producer9, gateway55, cache policy9).
- HTTPS integration: **12 checks passed**, including authorized real-cache reads, invalid credentials,
  rotation/revocation, parser reflection, rejected Origin/GET, rate limit, stalled TLS, private RO cache
  and credential-free output/audit. Verified certificate and hostname; no host ports published.
- Full `python -m pytest` stops on the same six inherited collection errors. With
  `--continue-on-collection-errors`: **2831 passed, 114 failed, 9 skipped, 6 errors, 29 subtests passed**.
  Same inherited failure IDs and normalized messages as baseline; no new failures. Detailed proof:
  [validation report](mcp-readonly-validation.md).
- Actual-cache/export/six-tool outputs: no matches against the ten unique configured credentials
  and their URL/Base64/JSON variants. This is scoped evidence, not a universal unknown-secret guarantee.
- Independent UID1027 container was denied both cache snapshot reads while UID1026 could read them.
  This demonstrates staging filesystem isolation; future production ACLs still require their own check.

## Production prerequisites remain blocking

The deployment must retire the 0777 cache surface: dedicated sanitized directory0700/files0600,
gateway mount RO; additionally restrict the legacy raw file and ensure every canonical writer
preserves0600. Remove inherited ACL grants and prove denial for another UID. These steps are
specified in [the migration/ingress plan](mcp-readonly-deployment.md); no automatic migration or
scheduler integration is claimed by this PR. Production must not start until those gates pass.

Proposed ChatGPT ingress: private OpenAI Secure MCP Tunnel (outbound HTTPS only), OAuth2.1
resource-server proxy with established identity provider, PKCE S256, exact resource/audience,
`tenazas:read` and allowlisted user/workspace; fixed certificate-verified HTTPS gateway upstream
with separate internal bearer. No DSM/Docker/SSH/Ollama/OpenClaw public listener or forwarding.
OAuth and tunnel are future work, requiring their own staging tests and user authorization.

**No merge or deployment is authorized by this review.**
