#!/usr/bin/env python3
"""
skill_synthesis.py
Detects co-occurring skill patterns and proposes composite skills.

Analyzes router classification logs for temporal proximity patterns
(skills invoked within N minutes of each other). Proposes chain definitions
for skill_chain_executor.py. NEVER auto-creates — staging only.

Usage:
  python3 skill_synthesis.py --analyze          # analyze patterns
  python3 skill_synthesis.py --status           # show proposals
  python3 skill_synthesis.py --approve <id>     # approve a composite
  python3 skill_synthesis.py --reject <id>      # reject a composite
  python3 skill_synthesis.py --run <id>         # run an approved composite
"""

import argparse
import hashlib
import json
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

RUNTIME = Path(__file__).parent
ROUTER_LOG = RUNTIME / "logs" / "router_classify.log"
PROPOSALS_PATH = RUNTIME / "learning" / "skill_composites.json"
LOG_PATH = RUNTIME / "logs" / "skill_synthesis.log"

# Skills that make sense to combine (exclude chat, tts, etc.)
COMBINABLE_SKILLS = {
    "weather", "calendar", "calendar-agenda", "email", "infra-monitor",
    "arr-monitor", "network-scanner", "smart-home-on", "smart-home-off",
    "smart-home-cinema", "smart-home-relax", "smart-home-work",
    "finance", "expense", "iol_broker", "smoking-tracker", "reminders",
}

# Minimum co-occurrences to propose a composite
MIN_COOCCURRENCE = 3

# Time window for "co-occurring" skills (seconds)
PROXIMITY_WINDOW = 300  # 5 minutes

# Max composites to keep
MAX_PROPOSALS = 30


def _log(entry: dict):
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    entry["ts"] = datetime.now().astimezone().isoformat()
    with open(LOG_PATH, "a") as f:
        f.write(json.dumps(entry, default=str) + "\n")


def _load_proposals() -> list:
    try:
        return json.loads(PROPOSALS_PATH.read_text()) if PROPOSALS_PATH.exists() else []
    except Exception:
        return []


def _save_proposals(proposals: list):
    PROPOSALS_PATH.parent.mkdir(parents=True, exist_ok=True)
    PROPOSALS_PATH.write_text(json.dumps(proposals[-MAX_PROPOSALS:],
                                          indent=2, ensure_ascii=False, default=str))


def _load_router_log(max_lines: int = 2000) -> list[dict]:
    """Load recent router classification entries."""
    if not ROUTER_LOG.exists():
        return []
    entries = []
    for line in ROUTER_LOG.read_text().splitlines()[-max_lines:]:
        try:
            e = json.loads(line)
            if e.get("tool") and e.get("tool") != "chat" and e.get("ts"):
                entries.append(e)
        except Exception:
            continue
    return entries


# ── Analysis ────────────────────────────────────────────────────

def find_cooccurrences(entries: list[dict]) -> list[tuple]:
    """Find skill pairs that frequently occur within PROXIMITY_WINDOW."""
    pairs = Counter()

    # Sort by timestamp
    sorted_entries = sorted(entries, key=lambda e: e.get("ts", 0))

    for i, a in enumerate(sorted_entries):
        tool_a = _normalize_tool(a["tool"])
        if tool_a not in COMBINABLE_SKILLS:
            continue
        ts_a = a.get("ts", 0)

        for j in range(i + 1, len(sorted_entries)):
            b = sorted_entries[j]
            ts_b = b.get("ts", 0)
            if ts_b - ts_a > PROXIMITY_WINDOW:
                break  # beyond window

            tool_b = _normalize_tool(b["tool"])
            if tool_b not in COMBINABLE_SKILLS or tool_b == tool_a:
                continue

            # Canonical order for dedup
            pair = tuple(sorted([tool_a, tool_b]))
            pairs[pair] += 1

    return [(pair, count) for pair, count in pairs.most_common(20)
            if count >= MIN_COOCCURRENCE]


