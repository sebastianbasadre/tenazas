#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
eval_exporter.py — Prometheus exporter for Tenazas multitool eval metrics.

Reads eval JSON files (baseline, finetuned, 3b_baseline) and exposes
F1, precision, recall, exact_match, hallucination_rate, per-tool F1
on 0.0.0.0:9107/metrics.

Refreshes every TENAZAS_EVAL_REFRESH_SECONDS (default 30s).
Tolerates missing files without crashing.

== Metric Contract ==

Global metrics (gauge, 0.0-1.0):
  tenazas_eval_f1{model="..."}                 — Overall F1 score
  tenazas_eval_precision{model="..."}           — Overall precision
  tenazas_eval_recall{model="..."}              — Overall recall
  tenazas_eval_exact_match{model="..."}         — Exact match rate
  tenazas_eval_hallucination_rate{model="..."}  — Hallucination rate (lower is better)
  tenazas_eval_over_trigger{model="..."}        — Over-trigger rate (lower is better)
  tenazas_eval_under_trigger{model="..."}       — Under-trigger rate (lower is better)

Per-tool metrics (gauge, 0.0-1.0):
  tenazas_eval_f1_per_tool{tool="...", model="..."} — F1 score per tool

Operational metrics:
  tenazas_eval_file_mtime_seconds{model="..."} (gauge) — Last mtime of eval file (unix)
  tenazas_eval_exporter_up (gauge)              — Exporter health: 1=running

Model label values: "baseline", "finetuned", "3b_baseline"
Tool label values: normalized with underscores (e.g. "network_scan", not "network-scanner")

Expected JSON format (from eval_runner.py):
    {
      "metrics": {
        "f1": 0.779,
        "precision": 0.787,
        "recall": 0.775,
        "exact_match": 0.75,
        "hallucination_rate": 0.0,
        "over_trigger_rate": 0.0,
        "under_trigger_rate": 0.15,
        "per_tool_f1": {"weather": 0.9, "network_scan": 1.0},
        "model": "qwen2.5:7b",
        "timestamp": "2026-04-08T22:35:49"
      }
    }
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

from prometheus_client import Gauge, start_http_server

PORT = int(os.getenv("TENAZAS_EVAL_EXPORTER_PORT", "9107"))
REFRESH_SECONDS = int(os.getenv("TENAZAS_EVAL_REFRESH_SECONDS", "30"))
BASE_DIR = Path(__file__).resolve().parent

EVAL_FILES = {
    "baseline": BASE_DIR / "eval_baseline.json",
    "finetuned": BASE_DIR / "eval_finetuned.json",
    "3b_baseline": BASE_DIR / "eval_3b_baseline.json",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | eval_exporter | %(message)s",
)
log = logging.getLogger("eval_exporter")

# ── Prometheus Gauges ───────────────────────────────────────────────

g_f1 = Gauge("tenazas_eval_f1", "Overall F1 score", ["model"])
g_precision = Gauge("tenazas_eval_precision", "Overall precision", ["model"])
g_recall = Gauge("tenazas_eval_recall", "Overall recall", ["model"])
g_exact_match = Gauge("tenazas_eval_exact_match", "Exact match rate", ["model"])
g_hallucination = Gauge("tenazas_eval_hallucination_rate", "Hallucination rate", ["model"])
g_over_trigger = Gauge("tenazas_eval_over_trigger", "Over-trigger rate", ["model"])
g_under_trigger = Gauge("tenazas_eval_under_trigger", "Under-trigger rate", ["model"])
g_f1_per_tool = Gauge("tenazas_eval_f1_per_tool", "Per-tool F1 score", ["model", "tool"])
g_file_mtime = Gauge("tenazas_eval_file_mtime_seconds", "Last mtime of eval file", ["model"])
g_exporter_up = Gauge("tenazas_eval_exporter_up", "Whether the eval exporter is running")

# Map JSON keys to Gauge objects
_GLOBAL_GAUGES = [
    (g_f1, "f1"),
    (g_precision, "precision"),
    (g_recall, "recall"),
    (g_exact_match, "exact_match"),
    (g_hallucination, "hallucination_rate"),
    (g_over_trigger, "over_trigger_rate"),
    (g_under_trigger, "under_trigger_rate"),
]


# ── Helpers ─────────────────────────────────────────────────────────

def _safe_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def load_eval_file(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        log.warning("Error leyendo %s: %s", path.name, e)
        return None


def update_metrics_for_model(model_name: str, path: Path) -> None:
    data = load_eval_file(path)
    if data is None:
        return

    metrics = data.get("metrics", {})
    if not isinstance(metrics, dict):
        log.warning("metrics no es dict en %s", path.name)
        return

    for gauge, key in _GLOBAL_GAUGES:
        val = _safe_float(metrics.get(key))
        if val is not None:
            gauge.labels(model=model_name).set(val)

    # Per-tool F1 — normalize tool names (hyphens to underscores)
    per_tool = metrics.get("per_tool_f1", {})
    if isinstance(per_tool, dict):
        for tool, f1_val in per_tool.items():
            val = _safe_float(f1_val)
            if val is not None:
                safe_tool = tool.replace("-", "_")
                g_f1_per_tool.labels(model=model_name, tool=safe_tool).set(val)

    try:
        g_file_mtime.labels(model=model_name).set(path.stat().st_mtime)
    except Exception:
        pass

    actual_model = metrics.get("model", model_name)
    f1 = metrics.get("f1", "?")
    log.info("model=%s (%s) f1=%s tools=%d", model_name, actual_model, f1, len(per_tool))


def refresh_once() -> None:
    for model_name, path in EVAL_FILES.items():
        update_metrics_for_model(model_name, path)


def main() -> None:
    log.info("Levantando exporter en 0.0.0.0:%s (refresh cada %ds)", PORT, REFRESH_SECONDS)
    start_http_server(PORT, addr="0.0.0.0")
    g_exporter_up.set(1)

    while True:
        try:
            refresh_once()
        except Exception as e:
            log.exception("Error refrescando métricas: %s", e)
        time.sleep(REFRESH_SECONDS)


if __name__ == "__main__":
    main()
