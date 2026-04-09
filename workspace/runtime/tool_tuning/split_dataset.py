#!/usr/bin/env python3
"""
split_dataset.py — Divide multitool_dataset.jsonl en train/eval.

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


def split_dataset(
    input_path: str,
    train_path: str,
    eval_path: str,
    eval_ratio: float = 0.2,
    seed: int = 42,
) -> dict:
    examples = []
    with open(input_path) as f:
        for line in f:
            line = line.strip()
            if line:
                examples.append(json.loads(line))

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
        "eval_ratio": round(len(eval_set) / len(examples), 3),
        "seed": seed,
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
