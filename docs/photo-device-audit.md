# Photo device inventory

The user requested regrouping the NAS photo library by camera/device after
sequential renaming. A read-only inventory was the first step; the subsequent
pilot and authorization for a full copy are recorded below. Original folders
are to remain unchanged.

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

## Completed 2000-2004 pilot

The user selected device/year/month and requested a trial of the existing year
folders 2000 through 2004. The trial used independent copies, preserving all
source files. Its destination is:
`/volume1/photo/PRUEBA_por_dispositivo_2000_2004`.

Results: 528 JPG files, 380,665,217 bytes, copied with per-file SHA256 comparison.
All copies were subsequently opened successfully and all original paths exist.
Device counts: Sony CYBERSHOT 316, Sony DSC-P72 78, Sony DCR-TRV520 29,
Sony DCR-TRV33 18, unidentified 87. The latter include 12 files whose image
metadata could not be read. Byte-copy verification does not validate image
decoding. One MOV and one DB file were excluded and remain in their source folders.

441 copies use their EXIF capture year/month. The other 87 use the original
folder year and Mes_desconocido; those dates are not inferred capture dates.
All unidentified devices use Sin_dispositivo. Original filenames are preserved
unless a collision requires a deterministic relative-path hash suffix.

The frozen input and SHA256 manifest remain private in Hermes workspace:
`photo-pilot-2000-2004-inventory.jsonl` and `photo-pilot-2000-2004-report`.
The pilot is implemented by `tools/photo_device_pilot.py`; it refuses to reuse
an existing destination, overwrite copies, or operate on a changed sample count.

Future full-library audits must account for these extra trial copies so they
are not mistaken for additional originals. The v2 inventory started before
the trial directory was created. No full-library reorganization has occurred.

## Full 2000-2026 copy requested

The user explicitly requested copies from the original 2000-2026 year folders
in a trial device/year/month tree, preserving the original arrangement.
`tools/photo_device_copy.py` is installed in the private Hermes workspace and
launched detached as PID 2240. Its destination will be:
`/volume1/photo/PRUEBA_por_dispositivo_2000_2026`.

The worker waits for the complete v2 inventory before planning and copying.
The previous pilot, google_backup and other non-year roots are excluded.
All inventoried regular files inside the year roots are copied: image metadata
comes from the audit; video metadata is read locally with ffprobe and restricted
file/pipe protocols. Unknown videos use Videos_sin_dispositivo; non-media files
use Archivos_auxiliares. Unknown image devices use Sin_dispositivo. Missing or
out-of-scope dates use the original folder year and Mes_desconocido.

The worker creates a new destination only, uses exclusive file creation,
disambiguates normalized folder labels and filename collisions, compares SHA256
for every copy, and checks source size/mtime before and after copying. It does
not deduplicate or delete originals. Insufficient space, changed sources or
other errors stop the job. Partial verified copies remain documented; the job
does not silently resume or overwrite them on restart.

Private status, plan, verified-copy manifest and final summary live in
`/opt/data/workspace/photo-copy-2000-2026-report`. The adjacent log and
photo-copy-2000-2026-process.json identify the detached process. The terminal
may close; container recreation or restart stops the worker and the inventory.
No completion notification or external message is configured.

Three Linux tests cover collisions, unchanged originals, excluded source roots,
missing/invalid dates, refusing overwrites/changed sources, symlinks and traversal.
The ffprobe reader was checked against a real video without modifying it.
Launch is not completion: consult status.json and require state=complete plus
summary.json before reporting that all files were copied.
