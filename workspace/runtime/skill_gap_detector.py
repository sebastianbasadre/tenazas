#!/usr/bin/env python3
"""
Skill Gap Detector — Detect when a repeated need deserves a new skill.

Signals:
- Frequent queries falling to chat with no specific tool (10+ in 24h)
- Repeated manual commands in episodic memory
- Chains of 2+ skills always used together

Output: skill proposals in staging/skills/
Never implements skills — leaves them ready for Claude Code.

Storage: state/skill_gaps.json
Log: logs/skill_gap_detector.log

Usage:
    python3 skill_gap_detector.py --detect     # run detection
    python3 skill_gap_detector.py --show       # show detected gaps
    python3 skill_gap_detector.py --propose    # generate proposals to staging
"""

import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from hashlib import sha256
from pathlib import Path

BASE_DIR = Path(__file__).parent
STATE_DIR = Path("/home/tenazas/.openclaw/state") if Path("/home/tenazas").exists() else BASE_DIR.parent / "state"
STAGING_DIR = BASE_DIR.parent / "staging" if (BASE_DIR.parent / "staging").exists() else STATE_DIR / "staging"
GAPS_FILE = STATE_DIR / "skill_gaps.json"
LOG_FILE = BASE_DIR / "logs" / "skill_gap_detector.log"
LOGS_DIR = BASE_DIR / "logs"

# Thresholds
CHAT_FALLBACK_THRESHOLD = 10     # queries to chat without tool in 24h
COMMAND_REPEAT_THRESHOLD = 5     # same manual command 5+ times
CHAIN_REPEAT_THRESHOLD = 3       # same skill chain 3+ times

ORCHESTRATOR_LOG = LOGS_DIR / "orchestrator.log"
TELEGRAM_LOG = LOGS_DIR / "telegram_handler.log"
MEMORY_FILE = BASE_DIR / "memory_log.json"
SHARED_MEMORY = BASE_DIR / "shared_memory.json"


def _now():
    return datetime.now().astimezone()


def _log(entry: dict):
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    entry["ts"] = _now().isoformat()
    try:
        with open(LOG_FILE, "a") as f:
            f.write(json.dumps(entry, default=str) + "\n")
    except OSError:
        pass


