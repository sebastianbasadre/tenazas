#!/usr/bin/env python3
"""Job search platform: discovery, matching and application tracking."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlencode, unquote, urlsplit, urlunsplit
from urllib.request import Request, urlopen
from xml.etree import ElementTree

from agent_protocol import Observation, Recommendation
from runtime_utils import load_json, locked_write_json, now, save_json
from runtime_config import OLLAMA_GPU_URL

BASE_DIR = Path(__file__).parent
STATE_DIR = (
    Path("/home/tenazas/.openclaw/state")
    if Path("/home/tenazas").exists()
    else BASE_DIR.parent / "state"
)
AGENT_DIR = STATE_DIR / "agents"
STATUS_FILE = AGENT_DIR / "job_search_status.json"
LOG_FILE = BASE_DIR / "logs" / "agent_job_search.log"
OPPS_FILE = BASE_DIR / "jobs" / "opportunities.json"
PROFILE_FILE = BASE_DIR / "user_profile.json"
MASTER_CV_FILE = BASE_DIR / "jobs" / "master_cv.txt"
CV_TEMPLATE_FILE = BASE_DIR / "jobs" / "cv_template.txt"
CV_TEMPLATE_SOURCE = BASE_DIR / "jobs" / "cv_template.pdf"
TAILORED_CV_DIR = BASE_DIR / "jobs" / "tailored"
CV_MATCH_MODEL = "qwen3-fast:8b"
MIN_CV_MATCH = 65
MAX_POST_AGE_DAYS = 21
_MATCH_TERMS = (
    "product management", "product manager", "technical account manager", "devops",
    "product discovery", "product strategy", "roadmap", "backlog", "okr", "kpi",
    "agile", "scrum", "kanban", "stakeholder", "saas", "b2b", "b2c", "cloud",
    "aws", "azure", "gcp", "docker", "kubernetes", "terraform", "linux", "ci/cd",
    "python", "sql", "api", "microservices", "observability", "monitoring",
    "prometheus", "grafana", "jira", "confluence", "analytics", "data",
    "leadership", "liderazgo", "inglés", "english", "español", "spanish",
)

STATUSES = (
    "detected",
    "interested",
    "applied",
    "screening",
    "interview",
    "offer",
    "rejected",
    "withdrawn",
    "archived",
)
ACTIVE = set(STATUSES) - {"rejected", "withdrawn", "archived"}
STATUS_RANK = {
    "applied": 6,
    "interview": 5,
    "offer": 4,
    "interested": 3,
    "detected": 2,
    "rejected": 1,
    "withdrawn": 0,
    "archived": 0,
}
OWNERSHIP = {
    "job",
    "jobs",
    "trabajo",
    "laburo",
    "empleo",
    "vacante",
    "application",
    "postulacion",
    "postulación",
    "interview",
    "entrevista",
    "recruiter",
    "cv",
}
PREFERRED_ROLE = "fast"

_SEARCH_INTENTS = ("buscar", "buscame", "buscarme", "encontrar", "nuevas vacantes", "oportunidades")
_WORD_RE = re.compile(r"[a-záéíóúüñ0-9+#.]+", re.IGNORECASE)
_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
_AGGREGATE_TITLE_RE = re.compile(
    r"^(?:\+?\d+\s+)?(?:empleos?|ofertas?\s+de\s+trabajo|se necesita urgente)\b",
    re.IGNORECASE,
)
_ROLE_ALIASES = {
    "tam": ("technical account manager",),
    "product owner": ("digital product owner", "technical product owner"),
    "delivery manager": (
        "agile delivery manager",
        "technical delivery manager",
        "service delivery manager",
        "program delivery manager",
    ),
}


def _log(entry: dict) -> None:
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    entry["ts"] = now().isoformat()
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
    except OSError:
        pass


def owns(topic: str) -> bool:
    q = topic.lower()
    return any(k in q for k in OWNERSHIP)


def _tokens(value: str) -> set[str]:
    return {token.lower() for token in _WORD_RE.findall(value or "") if len(token) > 1}


def _clean_url(url: str) -> str:
    if not url:
        return ""
    parts = urlsplit(url.strip())
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/"), "", ""))


def _canonical_job_url(url: str) -> str:
    """Normalize common job URLs so repeated discoveries collapse to one record."""
    cleaned = _clean_url(url)
    if not cleaned:
        return ""
    parts = urlsplit(cleaned)
    path = parts.path
    # LinkedIn job URLs often repeat with only the trailing numeric slug changed.
    if "linkedin.com" in parts.netloc and "/jobs/view/" in path:
        path = re.sub(r"-\d+$", "", path)
    return urlunsplit((parts.scheme, parts.netloc, path, "", ""))


def _position_title(title: str) -> str:
    """Extract the advertised role from search-engine decorated titles."""
    value = title or ""
    marker = re.search(r"\bcargo de\s+(.+?)(?:\s+-\s+LinkedIn|$)", value, re.IGNORECASE)
    return marker.group(1).strip() if marker else value


def _role_variants(role: str) -> list[str]:
    return [role, *_ROLE_ALIASES.get(role.strip().lower(), ())]


def save_master_cv(path: str | Path) -> dict:
    """Extract and persist a master CV received as PDF, DOCX, TXT or Markdown."""
    source = Path(path)
    suffix = source.suffix.lower()
    if suffix == ".pdf":
        from pypdf import PdfReader

        text = "\n".join(page.extract_text() or "" for page in PdfReader(str(source)).pages)
    elif suffix == ".docx":
        with zipfile.ZipFile(source) as archive:
            root = ElementTree.fromstring(archive.read("word/document.xml"))
        text = " ".join(node.text or "" for node in root.iter() if node.tag.endswith("}t"))
    elif suffix in {".txt", ".md"}:
        text = source.read_text(encoding="utf-8", errors="replace")
    else:
        raise ValueError("Formato no soportado. Usá PDF, DOCX, TXT o MD.")
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if len(text) < 200:
        raise ValueError("No pude extraer suficiente texto del CV.")
    MASTER_CV_FILE.parent.mkdir(parents=True, exist_ok=True)
    MASTER_CV_FILE.write_text(text, encoding="utf-8")
    return {"characters": len(text), "file": str(MASTER_CV_FILE)}


def _extract_document_text(path: str | Path) -> str:
    source = Path(path)
    suffix = source.suffix.lower()
    if suffix == ".pdf":
        from pypdf import PdfReader

        return "\n".join(page.extract_text() or "" for page in PdfReader(str(source)).pages)
    if suffix == ".docx":
        with zipfile.ZipFile(source) as archive:
            root = ElementTree.fromstring(archive.read("word/document.xml"))
        return "\n".join(node.text or "" for node in root.iter() if node.tag.endswith("}t"))
    if suffix in {".txt", ".md"}:
        return source.read_text(encoding="utf-8", errors="replace")
    raise ValueError("Formato no soportado. Usá PDF, DOCX, TXT o MD.")


def save_cv_template(path: str | Path) -> dict:
    """Persist a formatting/structure reference separately from the master CV."""
    source = Path(path)
    text = re.sub(r"\n{3,}", "\n\n", _extract_document_text(source)).strip()
    if len(text) < 200:
        raise ValueError("No pude extraer suficiente texto de la plantilla.")
    CV_TEMPLATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CV_TEMPLATE_FILE.write_text(text, encoding="utf-8")
    target = CV_TEMPLATE_SOURCE.with_suffix(source.suffix.lower())
    target.write_bytes(source.read_bytes())
    return {"characters": len(text), "file": str(target)}


def _inspect_job_page(url: str) -> dict:
    req = Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept-Language": "es,en;q=0.8"})
    try:
        raw = urlopen(req, timeout=20).read().decode("utf-8", errors="replace")
    except (URLError, OSError):
        return {"description": "", "accepting": False, "posted_date": "", "reason": "unreachable"}
    closed = bool(re.search(
        r"no longer accepting applications|ya no se aceptan solicitudes|"
        r"ya no se aceptan candidaturas|position (?:has been )?filled|job (?:is )?closed",
        raw,
        re.IGNORECASE,
    ))
    posted_date = ""
    blocks = re.findall(
        r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        raw,
        re.IGNORECASE | re.DOTALL,
    )
    for block in blocks:
        try:
            data = json.loads(block)
        except json.JSONDecodeError:
            continue
        candidates = data if isinstance(data, list) else [data]
        for candidate in candidates:
            if isinstance(candidate, dict) and candidate.get("@type") == "JobPosting":
                posted_date = str(candidate.get("datePosted", ""))[:10]
                description = str(candidate.get("description", ""))
                return {
                    "description": re.sub(r"<[^>]+>", " ", description),
                    "accepting": not closed,
                    "posted_date": posted_date,
                    "reason": "closed" if closed else "",
                }
    top_time = re.search(r'<time[^>]+datetime="(\d{4}-\d{2}-\d{2})"', raw, re.IGNORECASE)
    if top_time:
        posted_date = top_time.group(1)
    linkedin = re.search(
        r'<div[^>]+class="[^"]*show-more-less-html__markup[^"]*"[^>]*>(.*?)</div>',
        raw,
        re.IGNORECASE | re.DOTALL,
    )
    if linkedin:
        description = re.sub(
            r"\s+",
            " ",
            html.unescape(re.sub(r"<[^>]+>", " ", linkedin.group(1))),
        ).strip()
    else:
        description = ""
    return {
        "description": description,
        "accepting": not closed,
        "posted_date": posted_date,
        "reason": "closed" if closed else "",
    }


def _fetch_job_description(url: str) -> str:
    return _inspect_job_page(url)["description"]


def _is_recent(posted_date: str, max_age_days: int = MAX_POST_AGE_DAYS) -> bool:
    try:
        posted = date.fromisoformat(posted_date[:10])
    except (TypeError, ValueError):
        return False
    return date.today() - timedelta(days=max_age_days) <= posted <= date.today()


def _call_match_model(prompt: str) -> dict:
    payload = json.dumps(
        {
            "model": CV_MATCH_MODEL,
            "prompt": prompt,
            "stream": False,
            "format": "json",
            "options": {"temperature": 0.1, "num_predict": 350},
        }
    ).encode("utf-8")
    req = Request(
        f"{OLLAMA_GPU_URL}/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        response = json.loads(urlopen(req, timeout=90).read())
        content = response.get("response", "")
        parsed = json.loads(content)
        return parsed if isinstance(parsed, dict) else {"error": "Respuesta JSON inválida"}
    except (URLError, OSError, json.JSONDecodeError) as exc:
        return {"error": f"Fallo del modelo de análisis: {exc}"}


def _compare_cv_to_job(cv: str, title: str, jd: str) -> dict:
    """Fast, auditable CV/JD comparison that never invents experience."""
    cv_lower = cv.lower()
    job_lower = f"{title} {jd}".lower()
    relevant = [term for term in _MATCH_TERMS if term in job_lower]
    matched = [term for term in relevant if term in cv_lower]
    gaps = [term for term in relevant if term not in cv_lower]

    title_tokens = _tokens(_position_title(title))
    cv_tokens = _tokens(cv)
    role_overlap = len(title_tokens & cv_tokens) / max(1, len(title_tokens))
    skill_coverage = len(matched) / max(1, len(relevant))
    score = round(min(100, role_overlap * 35 + skill_coverage * 60 + (5 if "remot" in job_lower else 0)))
    recommendation = "aplicar" if score >= 70 else "considerar" if score >= 45 else "no aplica"
    return {
        "score": score,
        "strengths": matched[:8],
        "gaps": gaps[:8],
        "recommendation": recommendation,
        "summary": (
            f"Coincidencia de rol {role_overlap:.0%}; "
            f"cobertura de requisitos detectados {skill_coverage:.0%}."
        ),
    }


def _ranked_opportunities() -> list[dict]:
    return sorted(
        [item for item in _load_opportunities() if item.get("status") in ACTIVE],
        key=lambda item: item.get("match_score", 0),
        reverse=True,
    )


def _xml_escape(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _write_docx(path: Path, paragraphs: list[tuple[str, str]]) -> None:
    """Write a minimal, ATS-friendly DOCX without external office dependencies."""
    body = []
    for style, text in paragraphs:
        clean = _xml_escape(text.strip())
        if not clean:
            body.append("<w:p/>")
            continue
        properties = ""
        run_properties = ""
        if style == "title":
            properties = '<w:pPr><w:jc w:val="center"/></w:pPr>'
            run_properties = '<w:rPr><w:b/><w:sz w:val="32"/></w:rPr>'
        elif style == "subtitle":
            properties = '<w:pPr><w:jc w:val="center"/></w:pPr>'
            run_properties = '<w:rPr><w:b/><w:sz w:val="22"/></w:rPr>'
        elif style == "contact":
            properties = '<w:pPr><w:jc w:val="center"/></w:pPr>'
            run_properties = '<w:rPr><w:sz w:val="18"/></w:rPr>'
        elif style == "heading":
            run_properties = '<w:rPr><w:b/><w:sz w:val="24"/></w:rPr>'
        elif style == "bullet":
            properties = '<w:pPr><w:ind w:left="360" w:hanging="180"/></w:pPr>'
            clean = f"• {clean}"
        body.append(
            f'<w:p>{properties}<w:r>{run_properties}<w:t xml:space="preserve">'
            f"{clean}</w:t></w:r></w:p>"
        )
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{''.join(body)}<w:sectPr/></w:body></w:document>"
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" '
        'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        "</Types>"
    )
    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" '
        'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" '
        'Target="word/document.xml"/></Relationships>'
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", content_types)
        archive.writestr("_rels/.rels", rels)
        archive.writestr("word/document.xml", document)


def _tailored_paragraphs(cv: str, item: dict, analysis: dict) -> list[tuple[str, str]]:
    lines = [line.strip().replace("\x7f", "•") for line in cv.splitlines() if line.strip()]
    name = lines[0] if lines else "CANDIDATE"
    original_title = lines[1] if len(lines) > 1 else ""
    contact = lines[2] if len(lines) > 2 else ""
    headings = {
        "PROFESSIONAL SUMMARY", "CORE COMPETENCIES", "PROFESSIONAL EXPERIENCE",
        "CERTIFICATIONS", "EDUCATION", "TECHNICAL SKILLS", "LANGUAGES",
    }
    sections: dict[str, list[str]] = {}
    current = ""
    for line in lines[3:]:
        if line in headings:
            current = line
            sections[current] = []
        elif current:
            sections[current].append(line)

    strengths = analysis.get("strengths", [])
    focus = " · ".join(strength.title() for strength in strengths[:4])
    job_text = f"{item.get('title', '')} {item.get('job_description', '')}".lower()

    summary_lines = sections.get("PROFESSIONAL SUMMARY", [])
    summary = " ".join(summary_lines)
    if strengths:
        summary += (
            " Relevant verified capabilities for this role include "
            + ", ".join(strengths[:6])
            + "."
        )

    experience_lines = sections.get("PROFESSIONAL EXPERIENCE", [])
    experience_blocks = []
    date_indexes = [
        index
        for index, line in enumerate(experience_lines)
        if " · " in line and re.search(r"\d{4}", line)
    ]
    for position, date_index in enumerate(date_indexes):
        start = max(0, date_index - 1)
        next_date = date_indexes[position + 1] if position + 1 < len(date_indexes) else len(experience_lines)
        end = max(start + 1, next_date - 1)
        experience_blocks.append(experience_lines[start:end])
    ranked_experience = sorted(
        experience_blocks,
        key=lambda lines: sum(
            1
            for token in _tokens(" ".join(lines))
            if token in _tokens(job_text)
        ),
        reverse=True,
    )

    technical = sections.get("TECHNICAL SKILLS", [])
    competencies = sections.get("CORE COMPETENCIES", [])
    relevant_skills = sorted(
        technical + competencies,
        key=lambda line: sum(
            1 for strength in strengths if strength.lower() in line.lower()
        ),
        reverse=True,
    )
    paragraphs = [
        ("title", name),
        ("subtitle", f"{item.get('title', original_title)}" + (f" · {focus}" if focus else "")),
        ("contact", contact),
        ("heading", "PROFESSIONAL SUMMARY"),
        ("normal", summary),
        ("heading", "PROFESSIONAL EXPERIENCE"),
    ]

    # Keep the most relevant recent history. Every line remains verbatim from
    # the master CV; only selection and ordering change.
    for block in ranked_experience[:6]:
        for line in block:
            style = "bullet" if line.startswith("•") else "normal"
            paragraphs.append((style, line.lstrip("• ")))

    paragraphs.append(("heading", "EDUCATION & CERTIFICATIONS"))
    for line in sections.get("EDUCATION", []) + sections.get("CERTIFICATIONS", []):
        paragraphs.append(("normal", line))

    paragraphs.append(("heading", "SKILLS & TECHNOLOGIES"))
    if strengths:
        paragraphs.append(("normal", f"Role keywords: {', '.join(strengths)}"))
    for line in relevant_skills:
        paragraphs.append(("normal", line))
    for line in sections.get("LANGUAGES", []):
        paragraphs.append(("normal", f"Languages: {line}"))
    return paragraphs


def _prepare_tailored_cv_item(item: dict) -> dict:
    """Revalidate one vacancy and generate a truthful, ATS-safe tailored PDF."""
    if not MASTER_CV_FILE.exists():
        return {"error": "Falta el CV maestro."}
    page = _inspect_job_page(item.get("url", ""))
    if not page["accepting"]:
        return {"error": "La vacante ya no acepta postulaciones."}
    if not _is_recent(page["posted_date"]):
        return {"error": "La vacante ya no cumple el límite de antigüedad."}
    cv = MASTER_CV_FILE.read_text(encoding="utf-8")
    jd = page["description"] or item.get("job_description", "") or item.get("description", "")
    analysis = _compare_cv_to_job(cv, item.get("title", ""), jd)
    if analysis["score"] < MIN_CV_MATCH:
        return {"error": f"El match actualizado es {analysis['score']}%, menor al mínimo de 65%."}

    from tailored_cv_pdf import build_tailored_pdf

    role = re.sub(r"[^A-Za-z0-9]+", "_", item.get("title", "")).strip("_")
    company = re.sub(r"[^A-Za-z0-9]+", "_", item.get("company", "")).strip("_")
    filename = f"Sebastian_Basadre_{role or 'Role'}_{company or 'Company'}.pdf"
    path = TAILORED_CV_DIR / filename
    validation = build_tailored_pdf(
        cv,
        path,
        item.get("title", ""),
        analysis["strengths"],
        TAILORED_CV_DIR / "previews",
    )
    return {
        "path": str(path),
        "filename": filename,
        "title": item.get("title", ""),
        "company": item.get("company", ""),
        "url": item.get("url", ""),
        "score": analysis["score"],
        "strengths": analysis["strengths"],
        "gaps": analysis["gaps"],
        "pages": validation["pages"],
        "preview": validation["preview"],
        "changes": [
            "Usó la plantilla ReportLab ATS de una columna",
            "Adaptó título y resumen al puesto con keywords verificadas",
            "Reordenó competencias y bullets por relevancia",
            "Conservó hechos, métricas, educación y certificaciones del CV maestro",
            f"Validó {validation['pages']} página(s) y renderizó el preview",
        ],
    }


def prepare_tailored_cv(position: int) -> dict:
    """Generate a tailored CV for a vacancy selected by its displayed rank."""
    items = _ranked_opportunities()
    if position < 1 or position > len(items):
        return {"error": f"Vacante inválida. Elegí un número entre 1 y {len(items)}."}
    return _prepare_tailored_cv_item(items[position - 1])


def prepare_tailored_cv_for_id(opportunity_id: str) -> dict:
    """Generate a tailored CV for an exact opportunity ID."""
    item = next(
        (candidate for candidate in _load_opportunities() if candidate.get("id") == opportunity_id),
        None,
    )
    if not item:
        return {"error": "Vacante no encontrada."}
    return _prepare_tailored_cv_item(item)


def analyze_matches(limit: int = 10) -> list[dict]:
    """Visit top job pages and compare their descriptions with the master CV."""
    if not MASTER_CV_FILE.exists():
        return [{"error": "Falta el CV maestro. Subilo por Telegram primero."}]
    cv = MASTER_CV_FILE.read_text(encoding="utf-8")[:7000]
    opportunities = _ranked_opportunities()[:limit]
    output = []
    changed = False
    for item in opportunities:
        page = _inspect_job_page(item.get("url", ""))
        jd = page["description"] or item.get("description", "")
        analysis = _compare_cv_to_job(cv, item.get("title", ""), jd[:12000])
        if not page["accepting"]:
            analysis["recommendation"] = "no aplica"
            analysis["status_reason"] = "closed"
        elif not _is_recent(page["posted_date"]):
            analysis["recommendation"] = "no aplica"
            analysis["status_reason"] = "stale_or_unknown_date"
        item["cv_match"] = analysis
        item["job_description"] = jd[:12000]
        item["posted_date"] = page["posted_date"]
        item["accepting_applications"] = page["accepting"]
        item["reviewed_at"] = now().isoformat()
        output.append({"title": item["title"], "url": item["url"], **analysis})
        changed = True
    if changed:
        all_items = _load_opportunities()
        by_id = {item["id"]: item for item in opportunities}
        locked_write_json(str(OPPS_FILE), [{**item, **by_id.get(item.get("id"), {})} for item in all_items])
    return output


def validate_search_result(item: dict, profile: dict | None = None) -> tuple[bool, str]:
    """Reject spam, result pages and irrelevant documents before persistence."""
    profile = profile or _load_profile()
    title = str(item.get("title", "")).strip()
    raw_url = str(item.get("url", "")).strip()
    if not title or not raw_url:
        return False, "missing_title_or_url"

    decoded_url = unquote(raw_url)
    if _CJK_RE.search(title) or _CJK_RE.search(decoded_url):
        return False, "cjk_spam"
    if _AGGREGATE_TITLE_RE.search(title):
        return False, "aggregate_title"

    parts = urlsplit(raw_url)
    host = parts.netloc.lower()
    path = parts.path.lower()
    if "linkedin.com" in host and "/jobs/view/" not in path:
        return False, "linkedin_aggregate"
    if "indeed." in host and "/viewjob" not in path:
        return False, "indeed_aggregate"
    if "jooble." in host and "/desc/" not in path:
        return False, "jooble_aggregate"

    role_tokens = _tokens(_position_title(title))
    target_roles = [str(role) for role in profile.get("roles_target", [])]
    if target_roles and not any(
        _tokens(variant) <= role_tokens
        for role in target_roles
        for variant in _role_variants(role)
    ):
        return False, "target_role_missing"
    return True, ""


def _load_profile() -> dict:
    profile = load_json(str(PROFILE_FILE), {})
    return profile if isinstance(profile, dict) else {}


def _load_opportunities() -> list[dict]:
    data = load_json(str(OPPS_FILE), [])
    return data if isinstance(data, list) else []


def _opportunity_id(item: dict) -> str:
    identity = _canonical_job_url(item.get("url", ""))
    if not identity:
        identity = "|".join(
            str(item.get(key, "")).strip().lower() for key in ("title", "company", "location")
        )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]


def _opportunity_key(item: dict) -> str:
    url = _canonical_job_url(str(item.get("url", "")))
    if url:
        return url
    title = str(item.get("title", "")).strip().lower()
    company = str(item.get("company", "")).strip().lower()
    location = str(item.get("location", "")).strip().lower()
    return "|".join(part for part in (title, company, location) if part)


def score_opportunity(item: dict, profile: dict | None = None) -> dict:
    """Return a transparent 0-100 compatibility score and its reasons."""
    profile = profile or _load_profile()
    text = " ".join(
        str(item.get(key, "")) for key in ("title", "company", "location", "description", "snippet")
    ).lower()
    text_tokens = _tokens(text)
    title_tokens = _tokens(_position_title(str(item.get("title", ""))))
    roles = [str(role) for role in profile.get("roles_target", [])]
    skills = [str(skill) for skill in profile.get("tools", [])]
    city = str(profile.get("city", "")).lower()
    country = str(profile.get("country", "")).lower()

    score = 0
    reasons = []

    full_role_matches = [
        role
        for role in roles
        if any(_tokens(variant) <= title_tokens for variant in _role_variants(role))
    ]
    partial_role_matches = []
    role_matches = full_role_matches + partial_role_matches
    if full_role_matches:
        score += min(55, 50 + 5 * (len(full_role_matches) - 1))
        reasons.append(f"rol: {', '.join(role_matches[:2])}")
    elif partial_role_matches:
        score += min(40, 25 + 5 * (len(partial_role_matches) - 1))
        reasons.append(f"rol parcial: {', '.join(partial_role_matches[:2])}")

    skill_matches = [skill for skill in skills if _tokens(skill) & text_tokens]
    if skill_matches:
        score += min(30, 6 * len(skill_matches))
        reasons.append(f"skills: {', '.join(skill_matches[:4])}")

    if any(term in text for term in ("remote", "remoto", "híbrido", "hybrid")):
        score += 10
        reasons.append("modalidad flexible")
    elif (city and city in text) or (country and country in text):
        score += 8
        reasons.append("ubicación compatible")

    if item.get("salary"):
        score += 5
        reasons.append("salario informado")

    return {"score": min(score, 100), "reasons": reasons}


def normalize_opportunity(item: dict, profile: dict | None = None) -> dict:
    created_at = item.get("created_at") or now().isoformat()
    normalized = {
        "id": item.get("id", ""),
        "title": str(item.get("title", "")).strip(),
        "company": str(item.get("company", "")).strip(),
        "location": str(item.get("location", "")).strip(),
        "url": _clean_url(str(item.get("url", ""))),
        "description": str(item.get("description") or item.get("snippet") or "").strip(),
        "source": str(item.get("source", "manual")).strip(),
        "salary": item.get("salary"),
        "status": item.get("status", "detected"),
        "created_at": created_at,
        "updated_at": now().isoformat(),
        "notes": item.get("notes", []),
        "job_description": str(item.get("job_description", "")).strip(),
        "posted_date": str(item.get("posted_date", "")),
        "accepting_applications": item.get("accepting_applications"),
        "cv_match": item.get("cv_match"),
    }
    if normalized["status"] not in STATUSES:
        normalized["status"] = "detected"
    normalized["id"] = normalized["id"] or _opportunity_id(normalized)
    match = score_opportunity(normalized, profile)
    cv_score = (normalized.get("cv_match") or {}).get("score")
    normalized["match_score"] = cv_score if isinstance(cv_score, (int, float)) else match["score"]
    normalized["match_reasons"] = (
        ["comparación con CV maestro"] if isinstance(cv_score, (int, float)) else match["reasons"]
    )
    return normalized


def add_opportunities(items: list[dict], profile: dict | None = None) -> dict:
    """Insert or refresh opportunities while preserving user-managed pipeline state."""
    existing = {}
    for item in _load_opportunities():
        existing[item.get("fingerprint") or _opportunity_key(item)] = item
    added = 0
    updated = 0

    for raw in items:
        item = normalize_opportunity(raw, profile)
        item["fingerprint"] = _opportunity_key(item)
        previous = existing.get(item["fingerprint"]) or existing.get(item["id"])
        if previous:
            item["id"] = previous.get("id", item["id"])
            item["status"] = previous.get("status", item["status"])
            item["notes"] = previous.get("notes", item["notes"])
            item["created_at"] = previous.get("created_at", item["created_at"])
            item["updated_at"] = now().isoformat()
            existing[item["fingerprint"]] = {**previous, **item}
            updated += 1
        else:
            existing[item["fingerprint"]] = item
            added += 1

    opportunities = sorted(
        existing.values(),
        key=lambda item: (item.get("status") in ACTIVE, item.get("match_score", 0)),
        reverse=True,
    )
    locked_write_json(str(OPPS_FILE), opportunities)
    _log({"action": "upsert", "added": added, "updated": updated})
    return {"added": added, "updated": updated, "total": len(opportunities)}


def migrate_opportunities() -> int:
    """Collapse duplicate persisted opportunities by canonical job fingerprint."""
    items = _load_opportunities()
    grouped: dict[str, dict] = {}
    for item in items:
        key = item.get("fingerprint") or _opportunity_key(item)
        item["fingerprint"] = key
        current = grouped.get(key)
        if current is None:
            grouped[key] = item
            continue
        current_rank = STATUS_RANK.get(str(current.get("status", "detected")), 0)
        item_rank = STATUS_RANK.get(str(item.get("status", "detected")), 0)
        current_ts = str(current.get("updated_at") or current.get("created_at") or "")
        item_ts = str(item.get("updated_at") or item.get("created_at") or "")
        if (item_rank, item_ts) >= (current_rank, current_ts):
            merged = {**current, **item}
            merged["fingerprint"] = key
            grouped[key] = merged
    deduped = list(grouped.values())
    if len(deduped) != len(items):
        locked_write_json(str(OPPS_FILE), deduped)
    return len(items) - len(deduped)


def update_status(opportunity_id: str, status: str, note: str = "") -> dict:
    if status not in STATUSES:
        raise ValueError(f"Estado inválido: {status}")

    opportunities = _load_opportunities()
    for item in opportunities:
        if item.get("id") != opportunity_id:
            continue
        item["status"] = status
        item["updated_at"] = now().isoformat()
        if note:
            notes = item.setdefault("notes", [])
            notes.append({"ts": now().isoformat(), "text": note})
        locked_write_json(str(OPPS_FILE), opportunities)
        _log({"action": "status", "id": opportunity_id, "status": status})
        return item
    raise KeyError(f"Oportunidad no encontrada: {opportunity_id}")


def _search_queries(profile: dict, query: str = "") -> list[str]:
    roles = profile.get("roles_target", []) or ["tecnología"]
    location = profile.get("country") or profile.get("city") or ""
    queries = []
    after = (date.today() - timedelta(days=MAX_POST_AGE_DAYS)).isoformat()
    for role in roles[:4]:
        variants = _role_variants(str(role))
        for variant in variants:
            base = f'"{variant}" {location} remoto after:{after}'
            queries.extend(
                [
                    f'{base} site:linkedin.com/jobs/view',
                    f'{base} site:boards.greenhouse.io',
                    f'{base} site:jobs.lever.co',
                ]
            )
    return list(dict.fromkeys(queries))


def _linkedin_recent_results(profile: dict, max_results: int = 60) -> list[dict]:
    """Fetch individual LinkedIn postings published within the freshness window."""
    roles = profile.get("roles_target", []) or ["tecnología"]
    location = profile.get("country") or profile.get("city") or "Argentina"
    results = []
    seen = set()
    for role in roles[:4]:
        for variant in _role_variants(str(role)):
            params = urlencode(
                {
                    "keywords": variant,
                    "location": location,
                    "f_TPR": f"r{MAX_POST_AGE_DAYS * 86400}",
                    "start": "0",
                }
            )
            url = f"https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?{params}"
            try:
                raw = urlopen(
                    Request(url, headers={"User-Agent": "Mozilla/5.0", "Accept-Language": "es,en;q=0.8"}),
                    timeout=20,
                ).read().decode("utf-8", errors="replace")
            except (URLError, OSError):
                continue
            cards = re.findall(
                r'<div class="base-card.*?</li>',
                raw,
                re.IGNORECASE | re.DOTALL,
            )
            for card in cards:
                link = re.search(r'class="base-card__full-link[^"]*" href="([^"]+)"', card)
                title = re.search(r'class="base-search-card__title"[^>]*>(.*?)</h3>', card, re.DOTALL)
                company = re.search(r'class="base-search-card__subtitle"[^>]*>(.*?)</h4>', card, re.DOTALL)
                place = re.search(r'class="job-search-card__location"[^>]*>(.*?)</span>', card, re.DOTALL)
                if not link or not title:
                    continue
                clean_link = _clean_url(html.unescape(link.group(1)))
                if clean_link in seen:
                    continue
                seen.add(clean_link)
                results.append(
                    {
                        "title": re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", title.group(1))).strip(),
                        "company": re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", company.group(1))).strip() if company else "",
                        "location": re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", place.group(1))).strip() if place else "",
                        "url": clean_link,
                        "description": "",
                        "source": "linkedin_recent",
                        "status": "detected",
                    }
                )
                if len(results) >= max_results:
                    return results
    return results


def discover(query: str = "", max_results: int = 10, search_fn=None) -> dict:
    """Discover vacancies through the configured metasearch service."""
    if search_fn is None:
        from research_engine import web_search

        search_fn = web_search

    profile = _load_profile()
    migrate_opportunities()
    found = []
    rejected = []
    seen_results = set()
    errors = []

    def consider(candidate: dict) -> None:
        identity = _clean_url(candidate["url"]) or candidate["title"].strip().lower()
        if identity in seen_results or len(found) >= max_results:
            return
        seen_results.add(identity)
        valid, reason = validate_search_result(candidate, profile)
        if not valid:
            rejected.append({"title": candidate["title"], "reason": reason})
            return
        page = _inspect_job_page(candidate["url"])
        if not page["accepting"]:
            rejected.append({"title": candidate["title"], "reason": "closed"})
            return
        if not _is_recent(page["posted_date"]):
            rejected.append({"title": candidate["title"], "reason": "stale_or_unknown_date"})
            return
        candidate["description"] = page["description"] or candidate["description"]
        candidate["job_description"] = page["description"]
        candidate["posted_date"] = page["posted_date"]
        candidate["accepting_applications"] = True
        if MASTER_CV_FILE.exists():
            cv = MASTER_CV_FILE.read_text(encoding="utf-8")[:7000]
            cv_match = _compare_cv_to_job(cv, candidate["title"], candidate["description"])
            candidate["cv_match"] = cv_match
            candidate["match_score"] = cv_match["score"]
            if cv_match["score"] < MIN_CV_MATCH:
                rejected.append({"title": candidate["title"], "reason": "cv_match_below_65"})
                return
        found.append(candidate)

    for candidate in _linkedin_recent_results(profile):
        consider(candidate)
        if len(found) >= max_results:
            break

    search_queries = _search_queries(profile, query)
    per_query = 8
    for search_query in search_queries if len(found) < max_results else []:
        results = search_fn(search_query, num_results=per_query, language="es")
        for result in results:
            if result.get("error"):
                errors.append(result["error"])
                continue
            candidate = {
                "title": result.get("title", ""),
                "url": result.get("url", ""),
                "description": result.get("snippet", ""),
                "source": "searxng",
                "status": "detected",
            }
            consider(candidate)
        if len(found) >= max_results:
            break

    result = add_opportunities(found[:max_results], profile) if found else {
        "added": 0,
        "updated": 0,
        "total": len(_load_opportunities()),
    }
    result["errors"] = errors[:3]
    result["searched"] = len(found)
    result["rejected"] = len(rejected)
    result["rejection_reasons"] = [
        {"reason": reason, "count": sum(1 for item in rejected if item["reason"] == reason)}
        for reason in sorted({item["reason"] for item in rejected})
    ]
    _log({"action": "discover", **result})
    return result


def prune_invalid_discoveries(profile: dict | None = None) -> dict:
    """Remove untouched invalid SearXNG discoveries while preserving user-managed items."""
    profile = profile or _load_profile()
    opportunities = _load_opportunities()
    kept = []
    removed = []
    for item in opportunities:
        eligible = (
            item.get("source") in {"searxng", "linkedin_recent"}
            and item.get("status") == "detected"
            and not item.get("notes")
        )
        valid, reason = validate_search_result(item, profile)
        if eligible and not valid:
            removed.append({"id": item.get("id"), "reason": reason})
        else:
            kept.append(item)
    if removed:
        locked_write_json(str(OPPS_FILE), kept)
    _log({"action": "prune", "removed": len(removed), "kept": len(kept)})
    return {"removed": len(removed), "kept": len(kept)}


def audit_discoveries() -> dict:
    """Revalidate untouched discoveries against freshness, availability and CV threshold."""
    cv = MASTER_CV_FILE.read_text(encoding="utf-8")[:7000] if MASTER_CV_FILE.exists() else ""
    kept = []
    removed = []
    for item in _load_opportunities():
        eligible = (
            item.get("source") in {"searxng", "linkedin_recent"}
            and item.get("status") == "detected"
            and not item.get("notes")
        )
        if not eligible:
            kept.append(item)
            continue
        page = _inspect_job_page(item.get("url", ""))
        reason = ""
        if not page["accepting"]:
            reason = page["reason"] or "closed"
        elif not _is_recent(page["posted_date"]):
            reason = "stale_or_unknown_date"
        else:
            analysis = _compare_cv_to_job(
                cv,
                item.get("title", ""),
                page["description"] or item.get("description", ""),
            ) if cv else item.get("cv_match", {})
            if not isinstance(analysis.get("score"), (int, float)) or analysis["score"] < MIN_CV_MATCH:
                reason = "cv_match_below_65"
            else:
                item["description"] = page["description"] or item.get("description", "")
                item["job_description"] = page["description"]
                item["posted_date"] = page["posted_date"]
                item["accepting_applications"] = True
                item["cv_match"] = analysis
                item["match_score"] = analysis["score"]
                item["match_reasons"] = ["comparación con CV maestro"]
        if reason:
            removed.append({"id": item.get("id"), "reason": reason})
        else:
            kept.append(item)
    locked_write_json(str(OPPS_FILE), kept)
    counts = {
        reason: sum(1 for item in removed if item["reason"] == reason)
        for reason in sorted({item["reason"] for item in removed})
    }
    _log({"action": "audit", "removed": len(removed), "kept": len(kept), "reasons": counts})
    return {"removed": len(removed), "kept": len(kept), "reasons": counts}


def assess() -> dict:
    # Discovery may be invoked by a long-lived process that previously cached an
    # older module version. Keep persisted state clean on every fresh assessment.
    migrate_opportunities()
    prune_invalid_discoveries()
    opportunities = _load_opportunities()
    active = [item for item in opportunities if item.get("status") in ACTIVE]
    status_counts = {status: 0 for status in STATUSES}
    for item in opportunities:
        status = item.get("status", "detected")
        if status in status_counts:
            status_counts[status] += 1

    ranked = sorted(active, key=lambda item: item.get("match_score", 0), reverse=True)
    status = {
        "agent": "job_search",
        "ts": now().isoformat(),
        "total": len(opportunities),
        "active": len(active),
        "interviews": status_counts["interview"],
        "offers": status_counts["offer"],
        "applied": status_counts["applied"],
        "status_counts": status_counts,
        "items": ranked[:10],
    }
    save_json(str(STATUS_FILE), status)
    _log({"action": "assess", "active": len(active), "interviews": status["interviews"]})
    return status


def _format_top(items: list[dict], limit: int = 3) -> str:
    labels = []
    for item in items[:limit]:
        title = item.get("title") or "Sin título"
        labels.append(f"{title} ({item.get('match_score', 0)}%)")
    return "; ".join(labels)


def _format_links(items: list[dict], limit: int = 10) -> str:
    lines = []
    for index, item in enumerate(items[:limit], 1):
        lines.append(
            f"\n{index}. {item.get('title') or 'Sin título'} "
            f"({item.get('match_score', 0)}%)\n{item.get('url', '')}"
        )
    return "".join(lines)


def _format_cv_analysis(results: list[dict]) -> str:
    if results and results[0].get("error") and len(results) == 1:
        return results[0]["error"]
    lines = ["Análisis contra el CV maestro:"]
    for index, item in enumerate(results, 1):
        lines.append(
            f"\n{index}. {item['title']} — {item.get('score', '?')}%\n"
            f"{item['url']}\n"
            f"Fortalezas: {', '.join(item.get('strengths', [])[:3]) or 'sin datos'}\n"
            f"Brechas: {', '.join(item.get('gaps', [])[:3]) or 'sin datos'}\n"
            f"Recomendación: {item.get('recommendation', 'sin datos')}"
        )
    return "\n".join(lines)


def handle(query: str, correlation_id: str = None):
    q = query.lower()
    if "analiza" in q and ("vacante" in q or "job" in q or "empleo" in q):
        status = assess()
        summary = _format_cv_analysis(analyze_matches())
        obs = Observation("job_search", "personal", summary, 0.9, status)
        return obs, []
    discovery = None
    if any(intent in q for intent in _SEARCH_INTENTS):
        discovery = discover(query)

    status = assess()
    if discovery:
        if discovery["errors"] and discovery["searched"] == 0:
            summary = (
                "Jobs: no pude consultar las fuentes porque SearXNG no está disponible. "
                f"{status['active']} oportunidades guardadas"
            )
        else:
            summary = (
                f"Jobs: {discovery['added']} nuevas, {discovery['updated']} actualizadas. "
                f"{status['active']} activas"
            )
    elif status["active"]:
        summary = (
            f"Jobs: {status['active']} activas, {status.get('applied', 0)} postuladas, "
            f"{status['interviews']} entrevista(s), {status['offers']} oferta(s)"
        )
    else:
        summary = "Jobs: sin oportunidades activas"

    top = _format_top(status.get("items", []))
    if top:
        summary += f". Mejor match: {top}"
        summary += "\n\nLinks:" + _format_links(status.get("items", []))

    recs = []
    if status["interviews"]:
        recs.append(
            Recommendation(
                "prepare",
                "interview_brief",
                "low",
                False,
                "Hay entrevistas activas para preparar",
            )
        )
    unreviewed = status.get("status_counts", {}).get("detected", 0)
    if unreviewed:
        recs.append(
            Recommendation(
                "review",
                "job_matches",
                "low",
                False,
                f"Hay {unreviewed} oportunidades nuevas para revisar",
            )
        )

    obs = Observation("job_search", "personal", summary, 0.85, status)
    _log({"action": "handle", "query": query[:80], "correlation_id": correlation_id})
    return obs, recs


def main() -> int:
    parser = argparse.ArgumentParser(description="Job search discovery and application tracker")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("status")
    search = subparsers.add_parser("search")
    search.add_argument("query", nargs="?", default="")
    move = subparsers.add_parser("move")
    move.add_argument("id")
    move.add_argument("status", choices=STATUSES)
    move.add_argument("--note", default="")
    args = parser.parse_args()

    if args.command == "search":
        output = discover(args.query)
    elif args.command == "move":
        output = update_status(args.id, args.status, args.note)
    else:
        output = assess()
    print(json.dumps(output, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
