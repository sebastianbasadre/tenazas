#!/usr/bin/env python3
"""
eval_runner.py — Evaluate tenazas-multitool against the eval set.

Usage:
    python3 eval_runner.py --eval multitool_eval.jsonl --model tenazas-multitool --output eval_results.json
    python3 eval_runner.py --eval multitool_eval.jsonl --model qwen2.5:7b --output eval_baseline.json
    python3 eval_runner.py --compare eval_baseline.json eval_results.json
"""

import json
import time
import argparse
import urllib.request
import sys
from pathlib import Path
from datetime import datetime

try:
    from runtime_config import OLLAMA_GPU_URL
except ImportError:
    from workspace.runtime.runtime_config import OLLAMA_GPU_URL

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent))
from eval_metrics import aggregate_metrics
from multitool_parser import parse_multitool_response, ALLOWED_TOOLS

OLLAMA_NAS_URL = "http://localhost:11434"

# Use the real registry from multitool_parser.py
VALID_TOOLS = ALLOWED_TOOLS

SYSTEM_PROMPT = (
    "You are a deterministic multi-tool caller. "
    "Respond only with valid XML. Allowed format:\n"
    "<tools>\n  <tool>TOOL_NAME</tool>\n</tools>\n"
    "Allowed tools: weather, email, calendar, infra-monitor, "
    "network_scan, arr-monitor, self-evolve, reminders, "
    "smart-home-on, smart-home-off, smart-home-cinema, "
    "smart-home-relax, smart-home-work, tts.\n"
    "No extra text. If no tools apply, respond with <tools></tools>."
)


def _load_eval_set(path: str) -> list[dict]:
    examples = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                examples.append(json.loads(line))
    return examples


def _extract_ground_truth(example: dict) -> tuple[str, set[str]]:
    """Extract query and expected tools from a JSONL example."""
    messages = example.get("messages", [])
    query = ""
    expected_xml = ""
    for m in messages:
        if m["role"] == "user":
            query = m["content"]
        elif m["role"] == "assistant":
            expected_xml = m["content"]

    result = parse_multitool_response(expected_xml)
    return query, set(result["tools"]) if result["valid"] else set()


def _call_ollama(query: str, model: str, ollama_url: str, timeout: int = 30) -> str:
    """Call Ollama chat API and return response text."""
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": query},
        ],
        "stream": False,
        "options": {"temperature": 0.0, "num_predict": 128},
    }
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"{ollama_url}/api/chat",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read())
        return data["message"]["content"]


def _resolve_ollama_url(model: str) -> str:
    """Try GPU first, NAS CPU as fallback."""
    for url in [OLLAMA_GPU_URL, OLLAMA_NAS_URL]:
        try:
            req = urllib.request.Request(f"{url}/api/tags", method="GET")
            with urllib.request.urlopen(req, timeout=5) as resp:
                tags = json.loads(resp.read())
                models = [m["name"] for m in tags.get("models", [])]
                if any(model in m for m in models):
                    return url
        except Exception:
            pass
    return OLLAMA_GPU_URL


def run_eval(eval_path: str, model: str, output_path: str, verbose: bool = False) -> dict:
    examples = _load_eval_set(eval_path)
    print(f"[eval] {len(examples)} examples in eval set")
    print(f"[eval] Model: {model}")

    ollama_url = _resolve_ollama_url(model)
    print(f"[eval] Ollama URL: {ollama_url}")

    results = []
    errors = 0

    for i, example in enumerate(examples):
        query, expected = _extract_ground_truth(example)
        raw_response = ""

        try:
            raw_response = _call_ollama(query, model, ollama_url)
            parsed = parse_multitool_response(raw_response)
            predicted = set(parsed["tools"]) if parsed["valid"] else set()
        except Exception as e:
            print(f"[eval] ERROR example {i}: {e}")
            predicted = set()
            errors += 1

        results.append({
            "query": query,
            "predicted": sorted(predicted),
            "expected": sorted(expected),
            "raw": raw_response if verbose else "",
        })

        if (i + 1) % 10 == 0:
            print(f"[eval] {i+1}/{len(examples)} processed...")

    # Aggregate
    results_with_sets = [
        {**r, "predicted": set(r["predicted"]), "expected": set(r["expected"])}
        for r in results
    ]
    metrics = aggregate_metrics(results_with_sets, VALID_TOOLS)
    metrics["errors"] = errors
    metrics["model"] = model
    metrics["timestamp"] = datetime.now().isoformat()
    metrics["eval_file"] = eval_path

    output = {"metrics": metrics, "results": results}
    Path(output_path).write_text(json.dumps(output, indent=2, ensure_ascii=False))
    print(f"\n[eval] Results saved to: {output_path}")
    _print_summary(metrics)

    return metrics