def _load_json(path: Path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default if default is not None else {}


def _load_jsonl(path: Path, max_age_hours: int = 24) -> list:
    entries = []
    cutoff = _now() - timedelta(hours=max_age_hours)
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    ts_str = entry.get("ts", "")
                    if ts_str:
                        ts = datetime.fromisoformat(ts_str)
                        if ts >= cutoff:
                            entries.append(entry)
                except (json.JSONDecodeError, ValueError):
                    continue
    except FileNotFoundError:
        pass
    return entries


def _gap_id(gap_type: str, pattern: str) -> str:
    return sha256(f"{gap_type}:{pattern}".encode()).hexdigest()[:12]


# ── Detectors ───────────────────────────────────────────────────


def detect_chat_fallbacks() -> list:
    """Find queries that repeatedly fall to chat without a specific tool."""
    gaps = []

    entries = _load_jsonl(ORCHESTRATOR_LOG, max_age_hours=24)

    # Find queries classified as "chat" that might deserve a skill
    chat_queries = []
    for e in entries:
        cat = e.get("classification", e.get("category", ""))
        query = e.get("query", e.get("prompt_preview", ""))
        if cat == "chat" and query and len(query) > 5:
            # Normalize query to find patterns
            normalized = re.sub(r"\d+", "N", query.lower().strip())
            normalized = re.sub(r"\s+", " ", normalized)
            chat_queries.append(normalized[:80])

    # Group similar queries
    pattern_counts = Counter(chat_queries)
    for pattern, count in pattern_counts.items():
        if count >= CHAT_FALLBACK_THRESHOLD:
            gaps.append({
                "type": "chat_fallback",
                "pattern": pattern,
                "count": count,
                "detail": f"'{pattern}' fell to chat {count} times in 24h",
            })

    # Also check for semantic clusters (similar prefixes)
    prefix_counts = Counter()
    for q in chat_queries:
        words = q.split()[:3]
        if len(words) >= 2:
            prefix = " ".join(words)
            prefix_counts[prefix] += 1

    for prefix, count in prefix_counts.items():
        if count >= CHAT_FALLBACK_THRESHOLD:
            # Check it's not already captured
            if not any(prefix in g["pattern"] for g in gaps):
                gaps.append({
                    "type": "chat_fallback",
                    "pattern": prefix + "...",
                    "count": count,
                    "detail": f"Queries starting with '{prefix}' hit chat {count} times",
                })

    return gaps


def detect_repeated_commands() -> list:
    """Find repeated manual commands in episodic memory."""
    gaps = []

    memory = _load_json(SHARED_MEMORY, {})
    episodic = memory.get("episodic", [])

    # Look for command-like entries
    command_counts = Counter()
    for entry in episodic[-200:]:
        content = entry.get("content", "")
        # Detect docker exec, curl, bash commands
        if any(cmd in content for cmd in ["docker exec", "curl ", "bash ", "python3 ", "nmap "]):
            normalized = re.sub(r"\d+\.\d+\.\d+\.\d+", "IP", content[:100])
            command_counts[normalized] += 1

    for cmd, count in command_counts.items():
        if count >= COMMAND_REPEAT_THRESHOLD:
            gaps.append({
                "type": "repeated_command",
                "pattern": cmd,
                "count": count,
                "detail": f"Command '{cmd[:60]}' repeated {count} times",
            })

    return gaps


def detect_skill_chains() -> list:
    """Find skills that are always used together in sequence."""
    gaps = []

    entries = _load_jsonl(ORCHESTRATOR_LOG, max_age_hours=168)  # 7 days

    # Extract skill execution sequences within 2-minute windows
    skill_events = []
    for e in entries:
        cat = e.get("classification", e.get("category", ""))
        if cat and cat.startswith("skill:"):
            ts_str = e.get("ts", "")
            try:
                ts = datetime.fromisoformat(ts_str)
                skill_events.append((ts, cat))
            except (ValueError, TypeError):
                continue

    skill_events.sort(key=lambda x: x[0])

    # Find pairs within 2-minute windows
    chain_counts = Counter()
    for i in range(len(skill_events) - 1):
        ts_a, skill_a = skill_events[i]
        ts_b, skill_b = skill_events[i + 1]
        if skill_a != skill_b and (ts_b - ts_a) < timedelta(minutes=2):
            chain = f"{skill_a} → {skill_b}"
            chain_counts[chain] += 1

    for chain, count in chain_counts.items():
        if count >= CHAIN_REPEAT_THRESHOLD:
            gaps.append({
                "type": "skill_chain",
                "pattern": chain,
                "count": count,
                "detail": f"Chain '{chain}' used {count} times in 7 days",
            })

    return gaps


# ── Main API ────────────────────────────────────────────────────


def detect_all() -> list:
    """Run all gap detectors."""
    all_gaps = []
    detectors = [
        ("chat_fallbacks", detect_chat_fallbacks),
        ("repeated_commands", detect_repeated_commands),
        ("skill_chains", detect_skill_chains),
    ]

    for name, detector in detectors:
        try:
            gaps = detector()
            all_gaps.extend(gaps)
        except Exception as e:
            _log({"action": "detector_error", "detector": name, "error": str(e)[:200]})

    # Add IDs and timestamps
    for gap in all_gaps:
        gap["id"] = _gap_id(gap["type"], gap["pattern"])
        gap["detected_at"] = _now().isoformat()

    # Save
    GAPS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(GAPS_FILE, "w") as f:
        json.dump(all_gaps, f, indent=2, default=str)

    _log({"action": "detect_complete", "gaps": len(all_gaps),
          "by_type": Counter(g["type"] for g in all_gaps)})
    return all_gaps


def generate_proposals(gaps: list = None) -> list:
    """Generate skill proposals from detected gaps and save to staging."""
    if gaps is None:
        gaps = _load_json(GAPS_FILE, [])
    if not isinstance(gaps, list):
        gaps = []

    proposals = []
    for gap in gaps:
        proposal = {
            "id": gap["id"],
            "gap_type": gap["type"],
            "pattern": gap["pattern"],
            "count": gap["count"],
            "proposed_skill": {
                "name": _suggest_skill_name(gap),
                "description": gap["detail"],
                "inputs": _suggest_inputs(gap),
                "outputs": ["formatted_text", "structured_data"],
                "risks": _assess_risks(gap),
                "suggested_tests": _suggest_tests(gap),
            },
            "status": "proposed",
            "created_at": _now().isoformat(),
        }
        proposals.append(proposal)

        # Save to staging
        staging_path = STAGING_DIR / "skills" if (STAGING_DIR / "skills").exists() else STAGING_DIR / "datasets"
        staging_path.mkdir(parents=True, exist_ok=True)
        with open(staging_path / f"skill_proposal_{gap['id']}.json", "w") as f:
            json.dump(proposal, f, indent=2, default=str)

    _log({"action": "proposals_generated", "count": len(proposals)})
    return proposals


def _suggest_skill_name(gap: dict) -> str:
    pattern = gap.get("pattern", "")
    if gap["type"] == "chat_fallback":
        words = pattern.split()[:3]
        return "skill:" + "-".join(w for w in words if len(w) > 2)[:30]
    elif gap["type"] == "skill_chain":
        return "skill:composite-" + gap["id"][:8]
    elif gap["type"] == "repeated_command":
        return "skill:auto-" + gap["id"][:8]
    return "skill:unknown-" + gap["id"][:8]


def _suggest_inputs(gap: dict) -> list:
    if gap["type"] == "chat_fallback":
        return ["user_query: str"]
    elif gap["type"] == "skill_chain":
        return ["trigger: str", "context: dict"]
    return ["command: str"]


def _assess_risks(gap: dict) -> list:
    risks = ["May overlap with existing skills"]
    if gap["type"] == "repeated_command":
        risks.append("Command execution requires safety review")
    return risks


def _suggest_tests(gap: dict) -> list:
    return [
        f"Test that '{gap['pattern'][:40]}' routes to new skill",
        "Test output format matches expected schema",
        "Test no regression on existing skill routing",
    ]


# ── CLI ─────────────────────────────────────────────────────────


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="Skill Gap Detector")
    p.add_argument("--detect", action="store_true", help="Run gap detection")
    p.add_argument("--show", action="store_true", help="Show detected gaps")
    p.add_argument("--propose", action="store_true", help="Generate skill proposals")
    args = p.parse_args()

    if args.detect:
        gaps = detect_all()
        print(f"Detected {len(gaps)} skill gaps")
        for g in gaps:
            print(f"  [{g['type']}] {g['pattern'][:60]} ({g['count']}x)")
    elif args.show:
        gaps = _load_json(GAPS_FILE, [])
        if not gaps:
            print("No gaps detected. Run --detect first.")
        else:
            for g in gaps:
                print(f"  [{g['type']}] {g['pattern'][:60]} ({g['count']}x)")
    elif args.propose:
        gaps = detect_all()
        proposals = generate_proposals(gaps)
        print(f"Generated {len(proposals)} skill proposals")
        for p in proposals:
            print(f"  {p['proposed_skill']['name']}: {p['proposed_skill']['description'][:60]}")
    else:
        p.print_help()
