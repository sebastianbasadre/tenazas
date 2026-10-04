"""POSIX candidate writer for isolated staging; no production integration.

Provision/verify Synology ACLs on the host BEFORE using this writer. Synology
ACLs are not exposed by listxattr in this NAS's containers. No chmod/chown,
subprocess, scheduler or production paths are used here.
"""

import json
import os
import secrets
import stat
from pathlib import Path


class PublishedDurabilityError(OSError):
    """Replacement succeeded, but directory fsync did not; stop the migration."""


def _check(info, mode, uid, gid, directory=False):
    kind = stat.S_ISDIR if directory else stat.S_ISREG
    if (not kind(info.st_mode) or stat.S_IMODE(info.st_mode) != mode
            or (info.st_uid, info.st_gid) != (uid, gid)
            or (not directory and info.st_nlink != 1)):
        raise PermissionError("Private JSON permissions/ownership rejected")


def _check_visible_acl(fd):
    # This detects POSIX/visible ACLs, NOT hidden Synology ACLs. Host verification
    # and negative UID tests remain mandatory deployment gates.
    if any("acl" in key.lower() for key in os.listxattr(fd)):
        raise PermissionError("Extended ACL rejected")


def save_json(path, data, *, uid, gid):
    """Atomically write in an existing private directory; failures propagate.

    uid/gid are numeric deployment facts, never NAS username translations.
    Pre-publication errors preserve the previous target. Post-replace fsync
    failure is explicitly reported as published, not falsely rolled back.
    """
    if os.name != "posix" or (os.geteuid(), os.getegid()) != (uid, gid):
        raise PermissionError("Writer identity rejected")
    path = Path(path).absolute()
    if path.parent.resolve(strict=True) != path.parent:
        raise PermissionError("Parent links rejected")
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    temporary = ".private-json-" + secrets.token_hex(16)
    fd = None
    created = False
    try:
        _check(os.fstat(directory), 0o700, uid, gid, directory=True)
        _check_visible_acl(directory)
        try:
            previous = os.stat(path.name, dir_fd=directory, follow_symlinks=False)
        except FileNotFoundError:
            previous = None
        if previous is not None:
            _check(previous, 0o600, uid, gid)
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=directory)
        created = True
        _check(os.fstat(fd), 0o600, uid, gid)
        _check_visible_acl(fd)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            fd = None
            json.dump(data, stream, indent=2, ensure_ascii=False, default=str, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
            _check(os.fstat(stream.fileno()), 0o600, uid, gid)
            _check_visible_acl(stream.fileno())
        os.replace(temporary, path.name, src_dir_fd=directory, dst_dir_fd=directory)
        created = False
        try:
            os.fsync(directory)
        except OSError as error:
            raise PublishedDurabilityError("JSON published; durability unconfirmed") from error
    finally:
        if fd is not None:
            os.close(fd)
        try:
            if created:
                os.unlink(temporary, dir_fd=directory)
        finally:
            os.close(directory)
