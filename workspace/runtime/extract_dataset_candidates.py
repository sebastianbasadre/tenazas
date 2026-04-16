#!/usr/bin/env python3
"""
extract_dataset_candidates.py — Extract dataset candidates from Tenazas logs.

Reads router_decisions.jsonl and chat_failures.jsonl, produces:
  raw/chat_candidates.jsonl    — good chat interactions
  raw/failure_candidates.jsonl — bad/interesting cases for corrections

Usage:
    python3 extract_dataset_candidates.py
    python3 extract_dataset_candidates.py --min-date 2026-04-16
    python3 extract_dataset_candidates.py --stats
"""

import json
import sys
import uuid
from datetime import datetime
from pathlib import Path

RUNTIME_DIR = Path(__file__).parent
LOG_DIR = RUNTIME_DIR / "logs"
ROUTER_LOG = LOG_DIR / "router_decisions.jsonl"
FAIL_LOG = LOG_DIR / "chat_failures.jsonl"

OUTPUT_DIR = Path("/home/tenazas/datasets/premium_v2/raw")

# System prompt for all dataset examples
SYSTEM_PROMPT = (
    "Sos Tenazas, un asistente personal técnico y operativo. "
    "Hablás en español rioplatense, directo, claro y útil. "
    "No hablás como empresa ni como soporte corporativo. "
    "Priorizás resolver, ordenar y destrabar. "
    "Cuando faltan datos, preguntás lo mínimo necesario. "
    "Si no sabés algo o no lo podés verificar, lo decís claramente. "
    "Tus respuestas suelen ser breves, salvo que el problema requiera más detalle."
)


def _load_jsonl(path: Path, min_date: str = None) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
            if min_date and row.get("ts", "")[:10] < min_date:
                continue
            rows.append(row)
        except json.JSONDecodeError:
            continue
    return rows


def _make_id() -> str:
    return uuid.uuid4().hex[:12]


def extract_chat_candidates(min_date: str = None) -> list[dict]:
    """Extract good chat interactions from router decisions."""
    decisions = _load_jsonl(ROUTER_LOG, min_date)
    candidates = []

    for d in decisions:
        # Skip guardrails, errors, empty
        if d.get("guardrail_hit"):
            continue
        if not d.get("success", True):
            continue
        query = d.get("query", "").strip()
        if not query or len(query) < 3:
            continue

        route = d.get("route", "")
        path = d.get("path", "")

        # Determine type
        if path == "llm_chat":
            dtype = "chat"
        elif path == "composer":
            dtype = "skill_good"
        elif path == "humanize":
            dtype = "skill_humanized"
        else:
            dtype = "other"

        # We want: chat_free responses and good composer examples
        if dtype not in ("chat", "skill_good"):
            continue

        tags = [route, path]
        if d.get("skill"):
            tags.append(d["skill"])

        candidates.append({
            "id": _make_id(),
            "source": "router_log",
            "type": dtype,
            "query": query,
            "route": route,
            "path": path,
            "skill": d.get("skill"),
            "latency_ms": d.get("latency_ms", 0),
            "ts": d.get("ts", ""),
            "tags": tags,
            "quality": 0,  # scored later
            "needs_review": True,
        })

    return candidates


def extract_failure_candidates(min_date: str = None) -> list[dict]:
    """Extract failure cases for corrections dataset."""
    failures = _load_jsonl(FAIL_LOG, min_date)
    candidates = []

    for f in failures:
        query = f.get("query", "").strip()
        if not query:
            continue

        severity = f.get("severity", "medium")
        reason = f.get("reason", "unknown")

        candidates.append({
            "id": _make_id(),
            "source": "failure_log",
            "type": "correction",
            "query": query,
            "route": f.get("route", ""),
            "path": f.get("path", ""),
            "reason": reason,
            "severity": severity,
            "response_preview": f.get("response_preview", ""),
            "ts": f.get("ts", ""),
            "tags": ["failure", reason, severity],
            "quality": 0,
            "needs_review": True,
            "correction": "",  # to be filled manually
        })

    return candidates


def _write_jsonl(path: Path, items: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for item in items:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")


def main():
    args = sys.argv[1:]
    min_date = None
    for i, a in enumerate(args):
        if a == "--min-date" and i + 1 < len(args):
            min_date = args[i + 1]

    chat = extract_chat_candidates(min_date)
    failures = extract_failure_candidates(min_date)

    if "--stats" in args:
        print(f"Chat candidates: {len(chat)}")
        print(f"  chat_free: {sum(1 for c in chat if c['type'] == 'chat')}")
        print(f"  skill_good: {sum(1 for c in chat if c['type'] == 'skill_good')}")
        print(f"Failure candidates: {len(failures)}")
        if failures:
            from collections import Counter
            reasons = Counter(f["reason"] for f in failures)
            for r, c in reasons.most_common(5):
                print(f"  {r}: {c}")
        return

    _write_jsonl(OUTPUT_DIR / "chat_candidates.jsonl", chat)
    _write_jsonl(OUTPUT_DIR / "failure_candidates.jsonl", failures)

    print(f"Extracted {len(chat)} chat + {len(failures)} failure candidates")
    print(f"  → {OUTPUT_DIR / 'chat_candidates.jsonl'}")
    print(f"  → {OUTPUT_DIR / 'failure_candidates.jsonl'}")


if __name__ == "__main__":
    main()
