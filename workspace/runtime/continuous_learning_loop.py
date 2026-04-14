#!/usr/bin/env python3
"""
continuous_learning_loop.py
Pipeline: research → extract → memory → staging.

POLICY: Never auto-applies rules. Writes to learned_rules_staging.json
and sends to Telegram for Sebas to approve. Only approved rules go live.

Usage:
  python3 continuous_learning_loop.py --run          # full pipeline
  python3 continuous_learning_loop.py --status        # show staged rules
  python3 continuous_learning_loop.py --approve-all   # approve all staged
"""

import json
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import quote_plus
from urllib.request import Request, urlopen

RUNTIME = Path(__file__).parent
STATE_DIR = Path("/home/tenazas/.openclaw/state")
STAGING_PATH = STATE_DIR / "learned_rules_staging.json"
APPROVED_PATH = STATE_DIR / "learned_rules_approved.json"
MEMORY_DIR = Path("/home/tenazas/.openclaw/workspace/memory")
LOG_PATH = RUNTIME / "logs" / "continuous_learning.log"
CONFIG_PATH = Path("/home/tenazas/.openclaw/openclaw.json")

SEARXNG_URL = "http://192.168.2.2:8890"
OLLAMA_URL = "http://192.168.2.6:11434"
CHAT_ID = "1565794551"


def _log(entry: dict):
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    entry["ts"] = datetime.now().astimezone().isoformat()
    with open(LOG_PATH, "a") as f:
        f.write(json.dumps(entry, default=str) + "\n")


def _notify(msg: str):
    try:
        token = json.loads(CONFIG_PATH.read_text())["channels"]["telegram"]["botToken"]
        payload = json.dumps({"chat_id": CHAT_ID, "text": msg[:4090]}).encode()
        urlopen(Request(f"https://api.telegram.org/bot{token}/sendMessage",
                        data=payload, headers={"Content-Type": "application/json"}), timeout=10)
    except Exception:
        pass


def _load_json(path: Path) -> list:
    try:
        return json.loads(path.read_text()) if path.exists() else []
    except Exception:
        return []


def _save_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str))


# ── Step 1: Research ─────────────────────────────────────────────

def research(topics: list[str] = None) -> list[dict]:
    """Search for relevant information via SearXNG."""
    if not topics:
        topics = _generate_research_topics()

    findings = []
    for topic in topics[:3]:  # max 3 searches
        try:
            url = f"{SEARXNG_URL}/search?q={quote_plus(topic)}&format=json&language=es"
            resp = urlopen(Request(url, headers={"Accept": "application/json"}), timeout=10)
            data = json.loads(resp.read())
            for r in data.get("results", [])[:2]:
                findings.append({
                    "topic": topic,
                    "title": r.get("title", ""),
                    "snippet": r.get("content", "")[:300],
                    "url": r.get("url", ""),
                })
        except Exception:
            continue

    _log({"step": "research", "topics": len(topics), "findings": len(findings)})
    return findings


def _generate_research_topics() -> list[str]:
    """Generate research topics from recent errors and feedback."""
    topics = []

    # From feedback negatives
    fb_path = RUNTIME / "learning" / "feedback_log.jsonl"
    if fb_path.exists():
        for line in fb_path.read_text().splitlines()[-20:]:
            try:
                e = json.loads(line)
                if "negative" in e.get("feedback", ""):
                    cat = e.get("category", "")
                    query = e.get("query", "")
                    if query:
                        topics.append(f"como mejorar {cat} para '{query[:30]}'")
            except Exception:
                continue

    # From improvement queue
    queue_path = RUNTIME / "learning" / "improvement_queue.json"
    if queue_path.exists():
        try:
            queue = json.loads(queue_path.read_text())
            for p in queue[-5:]:
                if p.get("status") == "proposed" and p.get("type") == "low_f1":
                    topics.append(f"mejorar clasificacion de {p.get('tool', '')} NLU")
        except Exception:
            pass

    return topics[:5] if topics else ["tendencias homelab 2026", "ollama optimizacion"]


# ── Step 2: Extract ──────────────────────────────────────────────

