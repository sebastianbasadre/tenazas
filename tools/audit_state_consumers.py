"""Read-only NAS inventory: report locations/hashes, never file contents/argv.

Execute via python stdin on the NAS. No writes, process control or task execution.
Static references and a process snapshot cannot prove absence of dynamic users.
"""

import hashlib
import json
import os
import re
from pathlib import Path

ROOT = Path("/volume1/docker/openclaw")
EXCLUDE = {".git", "node_modules", ".venv", "venv", "logs", ".codex", "__pycache__", "models"}
SOURCE = {".py", ".sh", ".js", ".mjs", ".ts", ".json", ".yaml", ".yml", ".md", ".service", ".env"}
PATTERN = re.compile(r"(?<![A-Za-z0-9_])state\.json")


def main():
    hits, skipped, errors = [], [], []
    scanned = 0
    for directory, dirs, files in os.walk(ROOT, followlinks=False):
        dirs[:] = [name for name in dirs if name not in EXCLUDE and not (Path(directory) / name).is_symlink()]
        for name in files:
            path = Path(directory) / name
            if path.suffix not in SOURCE and name not in {"Dockerfile", "docker-compose.yml", ".env"}:
                continue
            try:
                if path.is_symlink() or path.stat().st_size > 2 * 1024 * 1024:
                    skipped.append(str(path.relative_to(ROOT)))
                    continue
                payload = path.read_bytes()
                scanned += 1
                lines = [index for index, line in enumerate(payload.decode("utf-8", errors="replace").splitlines(), 1)
                         if PATTERN.search(line)]
                if lines:
                    hits.append({"path": str(path.relative_to(ROOT)), "lines": lines,
                                 "sha256": hashlib.sha256(payload).hexdigest(),
                                 "documentation": path.suffix == ".md"})
            except OSError as error:
                errors.append({"path": str(path.relative_to(ROOT)), "error": type(error).__name__})
    processes = []
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit(): continue
        try:
            args = (proc / "cmdline").read_bytes().split(b"\0")
            scripts = [Path(arg.decode("utf-8", errors="replace")).name for arg in args
                       if arg.endswith((b".py", b".sh", b".mjs"))]
            if not scripts: continue
            status = (proc / "status").read_text()
            uid = re.search(r"^Uid:\s+(\d+)", status, re.M).group(1)
            processes.append({"pid": int(proc.name), "uid": int(uid), "script_basenames": scripts})
        except (OSError, AttributeError):
            continue
    print(json.dumps({"scanned_files": scanned, "references": hits, "skipped": skipped,
                      "read_errors": errors, "process_snapshot": processes,
                      "limitations": ["literal/static references only", "no process tracing",
                                      "DSM tasks and other containers require separate inventory"]}))


if __name__ == "__main__":
    main()
