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
try:
    from .runtime_utils import load_json, save_json
except ImportError:
    from runtime_utils import load_json, save_json
from urllib.parse import quote_plus
from urllib.request import Request, urlopen

try:
    from .runtime_config import OLLAMA_GPU_URL, SEARXNG_URL, get_default_telegram_chat_id
except ImportError:
    from runtime_config import OLLAMA_GPU_URL, SEARXNG_URL, get_default_telegram_chat_id

RUNTIME = Path(__file__).parent
STATE_DIR = Path("/home/tenazas/.openclaw/state")
STAGING_PATH = STATE_DIR / "learned_rules_staging.json"
APPROVED_PATH = STATE_DIR / "learned_rules_approved.json"
MEMORY_DIR = Path("/home/tenazas/.openclaw/workspace/memory")
LOG_PATH = RUNTIME / "logs" / "continuous_learning.log"
CONFIG_PATH = Path("/home/tenazas/.openclaw/openclaw.json")

OLLAMA_URL = OLLAMA_GPU_URL
CHAT_ID = get_default_telegram_chat_id()


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

    # Default topics based on actual system needs
    if not topics:
        topics = [
            "ollama keep_alive config prevent model unload",
            "synology docker container auto restart policy",
            "qwen2.5 fine-tuning chat classification accuracy",
        ]
    return topics[:5]


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
                f"Sos Tenazas, un asistente AI corriendo en Docker en un NAS Synology con "
                f"Ollama en HomeLab (RTX 3090). Analizá estos hallazgos y extraé 1-3 reglas "
                f"CONCRETAS y APLICABLES. Cada regla debe decir exactamente QUE hacer, "
                f"DONDE (archivo o servicio), y CUANDO aplicarla.\n\n"
                f"Ejemplo bueno: 'Si ollama no responde en 5s, reiniciar el servicio en HomeLab'\n"
                f"Ejemplo malo: 'Asegurate de incorporar funcionalidades innovadoras'\n\n"
                f"Hallazgos:\n{summaries}\n\n"
                f"Reglas concretas (una por linea, sin numeracion):"
            ),
            "stream": False,
            "options": {"temperature": 0.2, "num_predict": 256},
        }).encode()
        resp = urlopen(Request(f"{OLLAMA_URL}/api/generate",
                               data=payload, headers={"Content-Type": "application/json"}), timeout=60)
        raw = json.loads(resp.read()).get("response", "")

        for line in raw.strip().splitlines():
            line = line.strip().lstrip("-•0123456789. ").replace("**", "")
            # Skip meta lines (DONDE, CUANDO as separate lines)
            if line.startswith("DONDE:") or line.startswith("CUANDO:"):
                continue
            if len(line) > 15 and len(line) < 200:
                insights.append({
                    "rule": line,
                    "source": "research",
                    "confidence": 0.5,
                })
        # Max 3 rules per run
        insights = insights[:3]
    except Exception:
        pass

    _log({"step": "extract", "findings": len(findings), "insights": len(insights)})
    return insights


# ── Step 3: Memory (save to staging) ─────────────────────────────

def stage_rules(insights: list[dict]):
    """Save insights as staging rules. NEVER auto-apply.

    Rules stay in staging until Sebas approves via Telegram.
    """
    existing = load_json(STAGING_PATH, default=[])

    new_count = 0
    for insight in insights:
        # Dedup by rule text
        if any(r.get("rule") == insight["rule"] for r in existing):
            continue
        insight["staged_at"] = datetime.now().astimezone().isoformat()
        insight["status"] = "staged"
        existing.append(insight)
        new_count += 1

    # Keep last 50
    existing = existing[-50:]
    save_json(STAGING_PATH, existing)

    # Don't spam Telegram with learning rules — log only.
    # Sebas can check with: python3 continuous_learning_loop.py --status

    _log({"step": "stage", "new_rules": new_count, "total_staged": len(existing)})


def approve_all():
    """Move all staged rules to approved."""
    staged = load_json(STAGING_PATH, default=[])
    approved = load_json(APPROVED_PATH, default=[])

    new_approved = [r for r in staged if r.get("status") == "staged"]
    for r in new_approved:
        r["status"] = "approved"
        r["approved_at"] = datetime.now().astimezone().isoformat()

    approved.extend(new_approved)
    save_json(APPROVED_PATH, approved[-100:])

    # Clear staging
    remaining = [r for r in staged if r.get("status") != "staged"]
    save_json(STAGING_PATH, remaining)

    print(f"Approved {len(new_approved)} rules")
    _log({"step": "approve", "approved": len(new_approved)})
    return len(new_approved)


# ── Main pipeline ────────────────────────────────────────────────

