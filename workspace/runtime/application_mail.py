"""Read Gmail through the existing NAS-local Himalaya account, without changing flags.

Email is untrusted evidence. Matching creates reviewable suggestions only;
no LLM instructions, email actions or application-state changes are executed.
"""

import hashlib
import json
import os
import re
import subprocess
import time
import tomllib
import unicodedata
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from application_tracker import ALIASES, STATUSES, TERMINAL, TZ, Applications
from assistant_jobs import ROOT

MAX_BYTES = 2 * 1024 * 1024
JOB_WORDS = re.compile(
    r"(?i)postulaci[oó]n|candidatur|application|interview|entrevista|recruit|selecci[oó]n|hiring|offer|oferta laboral|assessment"
)


def owner(chat):
    if str(chat) != "personal":
        raise ValueError("Gmail está disponible solo para la cuenta personal autorizada.")


def normalized(value):
    return " ".join(
        re.findall(
            r"[a-z0-9]+",
            "".join(
                c
                for c in unicodedata.normalize("NFD", str(value).casefold())
                if unicodedata.category(c) != "Mn"
            ),
        )
    )


def gmail_config():
    private = ROOT / "assistant/job-gmail.toml"
    return (
        private
        if private.is_file()
        else Path(os.getenv("HIMALAYA_CONFIG", str(Path.home() / ".config/himalaya/config.toml")))
    )


def gmail_account(path=None):
    path = Path(path) if path is not None else gmail_config()
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise ValueError("No pude leer la configuración local de Gmail.") from None

    def values(item):
        if isinstance(item, dict):
            for value in item.values():
                yield from values(value)
        elif isinstance(item, str):
            yield item

    accounts = data.get("accounts", data)
    matches = [
        name
        for name, account in accounts.items()
        if isinstance(account, dict)
        and any(v in {"imap.gmail.com", "imap.googlemail.com"} for v in values(account))
    ]
    selected = os.getenv("OPENCLAW_JOB_GMAIL_ACCOUNT")
    selected = selected or (matches[0] if len(matches) == 1 else None)
    if selected not in matches or not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", selected or ""):
        raise ValueError(
            "Configurá una única cuenta Gmail o seleccioná OPENCLAW_JOB_GMAIL_ACCOUNT en el NAS."
        )
    return selected


