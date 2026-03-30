#!/usr/bin/env python3
"""
Context Manager — Token budget allocation and context pruning for Tenazas.

Controls how much context each source gets in the LLM prompt:
  RAG context:     40%
  Working memory:  30%
  Agent reasoning: 20%
  Reserved margin: 10%

Prevents context overflow and reduces token waste.
"""

import json
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).parent
TELEMETRY_LOG = BASE_DIR / "logs" / "context_manager.log"

# Budget allocation (percentage of max_tokens)
DEFAULT_BUDGET = {
    "rag_context": 0.40,
    "working_memory": 0.30,
    "agent_reasoning": 0.20,
    "reserved": 0.10,
}

# Approximate chars per token (conservative for multilingual)
CHARS_PER_TOKEN = 3.5


def _now():
    return datetime.now().astimezone()


def _log(entry: dict):
    TELEMETRY_LOG.parent.mkdir(parents=True, exist_ok=True)
    entry["ts"] = _now().isoformat()
    with open(TELEMETRY_LOG, "a") as f:
        f.write(json.dumps(entry, default=str) + "\n")


def allocate_context_budget(
    max_tokens: int = 8192,
    budget: dict | None = None,
) -> dict:
    """Allocate token budget per context source.

    Returns: {source: max_chars} for each budget category.
    """
    b = budget or DEFAULT_BUDGET
    allocation = {}
    for source, pct in b.items():
        tokens = int(max_tokens * pct)
        allocation[source] = {
            "tokens": tokens,
            "chars": int(tokens * CHARS_PER_TOKEN),
            "percentage": pct,
        }
    return allocation


def prune_low_value_context(
    items: list,
    budget_chars: int,
    min_score: float = 0.5,
) -> list:
    """Remove low-scoring items and trim to fit budget.

    Args:
        items: list of dicts with 'text' and 'score' keys
        budget_chars: max total characters
        min_score: minimum score to keep

    Returns: pruned list within budget.
    """
    # Filter by score
    filtered = [i for i in items if i.get("score", 0) >= min_score]
    # Sort by score descending
    filtered.sort(key=lambda x: x.get("score", 0), reverse=True)

    kept = []
    total = 0
    for item in filtered:
        text_len = len(item.get("text", ""))
        if total + text_len <= budget_chars:
            kept.append(item)
            total += text_len
        elif budget_chars - total > 100:
            # Truncate last item to fit
            remaining = budget_chars - total
            truncated = {**item, "text": item["text"][:remaining] + "...", "truncated": True}
            kept.append(truncated)
            break
        else:
            break

    return kept


def compress_old_context(items: list, max_age_hours: int = 24) -> list:
    """Compress or remove context older than max_age_hours.

    Older items get their text truncated to first 200 chars.
    """
    cutoff = _now().timestamp() - (max_age_hours * 3600)
    compressed = []
    for item in items:
        ts = item.get("ts", "")
        try:
            item_ts = datetime.fromisoformat(ts).timestamp()
        except (ValueError, TypeError):
            compressed.append(item)
            continue

        if item_ts < cutoff and len(item.get("text", "")) > 200:
            compressed.append({
                **item,
                "text": item["text"][:200] + "... [compressed]",
                "compressed": True,
            })
        else:
            compressed.append(item)

    return compressed


def prioritize_sources(
    rag_results: list,
    working_memory: list,
    agent_output: list,
    max_tokens: int = 8192,
) -> dict:
    """Allocate and prune all context sources within budget.

    Returns: {rag: list, working: list, agent: list, stats: dict}
    """
    budget = allocate_context_budget(max_tokens)

    rag_pruned = prune_low_value_context(
        rag_results, budget["rag_context"]["chars"]
    )
    working_pruned = prune_low_value_context(
        working_memory, budget["working_memory"]["chars"], min_score=0.0
    )
    agent_pruned = prune_low_value_context(
        agent_output, budget["agent_reasoning"]["chars"], min_score=0.0
    )

    stats = {
        "rag_items": len(rag_pruned),
        "working_items": len(working_pruned),
        "agent_items": len(agent_pruned),
        "total_chars": sum(
            len(i.get("text", ""))
            for lst in [rag_pruned, working_pruned, agent_pruned]
            for i in lst
        ),
        "budget_tokens": max_tokens,
    }

    _log({"type": "context_allocation", **stats})

    return {
        "rag": rag_pruned,
        "working": working_pruned,
        "agent": agent_pruned,
        "stats": stats,
    }
