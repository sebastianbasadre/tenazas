"""Read image metadata and write a private device inventory; never edit media."""

import argparse
import collections
import datetime
import hashlib
import json
import os
from pathlib import Path

from PIL import Image

IMAGES = {
    ".jpg",
    ".jpeg",
    ".tif",
    ".tiff",
    ".png",
    ".webp",
    ".heic",
    ".heif",
    ".dng",
    ".cr2",
    ".nef",
    ".arw",
    ".bmp",
    ".gif",
}


def clean(value):
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    return str(value or "").replace("\x00", "").strip()[:200]


def inspect_image(path):
    try:
        with Image.open(path) as im:
            exif = im.getexif()
            try:
                nested = exif.get_ifd(34665)
            except Exception:
                nested = {}
            make, model = clean(exif.get(271)), clean(exif.get(272))
            serial = clean(nested.get(42033))
            device = " / ".join(v for v in (make, model) if v)
            return {
                "status": "identified" if device else "no_device_metadata",
                "device": device or None,
                "make": make or None,
                "model": model or None,
                "body_id": hashlib.sha256(serial.encode()).hexdigest()[:12] if serial else None,
                "capture_time": clean(nested.get(36867) or exif.get(36867)) or None,
            }
    except Exception as exc:
        return {"status": "unreadable_or_unsupported", "error_type": type(exc).__name__}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve(strict=True)
    if not root.is_dir():
        raise ValueError("Root must be a directory")
    output = args.output.resolve()
    if output == root or root in output.parents:
        raise ValueError("Reports must be outside the media directory")
    output.mkdir(mode=0o700, parents=True, exist_ok=False)
    os.umask(0o077)
    try:
        import pillow_heif

        pillow_heif.register_heif_opener()
    except ImportError:
        pass
    devices, statuses, extensions = (
        collections.Counter(),
        collections.Counter(),
        collections.Counter(),
    )
    count = 0
    with (output / "inventory.jsonl").open("x", encoding="utf-8") as report:
        for base, dirs, files in os.walk(root, followlinks=False):
            dirs[:] = sorted(
                d
                for d in dirs
                if not d.startswith(("@", ".", "#")) and not (Path(base) / d).is_symlink()
            )
            for filename in sorted(files):
                path = Path(base) / filename
                if filename.startswith(("@", ".")) or path.is_symlink():
                    continue
                suffix = path.suffix.lower()
                result = inspect_image(path) if suffix in IMAGES else {"status": "not_image"}
                row = {"path": str(path.relative_to(root)), "extension": suffix, **result}
                report.write(json.dumps(row, ensure_ascii=True) + "\n")
                statuses[result["status"]] += 1
                extensions[suffix] += 1
                if result.get("device"):
                    devices[result["device"]] += 1
                count += 1
                if count % 5000 == 0:
                    report.flush()
                    print(json.dumps({"processed": count}), flush=True)
    summary = {
        "root": str(root),
        "generated_at": datetime.datetime.now(datetime.UTC).isoformat(),
        "files": count,
        "statuses": statuses,
        "devices": devices.most_common(),
        "extensions": extensions,
    }
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=True, indent=2) + "\n")
    print(json.dumps(summary, ensure_ascii=True), flush=True)


if __name__ == "__main__":
    main()
