#!/usr/bin/env python3
"""
Operational Memory — Domain-specific fact store for Tenazas.

Remembers user preferences, patterns, and context across interactions.
Organized by domain (finance, expense, jobs) with deduplication and
optional TTL for time-sensitive facts.

NOT for system logs, metrics, or infra state — those live in their own modules.
This is for personal operational context the agent should remember.

Persistence: runtime/memory/operational.json
Logs: runtime/logs/operational_memory.log

Usage:
  from runtime.operational_memory import save_fact, get_context, list_recent

  save_fact("finance", "budget_limit", "Presupuesto mensual: $800k",
            metadata={"amount": 800000})
  save_fact("jobs", "preference", "Prefiere remoto", metadata={"remote": True})

  facts = get_context("finance")  # all finance facts
  recent = list_recent("jobs", limit=5)
"""

import json
from datetime import datetime, timedelta
from pathlib import Path

# ── Config ──────────────────────────────────────────────────────

BASE_DIR = Path(__file__).parent
MEMORY_DIR = BASE_DIR / "memory"
MEMORY_FILE = MEMORY_DIR / "operational.json"
LOG_FILE = BASE_DIR / "logs" / "operational_memory.log"

VALID_DOMAINS = {"finance", "expense", "jobs", "general"}

VALID_FACT_TYPES = {
    "preference",       # user preference ("prefiero remoto", "presupuesto 800k")
    "pattern",          # observed pattern ("gasta mas los viernes")
    "entity",           # known entity ("empresa TIVIT", "recruiter Juan")
    "threshold",        # user-defined limit ("alerta si super > 80k")
    "note",             # free-form note ("entrevista fue tecnica")
    "context",          # situational context ("buscando trabajo activamente")
}

MAX_FACTS_PER_DOMAIN = 200
DEFAULT_TTL_DAYS = 90  # facts expire after 90 days by default


def _now():
    return datetime.now().astimezone()


def _log(entry: dict):
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    entry["ts"] = _now().isoformat()
    with open(LOG_FILE, "a") as f:
        f.write(json.dumps(entry, default=str) + "\n")


# ── Persistence ─────────────────────────────────────────────────


def _load_memory() -> dict:
    """Load the full memory store. Structure: {domain: [facts]}."""
    try:
        with open(MEMORY_FILE) as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return {}
        return data
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_memory(data: dict):
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    with open(MEMORY_FILE, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False, default=str)


# ── Core API ────────────────────────────────────────────────────


def save_fact(
    domain: str,
    fact_type: str,
    content: str,
    source: str = "",
    metadata: dict | None = None,
    ttl_days: int | None = None,
) -> dict | None:
    """Save a fact to operational memory.

    Args:
        domain: one of finance, expense, jobs, general
        fact_type: one of preference, pattern, entity, threshold, note, context
        content: the fact text
        source: where this fact came from (e.g., "user", "daily_brief", "idle_learning")
        metadata: optional structured data
        ttl_days: days until expiry (None = DEFAULT_TTL_DAYS, 0 = never expires)

    Returns:
        The saved fact dict, or None if validation fails.
    """
    if domain not in VALID_DOMAINS:
        _log({"action": "save_fact", "error": f"invalid domain: {domain}"})
        return None
    if fact_type not in VALID_FACT_TYPES:
        _log({"action": "save_fact", "error": f"invalid type: {fact_type}"})
        return None
    if not content or not content.strip():
        return None

    mem = _load_memory()
    domain_facts = mem.get(domain, [])

    # Dedup: same domain + type + content → update instead of duplicate
    for existing in domain_facts:
        if (existing.get("type") == fact_type
                and existing.get("content") == content.strip()):
            # Update timestamp and metadata
            existing["updated_at"] = _now().isoformat()
            if metadata:
                existing["metadata"] = metadata
            if source:
                existing["source"] = source
            _save_memory(mem)
            _log({"action": "update_fact", "domain": domain, "type": fact_type})
            return existing

    if ttl_days is None:
        ttl_days = DEFAULT_TTL_DAYS

    expires = None
    if ttl_days > 0:
        expires = (_now() + timedelta(days=ttl_days)).isoformat()

    fact = {
        "domain": domain,
        "type": fact_type,
        "content": content.strip(),
        "source": source,
        "metadata": metadata or {},
        "created_at": _now().isoformat(),
        "updated_at": _now().isoformat(),
        "expires_at": expires,
    }

    domain_facts.append(fact)

    # Cap per domain
    if len(domain_facts) > MAX_FACTS_PER_DOMAIN:
        domain_facts = domain_facts[-MAX_FACTS_PER_DOMAIN:]

    mem[domain] = domain_facts
    _save_memory(mem)

    _log({"action": "save_fact", "domain": domain, "type": fact_type,
          "content_preview": content[:50]})

    return fact


def get_context(domain: str, fact_type: str | None = None) -> list:
    """Get all active (non-expired) facts for a domain.

    Args:
        domain: finance, expense, jobs, general
        fact_type: optional filter by type

    Returns:
        List of fact dicts, newest first. Expired facts are excluded.
    """
    mem = _load_memory()
    facts = mem.get(domain, [])
    now_iso = _now().isoformat()

    # Filter expired
    active = []
    for f in facts:
        expires = f.get("expires_at")
        if expires and expires < now_iso:
            continue
        if fact_type and f.get("type") != fact_type:
            continue
        active.append(f)

    active.sort(key=lambda f: f.get("updated_at", ""), reverse=True)
    return active


