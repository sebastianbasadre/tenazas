"""Read-only deployment gate for the dedicated sanitized cache; never migrates production."""

import os
import stat
from pathlib import Path


def validate_cache(root):
    root = Path(root).absolute()
    if root.resolve(strict=True) != root:
        raise ValueError("Unsafe cache layout")

    def check(path, directory):
        info = path.lstat()
        expected = 0o700 if directory else 0o600
        kind = stat.S_ISDIR if directory else stat.S_ISREG
        if not kind(info.st_mode) or stat.S_IMODE(info.st_mode) != expected or info.st_uid != os.geteuid():
            raise ValueError("Unsafe cache permissions")
        if not directory and info.st_nlink != 1:
            raise ValueError("Unsafe cache link")

    check(root, True)
    if {entry.name for entry in root.iterdir()} != {"state.json", "infra.json", "incidents"}:
        raise ValueError("Unsafe cache layout")
    check(root / "state.json", False)
    check(root / "infra.json", False)
    incidents = root / "incidents"
    check(incidents, True)
    expected = {f"alert-{index:02}.json" for index in range(20)}
    for entry in incidents.iterdir():
        if entry.name not in expected:
            raise ValueError("Unsafe cache layout")
        check(entry, False)