class Gmail:
    def __init__(self):
        self.config = gmail_config()
        self.account = gmail_account(self.config)

    def run(self, arguments):
        if arguments[:2] not in (["envelope", "list"], ["message", "read"]) or (
            arguments[:2] == ["message", "read"] and "--preview" not in arguments
        ):
            raise ValueError("Operación de correo no permitida: solo lectura sin cambiar flags.")
        try:
            result = subprocess.run(
                [
                    "himalaya",
                    *arguments[:2],
                    "--config",
                    str(self.config),
                    "--account",
                    self.account,
                    "--quiet",
                    *arguments[2:],
                ],
                capture_output=True,
                timeout=25,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            raise ValueError(
                "Gmail no está disponible; revisá la conexión local de correo."
            ) from None
        if (
            result.returncode
            and arguments[:2] == ["envelope", "list"]
            and "--page" in arguments
            and int(arguments[arguments.index("--page") + 1]) > 1
            and b"out of bound" in result.stderr.lower()
        ):
            return "[]"
        if result.returncode or len(result.stdout) > MAX_BYTES:
            raise ValueError("No pude leer Gmail. No se modificó ningún correo ni postulación.")
        return result.stdout.decode("utf-8", errors="replace")

    def envelopes(self, page, after):
        # No client-provided query or shell; read only, bounded and newest first.
        raw = self.run(
            [
                "envelope",
                "list",
                "--folder",
                "INBOX",
                "--page",
                str(page),
                "--page-size",
                "50",
                "--output",
                "json",
                "after",
                after,
                "order",
                "by",
                "date",
                "desc",
            ]
        )
        value = json.loads(raw)
        if not isinstance(value, list) or len(value) > 50:
            raise ValueError("Gmail devolvió una respuesta inesperada.")
        return value

    def preview(self, identity):
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", str(identity)):
            raise ValueError("Identificador de mensaje inválido.")
        return self.run(
            ["message", "read", "--folder", "INBOX", "--preview", "--no-headers", str(identity)]
        )[:16000]


def match(message, applications, body=""):
    sender = message.get("from") or {}
    address = str(sender.get("addr", "")).casefold() if isinstance(sender, dict) else ""
    display = str(sender.get("name", "")) if isinstance(sender, dict) else str(sender)
    content = " " + normalized(str(message.get("subject", "")) + " " + display + " " + body) + " "
    exact = [a for a in applications if a["recruiter"] and a["recruiter"] == address]
    candidates = exact or [
        a
        for a in applications
        if len(normalized(a["company"]).replace(" ", "")) >= 4
        and " " + normalized(a["company"]) + " " in content
    ]
    if len(candidates) > 1:
        by_role = [a for a in candidates if " " + normalized(a["role"]) + " " in content]
        candidates = by_role
    if len(candidates) != 1:
        return None
    item = candidates[0]
    # Ignore old correspondence predating this application (one-day tolerance).
    try:
        timestamp = datetime.fromisoformat(str(message["date"]).replace("Z", "+00:00")).timestamp()
    except (KeyError, ValueError, TypeError):
        return None
    return item if timestamp >= item["applied"] - 86400 else None


def classify(subject, body):
    content = normalized(subject + " " + body)
    categories = [
        (
            "rejected",
            (
                "not moving forward",
                "not been selected",
                "we regret",
                "no continuaremos",
                "no has sido seleccionado",
                "no fuiste seleccionado",
                "no avanzaremos",
                "otros candidatos",
            ),
        ),
        ("offer", ("offer letter", "oferta laboral", "oferta de empleo", "pleased to offer you")),
        (
            "interview",
            (
                "interview invitation",
                "schedule an interview",
                "schedule your interview",
                "invitacion a entrevista",
                "coordinar una entrevista",
                "agendar una entrevista",
                "entrevista confirmada",
            ),
        ),
        (
            "technical",
            ("technical assessment", "coding challenge", "prueba tecnica", "evaluacion tecnica"),
        ),
        ("screening", ("recruiter call", "screening call", "primera llamada")),
    ]
    for status, phrases in categories:
        if any(phrase in content for phrase in phrases):
            return status
    return None


def pending(chat):
    with Applications().connect() as connection:
        return [
            dict(r)
            for r in connection.execute(
                "SELECT s.id,s.subject,s.proposed,a.company,a.role FROM application_mail_suggestions s JOIN tracked_applications a ON a.id=s.application AND a.chat=s.chat WHERE s.chat=? AND s.status='pending' ORDER BY s.created DESC LIMIT 10",
                (str(chat),),
            )
        ]


def suggestions_text(chat):
    owner(chat)
    rows = pending(chat)
    if not rows:
        return "No hay sugerencias pendientes. Podés pedir una nueva lectura de Gmail."
    lines = ["Sugerencias de correo (hasta 10 pendientes; requieren revisión):"]
    for row in rows:
        lines.append(
            f"{row['id']} · {row['company']} — {row['role']}\nPosible estado: {STATUSES[row['proposed']]}\nAsunto: {row['subject']}\nConfirmá o descartá esta sugerencia desde el asistente."
        )
    return "\n\n".join(lines)[:3800]


def scan(chat, client=None):
    owner(chat)
    applications = Applications().items(chat, active=True)
    if not applications:
        return {
            "new": 0,
            "text": "Registrá primero tus postulaciones en el asistente. No consulté Gmail.",
        }
    client = client or Gmail()
    after = (datetime.now(TZ) - timedelta(days=30)).date().isoformat()
    scanned, previews, added = 0, 0, 0
    truncated = False
    for page in range(1, 4):
        envelopes = client.envelopes(page, after)
        for message in envelopes:
            scanned += 1
            subject = str(message.get("subject", ""))
            application = match(message, applications)
            body = ""
            if application is None and not JOB_WORDS.search(subject):
                continue
            if previews >= 20:
                truncated = True
                continue
            body = client.preview(message.get("id", ""))
            previews += 1
            application = match(message, applications, body)
            proposed = classify(subject, body)
            if application is None or proposed is None or proposed == application["status"]:
                continue
            sender = message.get("from") or {}
            address = sender.get("addr", "") if isinstance(sender, dict) else str(sender)
            key = hashlib.sha256(
                json.dumps(
                    [message.get("id"), message.get("date"), subject, address], ensure_ascii=False
                ).encode()
            ).hexdigest()
            safe_subject = " ".join(subject.split())[:240]
            with Applications().connect() as connection:
                result = connection.execute(
                    "INSERT OR IGNORE INTO application_mail_suggestions(id,application,chat,message_key,subject,proposed,created) VALUES(?,?,?,?,?,?,?)",
                    (
                        uuid.uuid4().hex[:10],
                        application["id"],
                        str(chat),
                        key,
                        safe_subject,
                        proposed,
                        time.time(),
                    ),
                )
                added += result.rowcount
        if len(envelopes) < 50:
            break
        if page == 3:
            truncated = True
    suggestions = pending(chat)
    lines = [
        f"Gmail: revisé {scanned} cabeceras de INBOX de los últimos 30 días y hasta 20 mensajes relacionados. Nuevas sugerencias: {added}."
    ]
    if truncated:
        lines.append(
            "Lectura limitada: no representa toda la cuenta ni todos los mensajes del período."
        )
    for row in suggestions:
        lines.append(
            f"{row['id']} · {row['company']} — {row['role']}\nPosible estado: {STATUSES[row['proposed']]}\nAsunto: {row['subject']}\nConfirmá o descartá esta sugerencia desde el asistente."
        )
    lines.append(
        "No cambié estados, no marqué mensajes como leídos y no envié correos. Las sugerencias necesitan revisión."
    )
    return {"new": added, "text": "\n\n".join(lines)[:3800], "truncated": truncated}


def resolve(chat, identity, accept):
    owner(chat)
    store = Applications()
    with store.connect() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT * FROM application_mail_suggestions WHERE id=? AND chat=?",
            (identity, str(chat)),
        ).fetchone()
        if row is None or row["status"] != "pending":
            raise ValueError("Sugerencia inexistente o ya procesada en este chat.")
        item = store.find(connection, chat, row["application"])
        if accept:
            if item["updated"] > row["created"] or item["status"] in TERMINAL:
                raise ValueError(
                    "La postulación cambió desde la sugerencia. Revisala y actualizá el estado manualmente."
                )
            status = ALIASES[row["proposed"]]
            next_at = None if status in TERMINAL else time.time()
            next_action = (
                ""
                if status in TERMINAL
                else {
                    "interview": "Coordinar fecha de entrevista",
                    "offer": "Revisar oferta",
                    "technical": "Revisar evaluación técnica",
                }.get(status, "Revisar respuesta del recruiter")
            )
            connection.execute(
                "UPDATE tracked_applications SET status=?,updated=?,next_at=?,next_action=?,interview_at=NULL WHERE id=? AND chat=?",
                (status, time.time(), next_at, next_action, item["id"], str(chat)),
            )
            store.event(
                connection,
                item,
                "mail_confirmed",
                "Estado confirmado por el usuario: " + STATUSES[status] + " · " + row["subject"],
            )
        connection.execute(
            "UPDATE application_mail_suggestions SET status=? WHERE id=? AND chat=?",
            ("confirmed" if accept else "dismissed", identity, str(chat)),
        )
    return (
        "Estado actualizado con tu confirmación. No se enviaron correos."
        if accept
        else "Sugerencia descartada; la postulación no cambió."
    )
