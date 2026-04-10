#!/usr/bin/env python3
"""
clean_tools.py — Normalize tool names across all JSONL datasets.

Enforces strict 12-tool allowlist. Rewrites assistant XML to canonical form.
"""

import json
import re
import sys
from pathlib import Path

ALLOWED_TOOLS = {
    "weather", "email", "calendar", "infra-monitor", "network-scanner",
    "arr-monitor", "self-evolve", "reminders", "smart-home-on",
    "smart-home-off", "smart-home-cinema", "tts",
}

REMAP = {
    "smart-home-relax": "smart-home-on",
    "smart-home-work": "smart-home-on",
    "smart-home-status": "smart-home-on",
    "arr_monitor": "arr-monitor",
    "network_scan": "network-scanner",
    "network-scan": "network-scanner",
    "self_evolve": "self-evolve",
    "infra": "infra-monitor",
    "lights": "smart-home-on",
    "light": "smart-home-on",
    "luces": "smart-home-on",
}


def fix_tool_name(name: str) -> str:
    """Map a tool name to its canonical form. Raises if unknown."""
    name = name.strip()
    if name in ALLOWED_TOOLS:
        return name
    if name in REMAP:
        return REMAP[name]
    if name == "none" or name == "":
        return name
    raise ValueError(f"Unknown tool: {name!r}")


def fix_xml(text: str) -> str:
    """Rewrite <tool>name</tool> entries to canonical names."""
    def repl(m):
        return "<tool>" + fix_tool_name(m.group(1)) + "</tool>"
    return re.sub(r"<tool>([^<]+)</tool>", repl, text)


def fix_entry(entry: dict) -> dict:
    """Fix tool names in any dataset format."""
    # "label" field (input/label format)
    if "label" in entry:
        label = entry["label"]
        if label and label != "none":
            entry["label"] = fix_tool_name(label)

    # "tools" array field
    if "tools" in entry and isinstance(entry["tools"], list):
        entry["tools"] = [fix_tool_name(t) for t in entry["tools"]]

    # "output" field (input/output format)
    if "output" in entry and "<tool>" in str(entry.get("output", "")):
        entry["output"] = fix_xml(entry["output"])

    # "messages" format
    if "messages" in entry:
        for msg in entry["messages"]:
            if msg.get("role") == "assistant" and "<tool>" in msg.get("content", ""):
                msg["content"] = fix_xml(msg["content"])

    return entry


def clean_file(path: Path) -> dict:
    """Clean a single JSONL file. Returns stats."""
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    out = []
    fixed = 0
    errors = []

    for i, line in enumerate(lines, 1):
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
            orig = json.dumps(entry, ensure_ascii=False)
            entry = fix_entry(entry)
            new = json.dumps(entry, ensure_ascii=False)
            if orig != new:
                fixed += 1
            out.append(new)
        except (json.JSONDecodeError, ValueError) as e:
            errors.append(f"  line {i}: {e}")
            out.append(line)  # preserve as-is

    path.write_text("\n".join(out) + "\n", encoding="utf-8")
    return {"file": path.name, "total": len(out), "fixed": fixed, "errors": errors}


def main():
    base = Path(__file__).parent

    targets = list(base.glob("multitool_dataset*.jsonl"))
    targets += list((base / "datasets").glob("*.jsonl"))

    if not targets:
        print("[clean] No JSONL files found")
        sys.exit(1)

    print(f"[clean] Scanning {len(targets)} files...")
    total_fixed = 0
    all_errors = []

    for f in sorted(targets):
        stats = clean_file(f)
        total_fixed += stats["fixed"]
        status = f"  {stats['fixed']} fixed" if stats["fixed"] else ""
        err = f"  {len(stats['errors'])} errors" if stats["errors"] else ""
        print(f"  {stats['file']}: {stats['total']} rows{status}{err}")
        all_errors.extend(stats["errors"])

    print(f"\n[clean] Total fixed: {total_fixed}")
    if all_errors:
        print(f"[clean] Errors ({len(all_errors)}):")
        for e in all_errors:
            print(e)
        sys.exit(1)
    else:
        print("[clean] No errors.")


if __name__ == "__main__":
    main()
