"""Copy the 2000-2026 year folders into a new device/year/month tree."""

import argparse
import collections
import datetime as dt
import fcntl
import hashlib
import json
import os
import re
import shutil
import subprocess
import time
import unicodedata
from pathlib import Path

VIDEOS = {".mp4", ".mov", ".avi", ".wmv", ".3gp", ".mpg", ".mpeg", ".mts", ".m4v"}
YEARS = {str(y) for y in range(2000, 2027)}


def write_json(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=True, indent=2) + "\n")
    os.replace(temporary, path)


def safe_label(value):
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Za-z0-9_-]+", "_", value).strip("_")[:100] or "Sin_dispositivo"


def video_metadata(path):
    """Local metadata only. No network protocols or returned GPS/other tags."""
    try:
        p = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-threads",
                "1",
                "-protocol_whitelist",
                "file,pipe",
                "-probesize",
                "1048576",
                "-analyzeduration",
                "1000000",
                "-i",
                str(path),
                "-show_entries",
                "format_tags:stream_tags",
                "-of",
                "json",
            ],
            capture_output=True,
            timeout=10,
            check=True,
        )
        if len(p.stdout) > 1024**2:
            return {}, "metadata_too_large"
        data = json.loads(p.stdout)
        tags = {
            str(k).lower(): str(v)[:200] for k, v in data.get("format", {}).get("tags", {}).items()
        }
        for stream in data.get("streams", []):
            for key, value in stream.get("tags", {}).items():
                tags.setdefault(str(key).lower(), str(value)[:200])
        return {
            "make": tags.get("com.apple.quicktime.make")
            or tags.get("make")
            or tags.get("com.android.manufacturer"),
            "model": tags.get("com.apple.quicktime.model")
            or tags.get("model")
            or tags.get("com.android.model"),
            "capture_time": tags.get("com.apple.quicktime.creationdate")
            or tags.get("creation_time"),
        }, "read"
    except (OSError, ValueError, subprocess.SubprocessError):
        return {}, "unavailable"


def capture_group(value, fallback):
    if value:
        for parse in (
            lambda s: dt.datetime.strptime(s[:19], "%Y:%m:%d %H:%M:%S"),
            lambda s: dt.datetime.fromisoformat(s.replace("Z", "+00:00")),
        ):
            try:
                stamp = parse(value)
                if str(stamp.year) in YEARS:
                    return str(stamp.year), f"{stamp.month:02d}", "capture_metadata"
            except (ValueError, TypeError):
                pass
    return fallback, "Mes_desconocido", "source_folder_year"


def plan_row(row, root, destination, occupied, labels):
    relative = Path(row["path"])
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("unsafe_source")
    if not relative.parts or relative.parts[0] not in YEARS:
        return None
    source = root / relative
    if source.resolve(strict=True) != source or not source.is_file():
        raise ValueError("source_missing_or_symlink")
    metadata = row
    if row["extension"] in VIDEOS:
        metadata, video_status = video_metadata(source)
        device = (
            " / ".join(str(metadata[k]) for k in ("make", "model") if metadata.get(k))
            or "Videos_sin_dispositivo"
        )
    elif row["status"] == "not_image":
        device, video_status = "Archivos_auxiliares", None
    else:
        device, video_status = row.get("device") or "Sin_dispositivo", None
    folder = safe_label(device)
    label_key = folder.casefold()
    if label_key in labels and labels[label_key] != device:
        folder += "__" + hashlib.sha256(device.encode()).hexdigest()[:12]
    labels[folder.casefold()] = device
    year, month, date_source = capture_group(metadata.get("capture_time"), relative.parts[0])
    target = destination / folder / year / month / source.name
    key = unicodedata.normalize("NFC", str(target)).casefold()
    if key in occupied:
        stem = source.stem.encode("utf-8")[:150].decode("utf-8", errors="ignore")
        target = target.with_name(
            stem + "__" + hashlib.sha256(str(relative).encode()).hexdigest()[:20] + source.suffix
        )
        key = unicodedata.normalize("NFC", str(target)).casefold()
    if key in occupied:
        raise ValueError("unresolved_collision")
    occupied.add(key)
    stat = source.stat()
    return {
        "source": str(source),
        "destination": str(target),
        "device": device,
        "date_source": date_source,
        "metadata_status": row["status"],
        "video_metadata": video_status,
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
    }


