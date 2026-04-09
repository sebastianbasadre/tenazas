#!/usr/bin/env python3
import json, subprocess, os, glob, sys, hashlib, re
from datetime import datetime, timedelta
from urllib.request import urlopen, Request
from pathlib import Path

WORKSPACE = "/volume1/docker/openclaw/state/workspace"
MEMORY = f"{WORKSPACE}/memory/evolution-log.md"
RUNTIME_DIR = "/volume1/docker/openclaw/workspace/runtime"
LOGS_DIR = f"{RUNTIME_DIR}/logs"
LEARNING_DIR = f"{RUNTIME_DIR}/learning"
PROPOSALS_FILE = f"{LEARNING_DIR}/proposals.json"
PROPOSALS_RAW = f"{LEARNING_DIR}/proposals_raw.txt"
METRICS_STATE = f"{LOGS_DIR}/metrics_state.json"
now = datetime.now().strftime("%Y-%m-%d %H:%M")
DRY_RUN = "--dry-run" in sys.argv

# ── Secrets filter ─────────────────────────────────────────────
_SECRET_PATTERNS = re.compile(
    r'(bot[Tt]oken|api[_-]?[Kk]ey|Bearer |password|secret|credential)["\s:=]+\S+',
)


def _sanitize(text: str) -> str:
    """Strip potential secrets from text before sending to external LLM."""
    return _SECRET_PATTERNS.sub("[REDACTED]", text)


# ── Kimi K2 via OpenRouter ─────────────────────────────────────

def _estimate_tokens(text: str) -> int:
    """Rough estimate: ~4 chars per token for mixed en/es text."""
    return len(text) // 4


def build_weekly_context() -> str:
    """Collect last 7 days of logs, metrics, proposals, and system state.

    Returns sanitized context string, capped at ~200K tokens.
    """
    cutoff = datetime.now() - timedelta(days=7)
    cutoff_str = cutoff.strftime("%Y-%m-%d")
    sections = []

    # 1. Logs summary (last 7 days — errors/warnings only, heavily summarized)
    log_lines = []
    for log_path in sorted(glob.glob(f"{LOGS_DIR}/*.log")):
        fname = os.path.basename(log_path)
        try:
            with open(log_path) as f:
                lines = f.readlines()
            # Only keep error/warning/fail lines from last 50 lines
            interesting = []
            for line in lines[-50:]:
                low = line.lower()
                if any(w in low for w in ["error", "fail", "warn", "timeout", "crash", "offline", "degraded"]):
                    interesting.append(line.rstrip()[:150])
            if interesting:
                log_lines.append(f"=== {fname} ===")
                log_lines.extend(interesting[-15:])  # max 15 per file
        except Exception:
            pass
    if log_lines:
        sections.append("## LOGS (errores/warnings últimos 7 días)\n" + "\n".join(log_lines))
    else:
        sections.append("## LOGS\nSin errores ni warnings relevantes.")

    # 2. Metrics state
    try:
        with open(METRICS_STATE) as f:
            metrics = json.load(f)
        sections.append("## MÉTRICAS ACTUALES\n" + json.dumps(metrics, indent=2))
    except Exception:
        sections.append("## MÉTRICAS ACTUALES\nNo disponible")

    # 3. Existing proposals (to avoid repeats)
    try:
        with open(PROPOSALS_FILE) as f:
            existing = json.load(f)
        if existing:
            summaries = [f"- [{p.get('id','')}] {p.get('title', p.get('description',''))[:100]} ({p.get('status','')})"
                         for p in existing[-20:]]
            sections.append("## PROPUESTAS EXISTENTES (no repetir)\n" + "\n".join(summaries))
    except Exception:
        pass

    # 4. System state (containers, models)
    try:
        r = subprocess.run(
            ["docker", "exec", "openclaw", "bash", "-c",
             "docker ps --format '{{.Names}} {{.Status}}' 2>/dev/null | head -25"],
            capture_output=True, text=True, timeout=10)
        sections.append("## CONTAINERS\n" + (r.stdout.strip() or "No data"))
    except Exception:
        sections.append("## CONTAINERS\nNo disponible")

    try:
        r = subprocess.run(
            ["docker", "exec", "openclaw", "bash", "-c",
             "curl -s http://192.168.2.6:11434/api/tags 2>/dev/null | python3 -c \"import sys,json; [print(m['name']) for m in json.load(sys.stdin).get('models',[])]\""],
            capture_output=True, text=True, timeout=10)
        sections.append("## MODELOS OLLAMA ACTIVOS\n" + (r.stdout.strip() or "No data"))
    except Exception:
        pass

    # 5. Guardrail state
    try:
        with open(f"{RUNTIME_DIR}/guardrail_state.json") as f:
            gr = json.load(f)
        sections.append("## GUARDRAIL STATE\n" + json.dumps(gr, indent=2))
    except Exception:
        pass

    context = "\n\n".join(sections)
    context = _sanitize(context)

    # Cap at ~50K tokens (~200K chars) — smaller context gets better results
    max_chars = 200_000
    if len(context) > max_chars:
        context = context[:max_chars] + "\n\n[TRUNCATED]"

    return context


