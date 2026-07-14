#!/usr/bin/env python3
"""ATS-safe, one-column ReportLab CV renderer for Sebastian Basadre."""

from __future__ import annotations

import html
import re
import subprocess
from pathlib import Path

from pypdf import PdfReader
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import HRFlowable, Paragraph, SimpleDocTemplate, Spacer

BLACK = colors.HexColor("#1a1a1a")
DARK = colors.HexColor("#2c2c2c")
ACCENT = colors.HexColor("#1a5276")
HEADINGS = {
    "PROFESSIONAL SUMMARY",
    "CORE COMPETENCIES",
    "TECHNICAL SKILLS",
    "PROFESSIONAL EXPERIENCE",
    "CERTIFICATIONS",
    "EDUCATION",
    "LANGUAGES",
}


def _safe(value: str) -> str:
    return html.escape(value, quote=False).replace("&#x27;", "'")


def _styles(compact: bool = False) -> dict:
    body_size = 8.5 if compact else 9
    body_leading = 12 if compact else 13
    return {
        "name": ParagraphStyle(
            "Name", fontName="Helvetica-Bold", fontSize=16, alignment=TA_CENTER,
            leading=20, textColor=BLACK, spaceAfter=2,
        ),
        "title": ParagraphStyle(
            "Title", fontName="Helvetica", fontSize=9.5, alignment=TA_CENTER,
            leading=13, textColor=DARK, spaceAfter=2,
        ),
        "contact": ParagraphStyle(
            "Contact", fontName="Helvetica", fontSize=8.5, alignment=TA_CENTER,
            leading=12, textColor=DARK, spaceAfter=6,
        ),
        "section": ParagraphStyle(
            "Section", fontName="Helvetica-Bold", fontSize=9.5, textColor=ACCENT,
            leading=13, spaceBefore=7, spaceAfter=3,
        ),
        "body": ParagraphStyle(
            "Body", fontName="Helvetica", fontSize=body_size, textColor=BLACK,
            leading=body_leading, spaceAfter=2,
        ),
        "bullet": ParagraphStyle(
            "Bullet", fontName="Helvetica", fontSize=body_size, textColor=BLACK,
            leading=body_leading, leftIndent=12, firstLineIndent=-8, spaceAfter=2,
        ),
        "job_title": ParagraphStyle(
            "JobTitle", fontName="Helvetica-Bold", fontSize=9.5, textColor=BLACK,
            leading=13, spaceAfter=1,
        ),
        "job_meta": ParagraphStyle(
            "JobMeta", fontName="Helvetica", fontSize=8.5, textColor=DARK,
            leading=12, spaceAfter=3,
        ),
    }


def _section(story: list, title: str, styles: dict) -> None:
    story.append(Paragraph(title, styles["section"]))
    story.append(HRFlowable(width="100%", thickness=0.5, color=ACCENT, spaceAfter=4))


def _parse_master(text: str) -> dict:
    lines = [line.strip().replace("\x7f", "•") for line in text.splitlines() if line.strip()]
    data = {"name": lines[0], "title": lines[1], "contact": lines[2], "sections": {}}
    current = ""
    for line in lines[3:]:
        if line in HEADINGS:
            current = line
            data["sections"][current] = []
        elif current:
            data["sections"][current].append(line)
    return data


def _join_wrapped(lines: list[str]) -> list[str]:
    output = []
    for line in lines:
        if not output or line.startswith("•"):
            output.append(line)
        else:
            output[-1] += " " + line
    return output


def _join_categories(lines: list[str]) -> list[str]:
    output = []
    category = re.compile(r"^[A-Za-z][A-Za-z /&-]{2,45}:")
    for line in lines:
        if not output or category.match(line):
            output.append(line)
        else:
            output[-1] += " " + line
    return output


def _experience_roles(lines: list[str]) -> list[dict]:
    roles = []
    meta_indexes = [
        index for index, line in enumerate(lines)
        if " · " in line and bool(re.search(r"\d{4}", line))
    ]
    for position, meta_index in enumerate(meta_indexes):
        next_meta = meta_indexes[position + 1] if position + 1 < len(meta_indexes) else len(lines)
        bullet_end = max(meta_index + 1, next_meta - 1)
        roles.append(
            {
                "title": lines[meta_index - 1] if meta_index else "",
                "meta": lines[meta_index],
                "bullets": _join_wrapped(lines[meta_index + 1:bullet_end]),
            }
        )
    return roles


def _relevance(text: str, keywords: list[str]) -> int:
    value = text.lower()
    return sum(1 for keyword in keywords if keyword.lower() in value)


