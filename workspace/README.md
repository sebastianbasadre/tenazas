# Tenazas workspace

Versioned [runtime source](runtime/) is mounted at `/home/tenazas/runtime`.
The operational OpenClaw workspace lives in host `state/workspace/`, mounted under
`/home/tenazas/.openclaw/workspace/`. These are separate paths; Compose does not
automatically copy this entire directory into operational state.

- [Current context](TENAZAS-CONTEXT.md)
- [Routing contract](runtime/INTERFACE_CONTRACT.md)
- [Candidate staging](staging/README.md)
- [Setup and mounts](../docs/setup.md)
- [Architecture](../docs/architecture.md)

Review personal and generated files before publication. Some modules persist JSON
and artifacts beside their source as well as in the central state directory.
