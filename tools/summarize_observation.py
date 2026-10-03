"""Summarize passive samples; distinguish elapsed time from stability evidence."""

import argparse
import json
import time
from pathlib import Path


def summarize(path):
    samples = []
    for line in path.read_text().splitlines():
        try:
            samples.append(json.loads(line))
        except ValueError:
            continue
    if not samples:
        return {"samples": 0, "period_complete": False}
    cycles = {}
    epochs = {}
    legacy_restarts = {}
    for sample in samples:
        runtime = sample.get("runtime", {})
        if runtime.get("ts"):
            cycles[runtime["ts"]] = runtime
        services = sample.get("services", {})
        epoch = services.get("supervisor_started_at")
        if epoch:
            epochs[epoch] = services.get("services", {})
        else:
            for name, service in services.get("services", {}).items():
                legacy_restarts[name] = max(legacy_restarts.get(name, 0), service.get("restarts", 0))
    enhanced = [sample for sample in samples if sample.get("schema_version") == 2]
    memory = [sample["services"]["memory"].get("process_rss_sum_bytes") for sample in enhanced
              if sample.get("services", {}).get("memory", {}).get("process_rss_sum_bytes")]
    p95 = [sample["inference"].get("latency_p95_seconds", 0) for sample in enhanced if sample.get("inference")]
    started = enhanced[0]["ts"] if enhanced else None
    elapsed = bool(started and time.time() - started >= 86400)
    enhanced_gaps = sum(b["ts"] - a["ts"] > 120 for a, b in zip(enhanced, enhanced[1:]))
    coverage = bool(elapsed and len(enhanced) > 1 and enhanced[-1]["ts"] >= started + 86400 - 120 and not enhanced_gaps)
    return {
        "report_at": time.time(), "samples": len(samples),
        "initial_observation_started_at": samples[0]["ts"],
        "full_metrics_started_at": started,
        "period_complete": elapsed,
        "coverage_complete": coverage,
        "cycles_observed": len(cycles),
        "successful_cycles": sum(bool(item.get("workers")) and all(w.get("ok") for w in item["workers"]) for item in cycles.values()),
        "failed_worker_runs": [{"cycle": key, "worker": w.get("name")} for key, item in cycles.items()
                               for w in item.get("workers", []) if not w.get("ok")],
        "latest_job_states": samples[-1].get("jobs", {}),
        "service_restarts_observed": {name: sum(epoch.get(name, {}).get("restarts", 0) for epoch in epochs.values())
                                      for name in {name for epoch in epochs.values() for name in epoch}},
        "legacy_restart_maxima": legacy_restarts,
        "supervisor_epochs_observed": len(epochs),
        "rss_sum_bytes": {"first": memory[0], "last": memory[-1], "max": max(memory)} if memory else {},
        "inference_p95_seconds_max": max(p95) if p95 else None,
        "sampling_gaps_over_120s": sum(b["ts"] - a["ts"] > 120 for a, b in zip(samples, samples[1:])),
        "note": "RSS sum may count shared pages more than once; a change alone does not prove a leak",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    print(json.dumps(summarize(args.path), indent=2))
