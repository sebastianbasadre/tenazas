"""No Docker, scanning, NAS writes or real scheduling in these tests."""

import json
import subprocess
from contextlib import nullcontext
from pathlib import Path, PurePosixPath
from types import SimpleNamespace

import pytest
from tools import mcp_discovery_cadence as cadence


def test_guard_serializes_entire_legacy_step_and_closes_descriptor(monkeypatch):
    source = b"def _network_scan(curr: dict):\n    calls.append(curr)\n"
    guarded = cadence.make_guard(source)
    calls, operations = [], []
    fake_fcntl = SimpleNamespace(
        LOCK_EX=1, LOCK_NB=2, flock=lambda fd, flags: operations.append(("lock", fd, flags))
    )
    monkeypatch.setitem(__import__("sys").modules, "fcntl", fake_fcntl)
    fake_os = SimpleNamespace(
        O_RDWR=1,
        O_NOFOLLOW=2,
        O_NONBLOCK=4,
        open=lambda *args: 23,
        close=lambda fd: operations.append(("close", fd)),
    )
    namespace = {"Path": Path, "__file__": "/runtime/runtime.py", "os": fake_os, "calls": calls}
    exec(compile(guarded, "runtime.py", "exec"), namespace)
    namespace["_network_scan"]({"test": True})
    assert calls == [{"test": True}]
    assert operations == [("lock", 23, 3), ("close", 23)]

    def busy(fd, flags):
        raise BlockingIOError()

    fake_fcntl.flock = busy
    namespace["_network_scan"]({"must_not_scan": True})
    assert calls == [{"test": True}]
    assert operations[-1] == ("close", 23)


def test_guard_closes_descriptor_on_scanner_exception(monkeypatch):
    guarded = cadence.make_guard(b'def _network_scan(curr: dict):\n    raise ValueError("scan")\n')
    closed = []
    monkeypatch.setitem(
        __import__("sys").modules,
        "fcntl",
        SimpleNamespace(LOCK_EX=1, LOCK_NB=2, flock=lambda *args: None),
    )
    namespace = {
        "Path": Path,
        "__file__": "/runtime/runtime.py",
        "os": SimpleNamespace(
            O_RDWR=1, O_NOFOLLOW=2, O_NONBLOCK=4, open=lambda *args: 23, close=closed.append
        ),
    }
    exec(compile(guarded, "runtime.py", "exec"), namespace)
    with pytest.raises(ValueError, match="scan"):
        namespace["_network_scan"]({})
    assert closed == [23]


def test_launcher_preserves_gates_and_uses_distinct_authority_and_claim():
    source = (
        f'AUTH = CONTROL / "{cadence.TEMPLATE_ID}-authorization.json"\n'
        f'claim = CONTROL / "{cadence.TEMPLATE_ID}-execution.claim"\n'
        'lock = CONTROL / "275A182-administrative.lock"\n'
        'label = "com.centurylinklabs.watchtower.enable=false"\n'
        "watchtower_excluded()\n"
    ).encode()
    updated = cadence.make_launcher(source, PurePosixPath("/private/run-1")).decode()
    assert "AUTH = Path('/private/run-1/authorization.json')" in updated
    assert "claim = Path('/private/run-1/execution.claim')" in updated
    assert "275A182-administrative.lock" in updated
    assert "watchtower_excluded()" in updated
    assert cadence.TEMPLATE_ID not in updated
    with pytest.raises(RuntimeError, match="structure"):
        cadence.make_launcher(source + source, PurePosixPath("/private/run-2"))


def test_timer_avoids_catchup_and_overlapping_instances():
    service, timer = cadence.unit_files(240)
    assert b"Type=oneshot" in service
    assert b"OnUnitInactiveSec=240s" in timer
    assert b"Persistent=" not in timer
    assert b"OnActiveSec=30s" in timer
    for invalid in (True, 0, 30, 3601):
        with pytest.raises(RuntimeError):
            cadence.unit_files(invalid)


def test_guard_refuses_double_install_and_unexpected_signature():
    guarded = cadence.make_guard(b"def _network_scan(curr: dict):\n    pass\n")
    with pytest.raises(RuntimeError, match="already installed"):
        cadence.make_guard(guarded)
    with pytest.raises(RuntimeError, match="signature"):
        cadence.make_guard(b"def _network_scan(other):\n    pass\n")


