# Recurring staging refresh preparation

The existing source service uses release `275A180/source` and reads schema 2
from `/volume1/tenazas-mcp-discovery-v2/staging/runtime`. The October 24 manual
refresh passed publication checks and was served through ChatGPT. The original
first-run claim and subsequent refresh claim remain historical evidence.

`tools/mcp_discovery_cadence.py` prepares a recurring runner without starting it.
It requires explicit acceptance of the previously documented image-review
limitations for recurring runs of the pinned image/release. A timer interval
must be selected. The old deployment notes specify TTL 300, but that is not a
verification of the current producer configuration. A 240-second interval leaves
some margin for a typical 30-second quick scan and the source refresh; it cannot
guarantee freshness when scans fail, are skipped, or take longer. Freshness
timestamps and downstream TTL are never rewritten by this runner.

## NAS preparation

Leave DSM task 12 enabled. Wait until its current runtime process exits. Run as
root from the shared checkout, once the interval and recurring risk acceptance
are agreed:

```sh
sudo /usr/local/bin/python3.13 -ISB \
  /volume1/docker/openclaw/tools/mcp_discovery_cadence.py prepare \
  --interval-seconds 240 --accept-reviewed-image-limitations
```

This creates a private `cadence` directory, copies and pins the reviewed refresh
launcher, creates candidate systemd files, and backs up the original legacy
`runtime.py`. It wraps only `_network_scan()` in a shared coordination lock;
the remainder of normal runtime operation continues. It does not start or install
the timer. Preparation refuses to overwrite an existing cadence directory.
If preparation fails after creating that directory, inspect its contents and
backup; do not delete it and repeat blindly.

## Proof cycle, then schedule

```sh
sudo /usr/local/bin/python3.13 -ISB \
  /volume1/tenazas-mcp-discovery-v2/cadence/runner.py run
```

The runner holds both the network-step coordination lock and the existing
legacy discovery lock. A busy lock or active runtime/scanner causes a skip with
no observation timestamp change. Every actual cycle gets a separate private
authorization, launcher, execution claim, and validation report. The launcher
retains the original release hashes, image pin, filesystem checks, Watchtower
proof/configuration gate, and administrative lock. The authorization is retired
after each attempted cycle. Image-review approval records review with accepted
limitations; it is not a claim that sensitive image content is absent.

A failed scan or publication check creates `cadence/blocked.json`. Subsequent
cycles refuse to scan. Do not automatically clear this latch, repair data, or
retry a failed execution claim. Review the failed cycle first.

After one successful cycle, check `/network` for the new observation and confirm
that DSM task 12 is still enabled and running successfully. Then:

```sh
sudo /usr/local/bin/python3.13 -ISB \
  /volume1/tenazas-mcp-discovery-v2/cadence/runner.py schedule
sudo systemctl --no-pager list-timers tenazas-mcp-discovery-refresh.timer
sudo journalctl --no-pager -u tenazas-mcp-discovery-refresh.service -n 30
```

Scheduling starts a timer 30 seconds later, then waits the selected interval
after each service completion. Missed cycles are not caught up. Watch two
successful natural timer cycles, publication timestamps, and task 12 status.
The timer is **not enabled at boot** by this operation. Boot persistence is a
separate decision after natural-cycle verification.

## Stop and restoration

```sh
sudo systemctl stop tenazas-mcp-discovery-refresh.timer
sudo systemctl --no-pager show tenazas-mcp-discovery-refresh.service \
  -p ActiveState -p SubState
```

Wait for any active service to finish. Do not kill the service or its Docker
client to stop scheduling: that could release locks before the container exits.
No changes to task 12 are needed. With no cadence scan holding the gate, its
network step runs normally. Removing the harmless runtime coordination wrapper
is optional and should be done only after comparing the current source against
the recorded installed hash and reviewing any intervening runtime edits.

Private per-cycle evidence accumulates in `cadence/run-*`. Monitor disk usage;
no automatic deletion or retention policy is installed by this tool. No OAuth,
tunnel, exporter, source-service, or snapshot-producer configuration is modified.
