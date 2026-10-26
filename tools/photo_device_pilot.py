"""Copy a year-folder sample into device/year/month groups; preserve originals."""

import argparse
import collections
import datetime
import hashlib
import json
import os
import re
import shutil
import unicodedata
from pathlib import Path


def label(value):
    text = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Za-z0-9_-]+", "_", text).strip("_")[:100] or "Sin_dispositivo"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve(strict=True)
    destination = args.destination.absolute()
    report = args.report.absolute()
    if destination.parent != root or destination.exists():
        raise ValueError("Destination must be a new direct child of the media root")
    if report.exists() or report == root or root in report.parents:
        raise ValueError("Report must be new and outside the media directory")
    years = {"2000", "2001", "2002", "2003", "2004"}
    rows, used, devices, date_sources = [], set(), collections.Counter(), collections.Counter()
    for line in args.inventory.open(encoding="utf-8"):
        row = json.loads(line)
        relative = Path(row["path"])
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError("Unsafe source path")
        if not relative.parts or relative.parts[0] not in years or row["status"] == "not_image":
            continue
        source = root / relative
        if source.resolve(strict=True) != source or not source.is_file():
            raise ValueError("Source must be an existing non-symlink file")
        device = row.get("device") or "Sin_dispositivo"
        year, month, date_source = relative.parts[0], "Mes_desconocido", "source_folder_year"
        try:
            capture = datetime.datetime.strptime(
                row.get("capture_time", "")[:19], "%Y:%m:%d %H:%M:%S"
            )
            year, month, date_source = (
                f"{capture.year:04d}",
                f"{capture.month:02d}",
                "exif_capture_time",
            )
        except (TypeError, ValueError):
            pass
        target = destination / label(device) / year / month / source.name
        key = unicodedata.normalize("NFC", str(target)).casefold()
        if key in used:
            suffix = hashlib.sha256(str(relative).encode()).hexdigest()[:12]
            target = target.with_name(target.stem + "__" + suffix + target.suffix)
            key = unicodedata.normalize("NFC", str(target)).casefold()
        if key in used:
            raise ValueError("Unresolved filename collision")
        used.add(key)
        rows.append((source, target, row, date_source))
        devices[device] += 1
        date_sources[date_source] += 1
    if len(rows) != 528:
        raise ValueError("Pilot inventory changed; review expected 528 image files")
    required = sum(source.stat().st_size for source, *_ in rows)
    if shutil.disk_usage(root).free < required + 1024**3:
        raise ValueError("Insufficient space for verified independent copies")
    os.umask(0o077)
    report.mkdir(mode=0o700)
    destination.mkdir(mode=0o755)
    with (report / "manifest.jsonl").open("x", encoding="utf-8") as manifest:
        for index, (source, target, row, date_source) in enumerate(rows, 1):
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
            before = source.stat()
            digest = hashlib.sha256()
            with source.open("rb") as src, target.open("xb") as dst:
                for chunk in iter(lambda: src.read(1024**2), b""):
                    digest.update(chunk)
                    dst.write(chunk)
            after = source.stat()
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise ValueError("Source changed during copy; originals preserved")
            with target.open("rb") as copied:
                verified = hashlib.file_digest(copied, "sha256").hexdigest()
            if verified != digest.hexdigest():
                raise ValueError("Copy verification failed; originals preserved")
            shutil.copystat(source, target, follow_symlinks=False)
            manifest.write(
                json.dumps(
                    {
                        "source": str(source),
                        "destination": str(target),
                        "sha256": verified,
                        "device": row.get("device"),
                        "metadata_status": row["status"],
                        "date_source": date_source,
                    }
                )
                + "\n"
            )
            manifest.flush()
            if index % 100 == 0:
                print(json.dumps({"copied_and_verified": index}), flush=True)
    result = {
        "copied_and_verified": len(rows),
        "bytes": required,
        "destination": str(destination),
        "devices": devices,
        "date_sources": date_sources,
        "originals_preserved": True,
    }
    (report / "summary.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
