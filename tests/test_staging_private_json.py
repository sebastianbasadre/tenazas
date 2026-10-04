import json
import os
import stat

import pytest

from tools import staging_private_json as writer

pytestmark = pytest.mark.skipif(os.name != "posix", reason="Linux permission contract")


def write(path, data):
    writer.save_json(path, data, uid=os.geteuid(), gid=os.getegid())


def test_repeated_private_atomic_cycles(tmp_path):
    path = tmp_path / "state.json"
    previous = None
    for cycle in range(12):
        write(path, {"cycle": cycle})
        info = path.stat()
        assert stat.S_IMODE(info.st_mode) == 0o600
        assert (info.st_uid, info.st_gid) == (os.geteuid(), os.getegid())
        assert info.st_ino != previous
        assert json.loads(path.read_text()) == {"cycle": cycle}
        assert not list(tmp_path.glob(".private-json-*"))
        previous = info.st_ino


@pytest.mark.parametrize("fault", ["dump", "flush", "fsync", "replace"])
def test_prepublication_failure_preserves_original(tmp_path, monkeypatch, fault):
    path = tmp_path / "state.json"
    write(path, {"old": True})
    original = path.read_bytes(), path.stat().st_ino

    def fail(*args, **kwargs):
        raise OSError("injected staging failure")

    if fault == "dump":
        monkeypatch.setattr(writer.json, "dump", fail)
    elif fault == "flush":
        original_open = writer.os.fdopen

        class BrokenFlush:
            def __init__(self, stream): self.stream = stream
            def __enter__(self): return self
            def __exit__(self, *args): self.stream.close()
            def write(self, text): return self.stream.write(text)
            def flush(self): fail()
        monkeypatch.setattr(writer.os, "fdopen", lambda *a, **k: BrokenFlush(original_open(*a, **k)))
    else:
        monkeypatch.setattr(writer.os, fault, fail)
    with pytest.raises(OSError):
        write(path, {"new": True})
    assert (path.read_bytes(), path.stat().st_ino) == original
    assert not list(tmp_path.glob(".private-json-*"))


def test_private_from_creation_before_serialization(tmp_path, monkeypatch):
    original = writer.json.dump

    def inspect(data, stream, **kwargs):
        assert stat.S_IMODE(os.fstat(stream.fileno()).st_mode) == 0o600
        return original(data, stream, **kwargs)
    monkeypatch.setattr(writer.json, "dump", inspect)
    monkeypatch.setattr(writer.os, "chmod", lambda *a: pytest.fail("No chmod allowed"))
    write(tmp_path / "state.json", {})


@pytest.mark.parametrize("mode", [0o777, 0o755, 0o770])
def test_insecure_directory_rejected(tmp_path, mode):
    tmp_path.chmod(mode)
    with pytest.raises(PermissionError):
        write(tmp_path / "state.json", {})
    assert not list(tmp_path.iterdir())


def test_identity_is_explicit(tmp_path):
    with pytest.raises(PermissionError):
        writer.save_json(tmp_path / "state.json", {}, uid=os.geteuid() + 1, gid=os.getegid())


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "world-writable", "parent-link", "visible-acl"])
def test_unsafe_files_and_acl_rejected(tmp_path, monkeypatch, kind):
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    path = private / "state.json"
    if kind == "visible-acl":
        monkeypatch.setattr(writer.os, "listxattr", lambda fd: ["system.posix_acl_default"])
    elif kind == "parent-link":
        (tmp_path / "linked").symlink_to(private, target_is_directory=True)
        path = tmp_path / "linked/state.json"
    else:
        write(path, {})
        if kind == "symlink":
            path.unlink()
            path.symlink_to(tmp_path / "outside")
        elif kind == "hardlink": os.link(path, private / "alias")
        else: path.chmod(0o666)
    with pytest.raises(PermissionError):
        write(path, {"new": True})
    assert not list(private.glob(".private-json-*"))


def test_postreplace_fsync_failure_is_not_reported_as_rollback(tmp_path, monkeypatch):
    original = writer.os.fsync

    def fail_directory(fd):
        if stat.S_ISDIR(os.fstat(fd).st_mode): raise OSError("directory fsync failed")
        original(fd)
    monkeypatch.setattr(writer.os, "fsync", fail_directory)
    path = tmp_path / "state.json"
    with pytest.raises(writer.PublishedDurabilityError):
        write(path, {"published": True})
    assert json.loads(path.read_text()) == {"published": True}


def test_invalid_json_fails_closed(tmp_path):
    path = tmp_path / "state.json"
    write(path, {})
    with pytest.raises(ValueError): write(path, {"value": float("nan")})
    assert json.loads(path.read_text()) == {}
