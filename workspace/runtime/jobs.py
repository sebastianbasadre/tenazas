#!/usr/bin/env python3
"""
Jobs — Job search tracker for Tenazas.

Manages job opportunities, application pipeline, JD analysis,
match scoring, and interview preparation.

Persistence: runtime/jobs/opportunities.json
Logs: runtime/logs/jobs.log

Security:
  - No auto-applying to jobs
  - No sending emails or messages
  - No CV modification
  - No scraping
  - Read/write only within runtime/jobs/

Usage:
  python3 /home/tenazas/runtime/jobs.py --add "postulé a TIVIT para TAM"
  python3 /home/tenazas/runtime/jobs.py --pipeline
  python3 /home/tenazas/runtime/jobs.py --update ID screening
  python3 /home/tenazas/runtime/jobs.py --analyze "job description text..."
  python3 /home/tenazas/runtime/jobs.py --match ID
  python3 /home/tenazas/runtime/jobs.py --brief ID
"""

import json
import re
import uuid
from datetime import datetime
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

# ── Config ──────────────────────────────────────────────────────

BASE_DIR = Path(__file__).parent
JOBS_DIR = BASE_DIR / "jobs"
OPPORTUNITIES_FILE = JOBS_DIR / "opportunities.json"
PROFILE_FILE = JOBS_DIR / "profile.json"
LOG_FILE = BASE_DIR / "logs" / "jobs.log"

OLLAMA_URL = "http://192.168.2.6:11434"
ANALYSIS_MODEL = "deepseek-r1:14b"
FAST_MODEL = "qwen2.5:7b"

# ── Status Machine ──────────────────────────────────────────────

STATUSES = (
    "detected", "interested", "applied", "screening",
    "interview", "offer", "rejected", "closed",
)

# Valid forward transitions (plus rejected/closed from any active state)
TRANSITIONS = {
    "detected":    {"interested", "applied", "rejected", "closed"},
    "interested":  {"applied", "rejected", "closed"},
    "applied":     {"screening", "interview", "rejected", "closed"},
    "screening":   {"interview", "rejected", "closed"},
    "interview":   {"offer", "rejected", "closed"},
    "offer":       {"closed", "rejected"},
    "rejected":    set(),     # terminal
    "closed":      set(),     # terminal
}

ACTIVE_STATUSES = {"detected", "interested", "applied", "screening", "interview", "offer"}

# ── Seniority levels ────────────────────────────────────────────

SENIORITY_LEVELS = {"junior", "semi-senior", "senior", "lead", "manager", "director", "c-level"}

SENIORITY_KEYWORDS = {
    "junior": ["junior", "jr", "trainee", "entry"],
    "semi-senior": ["semi-senior", "semi senior", "ssr", "mid"],
    "senior": ["senior", "sr", "experienced"],
    "lead": ["lead", "tech lead", "team lead", "lider"],
    "manager": ["manager", "gerente", "jefe"],
    "director": ["director", "head of", "vp"],
    "c-level": ["cto", "ceo", "cfo", "coo", "c-level"],
}

# ── Sources ─────────────────────────────────────────────────────

KNOWN_SOURCES = {"linkedin", "referido", "indeed", "glassdoor", "computrabajo",
                 "web", "recruiter", "telegram", "email", "otro"}


def _now():
    return datetime.now().astimezone()


def _log(entry: dict):
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    entry["ts"] = _now().isoformat()
    with open(LOG_FILE, "a") as f:
        f.write(json.dumps(entry, default=str) + "\n")


# ── Persistence ─────────────────────────────────────────────────


def _load_opportunities() -> list:
    try:
        with open(OPPORTUNITIES_FILE) as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def _save_opportunities(opps: list):
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    with open(OPPORTUNITIES_FILE, "w") as f:
        json.dump(opps, f, indent=2, ensure_ascii=False, default=str)