def analyze_with_kimi(context: str) -> list:
    """Call Kimi K2 via OpenRouter to analyze weekly context.

    Returns list of proposal dicts, or [] on failure.
    """
    api_key = os.environ.get("OPENROUTER_API_KEY", "")
    if not api_key:
        print("Kimi: OPENROUTER_API_KEY not set, skipping")
        return []

    system_prompt = (
        "Sos el motor de auto-mejora de Tenazas, un asistente AI personal "
        "corriendo en Docker (NAS Synology DS923+). Tu trabajo es analizar "
        "los logs y métricas de la última semana y proponer mejoras concretas.\n\n"
        "Reglas:\n"
        "- Máximo 5 propuestas\n"
        "- Cada propuesta debe ser accionable y específica\n"
        "- Nunca proponer auto-deploy — Sebastián aprueba todo\n"
        "- Priorizar confiabilidad y observabilidad\n"
        "- No repetir propuestas que ya existen\n\n"
        "Respondé SOLO con un JSON array, sin markdown ni texto extra:\n"
        '[{"id": "short_id", "title": "titulo corto", '
        '"description": "descripcion detallada de la mejora", '
        '"priority": "high|medium|low", "effort": "small|medium|large", '
        '"files_affected": ["path/to/file.py"], "risk": "descripcion del riesgo"}]'
    )

    payload = json.dumps({
        "model": "moonshotai/kimi-k2",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Contexto semanal de Tenazas:\n\n{context}"},
        ],
        "max_tokens": 4096,
        "temperature": 0.3,
    }).encode()

    req = Request(
        "https://openrouter.ai/api/v1/chat/completions",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "HTTP-Referer": "http://tenazas.local",
            "X-OpenRouter-Title": "Tenazas-SelfEvolve",
        },
    )

    try:
        with urlopen(req, timeout=120) as r:
            data = json.loads(r.read())
        raw_response = data["choices"][0]["message"]["content"]
        finish_reason = data["choices"][0].get("finish_reason", "unknown")
        print(f"Kimi response: {len(raw_response)} chars, finish_reason={finish_reason}")
        if len(raw_response) < 20:
            print(f"Kimi raw (short): {repr(raw_response)}")
    except Exception as e:
        print(f"Kimi API error: {e}")
        return []

    # Parse JSON from response (handle markdown code blocks)
    text = raw_response.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)

    try:
        proposals = json.loads(text)
        if not isinstance(proposals, list):
            raise ValueError("Response is not a JSON array")
        return proposals
    except (json.JSONDecodeError, ValueError) as e:
        # Try to salvage partial JSON (truncated array)
        try:
            # Find last complete object in a truncated array: [...}, ...]
            last_close = text.rfind("}")
            if last_close > 0:
                salvaged = json.loads(text[:last_close + 1] + "]")
                if isinstance(salvaged, list) and salvaged:
                    print(f"Kimi: salvaged {len(salvaged)} proposals from truncated response")
                    return salvaged
        except Exception:
            pass
        print(f"Kimi JSON parse error: {e}")
        os.makedirs(LEARNING_DIR, exist_ok=True)
        with open(PROPOSALS_RAW, "w") as f:
            f.write(f"# Raw Kimi response — {now}\n# finish_reason: {finish_reason}\n\n{raw_response}\n")
        return []


