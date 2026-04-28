#!/usr/bin/env python3
"""
Eval Runner — Evaluate orchestrator classification against labeled datasets.

Loads JSONL examples, runs orchestrator.classify_query() on each,
computes per-tool Precision/Recall/F1, outputs console table + JSON.

Optional: push metrics to Prometheus via metrics_engine.

Safety: Read-only. Only reads dataset and calls classify_query (no side effects).

Log: workspace/runtime/logs/eval_runner.log
"""

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from runtime_utils import now

BASE_DIR = Path(__file__).parent
_host_evals = BASE_DIR.parent.parent / "state" / "workspace" / "evals"
_container_evals = Path("/home/tenazas/.openclaw/workspace/evals")
EVALS_DIR = _container_evals if _container_evals.parent.exists() else _host_evals
LOG_FILE = BASE_DIR / "logs" / "eval_runner.log"
DEFAULT_DATASET = EVALS_DIR / "dataset_augmented.jsonl"

# Map tool names from dataset to orchestrator categories
TOOL_TO_CATEGORY = {
    "smart-home-cinema": "skill:smart-home",
    "smart-home-on": "skill:smart-home",
    "smart-home-off": "skill:smart-home",
    "smart-home-relax": "skill:smart-home",
    "smart-home-work": "skill:smart-home",
    "weather": "skill:weather",
    "email": "skill:email",
    "calendar": "skill:calendar",
    "infra-monitor": "skill:infra-monitor",
    "arr-monitor": "skill:arr-monitor",
    "self-evolve": "skill:self-evolve",
    "tts": "skill:tts",
    "network-scanner": "skill:network-scanner",
    "reminders": "skill:reminders",
}

# Map tool names that have sub-actions to their expected action in handler
TOOL_TO_ACTION = {
    "smart-home-cinema": "cinema",
    "smart-home-on": "all-on",
    "smart-home-off": "all-off",
    "smart-home-relax": "relax",
    "smart-home-work": "work",
}



def _log(entry: dict):
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    entry["ts"] = now().isoformat()
    with open(LOG_FILE, "a") as f:
        f.write(json.dumps(entry, default=str) + "\n")


def _load_dataset(path: str) -> list[dict]:
    """Load JSONL dataset. Each line: {messages, metadata}."""
    examples = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    examples.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return examples


def _extract_expected_tool(example: dict) -> tuple[str, str]:
    """Extract (tool_name, query) from a dataset example.

    Supports two formats:
    - ChatML: messages[1].content = query, metadata.tool = tool
    - Simple: {query, tool}
    """
    meta = example.get("metadata", {})
    tool = meta.get("tool", example.get("tool", ""))
    query = example.get("query", "")

    if not query:
        messages = example.get("messages", [])
        for msg in messages:
            if msg.get("role") == "user":
                query = msg.get("content", "")
                break

    return tool, query


def _detect_smart_home_action(query: str) -> str:
    """Detect which smart-home action the query maps to.

    Mirrors the priority logic in orchestrator._handle_smart_home.
    """
    import re
    q = query.lower().strip()

    cinema_re = (
        r"cine|cinema|pel[ií]cula|movie|netflix|hbo|disney|serie"
        r"|modo\s*cine|modo\s*pel[ií]cula"
    )
    if re.search(cinema_re, q):
        return "cinema"
    if re.search(r"apag[aá]|off|todo.off|all.off", q):
        return "all-off"
    if re.search(r"prend[eé]|encend|on|todo.on|all.on", q):
        return "all-on"
    if re.search(r"relax", q):
        return "relax"
    if re.search(r"trabaj|work", q):
        return "work"
    return "unknown"


