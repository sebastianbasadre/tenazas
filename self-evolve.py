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


def _resolve_openrouter_key() -> str:
    """Resolve OPENROUTER_API_KEY from env, .env file, or docker-compose.yml.

    self-evolve.py runs on the NAS host, but the key is only set inside
    the openclaw container via docker-compose environment.
    """
    key = os.environ.get("OPENROUTER_API_KEY", "")
    if key:
        return key
    base = "/volume1/docker/openclaw"
    # Try .env file first (secrets moved here from compose)
    try:
        with open(f"{base}/.env") as f:
            for line in f:
                stripped = line.strip()
                if stripped.startswith("OPENROUTER_API_KEY="):
                    key = stripped.split("=", 1)[1].strip()
                    if key and not key.startswith("${"):
                        print("Kimi: resolved API key from .env")
                        return key
    except Exception:
        pass
    # Fallback: read from docker-compose.yml (legacy, key hardcoded in compose)
    try:
        with open(f"{base}/docker-compose.yml") as f:
            for line in f:
                stripped = line.strip()
                if stripped.startswith("- OPENROUTER_API_KEY="):
                    key = stripped.split("=", 1)[1].strip()
                    if key and not key.startswith("${"):
                        print("Kimi: resolved API key from docker-compose.yml")
                        return key
    except Exception as e:
        print(f"Kimi: could not read compose file: {e}")
    return ""


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
    api_key = _resolve_openrouter_key()
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

# ── Build rich Ollama context ─────────────────────────────────

def _build_ollama_context() -> dict:
    """Collect operational data for a high-quality weekly analysis."""
    ctx = {}

    # 1. Errors (last 7 days, from runtime logs)
    errors = []
    for log in glob.glob(f"{LOGS_DIR}/*.log") + glob.glob("/volume1/docker/openclaw/state/logs/*.log") + glob.glob("/tmp/openclaw/*.log"):
        try:
            with open(log) as f:
                for line in f.readlines()[-50:]:
                    if any(w in line.lower() for w in ["error", "fail", "timeout", "crash"]):
                        if not any(w in line.lower() for w in ["bonjour", "probing", "hash:"]):
                            errors.append(line.strip()[:150])
        except Exception:
            pass
    ctx["errors"] = errors[-15:] if errors else []

    # 2. Skills
    skills_dir = f"{WORKSPACE}/skills"
    skills = []
    no_skill = []
    try:
        for name in sorted(os.listdir(skills_dir)):
            path = os.path.join(skills_dir, name)
            if not os.path.isdir(path):
                continue
            has_skill = os.path.exists(os.path.join(path, "SKILL.md"))
            has_scripts = any(f.endswith(('.sh', '.py')) for f in os.listdir(path))
            skills.append(name)
            if not has_skill:
                no_skill.append(name)
    except Exception:
        pass
    ctx["total_skills"] = len(skills)
    ctx["skills_no_doc"] = no_skill

    # 3. Docker containers
    try:
        r = subprocess.run(["sudo", "docker", "ps", "--format", "{{.Names}}"],
                           capture_output=True, text=True, timeout=10)
        ctx["containers_up"] = len([l for l in r.stdout.strip().split('\n') if l.strip()])
        r2 = subprocess.run(["sudo", "docker", "ps", "-a", "--format", "{{.Names}} {{.Status}}"],
                            capture_output=True, text=True, timeout=10)
        ctx["containers_down"] = [l for l in r2.stdout.strip().split('\n') if 'Up' not in l and l.strip()]
    except Exception:
        ctx["containers_up"] = "?"
        ctx["containers_down"] = []

    # 4. Metrics state (runtime counters)
    try:
        with open(METRICS_STATE) as f:
            m = json.load(f)
        ctx["runtime_runs"] = m.get("runs", 0)
        ctx["runtime_failures"] = m.get("failures", 0)
        ctx["restarts"] = m.get("restarts", 0)
        ctx["vpn_disconnects"] = m.get("vpn_disconnects", 0)
        ctx["health_score"] = m.get("health_score", "?")
        ctx["freezes"] = m.get("freezes", 0)
        rv2 = m.get("router_v2", {})
        ctx["router"] = {
            "v2_calls": rv2.get("v2_calls", 0),
            "v1_fallbacks": rv2.get("v1_fallback_from_v2", 0),
            "cloud_fallbacks": rv2.get("cloud_fallbacks", 0),
            "vram_swaps": rv2.get("vram_swaps", 0),
            "role_distribution": {k: v for k, v in rv2.items() if k.startswith("role_")},
        }
        bench = m.get("benchmark_continuous", {})
        ctx["router_accuracy"] = bench.get("router_accuracy", "?")
    except Exception:
        pass

    # 5. ROI state
    try:
        with open(f"{LOGS_DIR}/roi_state.json") as f:
            roi = json.load(f)
        ctx["roi"] = {
            "skill_executions": roi.get("skill_executions", 0),
            "llm_chats": roi.get("llm_chats", 0),
            "automation_rate": roi.get("automation_rate", 0),
            "hours_saved": roi.get("hours_saved", 0),
            "errors_avoided": roi.get("errors_avoided", 0),
        }
    except Exception:
        pass

    # 6. Dreaming insights (lasting truths)
    try:
        dreams_path = f"{LEARNING_DIR}/dreams.md"
        with open(dreams_path) as f:
            lines = [l.strip() for l in f.readlines() if l.startswith("- [")]
        ctx["dream_insights"] = lines[-8:]  # last 8 insights
    except Exception:
        ctx["dream_insights"] = []

    # 7. Telegram usage (message count from handler log)
    try:
        tg_log = f"{LOGS_DIR}/telegram_router.log"
        msg_count = 0
        with open(tg_log) as f:
            for line in f.readlines()[-200:]:
                if "skill_response" in line or "llm_response" in line:
                    msg_count += 1
        ctx["telegram_messages_week"] = msg_count
    except Exception:
        pass

    # 8. Existing proposals (to avoid repeats)
    try:
        with open(PROPOSALS_FILE) as f:
            existing = json.load(f)
        ctx["existing_proposals"] = [p.get("title", "") for p in existing]
    except Exception:
        ctx["existing_proposals"] = []

    return ctx


