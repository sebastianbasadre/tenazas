#!/usr/bin/env python3
"""
Shared Memory — Multi-type memory system for Tenazas runtime.

4 memory types:
  - semantic:   Facts extracted from documentation
  - episodic:   Past events from memory_log.json
  - procedural: Known skills from SKILL.md paths
  - working:    Current session context (ephemeral)

Stored in shared_memory.json. Max 100 entries per type.
Logs to logs/shared_memory.log.
"""

import json
from datetime import datetime
from pathlib import Path
try:
    from .runtime_utils import load_json, save_json
except ImportError:
    from runtime_utils import load_json, save_json

BASE_DIR = Path(__file__).parent
MEMORY_FILE = BASE_DIR / "shared_memory.json"
MEMORY_LOG_FILE = BASE_DIR / "memory_log.json"
LOG_FILE = BASE_DIR / "logs" / "shared_memory.log"

MEMORY_TYPES = {"semantic", "episodic", "procedural", "working"}
MAX_ENTRIES_PER_TYPE = 100

SKILLS_DIR = Path("/home/tenazas/.openclaw/workspace/skills")


def _now():
    return datetime.now().astimezone()


def _log(entry: dict):
    """Append a JSON line to shared_memory.log."""
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "a") as f:
        f.write(json.dumps(entry, default=str) + "\n")


def load_memory() -> dict:
    """Load all shared memory from disk."""
    data = load_json(MEMORY_FILE, {})
    for mtype in MEMORY_TYPES:
        if mtype not in data:
            data[mtype] = []
    return data


def save_memory(data: dict):
    """Save shared memory to disk, enforcing max entries per type."""
    for mtype in MEMORY_TYPES:
        entries = data.get(mtype, [])
        if len(entries) > MAX_ENTRIES_PER_TYPE:
            data[mtype] = entries[-MAX_ENTRIES_PER_TYPE:]
    save_json(MEMORY_FILE, data)


def add_entry(memory_type: str, content: str, source: str = "", metadata: dict = None) -> bool:
    """
    Add an entry to a memory type.

    Args:
        memory_type: One of 'semantic', 'episodic', 'procedural', 'working'
        content: The text content of the memory entry
        source: Where this entry came from
        metadata: Optional extra metadata

    Returns:
        True if added successfully
    """
    if memory_type not in MEMORY_TYPES:
        _log({"ts": _now().isoformat(), "action": "add_entry", "type": memory_type,
              "ok": False, "error": f"Invalid memory type: {memory_type}"})
        return False

    data = load_memory()
    entry = {
        "ts": _now().isoformat(),
        "content": content,
        "source": source,
    }
    if metadata:
        entry["metadata"] = metadata

    data[memory_type].append(entry)
    save_memory(data)

    _log({"ts": _now().isoformat(), "action": "add_entry", "type": memory_type,
          "source": source, "ok": True})
    return True


def query_memory(memory_type: str, keyword: str = "", limit: int = 10) -> list:
    """
    Query entries from a memory type, optionally filtering by keyword.

    Args:
        memory_type: One of the 4 memory types
        keyword: Optional keyword to filter entries (case-insensitive substring match)
        limit: Max entries to return (most recent first)

    Returns:
        List of matching entries
    """
    if memory_type not in MEMORY_TYPES:
        return []

    data = load_memory()
    entries = data.get(memory_type, [])

    if keyword:
        kw = keyword.lower()
        entries = [e for e in entries if kw in e.get("content", "").lower()
                   or kw in e.get("source", "").lower()]

    results = entries[-limit:]
    results.reverse()

    _log({"ts": _now().isoformat(), "action": "query", "type": memory_type,
          "keyword": keyword, "results": len(results)})
    return results


def clear_working_memory():
    """Clear the working memory (session context). Other types are preserved."""
    data = load_memory()
    data["working"] = []
    save_memory(data)
    _log({"ts": _now().isoformat(), "action": "clear_working", "ok": True})


def ingest_episodic():
    """
    Import recent events from memory_log.json into episodic memory.
    Reads the runtime memory log and converts events into episodic entries.
    """
    memory_log = load_json(MEMORY_LOG_FILE, [])
    if not isinstance(memory_log, list):
        return

    data = load_memory()
    existing_ts = {e.get("ts") for e in data.get("episodic", [])}
    added = 0

    for entry in memory_log[-50:]:  # Last 50 entries
        ts = entry.get("ts", "")
        if ts in existing_ts:
            continue
        for event in entry.get("events", []):
            content = f"{event.get('type', 'unknown')}"
            if event.get("container"):
                content += f" — {event['container']}"
            data["episodic"].append({
                "ts": ts,
                "content": content,
                "source": "memory_log.json",
            })
            added += 1

    save_memory(data)
    _log({"ts": _now().isoformat(), "action": "ingest_episodic", "added": added})


def ingest_procedural():
    """
    Scan SKILL.md files and register them as procedural memory entries.
    """
    data = load_memory()
    existing_sources = {e.get("source") for e in data.get("procedural", [])}
    added = 0

    if SKILLS_DIR.exists():
        for skill_dir in sorted(SKILLS_DIR.iterdir()):
            skill_md = skill_dir / "SKILL.md"
            if skill_md.exists():
                source = str(skill_md)
                if source not in existing_sources:
                    data["procedural"].append({
                        "ts": _now().isoformat(),
                        "content": f"Skill: {skill_dir.name}",
                        "source": source,
                    })
                    added += 1

    save_memory(data)
    _log({"ts": _now().isoformat(), "action": "ingest_procedural", "added": added})


def get_memory_stats() -> dict:
    """Return count of entries per memory type."""
    data = load_memory()
    return {mtype: len(data.get(mtype, [])) for mtype in MEMORY_TYPES}
