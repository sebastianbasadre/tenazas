#!/usr/bin/env python3
"""Local-only bridge controlling the authenticated LinkedIn Chrome via CDP.

The bridge never receives or exports LinkedIn credentials/cookies. It exposes
only health/session/apply operations and stops on CAPTCHA, sensitive questions,
or unknown required fields.
"""

from __future__ import annotations

import json
import os
import re
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright

HOST = os.getenv("LINKEDIN_BRIDGE_HOST", "127.0.0.1")
PORT = int(os.getenv("LINKEDIN_BRIDGE_PORT", "9224"))
CDP_URL = os.getenv("LINKEDIN_CDP_URL", "http://127.0.0.1:9223")
TOKEN_FILE = Path.home() / ".openclaw-linkedin-profile" / "bridge-token"
WORKSPACE_RUNTIME = Path(os.getenv("LINKEDIN_WORKSPACE_RUNTIME", r"Z:\openclaw\workspace\runtime"))
SENSITIVE_RE = re.compile(
    r"salary|compensation|visa|sponsor|work authorization|gender|race|ethnicity|"
    r"disability|veteran|criminal|date of birth|age|marital",
    re.IGNORECASE,
)
CAPTCHA_RE = re.compile(r"captcha|security verification|prove.*human", re.IGNORECASE)
LOCK = threading.Lock()


def _token() -> str:
    return TOKEN_FILE.read_text(encoding="utf-8").strip()


def _linkedin_page(browser):
    pages = [page for context in browser.contexts for page in context.pages]
    for page in pages:
        if "linkedin.com" in page.url and page.url.startswith("https://www.linkedin.com"):
            return page
    context = browser.contexts[0]
    return context.new_page()


def session_status() -> dict:
    with sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp(CDP_URL)
        page = _linkedin_page(browser)
        authenticated = "/login" not in page.url and "/checkpoint/" not in page.url
        return {"ok": True, "authenticated": authenticated, "url": page.url, "title": page.title()}


def _visible_text(page) -> str:
    try:
        return page.locator("body").inner_text(timeout=5000)
    except Exception:
        return ""


def _unknown_required_fields(page) -> list[str]:
    unknown = []
    for field in page.locator("input[required], textarea[required], select[required]").all():
        try:
            if not field.is_visible():
                continue
            field_type = (field.get_attribute("type") or "").lower()
            if field_type in {"hidden", "file"}:
                continue
            value = field.input_value() if field.evaluate("(e) => 'value' in e") else ""
            if value:
                continue
            label = field.get_attribute("aria-label") or field.get_attribute("name") or "required field"
            unknown.append(label)
        except Exception:
            continue
    return unknown


def apply(url: str, cv_path: str) -> dict:
    if "linkedin.com/jobs/view/" not in url:
        return {"ok": False, "status": "unsupported_url", "message": "URL no válida de LinkedIn."}
    if cv_path.startswith("/home/tenazas/runtime/"):
        cv = WORKSPACE_RUNTIME / cv_path.removeprefix("/home/tenazas/runtime/")
    else:
        cv = Path(cv_path)
    if not cv.exists() or cv.suffix.lower() != ".pdf":
        return {"ok": False, "status": "cv_missing", "message": "CV PDF no encontrado."}

    with LOCK, sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp(CDP_URL)
        page = _linkedin_page(browser)
        page.bring_to_front()
        page.goto(url, wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(2500)
        text = _visible_text(page)
        if CAPTCHA_RE.search(text):
            return {"ok": False, "status": "captcha_required", "message": "LinkedIn pidió verificación humana."}
        if "No longer accepting applications" in text or "Ya no se aceptan solicitudes" in text:
            return {"ok": False, "status": "closed", "message": "La vacante ya no acepta postulaciones."}

        easy = page.get_by_role("button", name=re.compile(r"Easy Apply|Solicitud sencilla", re.I))
        if not easy.count():
            return {
                "ok": False,
                "status": "external_apply",
                "message": "La vacante no usa Easy Apply. Se requiere postulación externa.",
                "url": page.url,
            }
        easy.first.click()
        page.wait_for_timeout(1200)

        for _ in range(8):
            modal_text = _visible_text(page)
            if CAPTCHA_RE.search(modal_text):
                return {"ok": False, "status": "captcha_required", "message": "LinkedIn pidió CAPTCHA."}
            if SENSITIVE_RE.search(modal_text):
                return {
                    "ok": False,
                    "status": "sensitive_questions",
                    "message": "El formulario contiene preguntas sensibles que requieren respuesta manual.",
                }

            file_inputs = page.locator('input[type="file"]')
            for index in range(file_inputs.count()):
                field = file_inputs.nth(index)
                try:
                    field.set_input_files(str(cv))
                except Exception:
                    pass

            unknown = _unknown_required_fields(page)
            if unknown:
                return {
                    "ok": False,
                    "status": "unknown_required_fields",
                    "message": "Hay campos obligatorios sin respuesta.",
                    "fields": unknown[:10],
                }

            submit = page.get_by_role("button", name=re.compile(r"Submit application|Enviar solicitud", re.I))
            if submit.count() and submit.first.is_enabled():
                submit.first.click()
                page.wait_for_timeout(2500)
                confirmation = _visible_text(page)
                if re.search(r"application was sent|solicitud enviada|application submitted", confirmation, re.I):
                    return {"ok": True, "status": "submitted", "message": "Postulación enviada."}
                return {
                    "ok": False,
                    "status": "submission_unconfirmed",
                    "message": "Se pulsó enviar, pero LinkedIn no confirmó la postulación.",
                }

            next_button = page.get_by_role("button", name=re.compile(r"Next|Review|Siguiente|Revisar", re.I))
            if not next_button.count() or not next_button.first.is_enabled():
                return {
                    "ok": False,
                    "status": "manual_action_required",
                    "message": "El formulario requiere intervención manual.",
                }
            next_button.first.click()
            page.wait_for_timeout(800)

        return {"ok": False, "status": "too_many_steps", "message": "Formulario demasiado largo."}


class Handler(BaseHTTPRequestHandler):
    def _send(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self) -> bool:
        return self.headers.get("X-LinkedIn-Bridge-Token", "") == _token()

    def do_GET(self) -> None:
        if not self._authorized():
            self._send(HTTPStatus.UNAUTHORIZED, {"ok": False, "error": "unauthorized"})
            return
        if self.path == "/health":
            try:
                self._send(HTTPStatus.OK, session_status())
            except Exception as exc:
                self._send(HTTPStatus.BAD_GATEWAY, {"ok": False, "error": str(exc)})
            return
        self._send(HTTPStatus.NOT_FOUND, {"ok": False, "error": "not_found"})

    def do_POST(self) -> None:
        if not self._authorized():
            self._send(HTTPStatus.UNAUTHORIZED, {"ok": False, "error": "unauthorized"})
            return
        length = int(self.headers.get("Content-Length", "0"))
        try:
            payload = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self._send(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "invalid_json"})
            return
        if self.path == "/apply":
            result = apply(str(payload.get("url", "")), str(payload.get("cv_path", "")))
            self._send(HTTPStatus.OK if result.get("ok") else HTTPStatus.CONFLICT, result)
            return
        self._send(HTTPStatus.NOT_FOUND, {"ok": False, "error": "not_found"})

    def log_message(self, _format: str, *_args) -> None:
        return


if __name__ == "__main__":
    if not TOKEN_FILE.exists():
        raise SystemExit(f"Missing token file: {TOKEN_FILE}")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()