def _print_summary(metrics: dict):
    print("\n" + "=" * 50)
    print(f"  EVAL SUMMARY -- {metrics.get('model', '?')}")
    print("=" * 50)
    print(f"  Examples evaluated:  {metrics['n']}")
    print(f"  Exact Match:         {metrics['exact_match']:.1%}")
    print(f"  Precision:           {metrics['precision']:.1%}")
    print(f"  Recall:              {metrics['recall']:.1%}")
    print(f"  F1:                  {metrics['f1']:.1%}")
    print(f"  Hallucination rate:  {metrics['hallucination_rate']:.1%}")
    print(f"  Over-trigger rate:   {metrics['over_trigger_rate']:.1%}")
    print(f"  Under-trigger rate:  {metrics['under_trigger_rate']:.1%}")
    print(f"  Call errors:         {metrics.get('errors', 0)}")
    print("\n  Per-tool F1:")
    for tool, score in sorted(metrics.get("per_tool_f1", {}).items(),
                               key=lambda x: -x[1]):
        bar = "=" * int(score * 20)
        print(f"    {tool:<20} {score:.3f}  {bar}")
    print("=" * 50)


def compare_results(baseline_path: str, finetuned_path: str):
    baseline = json.loads(Path(baseline_path).read_text())["metrics"]
    finetuned = json.loads(Path(finetuned_path).read_text())["metrics"]

    print("\n" + "=" * 60)
    print("  COMPARISON: Baseline vs Fine-tuned")
    print("=" * 60)
    print(f"  {'Metric':<25} {'Baseline':>10} {'Fine-tuned':>12} {'Delta':>8}")
    print("-" * 60)

    for key in ["exact_match", "precision", "recall", "f1",
                "hallucination_rate", "over_trigger_rate", "under_trigger_rate"]:
        b = baseline.get(key, 0)
        ft = finetuned.get(key, 0)
        delta = ft - b
        sign = "+" if delta >= 0 else ""
        if key in ("hallucination_rate", "over_trigger_rate", "under_trigger_rate"):
            indicator = "OK" if delta <= -0.02 else ("!!" if delta >= 0.02 else "  ")
        else:
            indicator = "OK" if delta >= 0.02 else ("!!" if delta <= -0.02 else "  ")
        print(f"  {indicator} {key:<23} {b:>10.1%} {ft:>12.1%} {sign}{delta:>7.1%}")

    print("=" * 60)
    verdict = "IMPROVEMENT" if finetuned.get("f1", 0) > baseline.get("f1", 0) else "NO IMPROVEMENT"
    print(f"\n  F1 verdict: {verdict}")
    print(f"  Baseline model:   {baseline.get('model')}")
    print(f"  Fine-tuned model: {finetuned.get('model')}")


def export_to_prometheus(metrics: dict, port: int = 9107):
    """Expose eval metrics on :9107/metrics for Prometheus."""
    import http.server
    import threading

    model = metrics.get("model", "unknown").replace("-", "_").replace(".", "_")
    lines = [
        "# HELP tenazas_multitool_eval Multitool eval metrics",
        "# TYPE tenazas_multitool_eval gauge",
    ]
    for key in ["exact_match", "precision", "recall", "f1",
                "hallucination_rate", "over_trigger_rate", "under_trigger_rate"]:
        val = metrics.get(key, 0)
        lines.append(f'tenazas_multitool_eval{{model="{model}",metric="{key}"}} {val}')

    for tool, score in metrics.get("per_tool_f1", {}).items():
        safe_tool = tool.replace("-", "_")
        lines.append(
            f'tenazas_multitool_tool_f1{{model="{model}",tool="{safe_tool}"}} {score}'
        )

    payload = "\n".join(lines) + "\n"

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4")
            self.end_headers()
            self.wfile.write(payload.encode())
        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("", port), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    print(f"[eval] Metrics exposed on :{port}/metrics (Prometheus)")
    return server


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--eval", default="multitool_eval.jsonl")
    parser.add_argument("--model", default="tenazas-multitool")
    parser.add_argument("--output", default="eval_results.json")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--compare", nargs=2, metavar=("BASELINE", "FINETUNED"))
    parser.add_argument("--prometheus", action="store_true")
    args = parser.parse_args()

    if args.compare:
        compare_results(args.compare[0], args.compare[1])
        sys.exit(0)

    metrics = run_eval(args.eval, args.model, args.output, args.verbose)

    if args.prometheus:
        server = export_to_prometheus(metrics)
        print("[eval] Waiting for Prometheus requests... (Ctrl+C to exit)")
        try:
            while True:
                time.sleep(60)
        except KeyboardInterrupt:
            server.shutdown()