def _build_story(master: dict, title: str, keywords: list[str], compact: bool) -> list:
    styles = _styles(compact)
    sections = master["sections"]
    story = [
        Paragraph(_safe(master["name"]), styles["name"]),
        Paragraph(
            _safe(
                title
                + (
                    " · " + " · ".join(keyword.title() for keyword in keywords[:2])
                    if keywords else ""
                )
            ),
            styles["title"],
        ),
        Paragraph(_safe(master["contact"]), styles["contact"]),
        HRFlowable(width="100%", thickness=0.8, color=ACCENT, spaceAfter=4),
        Spacer(1, 4),
    ]

    summary = " ".join(sections.get("PROFESSIONAL SUMMARY", []))
    verified = [keyword for keyword in keywords if keyword.lower() in master_text_cache.lower()]
    if verified:
        summary = f"{summary} Relevant capabilities include {', '.join(verified[:5])}."
    _section(story, "PROFESSIONAL SUMMARY", styles)
    story.append(Paragraph(_safe(summary), styles["body"]))
    story.append(Spacer(1, 3))

    _section(story, "CORE COMPETENCIES", styles)
    competencies = _join_categories(sections.get("CORE COMPETENCIES", []))
    competencies.sort(key=lambda line: _relevance(line, keywords), reverse=True)
    for line in competencies:
        story.append(Paragraph(_safe(line), styles["body"]))
    story.append(Spacer(1, 3))

    _section(story, "PROFESSIONAL EXPERIENCE", styles)
    roles = _experience_roles(sections.get("PROFESSIONAL EXPERIENCE", []))
    for role_index, role in enumerate(roles[:5]):
        story.append(Paragraph(_safe(role["title"]), styles["job_title"]))
        story.append(Paragraph(_safe(role["meta"]), styles["job_meta"]))
        bullets = sorted(
            role["bullets"],
            key=lambda bullet: _relevance(bullet, keywords),
            reverse=True,
        )
        limit = 5 if role_index == 0 else 3 if role_index < 3 else 2
        for bullet in bullets[:limit]:
            story.append(
                Paragraph(
                    f"&#8226;&nbsp;{_safe(bullet.lstrip('• '))}",
                    styles["bullet"],
                )
            )
        story.append(Spacer(1, 4))

    _section(story, "CERTIFICATIONS", styles)
    story.append(Paragraph(_safe(" ".join(sections.get("CERTIFICATIONS", []))), styles["body"]))

    _section(story, "EDUCATION", styles)
    for line in _join_wrapped(sections.get("EDUCATION", [])):
        story.append(Paragraph(_safe(line), styles["body"]))

    _section(story, "TECHNICAL SKILLS", styles)
    skills = _join_categories(sections.get("TECHNICAL SKILLS", []))
    skills.sort(key=lambda line: _relevance(line, keywords), reverse=True)
    for line in skills:
        story.append(Paragraph(_safe(line), styles["body"]))

    _section(story, "LANGUAGES", styles)
    story.append(Paragraph(_safe(" ".join(sections.get("LANGUAGES", []))), styles["body"]))
    return story


master_text_cache = ""


def build_tailored_pdf(
    master_text: str,
    output: str | Path,
    title: str,
    keywords: list[str],
    preview_dir: str | Path,
) -> dict:
    """Build, enforce <=2 pages, and render page one for visual verification."""
    global master_text_cache
    master_text_cache = master_text
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    master = _parse_master(master_text)

    for compact in (False, True):
        doc = SimpleDocTemplate(
            str(output), pagesize=letter,
            leftMargin=0.55 * inch, rightMargin=0.55 * inch,
            topMargin=0.5 * inch, bottomMargin=0.5 * inch,
        )
        doc.build(_build_story(master, title, keywords, compact))
        pages = len(PdfReader(str(output)).pages)
        if pages <= 2:
            break
    if pages > 2:
        output.unlink(missing_ok=True)
        raise ValueError(f"El CV excede el máximo de 2 páginas ({pages}).")

    preview_dir = Path(preview_dir)
    preview_dir.mkdir(parents=True, exist_ok=True)
    preview_prefix = preview_dir / output.stem
    result = subprocess.run(
        ["pdftoppm", "-jpeg", "-r", "150", "-f", "1", "-l", "1",
         str(output), str(preview_prefix)],
        capture_output=True, text=True, timeout=30,
    )
    previews = sorted(preview_dir.glob(f"{output.stem}-*.jpg"))
    if result.returncode != 0 or not previews or previews[0].stat().st_size == 0:
        output.unlink(missing_ok=True)
        raise ValueError(f"No se pudo renderizar el preview: {result.stderr[:200]}")
    return {"pages": pages, "preview": str(previews[0]), "compact": compact}
