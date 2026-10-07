# Validated NAS reconciliation — local only, no deployment performed

Operator-validated facts supersede the earlier blocked NAS SSH preflight:
NAS 192.168.2.2, DSM SSH **4822**, public-key user **sbasadre**, no SFTP.
/volume1 is a real btrfs mount. Total=28770439729152 bytes, used=12026896465920,
available=16743543263232. These are the supplied baseline, not a new live probe.
Operator preflight PASS: runtime_supported=true, root_required=false, btrfs confirmed,
Docker capable (26/25), network confirmed, port 19105 unused and private bind available.
DSM parser validation on /tmp/tenazas-mcp-sources.service returned VERIFY_EXIT=0;
the actual unit has no Install section or WantedBy. These gates are completed.
Effective sandbox operation, service activation and E2E remain unexecuted here.
Docker is /usr/local/bin/docker; sbasadre belongs to docker and reads inventory
without sudo (26 total, 25 running, one exited at the supplied observation).

## Source wiring

The source is a separate service/process. Existing metrics_server.py on wildcard
9105 and network_exporter.py on 9108 are never changed, stopped or replaced.
Proposed dedicated port: **19105**, private bind **192.168.2.2** only. This is a
design selection; the operator preflight confirmed it free, not reserved/open. Preflight checks all
current local TCP socket states for collision; future startup must abort on conflict.
Do not modify firewall rules to make it work. Source JSON contains no IP/port/path.

Inventory uses the absolute Docker executable and a Names/State-only format.
HTTP handlers serve prevalidated in-memory JSON and never run Docker or collectors.
Storage uses only /volume1, with mount/symlink/pseudo-filesystem guards and nonzero
capacity. Decimal GB=10^9 bytes; used=total-available. Existing rootfs gauges are not
substituted. Device names, MAC/IP labels, commands and environment are never returned.

Network collector GETs only http://192.168.2.2:9105/metrics, with bounded size,
timeout, no redirects/proxies and no credential headers. Its allowlist is exactly:

* tenazas_network_last_scan_timestamp: original epoch seconds -> UTC ISO Z observed_at
* device_discovery_online: nonnegative integer count
* device_discovery_missing: nonnegative integer count

total=online+missing. Example supplied counts: 40+75=115. All other samples/labels
are ignored; missing/labeled-only/duplicate/nonfinite samples fail closed, never
renew observation time. No dependency on 9108 or device_inventory.json/last_seen.
healthy/unhealthy/latency_ms remain null. Clock/scan cadence may legitimately yield
stale results at TTL 300; do not fabricate a fresher date.

## Read-only preflight command — run later, not executed in this phase

The script is standalone, does not import the source package, needs no root for
normal checks, prints only metadata/counts and performs no writes/listener creation.
It supports older Python for diagnostics and reports whether Python>=3.11 is present.
No SFTP is used. From Windows PowerShell:

```powershell
Get-Content -Raw -LiteralPath tools/mcp_nas_preflight.py | ssh -p 4822 -o BatchMode=yes -o StrictHostKeyChecking=yes sbasadre@192.168.2.2 '/usr/local/bin/python3.13 -'
```

NAS interpreter is verified: /usr/local/bin/python3.13 (3.13.11). Never use
/usr/bin/python3 (3.8.15). DSM systemd 219 has -SECCOMP; see
[the directive audit](tenazas-mcp-dsm219.md). Unit uses v219-compatible
ReadOnlyDirectories, ProtectSystem=full and no address-family seccomp filter.
The preflight checks volume identity, Docker read access, the three network metrics,
interface address and current use of 19105. Its PASS is not deployment authorization.

## Build separate local packages — review only

```powershell
python tools/mcp_build_packages.py nas-sources --output C:/Temp/tenazas-mcp-reviewed
python tools/mcp_build_packages.py mcp-v2 --output C:/Temp/tenazas-mcp-reviewed
```

Outputs are fresh source-only tarballs/manifests; the builder refuses overwrites and
checkout-local output. NAS package contains source server/contracts and Docker helper.
VM package contains gateway/adapter/producer/contracts and corrected wiring units,
without Docker helper, NAS server, OAuth, cache, config, .env or TLS credentials.
The previous installed/transferred VM package is obsolete. Reverify all hashes and
member paths after rebuilding and transferring. The restricted staging helper must
be reviewed for the new payload layout/contracts before any future install.

## Exact future NAS deployment commands — NOT executed, require authorization