def load_profile() -> dict:
    """Load user profile for match scoring."""
    try:
        with open(PROFILE_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_profile(profile: dict):
    """Save user profile for match scoring."""
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    with open(PROFILE_FILE, "w") as f:
        json.dump(profile, f, indent=2, ensure_ascii=False)


# ── NLP Parsing ─────────────────────────────────────────────────

_ADD_PATTERNS = [
    # "postulé a TIVIT para TAM"
    re.compile(r"postul[eé]\s+(?:a|en)\s+(.+?)\s+(?:para|como)\s+(.+)", re.I),
    # "apliqué a Google como SRE Senior"
    re.compile(r"apliqu[eé]\s+(?:a|en)\s+(.+?)\s+(?:para|como)\s+(.+)", re.I),
    # "nueva oportunidad: MercadoLibre - Backend Dev"
    re.compile(r"(?:nueva\s+)?oportunidad:?\s+(.+?)\s*[-–]\s*(.+)", re.I),
    # "vi un puesto de TAM en TIVIT"
    re.compile(r"vi\s+(?:un\s+)?puesto\s+(?:de\s+)?(.+?)\s+en\s+(.+)", re.I),
    # "me contactó recruiter de Globant para Java Senior"
    re.compile(r"me\s+contact[oó]\s+(?:recruiter|reclutador)\s+de\s+(.+?)\s+para\s+(.+)", re.I),
]

_SALARY_PATTERN = re.compile(
    r"(?:salario|sueldo|comp(?:ensacion)?|usd|ars|\$)\s*:?\s*"
    r"(\$?\s*[0-9.,]+(?:\s*[-–a]\s*\$?\s*[0-9.,]+)?(?:\s*(?:usd|ars|k))?)",
    re.I,
)

_SOURCE_PATTERN = re.compile(
    r"(?:fuente|via|por|desde):?\s*(\w+)", re.I,
)


def _detect_seniority(text: str) -> str | None:
    """Detect seniority level from text."""
    text_lower = text.lower()
    for level, keywords in SENIORITY_KEYWORDS.items():
        for kw in keywords:
            if kw in text_lower:
                return level
    return None


def _detect_source(text: str) -> str | None:
    """Detect opportunity source from text."""
    m = _SOURCE_PATTERN.search(text)
    if m:
        src = m.group(1).lower()
        for known in KNOWN_SOURCES:
            if known in src or src in known:
                return known
    # Try direct keyword match
    text_lower = text.lower()
    for src in KNOWN_SOURCES:
        if src in text_lower:
            return src
    return None


def _detect_salary(text: str) -> str | None:
    """Extract salary info from text."""
    m = _SALARY_PATTERN.search(text)
    return m.group(1).strip() if m else None


def parse_opportunity(text: str) -> dict | None:
    """Parse a job opportunity from natural language.

    Returns:
        dict with: company, role, seniority, source, salary, notes
        or None if unparseable.
    """
    text = text.strip()
    company = None
    role = None

    for pattern in _ADD_PATTERNS:
        m = pattern.search(text)
        if m:
            groups = m.groups()
            if pattern == _ADD_PATTERNS[3]:  # "vi puesto de X en Y"
                role = groups[0].strip()
                company = groups[1].strip()
            elif pattern == _ADD_PATTERNS[4]:  # "me contactó recruiter de X para Y"
                company = groups[0].strip()
                role = groups[1].strip()
            else:
                company = groups[0].strip()
                role = groups[1].strip() if len(groups) > 1 else None
            break

    if not company and not role:
        return None

    return {
        "company": company or "Desconocida",
        "role": role or "Sin especificar",
        "seniority": _detect_seniority(text),
        "source": _detect_source(text),
        "salary": _detect_salary(text),
        "notes": "",
    }


# ── Opportunity CRUD ────────────────────────────────────────────


def add_opportunity(
    company: str,
    role: str,
    seniority: str | None = None,
    source: str | None = None,
    salary: str | None = None,
    notes: str = "",
    status: str = "detected",
    job_description: str = "",
) -> dict:
    """Add a job opportunity to the tracker.

    Args:
        company: company name
        role: job title / position
        seniority: level (junior/senior/lead/etc.)
        source: where found (linkedin/referido/etc.)
        salary: salary info as text
        notes: free-form notes
        status: initial status (default: detected)
        job_description: full JD text if available

    Returns:
        The saved opportunity dict.
    """
    if status not in STATUSES:
        raise ValueError(f"Invalid status: {status}")

    opp = {
        "id": uuid.uuid4().hex[:8],
        "company": company.strip(),
        "role": role.strip(),
        "seniority": seniority if seniority in SENIORITY_LEVELS else None,
        "source": source if source in KNOWN_SOURCES else None,
        "salary": salary,
        "notes": notes,
        "status": status,
        "job_description": job_description,
        "created_at": _now().isoformat(),
        "updated_at": _now().isoformat(),
        "history": [{"status": status, "date": _now().isoformat()}],
    }

    opps = _load_opportunities()
    opps.append(opp)
    _save_opportunities(opps)

    _log({"action": "add_opportunity", "id": opp["id"],
          "company": company, "role": role, "status": status})

    return opp


def add_from_text(text: str) -> dict | None:
    """Parse and add an opportunity from natural language.

    Returns:
        Saved opportunity dict, or None if unparseable.
    """
    parsed = parse_opportunity(text)
    if not parsed:
        return None

    # If text says "postulé", start as "applied"
    status = "detected"
    if re.search(r"postul[eé]|apliqu[eé]", text, re.I):
        status = "applied"
    elif re.search(r"me\s+contact", text, re.I):
        status = "screening"

    return add_opportunity(
        company=parsed["company"],
        role=parsed["role"],
        seniority=parsed["seniority"],
        source=parsed["source"],
        salary=parsed["salary"],
        status=status,
    )


def get_opportunity(opp_id: str) -> dict | None:
    """Get a single opportunity by ID."""
    for opp in _load_opportunities():
        if opp.get("id") == opp_id:
            return opp
    return None


def update_status(opp_id: str, new_status: str, notes: str = "") -> bool:
    """Transition an opportunity to a new status.

    Validates the transition against TRANSITIONS map.

    Returns:
        True if transition succeeded, False otherwise.
    """
    if new_status not in STATUSES:
        _log({"action": "update_status", "id": opp_id,
              "error": f"invalid status: {new_status}"})
        return False

    opps = _load_opportunities()
    for opp in opps:
        if opp.get("id") != opp_id:
            continue

        current = opp.get("status", "detected")
        allowed = TRANSITIONS.get(current, set())

        if new_status not in allowed:
            _log({"action": "update_status", "id": opp_id,
                  "error": f"invalid transition: {current} -> {new_status}"})
            return False

        opp["status"] = new_status
        opp["updated_at"] = _now().isoformat()
        if notes:
            opp["notes"] = (opp.get("notes", "") + f"\n[{new_status}] {notes}").strip()
        opp.setdefault("history", []).append({
            "status": new_status,
            "date": _now().isoformat(),
            "notes": notes,
        })

        _save_opportunities(opps)
        _log({"action": "update_status", "id": opp_id,
              "from": current, "to": new_status})
        return True

    _log({"action": "update_status", "id": opp_id, "error": "not found"})
    return False


def update_field(opp_id: str, field: str, value) -> bool:
    """Update a single field on an opportunity.

    Allowed fields: notes, salary, seniority, source, job_description.
    """
    allowed_fields = {"notes", "salary", "seniority", "source", "job_description"}
    if field not in allowed_fields:
        return False

    opps = _load_opportunities()
    for opp in opps:
        if opp.get("id") == opp_id:
            opp[field] = value
            opp["updated_at"] = _now().isoformat()
            _save_opportunities(opps)
            _log({"action": "update_field", "id": opp_id, "field": field})
            return True
    return False


def list_opportunities(status: str | None = None, active_only: bool = False) -> list:
    """List opportunities, optionally filtered.

    Args:
        status: filter by specific status
        active_only: only non-terminal statuses

    Returns:
        List of opportunity dicts, newest first.
    """
    opps = _load_opportunities()
    if status:
        opps = [o for o in opps if o.get("status") == status]
    elif active_only:
        opps = [o for o in opps if o.get("status") in ACTIVE_STATUSES]
    opps.sort(key=lambda o: o.get("updated_at", ""), reverse=True)
    return opps


# ── Pipeline Summary ────────────────────────────────────────────


def pipeline_summary() -> dict:
    """Get a summary of the job pipeline by status.

    Returns:
        dict with: total, active, by_status, recent (last 5 updated)
    """
    opps = _load_opportunities()
    by_status = {}
    for s in STATUSES:
        count = sum(1 for o in opps if o.get("status") == s)
        if count:
            by_status[s] = count

    active = [o for o in opps if o.get("status") in ACTIVE_STATUSES]
    active.sort(key=lambda o: o.get("updated_at", ""), reverse=True)

    return {
        "total": len(opps),
        "active": len(active),
        "by_status": by_status,
        "recent": active[:5],
    }


def format_pipeline(ps: dict) -> str:
    """Format pipeline summary for Telegram."""
    lines = [
        f"Pipeline laboral: {ps['total']} oportunidades ({ps['active']} activas)",
        "",
    ]

    if ps["by_status"]:
        for status in STATUSES:
            count = ps["by_status"].get(status, 0)
            if count:
                icon = {
                    "detected": "🔍", "interested": "👀", "applied": "📨",
                    "screening": "📞", "interview": "🎯", "offer": "🎉",
                    "rejected": "❌", "closed": "🔒",
                }.get(status, "·")
                lines.append(f"  {icon} {status}: {count}")

    if ps["recent"]:
        lines.extend(["", "Recientes:"])
        for o in ps["recent"]:
            lines.append(f"  [{o['id']}] {o['company']} — {o['role']} ({o['status']})")

    return "\n".join(lines)


# ── LLM-Assisted Analysis ──────────────────────────────────────


def _call_ollama(model: str, prompt: str, timeout: int = 120) -> str:
    """Call Ollama generate API. Returns response text or empty string."""
    payload = json.dumps({
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.3, "num_predict": 2048},
    }).encode()

    req = Request(
        f"{OLLAMA_URL}/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
    )

    try:
        resp = urlopen(req, timeout=timeout)
        data = json.loads(resp.read())
        return data.get("response", "")
    except (URLError, json.JSONDecodeError, OSError) as e:
        _log({"action": "ollama_call", "model": model, "error": str(e)})
        return ""


