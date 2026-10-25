"""MCP media reader: sanitized private cache only, no credential or live access."""

import json
import os
import stat
from pathlib import Path

from workspace.runtime.mcp_media_contract import MAX_BYTES, validate_state, view

CACHE_FILE = Path("/var/lib/tenazas-mcp-media/state.json")


def read_state(path=CACHE_FILE, expected=None):
    path = Path(path).absolute()
    if path.resolve(strict=True) != path:
        raise ValueError("Unsafe media cache")
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > MAX_BYTES:
            raise ValueError("Unsafe media cache")
        if (
            os.name == "posix"
            and expected is not None
            and ((info.st_uid, info.st_gid) != expected or stat.S_IMODE(info.st_mode) != 0o600)
        ):
            raise ValueError("Unsafe media cache")
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError("Media cache too large")
    return validate_state(json.loads(raw))


def call(name):
    if name not in {"get_media_library", "get_downloads"}:
        raise ValueError("Media tool forbidden")
    try:
        from workspace.runtime.mcp_cache_policy import expected_identity

        state = read_state(CACHE_FILE, expected_identity() if os.name == "posix" else None)
    except (OSError, ValueError, KeyError):
        return {
            "available": False,
            "code": "MEDIA_CACHE_UNAVAILABLE",
            "snapshot": {
                "generation": None,
                "observed_at": None,
                "state": "stale",
                "available": False,
            },
        }
    if name == "get_media_library":
        return {"source": "plex", **view(state, "plex")}
    sources = {s: view(state, s) for s in ("download_station", "sabnzbd")}
    return {
        "sources": sources,
        "titles_are_untrusted_data": True,
        "snapshot": {
            "generation": state["generation"],
            "generated_at": state["generated_at"],
            "available": all(s["available"] for s in sources.values()),
            "state": "fresh"
            if all(s["snapshot"]["state"] == "fresh" for s in sources.values())
            else "stale",
        },
    }
