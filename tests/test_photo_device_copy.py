import argparse
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

pytest.importorskip("fcntl")
from tools import photo_device_copy as copy


@pytest.fixture(autouse=True)
def restore_umask():
    previous = os.umask(0o077)
    os.umask(previous)
    yield
    os.umask(previous)


def test_copies_collisions_and_unknown_dates_without_modifying_sources(tmp_path, monkeypatch):
    monkeypatch.setattr(copy.shutil, "disk_usage", lambda _: SimpleNamespace(free=100 * 1024**3))
    root = tmp_path / "photo"
    first, second = root / "2004/a/same.jpg", root / "2004/b/same.jpg"
    for p, content in ((first, b"first"), (second, b"second")):
        p.parent.mkdir(parents=True)
        p.write_bytes(content)
    inventory = tmp_path / "inventory"
    inventory.mkdir()
    rows = [
        {
            "path": str(p.relative_to(root)),
            "extension": ".jpg",
            "status": "identified",
            "device": "Sony / Camera",
            "capture_time": "2004:03:01 00:00:00",
        }
        for p in (first, second)
    ]
    rows.append({"path": "google_backup/excluded.jpg", "extension": ".jpg", "status": "identified"})
    (inventory / "inventory.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))
    (inventory / "summary.json").write_text(json.dumps({"files": len(rows)}))
    args = argparse.Namespace(
        root=root,
        inventory=inventory,
        destination=root / "PRUEBA",
        report=tmp_path / "report",
        wait_seconds=1,
    )
    copy.run(args)
    manifest = [
        json.loads(line) for line in (args.report / "manifest.jsonl").read_text().splitlines()
    ]
    assert len(manifest) == 2
    assert len({r["destination"] for r in manifest}) == 2
    for r in manifest:
        assert r["sha256"] == hashlib.sha256(Path(r["destination"]).read_bytes()).hexdigest()
    assert first.read_bytes() == b"first"
    assert second.read_bytes() == b"second"
    assert json.loads((args.report / "status.json").read_text())["state"] == "complete"
    assert copy.capture_group(None, "2004") == ("2004", "Mes_desconocido", "source_folder_year")
    assert copy.capture_group("1904-01-01T00:00:00Z", "2004")[0] == "2004"


def test_refuses_overwrite_or_changed_source(tmp_path):
    source, target = tmp_path / "source", tmp_path / "target"
    source.write_bytes(b"original")
    stat = source.stat()
    row = {
        "source": str(source),
        "destination": str(target),
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
    }
    copy.copy_verified(row)
    with pytest.raises(ValueError, match="existing_destination"):
        copy.copy_verified(row)
    row["destination"] = str(tmp_path / "other")
    source.write_bytes(b"changed-content")
    with pytest.raises(ValueError, match="source_changed_after_plan"):
        copy.copy_verified(row)
    assert not (tmp_path / "other").exists()
    assert target.read_bytes() == b"original"


def test_symlinks_and_traversal_are_rejected(tmp_path):
    root = tmp_path / "photo"
    root.mkdir()
    with pytest.raises(ValueError, match="unsafe_source"):
        copy.plan_row({"path": "../outside"}, root, root / "PRUEBA", set(), {})
    (root / "2004").mkdir()
    external = tmp_path / "external"
    external.write_bytes(b"private")
    (root / "2004/link.jpg").symlink_to(external)
    with pytest.raises(ValueError, match="symlink"):
        copy.plan_row({"path": "2004/link.jpg"}, root, root / "PRUEBA", set(), {})