def analyze_description(job_description: str) -> dict:
    """Analyze a job description using deepseek-r1:14b.

    Extracts: requirements, nice_to_have, red_flags, company_culture hints.

    Args:
        job_description: full JD text

    Returns:
        dict with structured analysis, or {"raw": text} if parsing fails.
    """
    if not job_description.strip():
        return {"error": "empty job description"}

    prompt = f"""Analiza esta descripcion de puesto laboral y extrae informacion estructurada.

JOB DESCRIPTION:
{job_description[:3000]}

Responde en JSON con esta estructura:
{{
  "title": "titulo del puesto",
  "seniority": "junior/semi-senior/senior/lead/manager",
  "requirements": ["requisito 1", "requisito 2"],
  "nice_to_have": ["deseable 1", "deseable 2"],
  "technologies": ["tech 1", "tech 2"],
  "red_flags": ["señal negativa si hay"],
  "salary_mentioned": true/false,
  "remote": "remote/hybrid/onsite/unknown",
  "summary": "resumen en 2-3 oraciones"
}}

Responde SOLO el JSON."""

    _log({"action": "analyze_jd", "model": ANALYSIS_MODEL, "jd_len": len(job_description)})
    response = _call_ollama(ANALYSIS_MODEL, prompt, timeout=180)

    if not response:
        return {"error": "no LLM response"}

    # Try to parse JSON from response
    try:
        # Strip markdown code blocks if present
        clean = re.sub(r"```(?:json)?\s*\n?", "", response).strip().rstrip("`")
        result = json.loads(clean)
        if isinstance(result, dict):
            return result
    except json.JSONDecodeError:
        pass

    # Try finding JSON in response
    match = re.search(r"\{[\s\S]*\}", response)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass

    return {"raw": response, "parse_failed": True}


