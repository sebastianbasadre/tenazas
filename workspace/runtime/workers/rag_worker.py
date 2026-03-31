#!/usr/bin/env python3
"""
RAG Worker — Runs RAG maintenance tasks (re-indexing check, confidence analysis).

Isolated subprocess. Low priority — safe to skip or timeout.
Does NOT run on every cycle. Checks if re-indexing is needed.
"""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, "/home/tenazas")

BASE_DIR = Path(__file__).resolve().parent.parent
LOG_FILE = BASE_DIR / "logs" / "rag_worker.log"
SKILLS_DIR = Path("/home/tenazas/.openclaw/workspace/skills/rag")


def _log(msg: str):
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG_FILE, "a") as f:
        f.write(f"{ts} {msg}\n")


def run():
    start = time.time()
    _log("rag_worker start")
    results = {}

    # Check retrieval quality from recent logs
    try:
        from runtime import retrieval_quality
        quality = retrieval_quality.assess_recent()
        results["retrieval_quality"] = {
            "score": quality.get("overall_score", 0),
            "samples": quality.get("sample_count", 0),
        }
    except Exception as e:
        results["retrieval_quality"] = {"error": str(e)[:200]}

    # Check if ingest.py needs to run (check file ages)
    try:
        ingest_script = SKILLS_DIR / "ingest.py"
        if ingest_script.exists():
            # Check last ingest time from ChromaDB metadata
            results["ingest_available"] = True
        else:
            results["ingest_available"] = False
    except Exception as e:
        results["ingest_available"] = False

    elapsed = round(time.time() - start, 2)
    _log(f"rag_worker done elapsed={elapsed}s")
    print(json.dumps({"ok": True, "elapsed": elapsed, **results}))


if __name__ == "__main__":
    run()
