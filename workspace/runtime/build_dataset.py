#!/usr/bin/env python3
"""
build_dataset.py — Assemble final train/eval dataset from curated candidates.

Reads curated JSONL files, deduplicates, balances, splits 90/10.
Outputs train.jsonl, eval.jsonl, stats.json in ChatML format.

Usage:
    python3 build_dataset.py
    python3 build_dataset.py --max 2000
    python3 build_dataset.py --dry-run
"""

import hashlib
import json
import random
import sys
from collections import Counter
from pathlib import Path

CURATED_DIR = Path("/home/tenazas/datasets/premium_v2/curated")
FINAL_DIR = Path("/home/tenazas/datasets/premium_v2/final")

SYSTEM_PROMPT = (
    "Sos Tenazas, un asistente personal técnico y operativo. "
    "Hablás en español rioplatense, directo, claro y útil. "
    "No hablás como empresa ni como soporte corporativo. "
    "Priorizás resolver, ordenar y destrabar. "
    "Cuando faltan datos, preguntás lo mínimo necesario. "
    "Si no sabés algo o no lo podés verificar, lo decís claramente. "
    "Tus respuestas suelen ser breves, salvo que el problema requiera más detalle."
)

# Target mix
TARGET_MIX = {
    "chat": 0.40,
    "correction": 0.25,
    "multiturn": 0.20,
    "style": 0.15,
}


def _load_curated() -> list[dict]:
    """Load all curated JSONL files."""
    all_items = []
    for jsonl in CURATED_DIR.glob("*.jsonl"):
        for line in jsonl.read_text().splitlines():
            if not line.strip():
                continue
            try:
                item = json.loads(line)
                # Only include reviewed items with quality >= 5
                if item.get("quality", 0) >= 5:
                    all_items.append(item)
            except json.JSONDecodeError:
                continue
    return all_items


def _content_hash(item: dict) -> str:
    """Hash for dedup: based on query text."""
    q = item.get("query", "").strip().lower()
    return hashlib.sha256(q.encode()).hexdigest()[:16]


def _deduplicate(items: list[dict]) -> list[dict]:
    """Remove near-duplicate entries by query hash."""
    seen = set()
    unique = []
    for item in items:
        h = _content_hash(item)
        if h not in seen:
            seen.add(h)
            unique.append(item)
    return unique


def _to_chatml(item: dict) -> dict | None:
    """Convert a candidate to ChatML format for training."""
    # If it already has messages, use them
    if "messages" in item and item["messages"]:
        return {"messages": item["messages"]}

    query = item.get("query", "").strip()
    # For corrections, use the correction field as response
    response = item.get("correction", "").strip()

    if not query:
        return None
    if not response:
        # Chat candidates may not have response in logs
        # These need manual review — skip for now
        return None

    return {
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": query},
            {"role": "assistant", "content": response},
        ]
    }


def build(max_total: int = 2000, dry_run: bool = False) -> dict:
    """Build final dataset."""
    items = _load_curated()
    if not items:
        print("No curated data found. Create files in curated/ first.")
        return {"total": 0}

    # Deduplicate
    items = _deduplicate(items)

    # Convert to ChatML
    chatml = []
    skipped = 0
    for item in items:
        converted = _to_chatml(item)
        if converted:
            converted["_type"] = item.get("type", "chat")
            converted["_quality"] = item.get("quality", 0)
            chatml.append(converted)
        else:
            skipped += 1

    if not chatml:
        print(f"No convertible items (skipped {skipped}). "
              "Add 'correction' or 'messages' fields to candidates.")
        return {"total": 0}

    # Cap
    if len(chatml) > max_total:
        random.seed(42)
        chatml = random.sample(chatml, max_total)

    # Shuffle
    random.seed(42)
    random.shuffle(chatml)

    # Split 90/10
    split_idx = int(len(chatml) * 0.9)
    train = chatml[:split_idx]
    eval_set = chatml[split_idx:]

    # Stats
    type_dist = Counter(c["_type"] for c in chatml)
    quality_avg = sum(c["_quality"] for c in chatml) / len(chatml) if chatml else 0

    stats = {
        "total": len(chatml),
        "train": len(train),
        "eval": len(eval_set),
        "skipped": skipped,
        "by_type": dict(type_dist.most_common()),
        "quality_avg": round(quality_avg, 1),
        "deduplicated_from": len(items),
    }

    print(f"Dataset built: {stats['total']} total "
          f"({stats['train']} train / {stats['eval']} eval)")
    print(f"Skipped: {skipped} (no response)")
    print(f"Types: {dict(type_dist.most_common())}")
    print(f"Avg quality: {quality_avg:.1f}")

    if dry_run:
        print("\n[DRY RUN] No files written.")
        return stats

    # Write
    FINAL_DIR.mkdir(parents=True, exist_ok=True)

    def _write(path, data):
        with open(path, "w", encoding="utf-8") as f:
            for item in data:
                # Remove internal fields
                clean = {"messages": item["messages"]}
                f.write(json.dumps(clean, ensure_ascii=False) + "\n")

    _write(FINAL_DIR / "train.jsonl", train)
    _write(FINAL_DIR / "eval.jsonl", eval_set)
    (FINAL_DIR / "stats.json").write_text(
        json.dumps(stats, indent=2, ensure_ascii=False))

    print(f"\nWritten to {FINAL_DIR}/")
    return stats


if __name__ == "__main__":
    args = sys.argv[1:]
    max_total = 2000
    dry_run = "--dry-run" in args
    for i, a in enumerate(args):
        if a == "--max" and i + 1 < len(args):
            max_total = int(args[i + 1])

    build(max_total=max_total, dry_run=dry_run)