def run_pipeline(topics: list[str] = None):
    """Full pipeline: research → extract → error_mine → candidates → stage.

    Never auto-applies. Everything stays in staging for approval.
    """
    _log({"action": "pipeline_start"})

    # Step 1: Research
    findings = research(topics)

    # Step 2: Extract insights from research
    insights = []
    if findings:
        insights = extract_insights(findings)

    # Step 3: Mine production errors (Sprint 3)
    error_count = 0
    try:
        from . import error_mining
        report = error_mining.mine_all()
        error_count = report.get("total_errors", 0)
        _log({"step": "error_mining", "errors": error_count})
    except Exception as e:
        _log({"step": "error_mining", "error": str(e)[:200]})

    # Step 4: Generate improvement candidates from errors (Sprint 3)
    candidate_count = 0
    try:
        from . import improvement_candidates
        candidates = improvement_candidates.generate()
        candidate_count = len(candidates)
        _log({"step": "improvement_candidates", "new": candidate_count})
    except Exception as e:
        _log({"step": "improvement_candidates", "error": str(e)[:200]})

    # Step 5: Stage research insights (original behavior)
    if insights:
        stage_rules(insights)

    # Step 6: Research-to-runbook for repeated incidents (Sprint 4)
    runbook_count = 0
    try:
        runbook_count = _research_to_runbook()
    except Exception as e:
        _log({"step": "research_to_runbook", "error": str(e)[:200]})

    print(f"Pipeline complete: {len(findings)} findings → {len(insights)} rules staged, "
          f"{error_count} errors mined → {candidate_count} candidates, "
          f"{runbook_count} runbooks generated")


# ── Research-to-Runbook (Sprint 4) ─────────────────────────────


def _research_to_runbook() -> int:
    """Find repeated incidents from error_mining, research fixes, generate runbooks.

    Returns count of runbooks generated.
    """
    # Load today's error mining report
    try:
        from . import error_mining
        report = error_mining.get_today()
    except Exception:
        try:
            import error_mining
            report = error_mining.get_today()
        except Exception:
            return 0

    if not report:
        return 0

    repeated = [e for e in report.get("errors", [])
                if e.get("type") == "repeated_incident"]
    if not repeated:
        return 0

    staging_runbooks = RUNTIME.parent / "staging" / "runbooks"
    staging_runbooks.mkdir(parents=True, exist_ok=True)

    generated = 0
    for incident in repeated[:2]:  # max 2 runbooks per run
        detail = incident.get("detail", "")
        count = incident.get("count", 0)

        # Search for solutions
        search_query = f"{detail[:50]} fix homelab docker synology"
        search_findings = research([search_query])

        # Generate runbook summary via LLM
        runbook_content = _generate_runbook_content(detail, count, search_findings)
        if not runbook_content:
            continue

        # Save to staging
        incident_id = incident.get("id", "unknown")[:12]
        runbook_path = staging_runbooks / f"runbook_{incident_id}.md"
        runbook_path.write_text(runbook_content)

        _log({"step": "runbook_generated", "incident": detail[:60],
              "path": str(runbook_path)})
        generated += 1

    if generated > 0:
        _log({"step": "research_to_runbook", "generated": generated})

    return generated


def _generate_runbook_content(detail: str, count: int, findings: list) -> str:
    """Generate runbook markdown from incident detail and research findings."""
    findings_text = ""
    if findings:
        findings_text = "\n".join(
            f"- {f['title']}: {f['snippet'][:100]}" for f in findings[:3])

    # Try LLM for summary
    try:
        payload = json.dumps({
            "model": "qwen2.5:7b",
            "prompt": (
                f"Generá un runbook conciso para resolver este incidente recurrente "
                f"({count} veces) en un homelab Docker/Synology:\n\n"
                f"Problema: {detail}\n\n"
                f"Hallazgos de investigacion:\n{findings_text}\n\n"
                f"Formato: markdown con secciones Problema, Diagnostico, Solucion, "
                f"Prevencion. Maximo 200 palabras."
            ),
            "stream": False,
            "options": {"temperature": 0.2, "num_predict": 512},
        }).encode()
        resp = urlopen(Request(f"{OLLAMA_URL}/api/generate",
                               data=payload, headers={"Content-Type": "application/json"}),
                       timeout=60)
        content = json.loads(resp.read()).get("response", "")
        if content and len(content) > 50:
            return f"# Runbook: {detail[:60]}\n\n_Generado automaticamente ({count}x recurrente)_\n\n{content}"
    except Exception:
        pass

    # Fallback: template without LLM
    return (
        f"# Runbook: {detail[:60]}\n\n"
        f"_Incidente recurrente ({count}x). Requiere revision manual._\n\n"
        f"## Problema\n{detail}\n\n"
        f"## Investigacion\n{findings_text or 'Sin hallazgos automaticos.'}\n\n"
        f"## Diagnostico\nTODO: agregar pasos de diagnostico\n\n"
        f"## Solucion\nTODO: agregar pasos de solucion\n\n"
        f"## Prevencion\nTODO: agregar medidas preventivas\n"
    )


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
        staged = load_json(STAGING_PATH, default=[])
        pending = [r for r in staged if r.get("status") == "staged"]
        print(f"Staged rules: {len(pending)}")
        for r in pending:
            print(f"  [{r.get('confidence', '?')}] {r['rule'][:80]}")
    elif args.approve_all:
        approve_all()
    else:
        p.print_help()