def test_save_new_never_overwrites_existing_claim(tmp_path, monkeypatch):
    # Windows can exercise exclusive creation; Linux additionally enforces NOFOLLOW.
    if not hasattr(cadence.os, "O_NOFOLLOW"):
        monkeypatch.setattr(cadence.os, "O_NOFOLLOW", 0, raising=False)
    path = tmp_path / "execution.claim"
    cadence.save_new(path, b"first")
    with pytest.raises(FileExistsError):
        cadence.save_new(path, b"second")
    assert path.read_bytes() == b"first"


@pytest.mark.parametrize("failed_scan", [False, True])
def test_cycle_retires_authorization_and_latches_failures(tmp_path, monkeypatch, failed_scan):
    root, legacy, base = (tmp_path / name for name in ("cadence", "legacy", "base"))
    root.mkdir()
    legacy.mkdir()
    (legacy / "runtime.py").write_bytes(b"guarded")
    reader = base / "releases/275A180/writer/network_discovery_result.py"
    reader.parent.mkdir(parents=True)
    reader.write_bytes(b"reviewed-reader")
    template = (
        f'AUTH = CONTROL / "{cadence.TEMPLATE_ID}-authorization.json"\n'
        f'claim = CONTROL / "{cadence.TEMPLATE_ID}-execution.claim"\n'
        'lock = CONTROL / "275A182-administrative.lock"\n'
        'label = "com.centurylinklabs.watchtower.enable=false"\n'
    ).encode()
    (root / "template.py").write_bytes(template)
    config = {
        "image_review_limitations_accepted_for_recurring_pinned_runs": True,
        "image": cadence.IMAGE,
        "manifest_sha256": cadence.MANIFEST,
        "runner_sha256": cadence.sha(Path(cadence.__file__).read_bytes()),
        "runtime_sha256": cadence.sha(b"guarded"),
        "reader_sha256": cadence.sha(b"reviewed-reader"),
        "template_sha256": cadence.sha(template),
    }
    config_path = root / "config.json"
    config_path.write_text(json.dumps(config))
    monkeypatch.setattr(cadence, "ROOT", root)
    monkeypatch.setattr(cadence, "BASE", base)
    monkeypatch.setattr(cadence, "LEGACY", legacy)
    monkeypatch.setattr(cadence, "CONFIG", config_path)
    monkeypatch.setattr(cadence, "root_only", lambda: None)
    monkeypatch.setattr(cadence, "protected_read", lambda path: Path(path).read_bytes())
    monkeypatch.setattr(cadence, "locked", lambda path: nullcontext())
    monkeypatch.setattr(cadence, "active_scans", lambda: [])
    monkeypatch.setattr(
        cadence.runpy,
        "run_path",
        lambda path: {
            "read_result": lambda path: {"attempt_id": "old"},
            "DiscoveryStore": lambda *args: SimpleNamespace(records=lambda: []),
        },
    )
    monkeypatch.setattr(cadence, "validate_publication", lambda *args: {"status": "PASS"})
    if not hasattr(cadence.os, "O_NOFOLLOW"):
        monkeypatch.setattr(cadence.os, "O_NOFOLLOW", 0, raising=False)
    commands = []

    def command(args, **kwargs):
        commands.append(args)
        if failed_scan and args[-1] == "--run":
            raise subprocess.CalledProcessError(1, args)
        return SimpleNamespace(stdout="", returncode=0)

    monkeypatch.setattr(cadence.subprocess, "run", command)
    if failed_scan:
        with pytest.raises(subprocess.CalledProcessError):
            cadence.run_cycle()
        assert (root / "blocked.json").exists()
        failure = json.loads((root / "blocked.json").read_text())
        assert failure["stage"] == "writer_execution"
        assert failure["command_exit_code"] == 1
        with pytest.raises(RuntimeError, match="latched"):
            cadence.run_cycle()
        assert len(commands) == 2  # No automatic scan after failure.
    else:
        cadence.run_cycle()
        assert not (root / "blocked.json").exists()
        assert len(commands) == 3  # check, run, exact-name removal check.
    runs = list(root.glob("run-*"))
    assert len(runs) == 1
    assert not (runs[0] / "authorization.json").exists()
    assert (runs[0] / "authorization-retired.json").exists()
    assert (runs[0] / "result-before.json").exists()
    assert (runs[0] / "check.log").exists()
    assert (runs[0] / "run.log").exists()
    if failed_scan:
        assert json.loads((runs[0] / "failure.json").read_text()) == failure
