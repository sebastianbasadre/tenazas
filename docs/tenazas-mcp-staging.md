# Tenazas MCP staging overview

Current implementation and authoritative rollout instructions:

- [MCP v2 gateway/producer plan](tenazas-mcp-v2-staging.md)
- [Sanitized source contracts](tenazas-mcp-source-layer.md)
- [NAS deployment commands](tenazas-mcp-nas-deployment.md)
- [DSM 219 directive audit](tenazas-mcp-dsm219.md)
- [OAuth proxy security contract](tenazas-oauth-proxy.md)

The initial three-file MCP handoff has been integrated and hardened. The source
boundary now publishes strict JSON, not Prometheus mappings. Network reads only
three approved 9105 gauges; it has no dependency on the detailed 9108 exporter.
Storage uses confirmed /volume1 in decimal GB, never an assumed rootfs volume.
Producer preserves original observations and optional null fields, publishes a
single schema_version 1 generation atomically and enforces Linux single-writer policy.
Exactly six cached read-only tools use MCP 2025-11-25. OAuth remains separate.

NAS SSH is sbasadre@192.168.2.2 port 4822, no SFTP. NAS Python is
/usr/local/bin/python3.13 (3.13.11), DSM systemd 219 -SECCOMP. Operator supplied PASS
for read-only NAS preflight and actual unit parser validation (VERIFY_EXIT=0).
NAS source unit has no Install section or WantedBy. VM Python is separately
/usr/bin/python3 (3.14.4), not the unsuitable NAS interpreter at the same path.

Previously transferred/installed packages are historical and must not be activated.
Rebuild fresh source-only NAS/VM bundles and manifests after changes, verify every
payload hash, and obtain explicit authorization for transfers, activation and E2E.
Keep OAuth, TLS, bearer and tunnel configuration unchanged. No NAS mounts, credentials
or Docker helper belong in the VM bundle. No boot enablement or production promotion.

Remaining work: approved source launch, effective sandbox and real feed checks,
updated VM helper/package installation, offline bootstrap, local/OAuth/tunnel six-tool
and freshness tests, and ChatGPT E2E. Current parser/preflight PASS is not E2E PASS.