def extract_insights(findings: list[dict]) -> list[dict]:
    """Use LLM to extract actionable insights from research."""
    if not findings:
        return []

    insights = []
    summaries = "\n".join(f"- {f['title']}: {f['snippet'][:150]}" for f in findings[:5])

    try:
        payload = json.dumps({
            "model": "qwen2.5:3b",
            "prompt": (
                f"De estos hallazgos, extrae 1-3 reglas accionables para un asistente AI "
                f"que corre en Docker/NAS. Solo reglas concretas, una por linea.\n\n"
                f"{summaries}\n\nReglas:"
            ),
            "stream": False,
            "options": {"temperature": 0.3, "num_predict": 256},
        }).encode()
        resp = urlopen(Request(f"{OLLAMA_URL}/api/generate",
                               data=payload, headers={"Content-Type": "application/json"}), timeout=60)
        raw = json.loads(resp.read()).get("response", "")

        for line in raw.strip().splitlines():
            line = line.strip().lstrip("-•0123456789. ")
            if len(line) > 10:
                insights.append({
                    "rule": line[:200],
                    "source": "research",
                    "confidence": 0.5,
                })
    except Exception:
        pass

    _log({"step": "extract", "findings": len(findings), "insights": len(insights)})
    return insights


# ── Step 3: Memory (save to staging) ─────────────────────────────

def stage_rules(insights: list[dict]):
    """Save insights as staging rules. NEVER auto-apply.

    Rules stay in staging until Sebas approves via Telegram.
    """
    existing = _load_json(STAGING_PATH)

    for insight in insights:
        # Dedup by rule text
        if any(r.get("rule") == insight["rule"] for r in existing):
            continue
        insight["staged_at"] = datetime.now().astimezone().isoformat()
        insight["status"] = "staged"
        existing.append(insight)

    # Keep last 50
    existing = existing[-50:]
    _save_json(STAGING_PATH, existing)

    # Notify Sebas
    new_count = len(insights)
    if new_count > 0:
        rules_text = "\n".join(f"  - {i['rule'][:80]}" for i in insights[:5])
        _notify(
            f"Aprendizaje continuo — {new_count} reglas nuevas en staging:\n"
            f"{rules_text}\n\n"
            f"Responde 'aprobar reglas' para aplicar."
        )

    _log({"step": "stage", "new_rules": new_count, "total_staged": len(existing)})


def approve_all():
    """Move all staged rules to approved."""
    staged = _load_json(STAGING_PATH)
    approved = _load_json(APPROVED_PATH)

    new_approved = [r for r in staged if r.get("status") == "staged"]
    for r in new_approved:
        r["status"] = "approved"
        r["approved_at"] = datetime.now().astimezone().isoformat()

    approved.extend(new_approved)
    _save_json(APPROVED_PATH, approved[-100:])

    # Clear staging
    remaining = [r for r in staged if r.get("status") != "staged"]
    _save_json(STAGING_PATH, remaining)

    print(f"Approved {len(new_approved)} rules")
    _log({"step": "approve", "approved": len(new_approved)})
    return len(new_approved)


# ── Main pipeline ────────────────────────────────────────────────

def run_pipeline(topics: list[str] = None):
    """Full pipeline: research → extract → stage (never auto-apply)."""
    _log({"action": "pipeline_start"})

    findings = research(topics)
    if not findings:
        print("No findings from research")
        return

    insights = extract_insights(findings)
    if not insights:
        print("No insights extracted")
        return

    stage_rules(insights)
    print(f"Pipeline complete: {len(findings)} findings → {len(insights)} rules staged")


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--run", action="store_true", help="Run full pipeline")
    p.add_argument("--topics", nargs="+", help="Custom research topics")
    p.add_argument("--status", action="store_true", help="Show staged rules")
    p.add_argument("--approve-all", action="store_true", help="Approve all staged")
    args = p.parse_args()

    if args.run:
        run_pipeline(topics=args.topics)
    elif args.status:
        staged = _load_json(STAGING_PATH)
        pending = [r for r in staged if r.get("status") == "staged"]
        print(f"Staged rules: {len(pending)}")
        for r in pending:
            print(f"  [{r.get('confidence', '?')}] {r['rule'][:80]}")
    elif args.approve_all:
        approve_all()
    else:
        p.print_help()
