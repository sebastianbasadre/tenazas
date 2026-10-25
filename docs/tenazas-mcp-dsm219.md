# DSM systemd 219 (-SECCOMP) NAS unit audit — local only

Verified runtime supplied by operator: /usr/local/bin/python3.13, Python 3.13.11.
/usr/bin/python3 is 3.8.15 and cannot run this package. DSM systemd is 219 without
seccomp. Operator also validated the exact proposed unit with the real DSM parser:
`systemd-analyze verify /tmp/tenazas-mcp-sources.service`, VERIFY_EXIT=0.
The actual unit has no Install section or WantedBy. No service activation or
installation is implied by parser validation; this stabilization pass is local only.

## Every directive

| Directive | DSM 219 assessment |
|---|---|
| Description | Supported unit metadata |
| Type=simple | Supported; foreground Python process |
| User=sbasadre | Supported; existing non-root account with proven collection access |
| Group=docker | Supported; existing Docker socket access, no sudo/root collector |
| WorkingDirectory | Supported; unchanged dedicated release directory |
| ExecStart | Supported; uses actual Python 3.13 path and unchanged bind/volume |
| UMask=0077 | Supported; restrictive creation mask |
| NoNewPrivileges=true | Supported independently of seccomp (PR_SET_NO_NEW_PRIVS) |
| ProtectSystem=full | Supported; strict is not in v219's accepted value table |
| ProtectHome=true | Supported; hides /home,/root,/run/user; DSM custom homes may differ |
| PrivateTmp=true | Supported; does not require seccomp |
| ReadOnlyDirectories | v219 spelling; ReadOnlyPaths is not supported |
| CapabilityBoundingSet= | Supported; empty set drops all process capabilities |
| Restart=on-failure | Supported; restarts only this new source process |

ProtectSystem=strict is downgraded to full, but ReadOnlyDirectories=/ adds a
read-only root tree to retain filesystem protection, with explicit temporary/shm
read-only entries. This uses v219 mount namespaces, not seccomp. Its implementation
orders READONLY before PRIVATE_TMP at equal paths: explicit read-only /tmp and
/var/tmp take precedence, so do not claim writable private temporary directories.
The service needs no filesystem writes. Docker's Unix socket can still communicate;
a read-only mount does NOT make Docker API access read-only.

RestrictAddressFamilies is removed: v219 only enforces it with HAVE_SECCOMP. In this
DSM build it would be a disabled setting, not a real restriction. No SystemCallFilter
or later-version substitute is added. Explicit private bind and GET-only/source URL
logic remain; the process is not claimed to have a kernel address-family filter.

[Install]/WantedBy was supported but removed intentionally. This template supplies
no boot enablement; installation/manual start never enables it. A future check may
report static rather than disabled. Other dependency units could still start it,
so verify none were introduced; no such dependency is supplied by this repo.

Upstream v219 evidence:
- https://github.com/systemd/systemd/blob/v219/src/core/load-fragment-gperf.gperf.m4
- https://github.com/systemd/systemd/blob/v219/src/core/namespace.c
- https://github.com/systemd/systemd/blob/v219/src/core/execute.c

## Identity and least privilege

sbasadre:docker is the validated runnable non-root context, not a proof of strict
least privilege. A personal DSM account may have extra permissions, and Docker
membership permits powerful daemon operations regardless of NoNewPrivileges or
capability removal. The application only runs fixed docker ps; no HTTP command
interface exists. Prefer a separately approved dedicated source account with only
needed read access, or separation of privileged collector and HTTP identity. Do not
create an account or change groups in this phase. Unknown DSM primary groups/home
layout/ACLs must not be invented. ProtectHome does not promise to hide every Synology
custom home, and existing Docker config access must be checked without printing it.

## Remaining gates

DSM parser validation passed (VERIFY_EXIT=0). Remaining checks are effective mount
namespace and Docker access under the unit, source directory permissions and
private reachability checks. Upstream syntax compatibility does not prove every
vendor backport/kernel configuration can activate the sandbox. Do not remove sandbox
directives silently if activation fails. No firewall changes, existing exporter
restarts, auto-enablement or production promotion are authorized.

Port 19105 was reported free in the supplied preflight; it can be taken before future
startup, so recheck. New NAS source tarball/manifest must be rebuilt after this unit
change; prior archives are obsolete. MCP VM runtime remains unchanged.
