#!/usr/bin/env python3
"""
split_dataset.py — Divide datasets JSONL en train/eval.

Normaliza todos los ejemplos al formato messages que train_multitool.py espera.

Formatos de entrada aceptados:
  - {"messages": [...]}          → passthrough
  - {"input": "...", "label": "..."}  → convertido a messages con XML
  - {"input": "...", "tools": [...]}  → convertido a messages con XML

Uso:
    python3 split_dataset.py \
        --input multitool_dataset.jsonl \
        --train multitool_train.jsonl \
        --eval  multitool_eval.jsonl \
        --seed  42 \
        --eval-ratio 0.2

Siempre usar el mismo --seed para que el split sea reproducible entre runs.
"""

import json
import random
import argparse

SYSTEM_MSG = "Respond only with valid XML tool calls."


def _format_tools_xml(tools: list[str]) -> str:
    """Format tool list as XML string."""
    if not tools:
        return "<tools></tools>"
    inner = "".join(f"<tool>{t}</tool>" for t in tools)
    return f"<tools>{inner}</tools>"


def normalize_example(entry: dict) -> dict:
    """Normalize any input format to the messages format for training.

    Accepts:
      - {"messages": [...]} → returned as-is
      - {"input": "...", "label": "..."} → single tool label → messages
      - {"input": "...", "tools": [...]} → multi-tool list → messages

    Returns:
      {"messages": [{"role":"system",...}, {"role":"user",...}, {"role":"assistant",...}]}
    """
    if "messages" in entry:
        return entry

    user_msg = entry.get("input", "")

    if "label" in entry:
        label = entry["label"]
        if label == "none" or not label:
            tools = []
        else:
            tools = [label]
    elif "tools" in entry:
        tools = entry["tools"]
    else:
        tools = []

    return {
        "messages": [
            {"role": "system", "content": SYSTEM_MSG},
            {"role": "user", "content": user_msg},
            {"role": "assistant", "content": _format_tools_xml(tools)},
        ]
    }


def split_dataset(
    input_path: str,
    train_path: str,
    eval_path: str,
    eval_ratio: float = 0.2,
    seed: int = 42,
) -> dict:
    examples = []
    skipped = 0
    with open(input_path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
                examples.append(normalize_example(entry))
            except (json.JSONDecodeError, KeyError) as e:
                skipped += 1

    random.seed(seed)
    random.shuffle(examples)

    n_eval = max(1, int(len(examples) * eval_ratio))
    eval_set = examples[:n_eval]
    train_set = examples[n_eval:]

    with open(train_path, "w") as f:
        for ex in train_set:
            f.write(json.dumps(ex, ensure_ascii=False) + "\n")

    with open(eval_path, "w") as f:
        for ex in eval_set:
            f.write(json.dumps(ex, ensure_ascii=False) + "\n")

    stats = {
        "total": len(examples),
        "train": len(train_set),
        "eval": len(eval_set),
        "eval_ratio": round(len(eval_set) / len(examples), 3) if examples else 0,
        "seed": seed,
        "skipped": skipped,
    }
    print(f"[split] {stats}")
    return stats


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default="multitool_dataset.jsonl")
    parser.add_argument("--train", default="multitool_train.jsonl")
    parser.add_argument("--eval", default="multitool_eval.jsonl")
    parser.add_argument("--eval-ratio", type=float, default=0.2)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    split_dataset(args.input, args.train, args.eval, args.eval_ratio, args.seed)