def match_score(opp_id: str, profile: dict | None = None) -> dict:
    """Evaluate match between an opportunity and user profile.

    Uses qwen2.5:7b for fast evaluation.

    Args:
        opp_id: opportunity ID
        profile: user profile dict (or loads from profile.json)

    Returns:
        dict with: score (0-100), strengths, gaps, recommendation
    """
    opp = get_opportunity(opp_id)
    if not opp:
        return {"error": "opportunity not found"}

    if profile is None:
        profile = load_profile()
    if not profile:
        return {"error": "no profile configured", "hint": "save profile.json first"}

    jd = opp.get("job_description", "")
    role_info = f"{opp['company']} — {opp['role']}"
    if opp.get("seniority"):
        role_info += f" ({opp['seniority']})"

    prompt = f"""Evalua el match entre este candidato y esta oportunidad laboral.

CANDIDATO:
{json.dumps(profile, ensure_ascii=False, indent=2)[:1500]}

OPORTUNIDAD: {role_info}
{jd[:2000] if jd else "Sin job description detallada."}

Responde en JSON:
{{
  "score": 0-100,
  "strengths": ["fortaleza 1", "fortaleza 2"],
  "gaps": ["brecha 1", "brecha 2"],
  "recommendation": "aplicar/considerar/no aplica"
}}

Responde SOLO el JSON."""

    _log({"action": "match_score", "id": opp_id, "model": FAST_MODEL})
    response = _call_ollama(FAST_MODEL, prompt)

    if not response:
        return {"error": "no LLM response"}

    try:
        clean = re.sub(r"```(?:json)?\s*\n?", "", response).strip().rstrip("`")
        result = json.loads(clean)
        if isinstance(result, dict):
            result["opportunity"] = role_info
            return result
    except json.JSONDecodeError:
        pass

    match = re.search(r"\{[\s\S]*\}", response)
    if match:
        try:
            result = json.loads(match.group())
            result["opportunity"] = role_info
            return result
        except json.JSONDecodeError:
            pass

    return {"raw": response, "parse_failed": True}


