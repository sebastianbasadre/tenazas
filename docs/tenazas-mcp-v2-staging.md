# MCP v2: controlled staging plan, not executed

## Wiring correction — local only, staging blocked

The installed staging release has workspace/ directly under
/opt/tenazas-mcp/releases/mcp-v2-staging. The previous producer template's app/venv
paths were incompatible. The corrected template and gateway drop-in are local
proposals only; installed VM units/helper are not changed by this revision. See
[helper-changes.md](../deploy/mcp-v2/helper-changes.md) for the precise prepare,
switch and rollback integration points. Existing install/inspect success does not
authorize activation. The old package's producer template hash is now superseded;
any future corrected transfer must be separately approved and verified.

### Current producer configuration (supersedes Prometheus mappings)

The producer now consumes strict sanitized JSON /inventory, /storage, /network feeds.
See [tenazas-mcp-source-layer.md](tenazas-mcp-source-layer.md) for exact contracts,
units, timestamp semantics and the NAS-side deployment gates. Required keys:
inventory_url, storage_url, network_url, ollama_url, ttl_seconds, timeout_seconds.
No mapping fields or legacy Prometheus config are accepted. The current schema is
[producer.schema.json](../deploy/mcp-v2/producer.schema.json); configured_sources is
normative. Timing is explicitly 300/5. No real producer.json was generated.

NAS target /volume1, address, Docker capability and metrics are operator-validated.
Operator NAS preflight and real DSM parser validation passed: Python 3.13.11,
systemd 219 -SECCOMP, /volume1 btrfs, Docker, original network timestamp, port 19105
unused and private bind available. Source activation/VM rollout/E2E remain gates. The prior
transferred package is obsolete; a new reviewed payload/manifest must include
mcp_source_contract.py in the MCP release before any deployment. NAS source code and
its Docker collector must never be installed in the VM. No source deployment,
collector or port was started by this repo change.


MCP v2 is the automated pipeline, with public snapshot **schema_version 1**.
MCP protocol remains **2025-11-25**. The six tools and OAuth implementation remain
read-only. No commit, push, merge, remote modification or deployment is authorized
by this document. Do not change NAS, production, OAuth configuration or credentials.

## Audit and implementation

Completed gaps: producer CLI; explicit JSON source URL allowlist/config validation; offline
cache initialization; Linux flock across previous-state read, collection and atomic
publication; descriptor-based previous-state checks; bounded retry during rename;
strict UTC/source chronology; global stale detection; concurrent-reader tests;
staging service/timer templates. Existing OAuth source is unchanged in this phase.

Sources use bounded GET, no redirects/ambient proxies or URL credentials. The
explicit URLs are three sanitized JSON feeds and Ollama tags/ps. Original observation
timestamps survive into source freshness; unavailable optional network fields remain
null. Failed sources retain prior data and original dates. Alerts remain empty/inactive.

Producer config keys (all required): inventory_url, storage_url, network_url,
ollama_url, ttl_seconds, timeout_seconds. URLs must be supplied privately after NAS
source verification. TTL range 1–3600 (use 300), timeout >0–30 (use 5). No exporter
mappings, environment or production credentials are consumed.

Both `produce_once` and public `atomic_replace` require the same Linux lock. The
persistent .producer.lock is 0600, regular, single-linked, empty and owned by
tenazas-mcp:tenazas-mcp. Never remove a live lock. The cache gate accepts exactly
state.json, infra.json, empty incidents/, optional lock and at most one safe writer
temporary. A crash-abandoned temporary blocks further publication; recovery requires
stopping writers and operator review. Bootstrap requires an empty pre-provisioned
0700 directory and makes a valid, all-unavailable seed without any network call.
Interrupted bootstrap is not automatically repaired or overwritten.

## Deployment preconditions — collect privately after authorization

Record the effective tenazas-mcp unit, fragment/drop-ins, interpreter, User/Group,
working directory, TLS paths, bearer path and ports. Record tenazas-oauth-proxy
and tenazas-tunnel service state and disabled-at-boot status. Do not print secret
values or full environments. Confirm the existing gateway bearer file satisfies
its private 0400/0600 policy and equals the proxy's internal bearer, using a private
boolean check. The proxy's separately approved 0640 policy does not change the
gateway token-file policy. If staging uses one shared 0640 file, stop and resolve
the credential arrangement explicitly rather than weakening the gateway.

Verify the exact existing HTTPS upstream address/port and certificate trust. The
new gateway must occupy that same private loopback listener. Do not add public
listeners, modify UFW rules, change OAuth allowlists or weaken systemd sandboxing.
Verify outbound source reachability under existing restrictions; missing permission
or an undefined source contract is a deployment blocker, not permission to relax UFW.

