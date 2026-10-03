# Tenazas Read-Only MCP Gateway

## Goal

Expose a deliberately small, read-only observability surface for ChatGPT without granting
ChatGPT direct access to the NAS, Docker socket, SSH keys, or arbitrary command execution.

## Security boundary

The MCP gateway MUST run as a separate service from `openclaw`.

It MUST NOT receive:

- `/var/run/docker.sock`
- `./ssh-keys` or any SSH agent/socket
- host filesystem mounts
- shell/exec tools
- Docker lifecycle operations
- write-capable Tenazas endpoints
- secrets unrelated to authenticating the gateway itself

The gateway is deny-by-default. Only the tools explicitly listed below may be exposed.

## Phase 1 tool allowlist

| Tool | Purpose | Mutates infrastructure |
|---|---|---|
| `get_status` | Aggregated Tenazas and infrastructure health | No |
| `get_containers` | Sanitized container state summary | No |
| `get_models` | Ollama/model availability and selected model | No |
| `get_storage` | NAS/storage capacity and health summary | No |
| `get_network_health` | Sanitized service/network reachability | No |
| `get_recent_alerts` | Recent infrastructure alerts | No |

No generic URL fetcher, filesystem reader, shell command, SQL/query console, log path argument,
container-name action, or arbitrary host parameter is part of Phase 1.

## Data minimization

Responses must not include credentials, tokens, cookies, authorization headers, environment
variables, SSH material, raw configuration files, or arbitrary log contents. Internal addresses
should only be returned when needed for diagnosis and may be redacted at the adapter boundary.

The adapter projects model, storage and network snapshots onto an explicit field allowlist;
unknown fields and raw headers/configuration are discarded. Credential key variants are
normalized and removed recursively. Text containing credential labels, authentication schemes,
credential-bearing URLs, private-key material or opaque long identifiers is redacted before
truncation. Container and alert text receives the same filtering.

Only the configured state snapshot, infrastructure snapshot and direct JSON incident files
are readable. There is no client-supplied path or argument. Link files, junctions, non-regular
files, hard links, malformed JSON and oversized snapshots fail closed. POSIX reads pin directory
handles and use no-follow, read-only opens; Windows also checks resolved paths, file identity
and reparse points, but that platform's fallback is not a substitute for an immutable cache.
Snapshots are limited to 1 MiB, collections to 100 items and recent alerts to 20 from at most
1,000 directory entries.

The cache producer remains trusted: an arbitrary short password placed under a legitimate
observability field cannot be distinguished reliably from ordinary text. Do not mount raw
credential/configuration directories into a future gateway. Provide a dedicated sanitized,
read-only cache with an immutable directory layout and OS-level isolation.

This PR implements the local adapter only. It does not implement or validate a remote MCP
server, authentication, TLS, rate limiting, audit logging or deployment isolation. Those remain
acceptance requirements before any remote exposure; passing adapter tests does not prove them.

## Authentication and transport

The eventual remote transport must be encrypted and authenticated. Do not expose DSM, Docker,
SSH, Ollama, Prometheus, Grafana, or the OpenClaw gateway directly to the public Internet for
this integration.

The public-facing gateway should use a dedicated credential with rotation/revocation support,
rate limiting, and an audit trail. A private overlay network or authenticated reverse proxy may
sit in front of the gateway.

## Phase 1 non-goals

The following are intentionally absent:

- restart/stop/start container
- deploy/update/pull image
- reboot/shutdown NAS or hosts
- modify configuration
- write/delete files
- execute shell commands
- change smart-home state
- change model configuration

Adding any mutation requires a later security review and explicit user approval.

## Proposed data flow

```
ChatGPT
   |
   | authenticated + encrypted MCP
   v
Tenazas MCP gateway (read-only, isolated)
   |
   | fixed internal adapters only
   v
Sanitized Tenazas observability/status APIs
   |
   +--> runtime health
   +--> container summary
   +--> model availability
   +--> storage summary
   +--> network/service probes
   +--> recent alerts
```

## Acceptance criteria before remote exposure

1. The MCP process has no Docker socket and no SSH keys.
2. Tool enumeration exposes only the six allowlisted read-only tools.
3. Attempts to request unknown tools/actions fail closed.
4. Responses are sanitized for secrets.
5. Authentication failure returns no infrastructure data.
6. Audit logs record access without recording credentials.
7. Tests prove there is no restart/exec/write primitive reachable through the MCP surface.
8. The service is validated locally before any remote ingress is configured.
