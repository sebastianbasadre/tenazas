# Sanitized NAS source boundary — local implementation only

Current validated deployment facts and exact future commands are in
[tenazas-mcp-nas-deployment.md](tenazas-mcp-nas-deployment.md). NAS SSH is
sbasadre@192.168.2.2:4822; no SFTP. Dedicated source port 19105 is proposed, not
opened or reserved. Operator preflight confirmed it unused and the private bind
available; Python 3.13.11 and DSM 219 parser validation (VERIFY_EXIT=0) are confirmed. Existing 9105/9108 remain unchanged.

One source service has exactly GET /inventory, /storage and /network. HTTP handlers
serve prevalidated in-memory JSON and never run collectors, Docker, filesystem reads
or network proxies. A separate refresh loop uses fixed startup-configured NAS
collectors. Nothing starts at import; no NAS/staging unit, endpoint or port is
installed by these changes. SourceServer requires an explicit loopback/RFC1918 IPv4
address. The future CLI requires --bind and --port with no defaults; 0 is allowed
only by the testable factory for ephemeral loopback tests, not the CLI.

## Decision: JSON, not Prometheus text

Producer configuration now has exactly six required keys: inventory_url,
storage_url, network_url, ollama_url, ttl_seconds, timeout_seconds. It validates exact
source fields and consumes already normalized JSON. There are no mappings, unit
conversion or raw per-device exporter labels in the VM. The old eight-key mapping
configuration fails closed rather than silently interpreting bytes as GB. Ollama
tags/ps integration remains unchanged; its selected existing base is
http://192.168.2.6:11434. No live request was made in this implementation phase.

The MCP snapshot stays schema_version 1 with an additive nullable extension only
for healthy/unhealthy/latency_ms in network sections. All other numeric validation,
secret-field rejection, generation, TTL, atomic replacement and Linux policy remain.
Revised producer, snapshot contract and new mcp_source_contract.py must be shipped
together; the previously transferred package is obsolete and must not be reused.

## Exact source contracts

Each response has exactly the documented keys, UTC observed_at ending in Z,
bounded size (1 MiB) and finite numeric values. Unknown/private fields are rejected.

Inventory:

```json
{"observed_at":"<UTC_ISO8601_Z>","containers":[{"name":"<SAFE_PUBLIC_NAME>","up":true,"status":"running"}]}
```

Up is boolean, names obey the public identifier policy, status is one of running,
stopped, healthy, unhealthy, unknown. The NAS-only Docker collector uses fixed
`docker ps -a` with a Names/State-only format, not inspect or the full .ps JSON. It
discards env/mounts/commands/labels/ports and never logs raw output. observed_at is
UTC after the successful local collection. No Docker socket or Docker CLI belongs
in the staging VM; no SSH NAS→VM is involved.

Storage:

```json
{"observed_at":"<UTC_ISO8601_Z>","total_gb":2,"used_gb":0.5,"free_gb":1.5,"used_pct":25}
```

Numbers above are illustrative math, not NAS measurements. **Decimal GB** means
10^9 bytes. 2^30 bytes is 1.073741824 GB, not 1 GB. used_pct is 0–100. The conversion
helper validates consistency and handles zero without division; a real NAS collector
with zero total capacity reports unavailable instead of healthy zero storage.

/volume1 is now operator-validated as the real btrfs storage target. Source CLI
requires that exact mount, rejects root/symlinks/non-mounts/pseudo filesystems and
zero capacity. Baseline total/used/available bytes are recorded in the deployment
plan. Decimal conversion uses the confirmed volume, never exporter rootfs gauges.
No path/device identity enters source JSON. observed_at is the actual sample time.

Network:

```json
{"observed_at":"<UTC_ISO8601_Z>","total":2,"online":1,"missing":1,"healthy":null,"unhealthy":null,"latency_ms":null}
```

Counts are nonnegative integers; online+missing=total. They come from the approved 9105 gauges, not an active reachability probe. No IP/MAC/device names/labels or
raw rows leave the NAS. Optional health counts and latency remain null because the
existing source provides no equivalent measurement; no online→healthy inference.

Network is now sourced only from 9105: tenazas_network_last_scan_timestamp,
device_discovery_online and device_discovery_missing. total is online+missing;
the original scan epoch becomes UTC Z observed_at. All labels/other metrics are
ignored. No raw device inventory or 9108 is consumed, and no scrape timestamp is
fabricated. Missing/invalid authoritative gauges yield unavailable/503.

## Service behavior and failure boundaries

POST/PUT/PATCH/DELETE/OPTIONS/HEAD are rejected (HEAD has no body). Unknown paths,
query strings and path traversal get 404. No route or argument accepts a URL, path
or command from a client. Origin and GET framing/body headers are rejected. Concurrency
is capped at eight workers, client socket timeout is five seconds, responses close
connections. Logs contain fixed event/status only, never payloads/paths/queries or
exceptions. Failed collection yields route 503. The producer keeps last valid values
and their old dates, marks the source unavailable/stale, and never turns null into 0.

## Conditional future producer.json

```json
{
  "inventory_url": "http://192.168.2.2:19105/inventory",
  "storage_url": "http://192.168.2.2:19105/storage",
  "network_url": "http://192.168.2.2:19105/network",
  "ollama_url": "http://192.168.2.6:11434",
  "ttl_seconds": 300,
  "timeout_seconds": 5
}
```

The JSON describes planned endpoints. Port availability, runtime, private access and
source deployment still require gates; no real producer.json was created.

## Updated NAS/staging deployment plan

Use [tenazas-mcp-nas-deployment.md](tenazas-mcp-nas-deployment.md). Earlier last_seen
and unproven-volume assumptions are superseded by the validated facts. This phase
makes local code/artifact changes only; no remote preflight or deployment was run.