Inventory existing cron/timers/processes that write any MCP cache. Do not start the
legacy writer against the new directory. The new lock coordinates compliant v2
writers only; it cannot stop a legacy/external process that ignores flock.

Completed NAS preflight does not authorize activation. Still pending: live sanitized
source validation, effective sandbox/Docker access under the new unit, fresh package
transfer/install/hash review, current VM baseline/credential layout and final E2E.
No NAS publication or VM service change was executed by this stabilization pass.
Fixtures remain local-test inputs only; never deploy a fixture config.

## Exact payload and backup

Use the verified isolated release /opt/tenazas-mcp/releases/mcp-v2-staging/:

* workspace/__init__.py
* workspace/runtime/__init__.py
* workspace/runtime/mcp_source_contract.py
* workspace/runtime/mcp_snapshot_contract.py
* workspace/runtime/mcp_snapshot_producer.py
* workspace/runtime/mcp_cache_policy.py
* workspace/runtime/mcp_readonly_adapter.py
* workspace/runtime/mcp_readonly_gateway.py
* Runtime: existing /usr/bin/python3 (verified Python 3.14.4). These modules use only the standard library; no release venv is needed.

Do not copy OAuth files, .env, caches, handoffs, credentials, certificates, NAS helper
or training artifacts. Tests go in a separate validation directory/environment.

Before changing anything, create a root-only 0700 backup directory and use umask
077. Back up the complete existing /opt/tenazas-mcp/app plus the MCP environment
needed by the actual ExecStart; preserve ownership, permissions, ACL/xattrs. Back up
the MCP unit fragment/drop-ins with their original paths and a private effective-unit
capture. If cache is outside app, back it up separately. Record HEAD-independent
source hashes, active/disabled service state, old WorkingDirectory/argv and Python version.
Backups can contain secrets: keep them on VM, never in Git/downloads/logs. OAuth and
tunnel files are not overwritten; retain their existing service configuration.

## Authorized execution order

1. Verify preconditions; prepare backup and immutable release without activation.
2. Validate import/syntax with /usr/bin/python3 and run MCP tests on Linux
   with pytest 9.1.1 and ruff 0.16.10 in the validation environment. OAuth tests also
   require the pinned PyJWT[crypto] 2.15.1 test dependency; do not alter the deployed
   OAuth environment. Confirm payload hashes match the reviewed repo files.
3. Supply and privately validate the real /etc/tenazas-mcp-v2/producer.json, readable
   by the MCP account (root:tenazas-mcp 0640 is suitable). If source host/port or volume proof are
   missing, STOP before prepare/cache creation; do not generate placeholder config.
4. Verify workspace/ is directly under the release root. No v2-current symlink,
   app subdirectory or virtualenv is required. Require the real producer configuration
   before prepare/switch; source activation and approved real feed validation remain deployment gates.
5. Only after configuration and rollback gates, provision the previously absent
   /var/lib/tenazas-mcp-v2 as tenazas-mcp:tenazas-mcp mode 0700, outside the old cache.
   Initialize offline as tenazas-mcp (no collector calls):

   ```sh
   /usr/bin/python3 -m workspace.runtime.mcp_snapshot_producer --cache-root /var/lib/tenazas-mcp-v2 --initialize
   ```

   Run from the release root directory under the expected UID/GID. Validate the new
   seed schema and permissions before starting any gateway against it.
6. Stop tenazas-tunnel, then tenazas-mcp. Stop any old writer of the MCP cache. Leave
   the hardened OAuth proxy running and unmodified.
7. Add a reversible MCP drop-in: retain all existing hardening/TLS/bearer parameters,
   replace WorkingDirectory/ExecStart with the new release module and append
   `--cache-root /var/lib/tenazas-mcp-v2`. Retain the existing private bind/port.
   Clear gateway ReadWritePaths and set ReadOnlyPaths=/var/lib/tenazas-mcp-v2; preserve
   ProtectSystem=strict and NoNewPrivileges. Preserve every baseline argument, including
   token-file, cert, key and port/metadata options. Do not grant gateway write access.
   If effective restrictions conflict, stop for review instead of removing them.
8. Install the reviewed snapshot service/timer templates from deploy/mcp-v2. They use
   the named MCP account, umask 0077, no-new-privileges, strict filesystem protection
   and write access only to the new cache. Validate effective units after combining
   any local overrides. Run systemd-analyze verify, then daemon-reload. No enable.
9. Run one producer cycle via the oneshot service. Verify the approved real sources.
   Do not substitute fixture configuration. Start tenazas-mcp, smoke test locally
   through existing OAuth, then manually start the timer, then tenazas-tunnel.
10. Observe two timer cycles and execute the E2E below. All services/timer must remain
    disabled at boot. Stop and wait for review at the end; no promotion is implied.

Producer command (systemd template supplies directory/user):