def run_eval(dataset_path: str, verbose: bool = False) -> dict:
    """Run evaluation against orchestrator classify_query.

    Returns: {total, per_tool: {tool: {tp, fp, fn, precision, recall, f1, errors}}}
    """
    # Import orchestrator locally to avoid circular imports
    sys.path.insert(0, str(BASE_DIR))
    try:
        from orchestrator import classify_query
    except ImportError as e:
        print(f"Error importing orchestrator: {e}", file=sys.stderr)
        return {"error": str(e)}

    examples = _load_dataset(dataset_path)
    if not examples:
        return {"error": "empty dataset", "path": dataset_path}

    # Track per-tool stats
    tp = defaultdict(int)   # true positives
    fp = defaultdict(int)   # false positives
    fn = defaultdict(int)   # false negatives
    errors_by_tool: dict[str, list] = defaultdict(list)
    total = 0
    correct = 0

    for ex in examples:
        tool, query = _extract_expected_tool(ex)
        if not tool or not query:
            continue

        total += 1
        expected_category = TOOL_TO_CATEGORY.get(tool, f"skill:{tool}")
        predicted_category = classify_query(query)

        # For smart-home tools, also check the action
        if tool in TOOL_TO_ACTION and predicted_category == "skill:smart-home":
            predicted_action = _detect_smart_home_action(query)
            expected_action = TOOL_TO_ACTION[tool]

            if predicted_action == expected_action:
                tp[tool] += 1
                correct += 1
            else:
                fn[tool] += 1
                # It was predicted as smart-home but wrong action
                predicted_tool = f"smart-home-{predicted_action}"
                if predicted_tool != tool:
                    fp[predicted_tool] = fp.get(predicted_tool, 0) + 1
                if len(errors_by_tool[tool]) < 5:
                    errors_by_tool[tool].append({
                        "query": query,
                        "expected": f"{tool} ({expected_action})",
                        "predicted": f"{predicted_tool} ({predicted_action})",
                    })
        elif predicted_category == expected_category:
            tp[tool] += 1
            correct += 1
        else:
            fn[tool] += 1
            # Find which tool the prediction maps to for FP tracking
            for t, cat in TOOL_TO_CATEGORY.items():
                if cat == predicted_category:
                    fp[t] = fp.get(t, 0) + 1
                    break
            if len(errors_by_tool[tool]) < 5:
                errors_by_tool[tool].append({
                    "query": query,
                    "expected": f"{tool} ({expected_category})",
                    "predicted": predicted_category,
                })

    # Calculate metrics per tool
    all_tools = set(list(tp.keys()) + list(fn.keys()) + list(fp.keys()))
    per_tool = {}
    for t in sorted(all_tools):
        t_tp = tp.get(t, 0)
        t_fp = fp.get(t, 0)
        t_fn = fn.get(t, 0)
        precision = t_tp / (t_tp + t_fp) if (t_tp + t_fp) > 0 else 0.0
        recall = t_tp / (t_tp + t_fn) if (t_tp + t_fn) > 0 else 0.0
        f1 = (2 * precision * recall / (precision + recall)
               if (precision + recall) > 0 else 0.0)
        per_tool[t] = {
            "tp": t_tp, "fp": t_fp, "fn": t_fn,
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1": round(f1, 4),
            "errors": errors_by_tool.get(t, []),
        }

    accuracy = correct / total if total > 0 else 0.0
    avg_f1 = (sum(m["f1"] for m in per_tool.values()) / len(per_tool)
              if per_tool else 0.0)

    result = {
        "total": total,
        "correct": correct,
        "accuracy": round(accuracy, 4),
        "avg_f1": round(avg_f1, 4),
        "per_tool": per_tool,
        "dataset": dataset_path,
        "evaluated_at": now().isoformat(),
    }

    _log({
        "type": "eval_complete",
        "total": total,
        "accuracy": round(accuracy, 4),
        "avg_f1": round(avg_f1, 4),
        "tools_evaluated": len(per_tool),
    })

    return result


