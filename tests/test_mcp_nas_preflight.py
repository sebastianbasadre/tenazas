"""Exercise read-only NAS checks against fixtures; never contact the NAS."""

import sys
from types import SimpleNamespace

from tools import mcp_nas_preflight as check


def test_readonly_preflight_validated_fixtures(monkeypatch):
    reads = {
        "/proc/self/mountinfo": "10 1 0:1 / /volume1 rw - btrfs /dev/mapper/fixture rw",
        "/proc/net/tcp": "header\n",
        "/proc/net/tcp6": "header\n",
    }
    monkeypatch.setattr(check.pathlib.Path, "resolve", lambda p, **kwargs: p)
    monkeypatch.setattr(check.pathlib.Path, "read_text", lambda p, **kwargs: reads[p.as_posix()])
    monkeypatch.setattr(check.os.path, "ismount", lambda _: True)
    monkeypatch.setattr(
        check.os,
        "statvfs",
        lambda _: SimpleNamespace(f_frsize=1, f_blocks=28770439729152, f_bavail=16743543263232),
        raising=False,
    )
    monkeypatch.setattr(check.os, "getgroups", lambda: [123], raising=False)
    monkeypatch.setitem(
        sys.modules, "grp", SimpleNamespace(getgrgid=lambda _: SimpleNamespace(gr_name="docker"))
    )
    monkeypatch.setattr(check.shutil, "which", lambda _: "/sbin/ip")
    commands = []

    def run(args, **kwargs):
        commands.append(args)
        if args[0] == "/usr/local/bin/docker":
            return SimpleNamespace(stdout='{"State":"running"}\n' * 25 + '{"State":"exited"}')
        assert args == ["/sbin/ip", "-4", "-o", "addr", "show"]
        return SimpleNamespace(stdout="inet 192.168.2.2/24")

    monkeypatch.setattr(check.subprocess, "run", run)

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def read(self, _):
            return b'tenazas_network_last_scan_timestamp 1791244800\ndevice_discovery_online 40\ndevice_discovery_missing 75\nprivate_device{ip="private",mac="private"} 1'

    class Opener:
        def open(self, url, **kwargs):
            assert url == "http://192.168.2.2:9105/metrics"
            return Response()

    monkeypatch.setattr(check.urllib.request, "build_opener", lambda *_: Opener())
    result = check.preflight()
    assert result["pass"] and result["root_required"] is False
    assert result["docker"]["total"] == 26 and result["docker"]["running"] == 25
    assert result["network"]["online"] == 40 and result["network"]["missing"] == 75
    assert result["network"]["observed_at"] == "2026-10-06T00:00:00Z"
    assert result["source_port"]["currently_unused"] is True
    assert "private" not in str(result["network"])
    assert len(commands) == 2