```sh
/usr/bin/python3 -m workspace.runtime.mcp_snapshot_producer --cache-root /var/lib/tenazas-mcp-v2 --config-file /etc/tenazas-mcp-v2/producer.json
```

Exit 0: a complete valid generation was published (check source availability; this
does not imply all sources succeeded). Exit 2: another compliant writer holds the
lock. Exit 1: fixed generic failure, no raw exception/config/body logging.

## Validation commands and criteria

Run in a private shell as the MCP account from the release root directory:

```sh
python -c 'from workspace.runtime.mcp_cache_policy import validate_cache; validate_cache("/var/lib/tenazas-mcp-v2")'
python -c 'from pathlib import Path; import json; from workspace.runtime.mcp_snapshot_contract import validate_snapshot; validate_snapshot(json.loads(Path("/var/lib/tenazas-mcp-v2/state.json").read_bytes()))'
systemctl is-enabled tenazas-mcp tenazas-oauth-proxy tenazas-tunnel tenazas-mcp-snapshot.timer
systemctl list-timers tenazas-mcp-snapshot.timer
```

Commands must exit successfully for policy/schema; the boot check must report disabled
for the staged services (a disabled result itself has a nonzero systemctl exit status).
Inspect only safe metadata: generation, generated_at, freshness, size/modes, counts.
Never print the complete snapshot or token. Run as the appropriately authorized
operator where systemctl requires it; do not change file modes to make checks pass.

Run all five MCP/OAuth test modules and Ruff in the separate validation environment.
Record Python version, passed/failed/skipped counts and platform. No POSIX tests may
be skipped for the final Linux verification. Producer/network tests use fixtures.

For live smoke/E2E, use the existing private HTTPS endpoints and authorized token
flow; those addresses and the token acquisition mechanism are not provided here.
Supply Authorization from a private file/helper, never inline CLI/history or verbose
curl output. Call initialize (2025-11-25), discover, list and all six tools with empty
arguments and optional _meta. Validate string/int IDs, six read-only annotations,
unknown-tool rejection and no write/resource/shell capability. Call get_status once
and verify every subsection has its same generation. Existing OAuth 401/403 behavior
must remain unchanged. No internal bearer or client JWT may enter logs/responses.

Record generations before/after two scheduled cycles: each generation changes and
generated_at advances; successful observation times advance while failed-source
times do not. Stop only the timer and wait for any active producer oneshot to finish,
then stop the oneshot before testing TTL. After TTL+margin, all relevant tool responses
must be stale and retain the same last data/generation. Restart timer: successful
observations return fresh. Test source failures with an isolated fixture profile,
never by modifying NAS/production endpoints or the main OAuth configuration.

Run ChatGPT E2E through the existing OpenAI Secure MCP Tunnel: all six tools, freshness
before/after cadence and expiry, alerts empty/inactive, safe errors and no write tools.
Use a private local test harness for transport details ChatGPT does not surface.

Success: Linux tests pass, no new public listener/firewall weakening, proxy unchanged,
correct schema/permissions, single compliant writer, observable cadence/staleness,
all six tools and ChatGPT E2E pass, and boot remains disabled. Abort on any mismatch,
secret/topology leakage, unknown writer, incompatible credential layout, partial
generation, expired data reported fresh or missing backup/rollback evidence.

## Rollback

Stop tunnel, new timer, new producer service (wait/terminate its process before cache
recovery), then MCP gateway. Leave OAuth proxy unchanged. Remove only the new MCP
drop-in; restore any actually modified MCP unit/files/argv from the backup and
restore the old gateway/cache pair together. Restore the original writer only if it
was running before the window; never point it at the v2 directory. daemon-reload,
start old MCP, smoke through the unchanged OAuth proxy, then restore the tunnel's
previous active state. Do not enable any service. Keep the v2 cache/release and private
backup for diagnosis; do not delete a lock while a writer might still be alive.

Do not restore a legacy gateway against a v1 snapshot or new gateway against legacy
state. A directory-fsync failure after atomic replace may leave the new generation
visible but durability uncertain; stop/review, never claim old state was preserved
after every failure. Bootstrap failure leaves a partial dedicated directory requiring
review, not automatic deletion. No production/NAS rollback action is part of this plan.

## Proposed commits — not created

1. feat(mcp): add automated read-only snapshot pipeline — MCP modules, NAS-only source
   helper, four MCP test modules, deployment templates and MCP documentation.
2. feat(oauth): harden MCP OAuth resource proxy — OAuth modules/tests/documentation and
   the optional PyJWT dependency group (including approved 0640 bearer policy).
3. ci: add MCP and OAuth validation — workflow and CI dependency; assign the pytest
   Linux marker to commit 1 and cache .gitignore exclusion to commit 1. Split shared
   pyproject hunks rather than staging the entire file into one unrelated commit.