def _print_results(result: dict):
    """Pretty-print eval results to console."""
    if "error" in result:
        print(f"Error: {result['error']}")
        return

    print(f"\n{'='*72}")
    print(f" Eval Results — {result['total']} examples, "
          f"accuracy {result['accuracy']:.1%}, avg F1 {result['avg_f1']:.1%}")
    print(f"{'='*72}")
    print(f" {'Tool':<22} {'P':>7} {'R':>7} {'F1':>7} "
          f"{'TP':>4} {'FP':>4} {'FN':>4}")
    print(f" {'-'*22} {'-'*7} {'-'*7} {'-'*7} {'-'*4} {'-'*4} {'-'*4}")

    for tool, m in sorted(result["per_tool"].items()):
        flag = " !" if m["f1"] < 0.75 else ""
        print(f" {tool:<22} {m['precision']:>6.1%} {m['recall']:>6.1%} "
              f"{m['f1']:>6.1%} {m['tp']:>4} {m['fp']:>4} {m['fn']:>4}{flag}")

    print(f" {'-'*22} {'-'*7} {'-'*7} {'-'*7}")
    print(f" {'AVG':<22} {'':>7} {'':>7} {result['avg_f1']:>6.1%}")

    # Recommendations
    recs = []
    for tool, m in result["per_tool"].items():
        if m["f1"] < 0.75:
            recs.append(f"  - {tool}: F1={m['f1']:.1%} — "
                        f"Agregar más ejemplos al dataset")
        elif m["precision"] < m["recall"] and m["precision"] < 0.85:
            recs.append(f"  - {tool}: P={m['precision']:.1%} < R={m['recall']:.1%} — "
                        f"Patrones demasiado amplios, acotar regex")
        elif m["recall"] < m["precision"] and m["recall"] < 0.85:
            recs.append(f"  - {tool}: R={m['recall']:.1%} < P={m['precision']:.1%} — "
                        f"Faltan variaciones de queries")

    if recs:
        print(f"\n Recomendaciones:")
        for r in recs:
            print(r)

    # Show sample errors
    for tool, m in result["per_tool"].items():
        if m["errors"]:
            print(f"\n Errores en {tool}:")
            for err in m["errors"][:3]:
                print(f"   query: {err['query']}")
                print(f"   esperado: {err['expected']} → predicho: {err['predicted']}")
    print()


def _save_results(result: dict):
    """Save eval results to JSON file."""
    EVALS_DIR.mkdir(parents=True, exist_ok=True)
    ts = now().strftime("%Y%m%d_%H%M%S")
    path = EVALS_DIR / f"eval_results_{ts}.json"
    with open(path, "w") as f:
        json.dump(result, f, indent=2, default=str, ensure_ascii=False)
    print(f" Resultados guardados: {path}")
    return path


def _push_prometheus(result: dict):
    """Push eval metrics to Prometheus if metrics_engine available."""
    try:
        from metrics_engine import record
        for tool, m in result["per_tool"].items():
            record("eval_tool_metric", {
                "tool": tool,
                "precision": m["precision"],
                "recall": m["recall"],
                "f1": m["f1"],
            })
        record("eval_global", {
            "accuracy": result["accuracy"],
            "avg_f1": result["avg_f1"],
            "total": result["total"],
        })
        print(" Métricas publicadas en metrics_engine")
    except ImportError:
        print(" metrics_engine no disponible, métricas no publicadas",
              file=sys.stderr)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Eval Runner — evaluate orchestrator classification",
    )
    parser.add_argument("--dataset", default=str(DEFAULT_DATASET),
                        help="Path to JSONL dataset")
    parser.add_argument("--push-to-prometheus", action="store_true",
                        help="Push metrics to metrics_engine")
    parser.add_argument("--json-only", action="store_true",
                        help="Output JSON only, no console table")
    parser.add_argument("--verbose", action="store_true",
                        help="Show all errors, not just first 5")

    args = parser.parse_args()

    result = run_eval(args.dataset, verbose=args.verbose)

    if args.json_only:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        _print_results(result)
        _save_results(result)

    if args.push_to_prometheus:
        _push_prometheus(result)

    # Exit code: 1 if any tool F1 < 75%
    if any(m.get("f1", 0) < 0.75 for m in result.get("per_tool", {}).values()):
        sys.exit(1)
