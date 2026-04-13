#!/usr/bin/env python3
"""
apply_improvements.py
Processes approved proposals from pending_approvals.json.

For low_f1: generates new seed examples via Ollama and saves to seed file.
For classification_gap: logs suggested patterns (manual apply).
For container_down: suggests restart (manual).

Safety: only writes to seed files and improvement_queue.json.
Never modifies orchestrator.py or other critical code automatically.
"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path
from urllib.request import Request, urlopen

RUNTIME = Path(__file__).parent
PENDING_PATH = RUNTIME / "pending_approvals.json"
QUEUE_PATH = RUNTIME / "learning" / "improvement_queue.json"
SEEDS_PATH = RUNTIME.parent.parent / "state" / "workspace" / "evals" / "auto_seeds.jsonl"
# Container path fallback
if not SEEDS_PATH.parent.exists():
    SEEDS_PATH = Path("/home/tenazas/.openclaw/workspace/evals/auto_seeds.jsonl")

OLLAMA_URL = os.environ.get("OLLAMA_GPU_URL", "http://192.168.2.6:11434")
CHAT_ID = "1565794551"


def _log(msg):
    print(f"[apply] {msg}", flush=True)


def _load_json(path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except Exception:
        return default if default is not None else {}


def _save_json(path, data):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(data, indent=2, ensure_ascii=False))


def _ollama_generate(prompt, model="qwen2.5:3b", timeout=30):
    try:
        payload = json.dumps({
            "model": model, "prompt": prompt,
            "stream": False, "options": {"temperature": 0.8, "num_predict": 512},
        }).encode()
        req = Request(
            f"{OLLAMA_URL}/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        resp = urlopen(req, timeout=timeout)
        return json.loads(resp.read()).get("response", "").strip()
    except Exception:
        return None


def _notify(msg):
    try:
        config = json.loads(open("/home/tenazas/.openclaw/openclaw.json").read())
        token = config["channels"]["telegram"]["botToken"]
        payload = json.dumps({"chat_id": CHAT_ID, "text": msg[:4090]}).encode()
        urlopen(Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=payload,
            headers={"Content-Type": "application/json"},
        ), timeout=10)
    except Exception:
        pass


# ── Tool → seed generation ───────────────────────────────────────

# Map eval tool names to TOOL_SEEDS keys in dataset_builder
TOOL_MAP = {
    "arr-monitor": "arr-monitor",
    "infra-monitor": "infra-monitor",
    "smart-home-on": "smart-home-on",
    "smart-home-off": "smart-home-off",
    "smart-home-cinema": "smart-home-cinema",
    "self-evolve": "self-evolve",
    "calendar": "calendar",
    "email": "email",
    "weather": "weather",
    "chat": "chat",
}

TOOL_DESCRIPTIONS = {
    "arr-monitor": "consultar estado de descargas, sonarr, radarr, torrent",
    "infra-monitor": "consultar estado de containers, docker, NAS, infraestructura",
    "smart-home-on": "prender luces, encender, subir brillo",
    "smart-home-off": "apagar luces, oscurecer, bajar brillo",
    "smart-home-cinema": "modo cine, ver peliculas, netflix, series",
    "self-evolve": "analisis del sistema, propuestas de mejora, revision",
    "calendar": "agenda, eventos, reuniones, horarios",
    "email": "correos, inbox, mails, enviar correo",
    "weather": "clima, temperatura, lluvia, pronostico",
    "chat": "conversacion general, saludos, preguntas abiertas",
}


def _is_spanish(text: str) -> bool:
    """Reject obvious non-Spanish (Portuguese, etc)."""
    import re as _re
    t = text.lower()
    for m in ["você", "voce", "não", "também", "isso", "fazer", "muito",
              "hoje", "obrigad", "tudo bem", "então", "falar", "olhar",
              "ficar", "oque", "aqui", "pode ", "vou ", "quero ver o"]:
        if m in t:
            return False
    if _re.search(r"[ãõç]", t):
        return False
    return True


def generate_seeds_for_tool(tool_name: str, count: int = 10) -> list[str]:
    """Generate seed phrases for a tool via Ollama."""
    desc = TOOL_DESCRIPTIONS.get(tool_name, tool_name)
    prompt = (
        f"Genera {count} frases distintas en espanol rioplatense argentino que un usuario "
        f"diria para activar esta funcion: {desc}. "
        f"Frases cortas, naturales, variadas, en ESPANOL ARGENTINO (no portugues). "
        f"Una por linea, sin numeros ni explicaciones."
    )
    text = _ollama_generate(prompt, model="qwen3:8b", timeout=60)
    if not text:
        return []

    import re
    lines = [
        re.sub(r"^\d+[\.\)\-]\s*", "", line).strip().strip('"').strip("'")
        for line in text.splitlines()
        if line.strip() and len(line.strip()) > 3 and len(line.strip()) < 100
    ]
    return [l for l in lines[:count * 2] if _is_spanish(l)][:count]


# ── Process approved proposals ───────────────────────────────────


def process_approved():
    """Find approved proposals and apply safe improvements."""
    pending = _load_json(PENDING_PATH, {})
    queue = _load_json(QUEUE_PATH, [])

    approved = [
        (k, v) for k, v in pending.items()
        if v.get("status") == "approved"
    ]

    if not approved:
        _log("No approved proposals to process")
        return {"processed": 0}

    _log(f"Processing {len(approved)} approved proposals")
    results = []

    for approval_id, approval in approved:
        summary = approval.get("action_summary", "")
        _log(f"Processing: {summary[:80]}")

        # Detect type from summary
        if "[low_f1]" in summary:
            result = _apply_low_f1(summary, approval)
        elif "[classification_gap]" in summary:
            result = _apply_classification_gap(summary, approval)
        elif "[container_down]" in summary:
            result = {"action": "manual", "note": "Container restart requires manual action"}
        else:
            result = {"action": "skipped", "note": f"Unknown type in: {summary[:50]}"}

        result["approval_id"] = approval_id
        result["summary"] = summary[:100]
        results.append(result)

        # Mark as processed in pending_approvals
        pending[approval_id]["status"] = "processed"
        pending[approval_id]["processed_at"] = datetime.now().isoformat()
        pending[approval_id]["result"] = result.get("action", "unknown")

        # Sync: mark matching proposals in improvement_queue
        _sync_queue_status(summary, result.get("action", "unknown"))

    # Save updated pending
    _save_json(PENDING_PATH, pending)

    # Notify
    applied = [r for r in results if r.get("action") == "seeds_generated"]
    if applied:
        msg = f"Auto-improve aplicado ({len(applied)} propuestas):\n"
        for r in applied:
            msg += f"  - {r.get('tool', '?')}: +{r.get('seeds_count', 0)} seeds\n"
        msg += f"\nSeeds guardados en: auto_seeds.jsonl"
        _notify(msg)
        _log(f"Applied {len(applied)} improvements")

    return {"processed": len(results), "applied": len(applied), "results": results}


def _sync_queue_status(approval_summary: str, action: str):
    """Sync improvement_queue.json status when an approval is processed."""
    queue = _load_json(QUEUE_PATH, [])
    if not queue:
        return

    # Extract type and key info from approval summary
    # e.g. "[low_f1] Tool arr-monitor: F1=34%..."
    import re
    match_type = re.search(r"\[(\w+)\]", approval_summary)
    match_tool = re.search(r"Tool\s+(\S+):", approval_summary)
    a_type = match_type.group(1) if match_type else ""
    a_tool = match_tool.group(1) if match_tool else ""

    updated = False
    for p in queue:
        if p.get("status") != "proposed":
            continue
        # Match by type + tool (for low_f1) or by type alone
        if a_type and p.get("type") == a_type:
            if a_tool and p.get("tool", "") == a_tool:
                p["status"] = "processed" if action != "failed" else "failed"
                updated = True
            elif not a_tool:
                p["status"] = "processed" if action != "failed" else "failed"
                updated = True

    if updated:
        _save_json(QUEUE_PATH, queue)


def _apply_low_f1(summary: str, approval: dict) -> dict:
    """Generate new seeds for a tool with low F1."""
    # Extract tool name from summary: "[low_f1] Tool arr-monitor: F1=34%..."
    import re
    match = re.search(r"Tool\s+(\S+):", summary)
    if not match:
        return {"action": "skipped", "note": "Could not extract tool name"}

    tool = match.group(1)
    if tool not in TOOL_MAP:
        return {"action": "skipped", "note": f"Unknown tool: {tool}"}

    _log(f"Generating seeds for {tool}...")
    seeds = generate_seeds_for_tool(tool, count=10)
    if not seeds:
        return {"action": "failed", "note": "Ollama did not generate seeds", "tool": tool}

    # Append to seed file
    SEEDS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(SEEDS_PATH, "a", encoding="utf-8") as f:
        for seed in seeds:
            entry = json.dumps({"tool": tool, "query": seed}, ensure_ascii=False)
            f.write(entry + "\n")

    _log(f"  {tool}: +{len(seeds)} seeds → {SEEDS_PATH}")
    return {
        "action": "seeds_generated",
        "tool": tool,
        "seeds_count": len(seeds),
        "seeds": seeds,
    }


def _apply_classification_gap(summary: str, approval: dict) -> dict:
    """Log classification gaps for manual pattern review."""
    details = approval.get("action_details", "")
    return {
        "action": "logged",
        "note": "Classification gaps require manual pattern review in orchestrator.py",
        "details": details[:200],
    }


# ── CLI ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    result = process_approved()
    print(json.dumps(result, indent=2, ensure_ascii=False))
