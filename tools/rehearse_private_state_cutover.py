"""Synthetic cutover/rollback rehearsal, ONLY at /staging/legacy-migration.

The runner must mount its isolated staging directory at /staging. No production
path is accepted and no scheduler or container controls exist in this module.
"""

import json
import os
import sys
from pathlib import Path

from tools.staging_private_json import save_json

ROOT = Path("/staging/legacy-migration")
STATE = ROOT / "state.json"
VALUE = {"containers": [{"name": "synthetic-openclaw", "up": True}], "migration_fixture": True}


def metadata():
    result = []
    for path in [ROOT, STATE]:
        info = path.stat()
        result.append({"path": str(path), "mode": oct(info.st_mode & 0o7777),
                       "uid": info.st_uid, "gid": info.st_gid, "inode": info.st_ino})
    return result


def seed():
    assert os.geteuid() == 0
    ROOT.mkdir(mode=0o777)  # Intentionally reproduce the bad LEGACY fixture.
    STATE.write_text(json.dumps(VALUE))
    os.chown(ROOT, 1034, 100)
    os.chown(STATE, 1034, 100)
    print(json.dumps({"step": "synthetic-legacy", "metadata": metadata()}))


def prepare():
    assert os.geteuid() == 0
    assert json.loads(STATE.read_text()) == VALUE
    # Staging-only provisioning. No recursive chmod/chown, no file post-chmod.
    os.chown(ROOT, 1026, 1026)
    os.chmod(ROOT, 0o700)
    print(json.dumps({"step": "protected-parent", "metadata": metadata()}))


def cutover():
    assert (os.geteuid(), os.getegid()) == (1026, 1026)
    value = json.loads(STATE.read_text())
    assert value == VALUE
    old_inode = STATE.stat().st_ino
    try:
        save_json(STATE, value, uid=1026, gid=1026)
    except PermissionError:
        pass
    else:
        raise AssertionError("Normal writer must reject the unsafe legacy target")
    assert STATE.stat().st_ino == old_inode
    temporary = ROOT / ".migration-state.json"
    save_json(temporary, value, uid=1026, gid=1026)
    os.replace(temporary, STATE)  # Explicit one-off bootstrap, never normal writer.
    directory = os.open(ROOT, os.O_RDONLY | os.O_DIRECTORY)
    try: os.fsync(directory)
    finally: os.close(directory)
    assert STATE.stat().st_ino != old_inode
    assert json.loads(STATE.read_text()) == VALUE
    print(json.dumps({"step": "private-cutover", "metadata": metadata(), "schema_preserved": True}))


def rollback():
    assert (os.geteuid(), os.getegid()) == (1026, 1026)
    # Compatibility rollback retains private ownership/parent/file. Legacy
    # open('w') is safe from disclosure HERE, with no inherited ACL + umask077.
    # This deliberately does not claim atomicity for the old implementation.
    previous_umask = os.umask(0o077)
    try:
        with STATE.open("w") as stream:
            json.dump(VALUE, stream)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        os.umask(previous_umask)
    info = STATE.stat()
    assert (info.st_mode & 0o7777, info.st_uid, info.st_gid) == (0o600, 1026, 1026)
    assert json.loads(STATE.read_text()) == VALUE
    print(json.dumps({"step": "compatibility-rollback", "metadata": metadata(),
                      "never_restore_777": True, "legacy_atomicity": False}))


if __name__ == "__main__":
    {"seed": seed, "prepare": prepare, "cutover": cutover, "rollback": rollback}[sys.argv[1]]()
