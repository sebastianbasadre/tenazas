#!/usr/bin/env python3
"""
validate_tools.py — Assert strict tool name compliance across all datasets.

Checks:
  - Only allowed tools exist
  - XML is well-formed: <tools><tool>...</tool></tools>
  - No text outside XML in assistant output
  - No markdown
  - No unknown tool names
"""

import json
import re
import sys
from pathlib import Path

ALLOWED_TOOLS = {
    "weather", "email", "calendar", "infra-monitor", "network-scanner",
    "security-monitor", "arr-monitor", "self-evolve", "reminders", "smart-home-on",
    "smart-home-off", "smart-home-cinema", "tts",
}

# Strict XML pattern: <tools> optionally containing <tool>name</tool> entries
XML_PATTERN = re.compile(r"^<tools>(<tool>[^<]+</tool>)*</tools>$")
TOOL_EXTRACT = re.compile(r"<tool>([^<]+)</tool>")


def validate_assistant_xml(text: str) -> list[str]:
    """Validate assistant output is strict XML. Returns list of errors."""
    errors = []
    text = text.strip()

    # Must match strict XML pattern
    if not XML_PATTERN.match(text):
        # Check for common issues
        if "```" in text:
            errors.append("contains markdown")
        elif not text.startswith("<tools>"):
            errors.append(f"text outside XML: {text[:60]!r}")
        elif not text.endswith("</tools>"):
            errors.append(f"malformed XML end: {text[-60:]!r}")
        else:
            errors.append(f"invalid XML structure: {text[:80]!r}")

    # Check tool names
    for tool in TOOL_EXTRACT.findall(text):
        if tool not in ALLOWED_TOOLS:
            errors.append(f"invalid tool: {tool!r}")

    return errors


def validate_file(path: Path) -> dict:
    """Validate a single JSONL file. Returns stats."""
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    total = 0
    tool_counts = {}
    errors = []

    for i, line in enumerate(lines, 1):
        line = line.strip()
        if not line:
            continue
        total += 1

        try:
            entry = json.loads(line)
        except json.JSONDecodeError as e:
            errors.append(f"  line {i}: invalid JSON: {e}")
            continue

        # Extract assistant content from different formats
        assistant_text = None

        if "messages" in entry:
            for msg in entry["messages"]:
                if msg.get("role") == "assistant":
                    assistant_text = msg.get("content", "")
        elif "output" in entry and "<tool" in str(entry.get("output", "")):
            assistant_text = entry["output"]
        elif "label" in entry:
            # label format — tool names validated directly
            label = entry["label"]
            if label and label != "none" and label not in ALLOWED_TOOLS:
                errors.append(f"  line {i}: invalid label: {label!r}")
            if label and label != "none":
                tool_counts[label] = tool_counts.get(label, 0) + 1
            continue
        elif "tools" in entry:
            for t in entry["tools"]:
                if t not in ALLOWED_TOOLS:
                    errors.append(f"  line {i}: invalid tool in array: {t!r}")
                tool_counts[t] = tool_counts.get(t, 0) + 1
            continue
        else:
            continue  # skip non-tool entries (router labels etc)

        if assistant_text is not None:
            xml_errors = validate_assistant_xml(assistant_text)
            for e in xml_errors:
                errors.append(f"  line {i}: {e}")

            for tool in TOOL_EXTRACT.findall(assistant_text):
                tool_counts[tool] = tool_counts.get(tool, 0) + 1

    return {
        "file": path.name,
        "total": total,
        "tools": tool_counts,
        "errors": errors,
    }


def main():
    base = Path(__file__).parent

    targets = list(base.glob("multitool_dataset*.jsonl"))
    targets += list((base / "datasets").glob("*.jsonl"))

    print(f"[validate] Scanning {len(targets)} source datasets...\n")

    all_tools = set()
    all_errors = []
    total_rows = 0

    for f in sorted(targets):
        stats = validate_file(f)
        total_rows += stats["total"]
        all_tools.update(stats["tools"].keys())
        status = "OK" if not stats["errors"] else f"FAIL ({len(stats['errors'])} errors)"
        print(f"  {stats['file']}: {stats['total']} rows — {status}")
        if stats["errors"]:
            for e in stats["errors"][:5]:
                print(f"    {e}")
            if len(stats["errors"]) > 5:
                print(f"    ... and {len(stats['errors']) - 5} more")
        all_errors.extend(stats["errors"])

    print(f"\n[validate] Total: {total_rows} rows across {len(targets)} files")
    print(f"[validate] Unique tools found ({len(all_tools)}):")
    for t in sorted(all_tools):
        marker = "OK" if t in ALLOWED_TOOLS else "INVALID"
        print(f"  {marker}  {t}")

    invalid = all_tools - ALLOWED_TOOLS
    if invalid:
        print(f"\n[validate] FAIL: {len(invalid)} invalid tools: {sorted(invalid)}")
        sys.exit(1)
    elif all_errors:
        print(f"\n[validate] FAIL: {len(all_errors)} errors found")
        sys.exit(1)
    else:
        print(f"\n[validate] PASS: All {len(all_tools)} tools valid, 0 errors")


if __name__ == "__main__":
    main()