def _format_ollama_prompt(ctx: dict) -> str:
    """Build a specific, data-rich prompt that forces actionable output."""
    sections = []

    # Header with clear role
    sections.append(
        "You are the self-improvement engine of Tenazas, a personal AI assistant "
        "running in Docker (NAS Synology DS923+, GPU RTX 3090 on HomeLab).\n"
        "Analyze the operational data from the last week and propose 1-3 "
        "SPECIFIC and ACTIONABLE improvements. DO NOT repeat things that already exist.\n"
        "IMPORTANT: Respond entirely in Spanish (Latin American). Do NOT mix languages.\n"
    )

    # Errors
    if ctx.get("errors"):
        sections.append(f"ERRORES ({len(ctx['errors'])}):\n" + "\n".join(ctx["errors"][:10]))
    else:
        sections.append("ERRORES: Ninguno en los últimos 7 días.")

    # Runtime health
    sections.append(
        f"RUNTIME: {ctx.get('runtime_runs', '?')} ejecuciones, "
        f"{ctx.get('runtime_failures', '?')} failures, "
        f"{ctx.get('restarts', '?')} restarts, "
        f"{ctx.get('vpn_disconnects', '?')} VPN disconnects, "
        f"{ctx.get('freezes', '?')} freezes, "
        f"health_score={ctx.get('health_score', '?')}"
    )

    # Router stats
    r = ctx.get("router", {})
    if r:
        roles = r.get("role_distribution", {})
        role_str = ", ".join(f"{k}={v}" for k, v in roles.items())
        sections.append(
            f"ROUTER: {r.get('v2_calls', 0)} calls V2, "
            f"{r.get('v1_fallbacks', 0)} fallbacks a V1, "
            f"{r.get('cloud_fallbacks', 0)} cloud fallbacks, "
            f"{r.get('vram_swaps', 0)} VRAM swaps\n"
            f"  Roles: {role_str}\n"
            f"  Router accuracy: {ctx.get('router_accuracy', '?')}"
        )

    # ROI
    roi = ctx.get("roi")
    if roi:
        sections.append(
            f"ROI: {roi['skill_executions']} skills ejecutadas, "
            f"{roi['llm_chats']} chats LLM, "
            f"automation={roi['automation_rate']}%, "
            f"horas ahorradas={roi['hours_saved']}, "
            f"errores evitados={roi['errors_avoided']}"
        )

    # Docker
    sections.append(
        f"DOCKER: {ctx.get('containers_up', '?')} containers up, "
        f"caídos: {', '.join(ctx.get('containers_down', [])) or 'Ninguno'}"
    )

    # Dreaming insights
    dreams = ctx.get("dream_insights", [])
    if dreams:
        sections.append("PATRONES DETECTADOS (dreaming):\n" + "\n".join(dreams))

    # Skills
    sections.append(
        f"SKILLS: {ctx.get('total_skills', '?')} totales"
        + (f", sin doc: {', '.join(ctx['skills_no_doc'])}" if ctx.get("skills_no_doc") else "")
    )

    # Telegram
    if ctx.get("telegram_messages_week"):
        sections.append(f"TELEGRAM: ~{ctx['telegram_messages_week']} interacciones esta semana")

    # Anti-repeat
    existing = ctx.get("existing_proposals", [])
    if existing:
        sections.append("PROPUESTAS YA EXISTENTES (NO repetir):\n- " + "\n- ".join(existing))

    # Architecture context (prevent hallucinated files)
    sections.append(
        "ARQUITECTURA REAL (solo estos archivos existen):\n"
        "- runtime.py — loop principal cada 5 min (event-driven)\n"
        "- model_router.py — routing V2 por roles (fast/reason/heavy/code/vision)\n"
        "- orchestrator.py — clasificación de queries + skill execution\n"
        "- telegram_router.py — bot Telegram (polling, skill interception)\n"
        "- health_sweep.py — 25 probes cada 5 min\n"
        "- autofix_agent.py — auto-reparación 3 tiers\n"
        "- docker-compose.yml — 20 containers\n"
        "- startup.sh — arranque del container openclaw\n"
        "- VPN: gluetun container (NordVPN), NO openvpn.service\n"
        "- dreaming.py, roi_tracker.py, finance.py, expenses.py, jobs.py"
    )

    # Rules
    sections.append(
        "\nREGLAS ESTRICTAS:\n"
        "- Proponé mejoras que NO estén ya implementadas (el sistema ya tiene: "
        "monitoreo cada 5 min, health_sweep, Prometheus+Grafana, HyperBackup diario, "
        "autofix agent, guardrails de routing, dreaming, ROI tracking)\n"
        "- Referenciá SOLO archivos que existen en la lista de arriba\n"
        "- NO inventes archivos como 'start_containers.sh' o 'manage_vpn.sh'\n"
        "- Each proposal: 1 title line + which file + what concrete change\n"
        "- DO NOT include code blocks — only describe the change\n"
        "- Maximum 200 words total\n"
        "- Respond ENTIRELY in Spanish (Latin American). Never switch to Chinese or English mid-response."
    )

    return "\n\n".join(sections)


