# MCP read-only: proposed migration and ingress

This document and `deploy/mcp-readonly/compose.staging.yml` are review artifacts.
No production migration, ingress, image publication or service startup has been performed.
The later [private-state staging review](private-state-staging.md) records Synology
ACL inheritance and the proposed raw-state subdirectory. Its consumer compatibility
gate remains open; the earlier migration outline below must not be executed as-is.
The staging manifest is deliberately network-none with no published ports; it cannot
connect ChatGPT until a separately reviewed tunnel/OAuth configuration is provided.

## Mandatory cache cutover before production startup

The deployment must replace the gateway's consumption of the old 0777 raw cache with a
separate sanitized cache owned by UID/GID 1026, directories **0700**, all JSON files **0600**,
mounted **read-only** in the gateway. It must not merely chmod the raw data and then expose it.

1. Keep the future gateway stopped. Identify all existing raw-cache writers/readers and
   NAS ACLs. Confirm the trusted producer UID and credential inventory completeness.
2. Provision a dedicated cache in a protected NAS namespace, proposed
   `/volume1/@tenazas-private/mcp-cache`, owner 1026:1026, mode 0700. Verify native
   ACLs and ancestor replacement permissions; mkdir's mode alone is insufficient
   on this Synology. Do not reuse the raw runtime directory or mount the OpenClaw workspace.
3. Run the trusted exporter outside the gateway, passing the credential inventory only
   in memory/stdin. Export the fixed state/infra/incident schema. The exporter enforces
   0700/0600, atomic file replacement and rejects unmanaged entries. Configure one owner
   of periodic exports; no duplicate n8n workflow/scheduler is enabled by this PR.
4. Retire the 0777 raw-cache access through the proposed private-state subdirectory,
   coordinated reader references and the private atomic writer. Never rely on umask
   alone under inherited Synology ACLs or chmod the entire runtime directory: the
   staging rehearsal demonstrated inherited child permission recalculation. Close
   the full consumer compatibility gate first. This writer/routing integration is a
   **deployment prerequisite**, not applied automatically by this PR. Production
   remains blocked until demonstrated. Preserve the original raw OpenClaw schema;
   sanitized MCP data is a separate export.
5. Remove inherited extended ACL grants on the new dedicated cache and test reads as
   UID 1026 and denial as a different UID. Mode bits alone do not prove NAS ACL isolation.
6. Mount only the dedicated cache at `/app/workspace/runtime/mcp-cache:ro` in an immutable
   gateway image containing only adapter, cache policy and gateway. No producer, probes,
   original runtime, Docker socket, SSH keys, credential inventory or OpenClaw config.
7. Validate the resolved manifest and observed container mounts: RO cache/root, UID 1026,
   no capabilities, no-new-privileges, no host network and no published host ports.
   `mcp_cache_policy.validate_cache()` refuses unsafe modes/ownership/links/extra files at
   CLI startup and before each tool read. Credential and TLS files are separately mounted RO.
8. Repeat authentication, secret scanning, reader/other-UID permissions and real output
   checks on that exact deployment before enabling the approved ChatGPT connector.

Therefore the intended deployment replaces the old cache surface with 0700/0600 + RO.
The current production cache remains unchanged until explicit authorization and successful
validation of the canonical writer and ACL transition. This PR alone is not a migration.

## Exact proposed ChatGPT connection

Prefer a private **OpenAI Secure MCP Tunnel**, with no inbound NAS ports. The operator
must first confirm tunnel/workspace permissions and availability for the target account.
[Official tunnel documentation](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels).

```
ChatGPT (authorized workspace, user OAuth access token)
  -> OpenAI-hosted tunnel endpoint (workspace-associated)
  -> outbound-only tunnel-client in an isolated connector environment
  -> private OAuth resource-server proxy (fixed /mcp upstream)
  -> HTTPS loopback gateway with separate internal bearer credential
  -> dedicated sanitized cache mounted RO
```

The tunnel-client uses a dedicated, minimally scoped OpenAI runtime credential and outbound
HTTPS only to the documented OpenAI tunnel service. It receives no NAS/Docker/SSH credentials
and must not share OpenClaw's network or credential directories. The public listener belongs
to OpenAI, not the NAS. The current network-none staging manifest is **not** this future topology.

The OAuth resource-server proxy must use an established identity provider (proposed: Auth0),
Authorization Code + PKCE S256, exact registered redirect URIs, protected-resource and
authorization-server discovery, a dedicated MCP audience/resource, scope `tenazas:read`,
and a user/workspace allowlist. Verify signature, issuer, audience, expiry/not-before and
scope on every request. No anonymous or mixed-auth tools. The proxy strips the client
Authorization header and inserts a separately rotated internal bearer for the single
fixed HTTPS gateway destination, verifying its certificate. It must not offer arbitrary
URL proxying or pass through NAS service endpoints. Logs must omit headers, token values,
request bodies and returned cache data. Rate limiting precedes tool access.
[Official authentication documentation](https://developers.openai.com/plugins/build/auth).

OAuth discovery may traverse the tunnel; the authorization server must itself be reachable
for the user's browser and OAuth flow. Keep that identity provider independent from DSM and
OpenClaw. The internal bearer implementation in this PR is **not** a completed ChatGPT OAuth
implementation. This proxy, identity-provider setup, tunnel and end-to-end ChatGPT compatibility
require a later implementation and staging validation; none were created by this review.

If Secure MCP Tunnel is unavailable, stop and review a separate HTTPS MCP-only ingress proposal.
Do not silently fall back to anonymous access, generic NAS proxying or inbound port forwards.

## Internet exposure prohibition

This proposal exposes **none** of DSM, Docker, SSH, Ollama or OpenClaw directly to the Internet.
No router/NAS firewall changes, NAT/UPnP forwards, DSM reverse-proxy rules or public DNS entries
for those services are authorized or introduced. No `docker-compose.yml` changes are included.
Existing Internet exposure has not been audited; this is a guarantee about the proposed integration,
not a statement about unrelated router settings.

## Rollback

Disable the connector/tunnel and stop only the separate MCP components; revoke both OAuth grants
and the internal bearer. Preserve restrictive cache permissions. Never restore 0777 as rollback.
OpenClaw's original schema and service remain separate from the exported cache.

**Push/PR update does not authorize merge, production migration or deploy.**
