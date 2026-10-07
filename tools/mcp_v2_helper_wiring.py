"""Local integration proposal for the restricted helper; no CLI or deploy execution.

These functions only validate inputs and render commands/drop-ins for review.
They do not write files, run subprocesses, contact sources or change services.
"""

from __future__ import annotations

import shlex

RELEASE = "/opt/tenazas-mcp/releases/mcp-v2-staging"
CACHE = "/var/lib/tenazas-mcp-v2"
CONFIG = "/etc/tenazas-mcp-v2/producer.json"
GATEWAY_DROPIN = "/etc/systemd/system/tenazas-mcp.service.d/mcp-v2-staging.conf"


def gateway_dropin(baseline_argv):
    """Preserve trusted baseline arguments, replacing only explicit cache-root."""
    argv = list(baseline_argv)
    if argv[:3] != ["/usr/bin/python3", "-m", "workspace.runtime.mcp_readonly_gateway"]:
        raise ValueError("Unsupported baseline runtime")
    if any(
        not isinstance(arg, str) or any(ord(c) < 32 for c in arg) or "$" in arg or "%" in arg
        for arg in argv
    ):
        raise ValueError("Unsupported systemd argument")
    for flag in ("--token-file", "--cert", "--key"):
        if (
            argv.count(flag) != 1
            or argv.index(flag) + 1 >= len(argv)
            or not argv[argv.index(flag) + 1].startswith("/")
        ):
            raise ValueError("Required baseline file argument missing")
    filtered = []
    index = 0
    while index < len(argv):
        arg = argv[index]
        if arg == "--cache-root":
            if index + 1 >= len(argv):
                raise ValueError("Invalid baseline cache argument")
            index += 2
            continue
        if arg.startswith("--cache-root="):
            index += 1
            continue
        filtered.append(arg)
        index += 1
    filtered += ["--cache-root", CACHE]
    return (
        "[Service]\n"
        f"WorkingDirectory={RELEASE}\n"
        "ExecStart=\n"
        f"ExecStart={shlex.join(filtered)}\n"
        "ReadWritePaths=\n"
        f"ReadOnlyPaths={CACHE}\n"
        "ProtectSystem=strict\n"
        "NoNewPrivileges=true\n"
    )


def prepare_plan(real_config):
    """Gate before mkdir/bootstrap. Validates syntax without GET/network calls.

    Caller must separately verify provenance, real metric meanings and reachability.
    Placeholder syntax passing this validator is not evidence of real sources.
    """
    from workspace.runtime.mcp_snapshot_producer import configured_sources

    configured_sources(real_config)
    run_as_mcp = ("/usr/sbin/runuser", "-u", "tenazas-mcp", "-g", "tenazas-mcp", "--")
    validation = (
        "from pathlib import Path; "
        "from workspace.runtime.mcp_cache_policy import validate_cache; "
        "from workspace.runtime.mcp_readonly_adapter import _read_snapshot; "
        "from workspace.runtime.mcp_snapshot_contract import validate_snapshot; "
        f"validate_cache({CACHE!r}); "
        f"validate_snapshot(_read_snapshot(Path({CACHE!r})/'state.json', None))"
    )
    return {
        "cwd": RELEASE,
        "commands": (
            (
                "/usr/bin/install",
                "-d",
                "-o",
                "tenazas-mcp",
                "-g",
                "tenazas-mcp",
                "-m",
                "0700",
                CACHE,
            ),
            run_as_mcp
            + (
                "/usr/bin/python3",
                "-m",
                "workspace.runtime.mcp_snapshot_producer",
                "--cache-root",
                CACHE,
                "--initialize",
            ),
            run_as_mcp + ("/usr/bin/python3", "-c", validation),
        ),
        "rollback_remove_only": GATEWAY_DROPIN,
        "preserve_old_application": "/opt/tenazas-mcp/app",
        "preserve_old_cache": "/opt/tenazas-mcp/app/workspace/runtime/mcp-cache",
    }
