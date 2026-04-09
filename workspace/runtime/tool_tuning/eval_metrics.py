#!/usr/bin/env python3
"""
eval_metrics.py — Evaluation metrics for the multitool model.

Metrics:
  - exact_match:        predicted == expected (sets equal)
  - precision:          |predicted & expected| / |predicted|
  - recall:             |predicted & expected| / |expected|
  - f1:                 harmonic mean of precision and recall
  - hallucination_rate: predicted tools not in the valid registry
  - over_trigger_rate:  predicted tools when expected was empty
  - under_trigger_rate: predicted nothing when expected was non-empty
"""

from __future__ import annotations


def precision(predicted: set, expected: set) -> float:
    if not predicted:
        return 1.0 if not expected else 0.0
    return len(predicted & expected) / len(predicted)


def recall(predicted: set, expected: set) -> float:
    if not expected:
        return 1.0 if not predicted else 0.0
    return len(predicted & expected) / len(expected)


def f1(predicted: set, expected: set) -> float:
    p = precision(predicted, expected)
    r = recall(predicted, expected)
    if p + r == 0:
        return 0.0
    return 2 * p * r / (p + r)


def exact_match(predicted: set, expected: set) -> bool:
    return predicted == expected


def is_hallucination(tool: str, valid_tools: set) -> bool:
    return tool not in valid_tools


def aggregate_metrics(results: list[dict], valid_tools: set) -> dict:
    """Aggregate metrics over a batch of results.

    Each result: {"query": str, "predicted": set, "expected": set}
    """
    n = len(results)
    if n == 0:
        return {"error": "empty results"}

    total_precision = 0.0
    total_recall = 0.0
    total_f1 = 0.0
    exact_matches = 0
    hallucinations = 0
    total_predicted = 0
    over_triggers = 0
    under_triggers = 0
    per_tool_tp: dict[str, int] = {}
    per_tool_fp: dict[str, int] = {}
    per_tool_fn: dict[str, int] = {}

    for r in results:
        pred = set(r["predicted"])
        exp = set(r["expected"])

        total_precision += precision(pred, exp)
        total_recall += recall(pred, exp)
        total_f1 += f1(pred, exp)
        if exact_match(pred, exp):
            exact_matches += 1

        for t in pred:
            if is_hallucination(t, valid_tools):
                hallucinations += 1
        total_predicted += len(pred)

        if not exp and pred:
            over_triggers += 1
        if exp and not pred:
            under_triggers += 1

        for t in pred & exp:
            per_tool_tp[t] = per_tool_tp.get(t, 0) + 1
        for t in pred - exp:
            per_tool_fp[t] = per_tool_fp.get(t, 0) + 1
        for t in exp - pred:
            per_tool_fn[t] = per_tool_fn.get(t, 0) + 1

    per_tool_f1 = {}
    all_tools = set(per_tool_tp) | set(per_tool_fp) | set(per_tool_fn)
    for t in sorted(all_tools):
        tp = per_tool_tp.get(t, 0)
        fp = per_tool_fp.get(t, 0)
        fn = per_tool_fn.get(t, 0)
        p = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        r = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        per_tool_f1[t] = round(2 * p * r / (p + r), 3) if (p + r) > 0 else 0.0

    hallucination_rate = hallucinations / total_predicted if total_predicted > 0 else 0.0

    return {
        "n": n,
        "exact_match": round(exact_matches / n, 3),
        "precision": round(total_precision / n, 3),
        "recall": round(total_recall / n, 3),
        "f1": round(total_f1 / n, 3),
        "hallucination_rate": round(hallucination_rate, 3),
        "over_trigger_rate": round(over_triggers / n, 3),
        "under_trigger_rate": round(under_triggers / n, 3),
        "per_tool_f1": per_tool_f1,
    }
