"""Atomic, sanitized discovery outcome shared by the NAS writer and source reader."""

import json
import os
import stat
import tempfile
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

STATUSES = {
    "running",
    "success",
    "timeout",
    "execution_error",
    "parsing_error",
    "publication_error",
}
MAX_RESULT_BYTES = 4096


def utc_now():
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _date(value):
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError("Invalid discovery date")
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _identity(value):
    if not isinstance(value, str) or str(UUID(value)) != value:
        raise ValueError("Invalid discovery identity")


def validate_result(data):
    if not isinstance(data, dict) or set(data) != {
        "schema_version",
        "attempt_id",
        "status",
        "attempted_at",
        "finished_at",
        "last_success",
    }:
        raise ValueError("Invalid discovery result")
    if type(data["schema_version"]) is not int or data["schema_version"] != 1:
        raise ValueError("Invalid discovery version")
    _identity(data["attempt_id"])
    if data["status"] not in STATUSES:
        raise ValueError("Invalid discovery status")
    attempted = _date(data["attempted_at"])
    finished = data["finished_at"]
    if data["status"] == "running":
        if finished is not None:
            raise ValueError("Invalid unfinished discovery")
    elif finished is None or _date(finished) < attempted:
        raise ValueError("Invalid discovery chronology")
    last = data["last_success"]
    if last is not None:
        if not isinstance(last, dict) or set(last) != {
            "attempt_id",
            "observed_at",
            "total",
            "online",
            "missing",
        }:
            raise ValueError("Invalid discovery observation")
        _identity(last["attempt_id"])
        observed = _date(last["observed_at"])
        if any(
            type(last[k]) is not int or not 0 <= last[k] <= 10000
            for k in ("total", "online", "missing")
        ):
            raise ValueError("Invalid discovery counts")
        if last["online"] + last["missing"] != last["total"]:
            raise ValueError("Inconsistent discovery counts")
        if data["status"] == "success":
            if last["attempt_id"] != data["attempt_id"] or last["observed_at"] != finished:
                raise ValueError("Discovery generation mismatch")
        elif last["attempt_id"] == data["attempt_id"] or observed > attempted:
            raise ValueError("Failed discovery renewed observation")
    elif data["status"] == "success":
        raise ValueError("Missing successful observation")
    return data


def read_result(path):
    path = Path(path)
    if path.is_symlink():
        raise ValueError("Unsafe discovery result")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    descriptor = os.open(path, flags | getattr(os, "O_BINARY", 0))
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError("Unsafe discovery result")
        raw = stream.read(MAX_RESULT_BYTES + 1)
    if len(raw) > MAX_RESULT_BYTES:
        raise ValueError("Discovery result size limit")

    def unique_fields(pairs):
        data = {}
        for key, value in pairs:
            if key in data:
                raise ValueError("Duplicate discovery field")
            data[key] = value
        return data

    return validate_result(json.loads(raw, object_pairs_hook=unique_fields))


def write_result(path, data):
    """Public aggregate only; 0644 permits the separate source account to read it."""
    raw = json.dumps(validate_result(data), allow_nan=False, separators=(",", ":")).encode()
    path = Path(path)
    if path.is_symlink() or len(raw) > MAX_RESULT_BYTES:
        raise ValueError("Unsafe discovery result")
    fd, name = tempfile.mkstemp(prefix=".discovery-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            if os.name == "posix":
                os.fchmod(stream.fileno(), 0o644)
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        if os.name == "posix":
            directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        Path(name).unlink(missing_ok=True)


@contextmanager
def discovery_lock(path):
    """Serialize attempts; never remove a live lock. Windows supports local mock tests."""
    lock = Path(str(path) + ".lock")
    if lock.is_symlink():
        raise ValueError("Unsafe discovery lock")
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    descriptor = os.open(lock, flags | getattr(os, "O_BINARY", 0), 0o600)
    with os.fdopen(descriptor, "r+b") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise ValueError("Unsafe discovery lock")
        if os.name == "posix":
            import fcntl

            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            try:
                yield
            finally:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        else:
            import msvcrt

            if stream.seek(0, os.SEEK_END) == 0:
                stream.write(b"\0")
                stream.flush()
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            try:
                yield
            finally:
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)


def network_from_result(data):
    data = validate_result(data)
    if data["status"] != "success":
        raise ValueError("Discovery unavailable")
    last = data["last_success"]
    if _date(last["observed_at"]) > datetime.now(UTC):
        raise ValueError("Future discovery observation")
    return {
        "observed_at": last["observed_at"],
        **{k: last[k] for k in ("total", "online", "missing")},
        "healthy": None,
        "unhealthy": None,
        "latency_ms": None,
    }
