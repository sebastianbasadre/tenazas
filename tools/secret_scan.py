#!/usr/bin/env python3
"""Lightweight repository secret scanner for local/CI use."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path


PATTERNS = [
    re.compile(r"bot[0-9]{8,}:[A-Za-z0-9_-]{30,}"),
    re.compile(r"AIza[0-9A-Za-z_-]{30,}"),
    re.compile(r"sk-or-v1-[0-9A-Za-z_-]{20,}"),
    re.compile(r"-----BEGIN (?:RSA |OPENSSH |EC |DSA )?PRIVATE KEY-----"),
]
ASSIGNMENT_PATTERN = re.compile(
    r"(?i)(api[_-]?key|secret|password|refresh[_-]?token)\s*[:=]\s*['\"]([^'\"]{12,})['\"]"
)

ALLOWLIST_PATH_PARTS = {
    ".env.example",
    "openclaw.example.json",
    "docs/improvement-execution-plan.md",
    "tests/test_secret_scan.py",
    "tools/secret_scan.py",
}


def tracked_files() -> list[Path]:
    proc = subprocess.run(["git", "ls-files"], capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise SystemExit(proc.stderr.strip() or "git ls-files failed")
    return [Path(line.strip()) for line in proc.stdout.splitlines() if line.strip()]


def should_scan(path: Path) -> bool:
    text = path.as_posix()
    if text in ALLOWLIST_PATH_PARTS:
        return False
    if any(part in {".git", "__pycache__"} for part in path.parts):
        return False
    if path.suffix.lower() in {".png", ".jpg", ".jpeg", ".gif", ".zip", ".gz", ".sqlite", ".wal", ".shm"}:
        return False
    return path.exists() and path.is_file()


def scan_file(path: Path) -> list[str]:
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []
    findings: list[str] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        for pattern in PATTERNS:
            if pattern.search(line):
                findings.append(f"{path}:{lineno}: possible secret")
                break
        else:
            match = ASSIGNMENT_PATTERN.search(line)
            if match and not _looks_like_env_reference(match.group(2)):
                findings.append(f"{path}:{lineno}: possible secret")
    return findings


def _looks_like_env_reference(value: str) -> bool:
    stripped = value.strip()
    if not stripped:
        return True
    if stripped.startswith("$") or "${" in stripped:
        return True
    if re.fullmatch(r"[A-Z][A-Z0-9_:-]+", stripped):
        return True
    if stripped.lower() in {"change-me", "replace-me", "replace-with-random-32-byte-token"}:
        return True
    return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--all", action="store_true", help="scan tracked files; default behavior")
    args = parser.parse_args()
    _ = args

    findings: list[str] = []
    for path in tracked_files():
        if should_scan(path):
            findings.extend(scan_file(path))

    if findings:
        print("\n".join(findings))
        return 1
    print("No tracked-file secrets matched scanner patterns.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