def copy_verified(row):
    source, target = Path(row["source"]), Path(row["destination"])
    if source.resolve(strict=True) != source:
        raise ValueError("source_changed_to_symlink")
    before = source.stat()
    if (before.st_size, before.st_mtime_ns) != (row["size"], row["mtime_ns"]):
        raise ValueError("source_changed_after_plan")
    if target.exists() or target.is_symlink():
        raise ValueError("refuse_existing_destination")
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
    digest = hashlib.sha256()
    with source.open("rb") as src, target.open("xb") as dst:
        for block in iter(lambda: src.read(1024**2), b""):
            dst.write(block)
            digest.update(block)
    after = source.stat()
    if (after.st_size, after.st_mtime_ns) != (before.st_size, before.st_mtime_ns):
        raise ValueError("source_changed_during_copy")
    with target.open("rb") as copied:
        if hashlib.file_digest(copied, "sha256").hexdigest() != digest.hexdigest():
            raise ValueError("copy_hash_mismatch")
    shutil.copystat(source, target, follow_symlinks=False)
    with target.open("rb") as readable:
        readable.read(1)
    return digest.hexdigest()


def run(args):
    os.umask(0o077)
    root = args.root.resolve(strict=True)
    destination, report = args.destination.absolute(), args.report.absolute()
    if destination.parent != root or destination.exists() or destination.is_symlink():
        raise ValueError("destination_must_be_new_child")
    if report.resolve() != report or report.exists() or report == root or root in report.parents:
        raise ValueError("report_must_be_new_outside_media")
    report.mkdir(mode=0o700)
    status_path = report / "status.json"
    status = {
        "state": "waiting_for_inventory",
        "copied_and_verified": 0,
        "originals_preserved": True,
        "destination": str(destination),
    }
    write_json(status_path, status)
    lock = (report.parent / ".photo-full-copy.lock").open("a")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        deadline = time.monotonic() + args.wait_seconds
        while True:
            try:
                inventory_summary = json.loads((args.inventory / "summary.json").read_text())
                if isinstance(inventory_summary.get("files"), int):
                    break
            except (FileNotFoundError, json.JSONDecodeError):
                pass
            if time.monotonic() > deadline:
                raise ValueError("inventory_wait_timeout")
            time.sleep(20)
        status["state"] = "planning"
        write_json(status_path, status)
        occupied, labels, devices, date_sources = (
            set(),
            {},
            collections.Counter(),
            collections.Counter(),
        )
        total, required, inventory_count = 0, 0, 0
        with (report / "plan.jsonl").open("x") as plan:
            for line in (args.inventory / "inventory.jsonl").open():
                record = json.loads(line)
                inventory_count += 1
                row = plan_row(record, root, destination, occupied, labels)
                if row is None:
                    continue
                plan.write(json.dumps(row) + "\n")
                total += 1
                required += row["size"]
                devices[row["device"]] += 1
                date_sources[row["date_source"]] += 1
                if total % 500 == 0:
                    status["planned_files"] = total
                    write_json(status_path, status)
        if inventory_count != inventory_summary["files"] or not total:
            raise ValueError("incomplete_inventory")
        if shutil.disk_usage(root).free < required + 10 * 1024**3:
            raise ValueError("insufficient_space")
        status.update(state="copying", planned_files=total, bytes_to_copy=required)
        write_json(status_path, status)
        destination.mkdir(mode=0o755)
        with (report / "manifest.jsonl").open("x") as manifest:
            for line in (report / "plan.jsonl").open():
                row = json.loads(line)
                row["sha256"] = copy_verified(row)
                manifest.write(json.dumps(row) + "\n")
                manifest.flush()
                status["copied_and_verified"] += 1
                if status["copied_and_verified"] % 100 == 0:
                    write_json(status_path, status)
        status.update(state="complete", devices=dict(devices), date_sources=dict(date_sources))
        write_json(report / "summary.json", status)
        write_json(status_path, status)
    except Exception as exc:
        status.update(
            state="failed",
            error_type=type(exc).__name__,
            error_code=str(exc) if type(exc) is ValueError else "details_private",
        )
        write_json(status_path, status)
        raise
    finally:
        lock.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--wait-seconds", type=int, default=43200)
    run(parser.parse_args())


if __name__ == "__main__":
    main()
