# Photo device inventory

The user requested regrouping the NAS photo library by camera/device after
sequential renaming. A read-only inventory is the first step; no reorganization
has been authorized against a specific final manifest yet.

The photo share is mounted in Hermes as `/mnt/photo`. Private reports are in
`/opt/data/workspace/photo-device-audit-20261025-v2`, corresponding to
`/volume1/docker/hermes-agent/data/workspace/photo-device-audit-20261025-v2`.
Do not commit the inventory or original photos to Git.

The initial filesystem count found 167,648 files, including 149,684 JPGs,
1,951 JPEGs, 13,013 MP4s and 1,142 MOVs. This excludes hidden/system directories,
recycle directories and symlinks. Those are filesystem counts, not validated
image counts. Google backup contents may overlap with the year folders.

`tools/photo_device_audit.py` reads headers using Pillow, collecting EXIF make,
model and original capture time. If an EXIF body serial is present, only a short
hash is retained. GPS, raw serial numbers and image contents are not exported.
Names are inventory identifiers and are not interpreted as commands.

Missing camera metadata, unsupported/unreadable files and non-image files have
separate statuses. Videos are not identified by this pass. Identical camera
models need not represent the same physical device. No camera is inferred from
the current sequential filename. Camera strings are not yet safe folder names.

The first partial pass identified 2,683 of 2,892 files, including Sony DSC-H1,
DSC-H9, DSC-W5, Canon PowerShot A95 and Samsung ES15. This is not a final ratio
for the full library. That foreground pass was stopped and retained as v1;
the v2 scan runs detached so it survives the SSH terminal closing. It does not
survive container recreation. No timer, organizer or media writer is installed.

Reports:
- `inventory.jsonl`: one private record per file.
- `summary.json`: appears only after the scan completes.
- The sibling `photo-device-audit-20261025-v2.log` records progress.

Three synthetic checks verified camera extraction, missing EXIF, invalid image
classification and unchanged source image bytes. No actual library image was
modified by these checks or the inventory.

Before organizing, compare the completed count with the initial inventory,
review camera grouping and unsupported files, and prepare a manifest. Proposed
layout: camera/model, then original capture year and month. Missing dates and
devices remain explicit review cases. Video handling, duplicate backup content,
filename collisions, preservation of original files and recovery must be resolved
before any moves. No photo should be deleted as an implicit duplicate cleanup.

To run another independent inventory with Pillow available:

```sh
python tools/photo_device_audit.py --root /mnt/photo --output /opt/data/workspace/UNIQUE_REPORT_DIRECTORY
```

The output directory must not already exist and must be outside the photo share.
