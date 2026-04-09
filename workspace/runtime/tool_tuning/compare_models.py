#!/usr/bin/env python3
"""
compare_models.py — Compare eval results across multiple models.

Usage:
    python3 compare_models.py eval_baseline.json eval_finetuned.json eval_3b_baseline.json eval_3b_finetuned.json
    python3 compare_models.py *.json
"""

import json
import sys
from pathlib import Path


def load_metrics(path: str) -> dict | None:
    p = Path(path)
    if not p.exists():
        return None
    data = json.loads(p.read_text())
    return data.get("metrics", data)


def main():
    if len(sys.argv) < 2:
        print("Usage: compare_models.py <eval_file1.json> [eval_file2.json ...]")
        sys.exit(1)

    files = sys.argv[1:]
    models = {}

    for f in files:
        m = load_metrics(f)
        if m:
            name = Path(f).stem
            models[name] = m
        else:
            print(f"  (skip: {f} not found)")

    if not models:
        print("No valid eval files found.")
        sys.exit(1)

    # Header
    names = list(models.keys())
    col_w = max(len(n) for n in names) + 2
    print(f"\n{'Metric':<25}", end="")
    for n in names:
        print(f"{n:>{col_w}}", end="")
    print()
    print("-" * (25 + col_w * len(names)))

    # Metrics
    keys = ["exact_match", "precision", "recall", "f1",
            "hallucination_rate", "over_trigger_rate", "under_trigger_rate"]
    for key in keys:
        print(f"  {key:<23}", end="")
        for n in names:
            val = models[n].get(key, 0)
            print(f"{val:>{col_w}.1%}", end="")
        print()

    # Per-tool F1
    all_tools = set()
    for m in models.values():
        all_tools.update(m.get("per_tool_f1", {}).keys())

    if all_tools:
        print(f"\n{'Per-tool F1':<25}", end="")
        for n in names:
            print(f"{n:>{col_w}}", end="")
        print()
        print("-" * (25 + col_w * len(names)))

        for tool in sorted(all_tools):
            print(f"  {tool:<23}", end="")
            for n in names:
                val = models[n].get("per_tool_f1", {}).get(tool, 0)
                print(f"{val:>{col_w}.3f}", end="")
            print()

    # Verdict
    print(f"\n{'Model':>25}", end="")
    for n in names:
        model_name = models[n].get("model", "?")
        print(f"{model_name:>{col_w}}", end="")
    print()


if __name__ == "__main__":
    main()