def interview_brief(opp_id: str, profile: dict | None = None) -> str:
    """Generate an interview preparation brief.

    Args:
        opp_id: opportunity ID
        profile: user profile (or loads from file)

    Returns:
        Markdown-formatted interview prep text.
    """
    opp = get_opportunity(opp_id)
    if not opp:
        return "Oportunidad no encontrada."

    if profile is None:
        profile = load_profile()

    role_info = f"{opp['company']} — {opp['role']}"
    jd = opp.get("job_description", "")
    notes = opp.get("notes", "")
    history = opp.get("history", [])

    context = f"Puesto: {role_info}\n"
    if opp.get("seniority"):
        context += f"Seniority: {opp['seniority']}\n"
    if jd:
        context += f"\nJob Description:\n{jd[:2000]}\n"
    if notes:
        context += f"\nNotas previas:\n{notes}\n"
    if profile:
        context += f"\nPerfil del candidato:\n{json.dumps(profile, ensure_ascii=False, indent=2)[:1000]}\n"

    prompt = f"""Prepara un brief de entrevista para este candidato.

{context}

Genera un documento con:
1. Resumen de la empresa y el puesto
2. Puntos clave a destacar del candidato
3. Preguntas probables y como responderlas
4. Preguntas para hacer al entrevistador
5. Posibles red flags o puntos debiles a preparar

Responde en formato markdown legible."""

    _log({"action": "interview_brief", "id": opp_id, "model": FAST_MODEL})
    response = _call_ollama(FAST_MODEL, prompt, timeout=180)

    if not response:
        return f"No pude generar brief para {role_info}. HomeLab puede estar offline."

    return response


# ── CLI ─────────────────────────────────────────────────────────


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Tenazas Job Search Tracker")
    parser.add_argument("--add", type=str, help="Add opportunity from text")
    parser.add_argument("--update", nargs=2, metavar=("ID", "STATUS"),
                        help="Update opportunity status")
    parser.add_argument("--pipeline", action="store_true", help="Pipeline summary")
    parser.add_argument("--list", action="store_true", help="List active opportunities")
    parser.add_argument("--all", action="store_true", help="List all (including closed)")
    parser.add_argument("--analyze", type=str, help="Analyze job description text")
    parser.add_argument("--match", type=str, metavar="ID", help="Match score for opportunity")
    parser.add_argument("--brief", type=str, metavar="ID", help="Interview brief")
    parser.add_argument("--note", nargs=2, metavar=("ID", "NOTE"), help="Add note")
    parser.add_argument("--jd", nargs=2, metavar=("ID", "TEXT"), help="Set job description")
    parser.add_argument("--json", action="store_true", help="Output as JSON")

    args = parser.parse_args()

    if args.add:
        result = add_from_text(args.add)
        if result:
            if args.json:
                print(json.dumps(result, ensure_ascii=False, default=str))
            else:
                print(f"[{result['id']}] {result['company']} — {result['role']} "
                      f"({result['status']})")
        else:
            print("No pude parsear. Ejemplos:")
            print('  --add "postulé a TIVIT para TAM"')
            print('  --add "nueva oportunidad: Globant - SRE Senior"')

    elif args.update:
        opp_id, new_status = args.update
        if update_status(opp_id, new_status):
            print(f"[{opp_id}] -> {new_status}")
        else:
            print(f"No se pudo actualizar. Verificar ID y transicion valida.")

    elif args.pipeline:
        ps = pipeline_summary()
        if args.json:
            print(json.dumps(ps, ensure_ascii=False, default=str))
        else:
            print(format_pipeline(ps))

    elif args.list or args.all:
        opps = list_opportunities(active_only=not args.all)
        if args.json:
            print(json.dumps(opps, ensure_ascii=False, default=str))
        else:
            if not opps:
                print("Sin oportunidades registradas.")
            else:
                for o in opps:
                    sal = f" | {o['salary']}" if o.get("salary") else ""
                    print(f"[{o['id']}] {o['company']} — {o['role']} "
                          f"({o['status']}){sal}")

    elif args.analyze:
        result = analyze_description(args.analyze)
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))

    elif args.match:
        result = match_score(args.match)
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))

    elif args.brief:
        print(interview_brief(args.brief))

    elif args.note:
        opp_id, note = args.note
        if update_field(opp_id, "notes", note):
            print(f"Nota actualizada para [{opp_id}]")
        else:
            print("Oportunidad no encontrada.")

    elif args.jd:
        opp_id, jd_text = args.jd
        if update_field(opp_id, "job_description", jd_text):
            print(f"JD guardada para [{opp_id}]")
        else:
            print("Oportunidad no encontrada.")

    else:
        parser.print_help()