def save_kimi_proposals(proposals: list) -> list:
    """Merge Kimi proposals into learning/proposals.json. Returns saved list."""
    os.makedirs(LEARNING_DIR, exist_ok=True)

    # Load existing
    existing = []
    try:
        with open(PROPOSALS_FILE) as f:
            existing = json.load(f)
    except Exception:
        pass

    existing_ids = {p.get("id", "") for p in existing}
    ts = datetime.now().astimezone().isoformat()
    saved = []

    for p in proposals[:5]:  # hard cap at 5
        # Generate stable ID if missing
        pid = p.get("id", "")
        if not pid:
            pid = hashlib.sha256(p.get("title", str(p)).encode()).hexdigest()[:12]
        if pid in existing_ids:
            continue

        entry = {
            "id": pid,
            "type": "kimi_weekly_analysis",
            "title": p.get("title", "Sin título"),
            "description": p.get("description", ""),
            "priority": p.get("priority", "medium"),
            "effort": p.get("effort", "medium"),
            "files_affected": p.get("files_affected", []),
            "risk": p.get("risk", ""),
            "status": "proposed",
            "source": "kimi-k2",
            "created_at": ts,
        }
        existing.append(entry)
        existing_ids.add(pid)
        saved.append(entry)

    with open(PROPOSALS_FILE, "w") as f:
        json.dump(existing, f, indent=2, ensure_ascii=False)

    return saved


def format_kimi_telegram(proposals: list) -> str:
    """Format Kimi proposals for Telegram notification."""
    if not proposals:
        return ""
    lines = [f"🤖 Kimi K2 — {len(proposals)} propuesta(s) nueva(s):"]
    for p in proposals:
        prio = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(p.get("priority", ""), "⚪")
        lines.append(f"{prio} {p.get('title', 'Sin título')}")
        if p.get("description"):
            lines.append(f"   {p['description'][:120]}")
    lines.append("\n📝 Esperando aprobación de Sebas.")
    return "\n".join(lines)

# 1. Errors
errors = []
for log in glob.glob("/volume1/docker/openclaw/state/logs/*.log") + glob.glob("/tmp/openclaw/*.log"):
    try:
        with open(log) as f:
            for line in f:
                if any(w in line.lower() for w in ["error", "fail"]):
                    if not any(w in line.lower() for w in ["bonjour", "probing"]):
                        errors.append(line.strip()[:120])
    except:
        pass
errors = errors[-20:] if errors else ["Sin errores en los ultimos 7 dias."]

# 2. Skills
skills = []
skills_dir = f"{WORKSPACE}/skills"
for name in sorted(os.listdir(skills_dir)):
    path = os.path.join(skills_dir, name)
    if not os.path.isdir(path):
        continue
    has_skill = os.path.exists(os.path.join(path, "SKILL.md"))
    has_scripts = any(f.endswith(('.sh','.py')) for f in os.listdir(path))
    skills.append(f"- {name}: SKILL={'SI' if has_skill else 'NO'} Scripts={'SI' if has_scripts else 'NO'}")

no_skill = [s.split(':')[0].replace('- ','') for s in skills if 'SKILL=NO' in s]

# 3. Docker
try:
    r = subprocess.run(["sudo", "docker", "ps", "--format", "{{.Names}}"], capture_output=True, text=True, timeout=10)
    total = len(r.stdout.strip().split('\n'))
    r2 = subprocess.run(["sudo", "docker", "ps", "-a", "--format", "{{.Names}} {{.Status}}"], capture_output=True, text=True, timeout=10)
    downed = [l for l in r2.stdout.strip().split('\n') if 'Up' not in l and l.strip()]
    downed_str = ', '.join(downed) if downed else "Ninguno"
except:
    total = "?"
    downed_str = "Error"