def list_recent(domain: str, limit: int = 10) -> list:
    """Get the most recent facts for a domain.

    Args:
        domain: finance, expense, jobs, general
        limit: max number of facts to return

    Returns:
        List of fact dicts, newest first, up to limit.
    """
    return get_context(domain)[:limit]


def search_facts(keyword: str, domain: str | None = None) -> list:
    """Search facts by keyword across domains.

    Args:
        keyword: text to search in content and metadata
        domain: optional domain filter

    Returns:
        Matching facts, newest first.
    """
    mem = _load_memory()
    results = []
    now_iso = _now().isoformat()
    keyword_lower = keyword.lower()

    domains = [domain] if domain else VALID_DOMAINS
    for d in domains:
        for f in mem.get(d, []):
            expires = f.get("expires_at")
            if expires and expires < now_iso:
                continue
            if (keyword_lower in f.get("content", "").lower()
                    or keyword_lower in json.dumps(f.get("metadata", {})).lower()):
                results.append(f)

    results.sort(key=lambda f: f.get("updated_at", ""), reverse=True)
    return results


def delete_fact(domain: str, content: str) -> bool:
    """Delete a specific fact by domain and content match.

    Returns:
        True if deleted, False if not found.
    """
    mem = _load_memory()
    facts = mem.get(domain, [])
    original_len = len(facts)

    facts = [f for f in facts if f.get("content") != content]

    if len(facts) == original_len:
        return False

    mem[domain] = facts
    _save_memory(mem)
    _log({"action": "delete_fact", "domain": domain, "content_preview": content[:50]})
    return True


def cleanup_expired() -> int:
    """Remove expired facts from all domains.

    Returns:
        Number of facts removed.
    """
    mem = _load_memory()
    now_iso = _now().isoformat()
    removed = 0

    for domain in list(mem.keys()):
        facts = mem[domain]
        active = []
        for f in facts:
            expires = f.get("expires_at")
            if expires and expires < now_iso:
                removed += 1
            else:
                active.append(f)
        mem[domain] = active

    if removed > 0:
        _save_memory(mem)
        _log({"action": "cleanup_expired", "removed": removed})

    return removed


def get_stats() -> dict:
    """Get memory statistics per domain."""
    mem = _load_memory()
    stats = {"total": 0}
    for domain in VALID_DOMAINS:
        count = len(mem.get(domain, []))
        stats[domain] = count
        stats["total"] += count
    return stats


# ── CLI ─────────────────────────────────────────────────────────


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Tenazas Operational Memory")
    parser.add_argument("--save", nargs=3, metavar=("DOMAIN", "TYPE", "CONTENT"),
                        help="Save a fact")
    parser.add_argument("--context", type=str, metavar="DOMAIN",
                        help="Get context for domain")
    parser.add_argument("--recent", type=str, metavar="DOMAIN",
                        help="List recent facts")
    parser.add_argument("--search", type=str, help="Search facts by keyword")
    parser.add_argument("--stats", action="store_true", help="Memory statistics")
    parser.add_argument("--cleanup", action="store_true", help="Remove expired facts")
    parser.add_argument("--json", action="store_true", help="Output as JSON")

    args = parser.parse_args()

    if args.save:
        domain, fact_type, content = args.save
        result = save_fact(domain, fact_type, content, source="cli")
        if result:
            print(f"Guardado: [{domain}] {fact_type}: {content}")
        else:
            print(f"Error. Dominios: {', '.join(sorted(VALID_DOMAINS))}")
            print(f"Tipos: {', '.join(sorted(VALID_FACT_TYPES))}")

    elif args.context:
        facts = get_context(args.context)
        if args.json:
            print(json.dumps(facts, ensure_ascii=False, indent=2, default=str))
        else:
            if not facts:
                print(f"Sin memoria para {args.context}")
            else:
                for f in facts:
                    print(f"  [{f['type']}] {f['content']}"
                          + (f" (src: {f['source']})" if f.get("source") else ""))

    elif args.recent:
        facts = list_recent(args.recent)
        if args.json:
            print(json.dumps(facts, ensure_ascii=False, indent=2, default=str))
        else:
            for f in facts:
                print(f"  {f.get('updated_at', '?')[:10]} [{f['type']}] {f['content']}")

    elif args.search:
        results = search_facts(args.search)
        if args.json:
            print(json.dumps(results, ensure_ascii=False, indent=2, default=str))
        else:
            if not results:
                print(f"No se encontró: {args.search}")
            else:
                for f in results:
                    print(f"  [{f['domain']}/{f['type']}] {f['content']}")

    elif args.stats:
        s = get_stats()
        print(json.dumps(s, indent=2) if args.json else
              "\n".join(f"  {k}: {v}" for k, v in s.items()))

    elif args.cleanup:
        removed = cleanup_expired()
        print(f"Eliminados: {removed} facts expirados")

    else:
        parser.print_help()
