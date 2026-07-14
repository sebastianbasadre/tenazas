#!/usr/bin/env python3
"""
Safe editor for OpenClaw/Tenazas config.

Rules:
- Gateway config lives in openclaw.json and has strict root keys.
- Tenazas runtime extensions live in tenazas.json. CLI paths that start with
  tenazas. are mapped there, with the prefix removed.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = PROJECT_ROOT / "state"
if not STATE_DIR.exists():
    STATE_DIR = Path("/home/tenazas/.openclaw")

OPENCLAW_CONFIG = STATE_DIR / "openclaw.json"
TENAZAS_CONFIG = STATE_DIR / "tenazas.json"
ALLOWED_OPENCLAW_ROOT = {
    "agents",
    "auth",
    "channels",
    "commands",
    "gateway",
    "mcp",
    "meta",
    "models",
    "plugins",
    "tools",
}


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return data


def write_json_atomic(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    json.loads(tmp.read_text(encoding="utf-8"))
    tmp.replace(path)


def timestamp() -> str:
    return time.strftime("%Y%m%d-%H%M%S")


def backup(path: Path) -> Path:
    if not path.exists():
        raise FileNotFoundError(path)
    dest = path.with_name(f"{path.name}.{timestamp()}")
    shutil.copy2(path, dest)
    return dest


def target_for(key: str | None, explicit: str | None = None) -> tuple[str, Path, str | None]:
    if explicit == "openclaw":
        return "openclaw", OPENCLAW_CONFIG, key
    if explicit == "tenazas":
        if key and key.startswith("tenazas."):
            key = key.removeprefix("tenazas.")
        return "tenazas", TENAZAS_CONFIG, key
    if key and key.startswith("tenazas."):
        return "tenazas", TENAZAS_CONFIG, key.removeprefix("tenazas.")
    return "openclaw", OPENCLAW_CONFIG, key


def parse_value(raw: str) -> Any:
    lowered = raw.strip().lower()
    if lowered in {"true", "false", "null"}:
        return json.loads(lowered)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def split_key_value(raw: str) -> tuple[str, Any]:
    if "=" not in raw:
        raise ValueError("set expects key=value")
    key, value = raw.split("=", 1)
    key = key.strip()
    if not key:
        raise ValueError("key cannot be empty")
    return key, parse_value(value.strip())


def get_path(data: dict[str, Any], dotted: str) -> Any:
    cur: Any = data
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def set_path(data: dict[str, Any], dotted: str, value: Any) -> None:
    cur = data
    parts = dotted.split(".")
    for part in parts[:-1]:
        nxt = cur.setdefault(part, {})
        if not isinstance(nxt, dict):
            raise ValueError(f"{part} is not an object")
        cur = nxt
    cur[parts[-1]] = value


def remove_path(data: dict[str, Any], dotted: str) -> bool:
    cur = data
    parts = dotted.split(".")
    for part in parts[:-1]:
        cur = cur.get(part)
        if not isinstance(cur, dict):
            return False
    return cur.pop(parts[-1], None) is not None


def validate_openclaw(data: dict[str, Any]) -> None:
    unknown = sorted(set(data) - ALLOWED_OPENCLAW_ROOT)
    if unknown:
        raise ValueError(f"unknown openclaw.json root keys: {', '.join(unknown)}")
    required = {"auth", "agents", "models", "tools", "channels", "gateway"}
    missing = sorted(required - set(data))
    if missing:
        raise ValueError(f"missing required openclaw.json root keys: {', '.join(missing)}")
    if not isinstance(data.get("models", {}).get("providers", {}), dict):
        raise ValueError("models.providers must be an object")
    if not isinstance(data.get("channels", {}).get("telegram", {}), dict):
        raise ValueError("channels.telegram must be an object")


def validate_target(name: str, data: dict[str, Any]) -> None:
    if name == "openclaw":
        validate_openclaw(data)


def docker(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["docker", *args], text=True, capture_output=True, check=check)


def restart_and_wait() -> None:
    docker("restart", "openclaw")
    deadline = time.time() + 60
    last = ""
    while time.time() < deadline:
        result = docker(
            "inspect",
            "-f",
            "{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}",
            "openclaw",
            check=False,
        )
        last = (result.stdout or result.stderr).strip()
        if last in {"healthy", "running"}:
            return
        time.sleep(3)
    raise RuntimeError(f"openclaw did not become healthy/running in 60s; last={last}")


def write_with_rollback(name: str, path: Path, data: dict[str, Any]) -> Path:
    validate_target(name, data)
    backup_path = backup(path)
    try:
        write_json_atomic(path, data)
        restart_and_wait()
        return backup_path
    except Exception:
        shutil.copy2(backup_path, path)
        try:
            restart_and_wait()
        finally:
            raise


def cmd_get(args: argparse.Namespace) -> int:
    name, path, key = target_for(args.key, args.target)
    data = load_json(path)
    value = get_path(data, key) if key else data
    print(json.dumps(value, indent=2, ensure_ascii=False))
    return 0


def cmd_set(args: argparse.Namespace) -> int:
    key, value = split_key_value(args.assignment)
    name, path, mapped_key = target_for(key, args.target)
    data = load_json(path)
    set_path(data, mapped_key or key, value)
    backup_path = write_with_rollback(name, path, data)
    print(f"updated {path} backup={backup_path}")
    return 0


def cmd_remove(args: argparse.Namespace) -> int:
    name, path, mapped_key = target_for(args.key, args.target)
    data = load_json(path)
    removed = remove_path(data, mapped_key or args.key)
    if not removed:
        print("key not present")
        return 0
    backup_path = write_with_rollback(name, path, data)
    print(f"removed from {path} backup={backup_path}")
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    targets = ["openclaw", "tenazas"] if args.target == "all" else [args.target]
    for target in targets:
        path = OPENCLAW_CONFIG if target == "openclaw" else TENAZAS_CONFIG
        data = load_json(path)
        validate_target(target, data)
        print(f"ok {target}: {path}")
    return 0


def list_backups(path: Path) -> list[Path]:
    return sorted(path.parent.glob(path.name + ".[0-9][0-9][0-9][0-9]*"))


def cmd_restore(args: argparse.Namespace) -> int:
    name, path, _ = target_for(None, args.target)
    backups = list_backups(path)
    if args.list:
        for item in backups:
            print(item.name)
        return 0
    if not args.timestamp:
        raise ValueError("restore requires --timestamp or --list")
    chosen = path.with_name(f"{path.name}.{args.timestamp}")
    if chosen not in backups:
        raise FileNotFoundError(chosen)
    current_backup = backup(path)
    try:
        data = load_json(chosen)
        validate_target(name, data)
        shutil.copy2(chosen, path)
        restart_and_wait()
    except Exception:
        shutil.copy2(current_backup, path)
        restart_and_wait()
        raise
    print(f"restored {chosen} current_backup={current_backup}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)

    get = sub.add_parser("get")
    get.add_argument("key", nargs="?")
    get.add_argument("--target", choices=["openclaw", "tenazas"])
    get.set_defaults(func=cmd_get)

    setp = sub.add_parser("set")
    setp.add_argument("assignment")
    setp.add_argument("--target", choices=["openclaw", "tenazas"])
    setp.set_defaults(func=cmd_set)

    remove = sub.add_parser("remove")
    remove.add_argument("key")
    remove.add_argument("--target", choices=["openclaw", "tenazas"])
    remove.set_defaults(func=cmd_remove)

    validate = sub.add_parser("validate")
    validate.add_argument("--target", choices=["openclaw", "tenazas", "all"], default="all")
    validate.set_defaults(func=cmd_validate)

    restore = sub.add_parser("restore")
    restore.add_argument("--target", choices=["openclaw", "tenazas"], default="openclaw")
    restore.add_argument("--list", action="store_true")
    restore.add_argument("--timestamp")
    restore.set_defaults(func=cmd_restore)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1)