def find_sequences(entries: list[dict]) -> list[tuple]:
    """Find ordered sequences of 3+ skills within a session."""
    sequences = Counter()
    sorted_entries = sorted(entries, key=lambda e: e.get("ts", 0))

    window = []
    for e in sorted_entries:
        tool = _normalize_tool(e["tool"])
        if tool not in COMBINABLE_SKILLS:
            continue
        ts = e.get("ts", 0)

        # Expire old entries from window
        window = [(t, tm) for t, tm in window if ts - tm < PROXIMITY_WINDOW]
        window.append((tool, ts))

        if len(window) >= 3:
            # Take last 3 unique tools
            seen = []
            for t, _ in window:
                if t not in seen:
                    seen.append(t)
            if len(seen) >= 3:
                seq = tuple(seen[-3:])
                sequences[seq] += 1

    return [(seq, count) for seq, count in sequences.most_common(10)
            if count >= 2]


def _normalize_tool(tool: str) -> str:
    """Normalize tool names to base skill."""
    if tool.startswith("smart-home-"):
        return tool  # keep specific action
    return tool


def _tool_to_skill_category(tool: str) -> str:
    """Map tool name to orchestrator skill category."""
    mapping = {
        "weather": "skill:weather",
        "calendar": "skill:calendar",
        "calendar-agenda": "skill:calendar-agenda",
        "email": "skill:email",
        "infra-monitor": "skill:infra-monitor",
        "arr-monitor": "skill:arr-monitor",
        "network-scanner": "skill:network-scanner",
        "finance": "skill:finance",
        "expense": "skill:expense",
        "iol_broker": "skill:iol_broker",
        "reminders": "skill:reminders",
        "smoking-tracker": "skill:smoking-tracker",
    }
    if tool.startswith("smart-home-"):
        return "skill:smart-home"
    return mapping.get(tool, f"skill:{tool}")


# ── Proposal generation ─────────────────────────────────────────

def analyze() -> list[dict]:
    """Analyze patterns and generate composite proposals.

    NEVER auto-creates skills. Proposals go to staging.
    """
    entries = _load_router_log()
    if not entries:
        _log({"action": "analyze", "result": "no_data"})
        return []

    existing = _load_proposals()
    existing_keys = {p.get("key") for p in existing}

    new_proposals = []

    # Pair-based composites
    pairs = find_cooccurrences(entries)
    for (tool_a, tool_b), count in pairs:
        key = f"pair:{tool_a}+{tool_b}"
        if key in existing_keys:
            continue

        prop_id = hashlib.sha256(key.encode()).hexdigest()[:8]
        proposal = {
            "id": prop_id,
            "key": key,
            "type": "parallel",
            "name": f"{tool_a} + {tool_b}",
            "description": f"Ejecutar {tool_a} y {tool_b} en paralelo "
                           f"(co-ocurrencia: {count}x en log)",
            "skills": [tool_a, tool_b],
            "chain_definition": {
                "mode": "parallel",
                "steps": [
                    {"skill": _tool_to_skill_category(tool_a), "query": tool_a},
                    {"skill": _tool_to_skill_category(tool_b), "query": tool_b},
                ],
            },
            "evidence": {"cooccurrences": count, "window_s": PROXIMITY_WINDOW},
            "status": "proposed",
            "created_at": datetime.now().astimezone().isoformat(),
        }
        new_proposals.append(proposal)

    # Sequence-based composites (3+ skills)
    sequences = find_sequences(entries)
    for seq, count in sequences:
        key = f"seq:{'+'.join(seq)}"
        if key in existing_keys:
            continue

        prop_id = hashlib.sha256(key.encode()).hexdigest()[:8]
        proposal = {
            "id": prop_id,
            "key": key,
            "type": "chain",
            "name": " → ".join(seq),
            "description": f"Cadena secuencial: {' → '.join(seq)} "
                           f"(secuencia: {count}x en log)",
            "skills": list(seq),
            "chain_definition": {
                "mode": "chain",
                "steps": [
                    {"skill": _tool_to_skill_category(t), "query": t}
                    for t in seq
                ],
            },
            "evidence": {"sequences": count, "window_s": PROXIMITY_WINDOW},
            "status": "proposed",
            "created_at": datetime.now().astimezone().isoformat(),
        }
        new_proposals.append(proposal)

    existing.extend(new_proposals)
    _save_proposals(existing)
    _log({"action": "analyze", "entries": len(entries), "pairs": len(pairs),
          "sequences": len(sequences), "new_proposals": len(new_proposals)})
    return new_proposals


