#!/usr/bin/env python3
"""
score_candidates.py — Heuristic scoring of dataset candidates.

Reads raw candidates, applies quality scoring rules, writes scored output.
No LLM needed — pure heuristics.

Usage:
    python3 score_candidates.py
    python3 score_candidates.py --top 20
    python3 score_candidates.py --threshold 7
"""

import json
import re
import sys
from pathlib import Path

RAW_DIR = Path("/home/tenazas/datasets/premium_v2/raw")
CURATED_DIR = Path("/home/tenazas/datasets/premium_v2/curated")

# ── Scoring rules ────────────────────────────────────────────────

# Negative signals (subtract points)
_BAD_PHRASES = [
    "como asistente",
    "según los datos",
    "como tu asistente",
    "estoy aquí para",
    "no dudes en",
    "con mucho gusto",
    "encantado de",
    "como IA",
    "como modelo de lenguaje",
    "lamentablemente no puedo",
]

_TOPIC_BLEED_PATTERNS = [
    r"\bclima\b.*\bemail\b",
    r"\bemail\b.*\bclima\b",
    r"\bcalendario\b.*\bnafta\b",
]


def score_candidate(c: dict) -> int:
    """Score a candidate 0-10 based on heuristics."""
    score = 5  # baseline

    query = c.get("query", "")
    # We may not have the response in router logs, but we have metadata

    # ── Positive signals ──
    # Short query (real user behavior)
    if 5 < len(query) < 100:
        score += 1

    # Successful execution
    if c.get("path") == "composer":
        score += 2  # deterministic = reliable

    # Chat free that succeeded
    if c.get("type") == "chat" and c.get("path") == "llm_chat":
        score += 1

    # Low latency = good
    latency = c.get("latency_ms", 0)
    if 0 < latency < 500:
        score += 1

    # ── Negative signals ──
    # Very short query (probably noise)
    if len(query) < 5:
        score -= 2

    # Very long query (copy-paste, not natural)
    if len(query) > 300:
        score -= 1

    # Failed or raw fallback
    if c.get("path") == "raw":
        score -= 3
    if c.get("path") == "humanize" and c.get("type") == "skill_good":
        score -= 1  # expected composer but got humanize

    # Failure candidates start lower
    if c.get("source") == "failure_log":
        score -= 2
        if c.get("severity") == "high":
            score -= 1

    # Check response preview for bad patterns
    preview = c.get("response_preview", "").lower()
    for phrase in _BAD_PHRASES:
        if phrase in preview:
            score -= 1

    for pattern in _TOPIC_BLEED_PATTERNS:
        if re.search(pattern, preview):
            score -= 2

    # Empty response
    if preview and len(preview.strip()) < 10:
        score -= 2

    # Clamp to 0-10
    return max(0, min(10, score))


def score_file(input_path: Path) -> list[dict]:
    """Score all candidates in a JSONL file."""
    if not input_path.exists():
        return []

    scored = []
    for line in input_path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            c = json.loads(line)
            c["quality"] = score_candidate(c)
            c["needs_review"] = c["quality"] < 8
            scored.append(c)
        except json.JSONDecodeError:
            continue

    return sorted(scored, key=lambda x: -x["quality"])


def main():
    args = sys.argv[1:]
    threshold = 0
    top_n = None
    for i, a in enumerate(args):
        if a == "--threshold" and i + 1 < len(args):
            threshold = int(args[i + 1])
        if a == "--top" and i + 1 < len(args):
            top_n = int(args[i + 1])

    all_scored = []

    # Score chat candidates
    chat_scored = score_file(RAW_DIR / "chat_candidates.jsonl")
    all_scored.extend(chat_scored)

    # Score failure candidates
    fail_scored = score_file(RAW_DIR / "failure_candidates.jsonl")
    all_scored.extend(fail_scored)

    if not all_scored:
        print("No candidates to score. Run extract_dataset_candidates.py first.")
        return

    # Filter by threshold
    filtered = [c for c in all_scored if c["quality"] >= threshold]
    filtered.sort(key=lambda x: -x["quality"])

    if top_n:
        filtered = filtered[:top_n]

    # Stats
    from collections import Counter
    quality_dist = Counter(c["quality"] for c in all_scored)
    type_dist = Counter(c["type"] for c in all_scored)

    print(f"Total scored: {len(all_scored)}")
    print(f"Above threshold ({threshold}): {len(filtered)}")
    print()
    print("Quality distribution:")
    for q in sorted(quality_dist.keys(), reverse=True):
        bar = "█" * quality_dist[q]
        print(f"  {q:2d}: {bar} ({quality_dist[q]})")
    print()
    print("Type distribution:")
    for t, count in type_dist.most_common():
        print(f"  {t}: {count}")
    print()

    # Show top candidates
    show = filtered[:20] if not top_n else filtered
    print(f"Top {len(show)} candidates:")
    for c in show:
        q = c.get("query", "")[:60]
        print(f"  [{c['quality']:2d}] {c['type']:15s} | {q}")

    # Write scored output
    output = CURATED_DIR / "scored_all.jsonl"
    output.parent.mkdir(parents=True, exist_ok=True)
    with open(output, "w", encoding="utf-8") as f:
        for c in all_scored:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
    print(f"\nScored output: {output}")


if __name__ == "__main__":
    main()
