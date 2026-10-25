"""VM-only fetch of a fixed sanitized NAS media feed; no NAS credentials."""

import argparse
import json
import os
import stat
import tempfile
from pathlib import Path

from workspace.runtime.mcp_media_contract import MAX_BYTES, validate_state
from workspace.runtime.mcp_media_reader import read_state
from workspace.runtime.mcp_snapshot_producer import ReadOnlyHTTP

SOURCE = "http://192.168.2.2:19105/media"


def produce(root):
    import fcntl

    from workspace.runtime.mcp_cache_policy import expected_identity

    root = Path(root)
    expected = expected_identity()
    info = root.lstat()
    if (
        root.resolve(strict=True) != root
        or not stat.S_ISDIR(info.st_mode)
        or stat.S_IMODE(info.st_mode) != 0o700
        or (info.st_uid, info.st_gid) != expected
        or (os.geteuid(), os.getegid()) != expected
    ):
        raise ValueError("Unsafe media root")
    descriptor = os.open(root / ".producer.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            raw = ReadOnlyHTTP(timeout=10, allowed_urls=(SOURCE,)).get(SOURCE)
            state = validate_state(json.loads(raw))
        except Exception:
            # Preserve the original observation while marking upstream failure.
            state = read_state(root / "state.json", expected)
            for row in state["sources"].values():
                row.update(available=False, code="SOURCE_UNAVAILABLE")
            raw = json.dumps(state, allow_nan=False).encode()
        validate_state(state)
        if len(raw) > MAX_BYTES:
            raise ValueError("Media response too large")
        fd, temporary = tempfile.mkstemp(prefix=".media-", dir=root)
        try:
            with os.fdopen(fd, "wb") as stream:
                os.fchmod(stream.fileno(), 0o600)
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, root / "state.json")
            directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-root", type=Path, default=Path("/var/lib/tenazas-mcp-media"))
    args = parser.parse_args()
    try:
        produce(args.cache_root)
    except Exception:
        print("MEDIA_PUBLICATION_FAILED_DETAILS_SUPPRESSED")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
