"""Strict Linux cache gate; never repairs production permissions."""

import os
import re
import stat
from contextlib import suppress
from pathlib import Path

from workspace.runtime.mcp_snapshot_contract import MAX_BYTES


def expected_identity():
    if os.name != "posix":
        raise ValueError("Linux cache policy required")
    import grp
    import pwd

    return pwd.getpwnam("tenazas-mcp").pw_uid, grp.getgrnam("tenazas-mcp").gr_gid


def validate_entry(path, directory=False):
    uid, gid = expected_identity()
    info = Path(path).lstat()
    kind = stat.S_ISDIR if directory else stat.S_ISREG
    if (
        not kind(info.st_mode)
        or stat.S_IMODE(info.st_mode) != (0o700 if directory else 0o600)
        or (info.st_uid, info.st_gid) != (uid, gid)
    ):
        raise ValueError("Unsafe cache permissions")
    if not directory and (info.st_nlink != 1 or info.st_size > MAX_BYTES):
        raise ValueError("Unsafe cache file")


def validate_cache(root):
    root = Path(root).absolute()
    if root.resolve(strict=True) != root:
        raise ValueError("Unsafe cache layout")
    validate_entry(root, True)
    entries = {p.name for p in root.iterdir()}
    temporary = entries - {"state.json", "infra.json", "incidents", ".producer.lock"}
    if (
        not {"state.json", "infra.json", "incidents"} <= entries
        or len(temporary) > 1
        or any(not re.fullmatch(r"\.state-[a-z0-9_]{8}\.tmp", name) for name in temporary)
    ):
        raise ValueError("Unsafe cache layout")
    for name in temporary:
        # Writer can finish its atomic rename between listing and lstat.
        with suppress(FileNotFoundError):
            validate_entry(root / name)
    validate_entry(root / "state.json")
    if ".producer.lock" in entries:
        validate_entry(root / ".producer.lock")
    validate_entry(root / "infra.json")
    validate_entry(root / "incidents", True)
    if any((root / "incidents").iterdir()):
        raise ValueError("Unexpected incidents")