# Build context and prompt
_ollama_ctx = _build_ollama_context()
_ollama_prompt = _format_ollama_prompt(_ollama_ctx)

# Keep legacy vars for backward compat (Telegram message, logging)
errors = _ollama_ctx.get("errors", ["Sin errores en los ultimos 7 dias."])
if not errors:
    errors = ["Sin errores en los ultimos 7 dias."]
total = _ollama_ctx.get("containers_up", "?")

# 5. Call Ollama (qwen2.5:7b for quality analysis)
payload = json.dumps({"model": "qwen2.5:7b", "prompt": _ollama_prompt, "stream": False}).encode()
try:
    req = Request("http://192.168.2.6:11434/api/generate",
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
if _resolve_openrouter_key():
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

# 8. Send to Telegram — summary + per-proposal approval buttons
try:
    with open("/volume1/docker/openclaw/state/openclaw.json") as f:
        config = json.load(f)
    bot_token = config["channels"]["telegram"]["botToken"]

    # 8a. Send Ollama summary (plain text, no buttons)
    msg = f"🧬 Self Evolution — {now} ART\n━━━━━━━━━━━━━\n{analysis}\n━━━━━━━━━━━━━"
    tg_payload = json.dumps({"chat_id": "1565794551", "text": msg}).encode()
    tg_req = Request(f"https://api.telegram.org/bot{bot_token}/sendMessage",
                     data=tg_payload,
                     headers={"Content-Type": "application/json"})
    urlopen(tg_req, timeout=10)

    # 8b. Send each Kimi proposal with inline approval buttons
    if kimi_proposals:
        sys.path.insert(0, RUNTIME_DIR)
        from exec_approvals import send_approval_request

        for p in kimi_proposals:
            prio = {"high": "🔴", "medium": "🟡", "low": "🟢"}.get(p.get("priority", ""), "⚪")
            title = p.get("title", "Sin título")
            desc = p.get("description", "")
            risk = p.get("risk", "")
            files = ", ".join(p.get("files_affected", []))
            details = f"{desc}\n\nRisk: {risk}\nFiles: {files}" if risk else desc

            try:
                aid = send_approval_request(
                    action_summary=f"{prio} {title}",
                    action_details=details,
                    skill_name="self-evolve",
                    token=bot_token,
                )
                print(f"  Approval sent: {aid} — {title}")
            except Exception as e:
                print(f"  Approval button error for '{title}': {e}")
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

# 11. Auto-training semanal — solo si pasaron 6+ dias desde el ultimo
try:
    _training_log = Path("/volume1/docker/openclaw/state/training_log.json")
    _should_train = True
    if _training_log.exists():
        _entries = json.loads(_training_log.read_text())
        if _entries:
            _last_ts = datetime.fromisoformat(_entries[-1]["timestamp"])
            _days_since = (datetime.now() - _last_ts).days
            _should_train = _days_since >= 6
            if not _should_train:
                print(f"[self_evolve] Training skipped — last was {_days_since} days ago")

    if _should_train:
        print("[self_evolve] Launching weekly training pipeline...")
        subprocess.Popen([
            "sudo", "/usr/local/bin/docker", "exec", "openclaw", "python3",
            "/home/tenazas/.openclaw/workspace/skills/model-trainer/trainer_orchestrator.py",
            "--action", "full_pipeline",
        ])
        print("[self_evolve] Training pipeline launched in background")
except Exception as _e:
    print(f"[self_evolve] Training auto skip: {_e}")