# 4. Prompt
prompt = f"""Analiza este reporte semanal de Tenazas (asistente AI en Docker) y propone 1-3 mejoras concretas. Responde en espanol, maximo 200 palabras.

Errores: {'; '.join(errors[:5])}
Total skills: {len(skills)}
Skills sin SKILL.md: {', '.join(no_skill) if no_skill else 'Ninguno'}
Docker: {total} containers, Caidos: {downed_str}

Reglas: Solo mejoras accionables. Nunca auto-desplegar. Foco en confiabilidad."""

# 5. Call Ollama
payload = json.dumps({"model": "qwen2.5:3b", "prompt": prompt, "stream": False}).encode()
try:
    req = Request("http://127.0.0.1:11434/api/generate",
                  data=payload,
                  headers={"Content-Type": "application/json"})
    resp = urlopen(req, timeout=180)
    data = json.loads(resp.read().decode())
    analysis = data.get("response", "Ollama no respondio")
except Exception as e:
    analysis = f"Error Ollama: {e}"

# 6. Kimi K2 analysis (if OPENROUTER_API_KEY available)
kimi_proposals = []
kimi_msg = ""
if os.environ.get("OPENROUTER_API_KEY"):
    try:
        print("Building weekly context for Kimi K2...")
        context = build_weekly_context()
        print(f"Context: {len(context)} chars (~{_estimate_tokens(context)} tokens)")
        kimi_proposals = analyze_with_kimi(context)
        print(f"Kimi returned {len(kimi_proposals)} proposals")
        if kimi_proposals and not DRY_RUN:
            saved = save_kimi_proposals(kimi_proposals)
            kimi_msg = format_kimi_telegram(saved)
            print(f"Saved {len(saved)} new proposals to {PROPOSALS_FILE}")
        elif kimi_proposals and DRY_RUN:
            print("DRY RUN — proposals NOT saved")
    except Exception as e:
        print(f"Kimi analysis failed (continuing with Ollama): {e}")

if DRY_RUN:
    print(f"\nSelf Evolution DRY RUN — {now} ART")
    print("━━━ Ollama Analysis ━━━")
    print(analysis)
    if kimi_proposals:
        print("━━━ Kimi K2 Proposals ━━━")
        print(json.dumps(kimi_proposals, indent=2, ensure_ascii=False))
    else:
        print("━━━ Kimi K2: no proposals ━━━")
    sys.exit(0)

# 7. Log
os.makedirs(os.path.dirname(MEMORY), exist_ok=True)
with open(MEMORY, "a") as f:
    f.write(f"\n## {now}\n{analysis}\n")

# 8. Send to Telegram
try:
    with open("/volume1/docker/openclaw/state/openclaw.json") as f:
        config = json.load(f)
    bot_token = config["channels"]["telegram"]["botToken"]
    msg = f"🧬 Self Evolution — {now} ART\n━━━━━━━━━━━━━\n{analysis}\n━━━━━━━━━━━━━\n📝 Esperando aprobacion de Sebas."
    if kimi_msg:
        msg += f"\n\n{kimi_msg}"
    tg_payload = json.dumps({"chat_id": "1565794551", "text": msg}).encode()
    tg_req = Request(f"https://api.telegram.org/bot{bot_token}/sendMessage",
                     data=tg_payload,
                     headers={"Content-Type": "application/json"})
    urlopen(tg_req, timeout=10)
except Exception as e:
    print(f"Telegram error: {e}")

# 9. Output
print(f"Self Evolution - {now} ART")
print("---")
print(analysis)
if kimi_msg:
    print("---")
    print(kimi_msg)
print("---")

# 10. REM dreaming — memory consolidation post-evolve
try:
    sys.path.insert(0, RUNTIME_DIR)
    from dreaming import run_rem_session
    print("[self_evolve] Iniciando REM dreaming post-evolve...")
    dream_result = run_rem_session(force=True)
    print(f"[self_evolve] Dreaming completado: {dream_result}")
except Exception as e:
    print(f"[self_evolve] Dreaming skipped: {e}")
