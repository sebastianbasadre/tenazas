# Minimal restricted-helper correction — local proposal, not installed

## 2026-10-08 scope reminder

Only a local discovery/source diff and mock tests are authorized in this phase.
The installed VM helper's switch and rollback still act on the tunnel and must
not be used. The discovery proposal needs no VM release/helper modification; see
[its deployment and rollback review](../../docs/tenazas-mcp-discovery-review.md).

The currently installed helper is not copied into this repository. This proposal
defines the exact integration changes and pure renderers in
`tools/mcp_v2_helper_wiring.py`; it does not claim to be an applied diff against the
VM script. Preserve its capture-baseline/install/inspect behavior and sudo restrictions.
No remote action or service change is authorized by these files.

## Gate before prepare and switch

Require the six real producer configuration fields and validate with
`configured_sources`. Validation creates no network request. Reject absent config,
placeholder values, extra keys, invalid source endpoints/limits or credentials in URLs. The
operator must separately attest approved real endpoints, normalized JSON contracts, units and confirmed NAS volume,
inventory observation timestamps and reachability; syntax alone cannot prove that
a hostname is real. Never substitute fictional test URLs for staging configuration.
NAS volume, bind/port, runtime and preflight are operator-validated; parser VERIFY_EXIT=0.
Keep activation blocked until real sanitized feeds, fresh package hashes, effective
sandbox and current rollback baseline are checked. Do not reuse prior packages.

Do not overwrite a preexisting cache or config. If the cache exists, validate its
owner/group/modes and schema first and stop for review rather than chmod/chowning
unknown material. The bootstrap implementation intentionally refuses populated caches.

## prepare branch

Run the reviewed command plan as root with cwd
`/opt/tenazas-mcp/releases/mcp-v2-staging`:

```text
/usr/bin/install -d -o tenazas-mcp -g tenazas-mcp -m 0700 /var/lib/tenazas-mcp-v2
/usr/sbin/runuser -u tenazas-mcp -g tenazas-mcp -- /usr/bin/python3 -m workspace.runtime.mcp_snapshot_producer --cache-root /var/lib/tenazas-mcp-v2 --initialize
```

Use the actual named account UID/GID lookup, not hardcoded identifiers. Before the
first command, require a previously absent dedicated cache and a captured rollback
baseline. After bootstrap, run `validate_cache` and `validate_snapshot` as tenazas-mcp
using the descriptor-safe reader. `prepare_plan` renders that validation command.
Only then emit PREPARE_OK. Do not call collectors or modify the old cache during
offline initialization. Do not start the gateway/timer. Partial initialization is
a failed gate requiring operator review, not an invitation to remove contents.

Offline initialization creates schema_version=1, a UUID generation, generated_at
UTC Z, TTL 300, all sources unavailable, empty sanitized sections and alerts. It
creates state.json/infra.json as 0600, incidents/ as 0700 and a persistent 0600 lock;
state publication is atomic. Valid unavailable state must report stale.

## switch branch

Generate one reversible MCP-only drop-in at
`/etc/systemd/system/tenazas-mcp.service.d/mcp-v2-staging.conf` using the trusted
baseline argv. Preserve every existing argument, particularly --token-file, --cert,
--key and any port/metadata options; replace/add only --cache-root with
`/var/lib/tenazas-mcp-v2`. Reject unsupported baseline runtime/quoting for review.
Do not reconstruct arguments from guesses or copy Authorization values into a unit.

The reviewed template `tenazas-mcp-gateway.conf` represents the verified baseline.
Use the renderer if the effective baseline differs in additional arguments. Set
WorkingDirectory to the release root (workspace is directly below it), clear
ReadWritePaths, add ReadOnlyPaths=/var/lib/tenazas-mcp-v2, and preserve strict system
protection and no-new-privileges. The gateway needs no cache write permission.

Replace the producer template with the corrected one: same release root, existing
/usr/bin/python3, explicit cache/config paths. No v2-current symlink, app subdirectory
or venv is required. Its only persistent writable path is the dedicated cache; /tmp,
/var/tmp and /dev/shm are explicitly read-only, with PrivateTmp isolation retained.
Preserve existing timer cadence and disabled-at-boot state. Do not alter OAuth, TLS,
bearer, tunnel configuration or UFW. Service activation still requires later explicit
authorization and passing configuration/backup/schema gates.

## rollback branch

Retain the existing private baseline mechanism. Before switch, record the exact unit
fragment/drop-ins and the generated drop-in path/hash. Stop new writers before any
restoration; never remove a live lock. Remove only the generated v2 drop-in or restore
its baseline predecessor if one existed; restore the old effective MCP unit and its
WorkingDirectory=/opt/tenazas-mcp/app plus original argv/cache behavior. Preserve old
application and `/opt/tenazas-mcp/app/workspace/runtime/mcp-cache` throughout deployment,
so no v1/legacy data conversion is needed. Do not point an old gateway at the v2 cache.
Restore baseline service active/disabled state using the existing rollback procedure;
leave OAuth unchanged. No enable, unrelated deletion or production action.

No prepared helper plan or drop-in is installed by importing the renderer module.