First pass preflight and review DSM systemd support and source user/sandbox settings.
Create a new source release directory only after authorization; never overwrite the
existing Tenazas runtime. The target in the proposed unit is:
/volume1/docker/tenazas-mcp-sources. Copy by SSH stdin, not SFTP:

```powershell
# Only after destination approval/provisioning; no shell or SFTP for binary transfer.
foreach ($artifactName in @('nas-sources.tar.gz', 'nas-sources.manifest.json')) {
  $artifactProcess = [System.Diagnostics.Process]::new()
  $artifactProcess.StartInfo = [System.Diagnostics.ProcessStartInfo]::new('ssh')
  $artifactProcess.StartInfo.UseShellExecute = $false
  $artifactProcess.StartInfo.RedirectStandardInput = $true
  foreach ($sshArgument in @('-p', '4822', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes', 'sbasadre@192.168.2.2', "cat > /volume1/docker/tenazas-mcp-sources/$artifactName")) {
    $artifactProcess.StartInfo.ArgumentList.Add($sshArgument)
  }
  [void]$artifactProcess.Start()
  $artifactStream = [System.IO.File]::OpenRead("C:/Temp/tenazas-mcp-reviewed/$artifactName")
  try { $artifactStream.CopyTo($artifactProcess.StandardInput.BaseStream) }
  finally { $artifactStream.Dispose(); $artifactProcess.StandardInput.Close() }
  $artifactProcess.WaitForExit()
  if ($artifactProcess.ExitCode -ne 0) { throw 'Source transfer failed' }
  $artifactProcess.Dispose()
}

```

On NAS, verify SHA256 against the locally approved manifest before safe extraction
into the dedicated empty source directory. No symlink/hardlink/traversal members;
never extract onto /home/tenazas/runtime or existing exporters. The local package
allowlist has seven source/template files only. Full remote hash/member verification
and an empty destination are prerequisites, not bypassed by these command examples.

Foreground launch command from that dedicated directory (use actual verified Python):

```sh
/usr/local/bin/python3.13 -m workspace.runtime.mcp_sanitized_sources --bind 192.168.2.2 --port 19105 --filesystem /volume1 --filesystem-confirmed
```

Run as sbasadre with existing docker membership, no sudo for normal collection.
Install the reviewed NAS unit only through an authorized admin mechanism and only
if DSM supports the sandbox directives. Do not relax sandboxing to make it start.
Later manual unit activation, never boot enablement:

```sh
systemd-analyze verify /etc/systemd/system/tenazas-mcp-sources.service
systemctl daemon-reload
systemctl start tenazas-mcp-sources
systemctl is-enabled tenazas-mcp-sources
```

These require separate administrative authorization. No existing NAS services are
restarted. If access would require firewall changes or source port is occupied, stop.
If DSM systemd/user/Python prerequisites cannot be met, review an alternative
launcher; do not silently create a container or scheduled task.

## Producer JSON and future VM steps — blocked, not executed

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

Exactly six keys, no maps/environment/auth fields. Values describe the planned
source, not a currently listening endpoint. No private config file is created now.

After source live validation and separate VM authorization, verify the new VM
archive/manifest, update the restricted helper only through its approved mechanism,
install and inspect the corrected package, capture/check rollback baseline and
privately provision producer.json. The future gate sequence is:

```sh
sudo -n /usr/local/libexec/tenazas-mcp-v2/deploy-mcp-v2 install
sudo -n /usr/local/libexec/tenazas-mcp-v2/deploy-mcp-v2 inspect
sudo -n /usr/local/libexec/tenazas-mcp-v2/deploy-mcp-v2 prepare
sudo -n /usr/local/libexec/tenazas-mcp-v2/deploy-mcp-v2 switch
```

Prepare/switch are explicitly NOT authorized in the current phase. Verify helper
implementation first: explicit cache-root, valid offline seed, gateway read-only
cache, corrected producer paths, baseline argv preserved, OAuth untouched. Perform
all six local/OAuth tool checks and freshness/scan-time verification before tunnel
activation and ChatGPT E2E. Keep boot enablement absent (NAS source is static; VM services remain disabled). No production action.

Remaining blockers: DSM effective sandbox/namespace activation checks,
19105 recheck/access under existing rules, approved source installation, new
VM helper/package verification, actual scan age/clock agreement and final E2E.
Docker group is powerful; no HTTP command interface is exposed, but source process
privilege still requires review. LAN HTTP has no new authentication/TLS guarantees.