# ── Approval workflow ───────────────────────────────────────────

def approve(prop_id: str) -> bool:
    """Approve a composite skill proposal."""
    proposals = _load_proposals()
    for p in proposals:
        if p["id"] == prop_id and p["status"] == "proposed":
            p["status"] = "approved"
            p["approved_at"] = datetime.now().astimezone().isoformat()
            _save_proposals(proposals)
            _log({"action": "approve", "id": prop_id})
            return True
    return False


def reject(prop_id: str) -> bool:
    """Reject a composite skill proposal."""
    proposals = _load_proposals()
    for p in proposals:
        if p["id"] == prop_id and p["status"] in ("proposed", "approved"):
            p["status"] = "rejected"
            p["rejected_at"] = datetime.now().astimezone().isoformat()
            _save_proposals(proposals)
            _log({"action": "reject", "id": prop_id})
            return True
    return False


def get_pending() -> list[dict]:
    """Get proposed composites awaiting approval."""
    return [p for p in _load_proposals() if p["status"] == "proposed"]


def get_approved() -> list[dict]:
    """Get approved composites ready to run."""
    return [p for p in _load_proposals() if p["status"] == "approved"]


# ── Execution ───────────────────────────────────────────────────

def run_composite(prop_id: str) -> str:
    """Execute an approved composite via skill_chain_executor.

    Only runs approved composites. Returns merged output.
    """
    proposals = _load_proposals()
    composite = None
    for p in proposals:
        if p["id"] == prop_id:
            composite = p
            break

    if not composite:
        return f"Composite {prop_id} not found"
    if composite["status"] != "approved":
        return f"Composite {prop_id} not approved (status: {composite['status']})"

    chain = composite.get("chain_definition", {})
    mode = chain.get("mode", "parallel")
    steps = chain.get("steps", [])

    if not steps:
        return "No steps defined"

    from skill_chain_executor import run_parallel, run_chain, merge_outputs

    if mode == "parallel":
        results = run_parallel(steps)
    else:
        results = run_chain(steps, stop_on_fail=False)

    output = merge_outputs(results)
    _log({"action": "run", "id": prop_id, "mode": mode, "steps": len(steps),
          "success": sum(1 for r in results if r.get("success"))})
    return output


# ── CLI ─────────────────────────────────────────────────────────

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--analyze", action="store_true", help="Analyze patterns, propose composites")
    p.add_argument("--status", action="store_true", help="Show all proposals")
    p.add_argument("--approve", help="Approve composite by ID")
    p.add_argument("--reject", help="Reject composite by ID")
    p.add_argument("--run", help="Run approved composite by ID")
    args = p.parse_args()

    if args.analyze:
        new = analyze()
        print(f"Analysis complete: {len(new)} new proposals")
        for prop in new:
            print(f"  [{prop['type']:8s}] {prop['id']} — {prop['name']}")
    elif args.approve:
        ok = approve(args.approve)
        print(f"Approved: {ok}")
    elif args.reject:
        ok = reject(args.reject)
        print(f"Rejected: {ok}")
    elif args.run:
        output = run_composite(args.run)
        print(output)
    elif args.status:
        for p in _load_proposals():
            print(f"  [{p['status']:10s}] {p['id']} ({p['type']}) — {p['name']}")
    else:
        p.print_help()
