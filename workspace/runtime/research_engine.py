#!/usr/bin/env python3
"""
Research Engine — Local knowledge search for unknown problems.

When Tenazas encounters unknown problems, searches local knowledge:
- RAG query.py patterns
- infra_memory patterns
- Runbooks

Does NOT call external APIs or web search. Only local knowledge.

Log: workspace/runtime/logs/research.log
"""

import json
import subprocess
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).parent
TELEMETRY_LOG = BASE_DIR / "logs" / "research.log"

# Paths to knowledge sources
QUERY_PY = BASE_DIR.parent / "skills" / "rag" / "query.py"
RUNBOOKS_DIR = BASE_DIR / "runbooks"


def _now():
    return datetime.now().astimezone()


def _log(entry: dict):
    TELEMETRY_LOG.parent.mkdir(parents=True, exist_ok=True)
    entry["ts"] = _now().isoformat()
    with open(TELEMETRY_LOG, "a") as f:
        f.write(json.dumps(entry, default=str) + "\n")


def _load_json(path: Path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default if default is not None else {}


# ── Knowledge Sources ───────────────────────────────────────────


def _search_rag(problem: str, top_k: int = 5) -> list:
    """Search RAG via query.py. Returns list of findings."""
    if not QUERY_PY.exists():
        return []

    try:
        result = subprocess.run(
            ["python3", str(QUERY_PY), problem, "--json", "--top-k", str(top_k)],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0 and result.stdout.strip():
            data = json.loads(result.stdout.strip())
            if isinstance(data, list):
                return [
                    {
                        "source": "rag",
                        "content": item.get("text", item.get("content", ""))[:500],
                        "metadata": {
                            k: v for k, v in item.items()
                            if k not in ("text", "content")
                        },
                    }
                    for item in data
                    if item.get("text") or item.get("content")
                ]
    except (subprocess.TimeoutExpired, json.JSONDecodeError, FileNotFoundError):
        pass
    return []


def _search_infra_memory(problem: str) -> list:
    """Search infra_memory patterns for related issues."""
    findings = []
    try:
        from runtime import infra_memory
        patterns = infra_memory.get_patterns()

        problem_lower = problem.lower()
        for p in patterns:
            # Simple keyword matching against pattern fields
            pattern_text = f"{p.get('event', '')} {p.get('target', '')} {p.get('pattern', '')}".lower()
            keywords = problem_lower.split()
            matches = sum(1 for kw in keywords if kw in pattern_text)
            if matches > 0:
                findings.append({
                    "source": "infra_memory",
                    "content": f"Pattern: {p.get('pattern', '')} — "
                               f"event={p.get('event', '')}, target={p.get('target', '')}, "
                               f"confidence={p.get('confidence', 0)}, "
                               f"action={p.get('recommended_action', '')}",
                    "metadata": {
                        "pattern": p.get("pattern", ""),
                        "confidence": p.get("confidence", 0),
                        "keyword_matches": matches,
                    },
                })
    except ImportError:
        pass
    except Exception as e:
        _log({"type": "search_error", "source": "infra_memory", "error": str(e)})

    return findings


def _search_runbooks(problem: str) -> list:
    """Search saved runbooks for relevant procedures."""
    findings = []
    if not RUNBOOKS_DIR.exists():
        return findings

    problem_lower = problem.lower()
    for path in RUNBOOKS_DIR.glob("*.json"):
        try:
            with open(path) as f:
                rb = json.load(f)

            # Match against runbook title, problem, target
            rb_text = f"{rb.get('title', '')} {rb.get('problem', '')} {rb.get('target', '')}".lower()
            keywords = problem_lower.split()
            matches = sum(1 for kw in keywords if kw in rb_text)

            if matches > 0:
                steps_summary = "; ".join(
                    s.get("action", "") for s in rb.get("steps", [])[:3]
                )
                findings.append({
                    "source": "runbook",
                    "content": f"Runbook: {rb.get('title', '')} — "
                               f"Target: {rb.get('target', '')} — "
                               f"Steps: {steps_summary}",
                    "metadata": {
                        "runbook_id": rb.get("id", ""),
                        "severity": rb.get("severity", ""),
                        "keyword_matches": matches,
                    },
                })
        except (json.JSONDecodeError, KeyError):
            continue

    return findings


# ── Confidence Calculation ──────────────────────────────────────


def _compute_confidence(findings: list) -> float:
    """Compute research confidence based on findings quality."""
    if not findings:
        return 0.0

    # More sources = higher confidence
    source_types = set(f["source"] for f in findings)
    source_score = min(len(source_types) / 3.0, 1.0)  # max 3 source types

    # More findings = higher confidence (diminishing returns)
    count_score = min(len(findings) / 5.0, 1.0)

    # Average keyword matches from metadata
    match_scores = [
        f.get("metadata", {}).get("keyword_matches", 0) for f in findings
    ]
    avg_matches = sum(match_scores) / len(match_scores) if match_scores else 0
    match_score = min(avg_matches / 3.0, 1.0)

    confidence = (source_score * 0.4) + (count_score * 0.3) + (match_score * 0.3)
    return round(min(confidence, 1.0), 4)


# ── Main API ────────────────────────────────────────────────────


def query_knowledge(problem: str) -> dict:
    """Search local knowledge for information about a problem.

    Searches: RAG, infra_memory patterns, runbooks.

    Args:
        problem: problem description or search query

    Returns:
        {
            "findings": list of finding dicts,
            "sources": list of source types that contributed,
            "confidence": float 0-1,
        }
    """
    if not problem or not problem.strip():
        return {"findings": [], "sources": [], "confidence": 0.0}

    all_findings = []

    # Search all local sources
    rag_results = _search_rag(problem)
    all_findings.extend(rag_results)

    memory_results = _search_infra_memory(problem)
    all_findings.extend(memory_results)

    runbook_results = _search_runbooks(problem)
    all_findings.extend(runbook_results)

    # Sort by keyword matches (higher = more relevant)
    all_findings.sort(
        key=lambda f: f.get("metadata", {}).get("keyword_matches", 0),
        reverse=True,
    )

    sources = list(set(f["source"] for f in all_findings))
    confidence = _compute_confidence(all_findings)

    result = {
        "findings": all_findings,
        "sources": sources,
        "confidence": confidence,
    }

    _log({
        "type": "research_query",
        "problem": problem[:200],
        "findings_count": len(all_findings),
        "sources": sources,
        "confidence": confidence,
    })

    return result
