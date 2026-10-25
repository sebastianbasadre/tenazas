"""Owner-scoped application tracking, history and dated next actions.

Uses the assistant's existing local SQLite database. Does not submit applications
or contact recruiters. All user-facing dates are in Buenos Aires time.
"""

import hashlib
import re
import time
import unicodedata
import uuid
from contextlib import contextmanager
from datetime import datetime
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

from assistant_jobs import Jobs

TZ = ZoneInfo("America/Argentina/Buenos_Aires")
STATUSES = {
    "applied": "postulada",
    "screening": "screening",
    "interview": "entrevista",
    "technical": "técnica",
    "offer": "oferta",
    "accepted": "aceptada",
    "rejected": "rechazada",
    "withdrawn": "retirada",
    "paused": "pausada",
}
TERMINAL = {"accepted", "rejected", "withdrawn", "paused"}
ALIASES = {
    **{v: k for k, v in STATUSES.items()},
    **{k: k for k in STATUSES},
    "tecnica": "technical",
    "postulado": "applied",
}


def text(value, maximum=500):
    value = str(value).strip()
    if (
        not value
        or len(value) > maximum
        or any(unicodedata.category(c) == "Cc" and c != "\n" for c in value)
    ):
        raise ValueError(f"Indicá texto de entre 1 y {maximum} caracteres.")
    return value


def local_date(value):
    return (
        datetime.fromtimestamp(value, TZ).strftime("%d/%m/%Y %H:%M")
        if value is not None
        else "sin fecha"
    )


def parse_date(value):
    try:
        result = datetime.strptime(value.strip(), "%Y-%m-%d %H:%M").replace(tzinfo=TZ)
    except ValueError:
        raise ValueError("Fecha inválida. Usá AAAA-MM-DD HH:MM, horario de Argentina.") from None
    return result.timestamp()


def canonical_url(value):
    if not value:
        return ""
    value = text(value, 2000)
    parts = urlsplit(value)
    if (
        parts.scheme not in {"https", "http"}
        or not parts.hostname
        or parts.username
        or parts.password
        or re.search(r"\s", value)
    ):
        raise ValueError("El enlace debe ser http/https y no contener credenciales.")
    query = [
        (k, v)
        for k, v in parse_qsl(parts.query)
        if not k.lower().startswith("utm_") and k.lower() not in {"trk", "trackingid", "refid"}
    ]
    return urlunsplit(
        (parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/"), urlencode(query), "")
    )


class Applications:
    @contextmanager
    def connect(self):
        with Jobs().connect() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS tracked_applications (
                    id TEXT PRIMARY KEY, chat TEXT NOT NULL, fingerprint TEXT NOT NULL,
                    company TEXT NOT NULL, role TEXT NOT NULL, url TEXT NOT NULL,
                    cv TEXT NOT NULL DEFAULT '', recruiter TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL, applied REAL NOT NULL, created REAL NOT NULL,
                    updated REAL NOT NULL, next_at REAL, next_action TEXT NOT NULL DEFAULT '',
                    interview_at REAL, UNIQUE(chat,fingerprint));
                CREATE TABLE IF NOT EXISTS application_events (
                    id INTEGER PRIMARY KEY, application TEXT NOT NULL, chat TEXT NOT NULL,
                    kind TEXT NOT NULL, detail TEXT NOT NULL, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS application_preferences (
                    chat TEXT PRIMARY KEY, clock TEXT NOT NULL, enabled INTEGER NOT NULL,
                    configured REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS application_mail_suggestions (
                    id TEXT PRIMARY KEY, application TEXT NOT NULL, chat TEXT NOT NULL,
                    message_key TEXT NOT NULL, subject TEXT NOT NULL, proposed TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending', created REAL NOT NULL,
                    UNIQUE(chat,application,message_key));
                CREATE INDEX IF NOT EXISTS application_owner_due ON tracked_applications(chat,next_at);
            """)
            yield connection

    @staticmethod
    def event(connection, item, kind, detail):
        connection.execute(
            "INSERT INTO application_events(application,chat,kind,detail,created) VALUES(?,?,?,?,?)",
            (item["id"], item["chat"], kind, detail, time.time()),
        )

    @staticmethod
    def find(connection, chat, application):
        row = connection.execute(
            "SELECT * FROM tracked_applications WHERE id=? AND chat=?", (application, str(chat))
        ).fetchone()
        if row is None:
            raise ValueError("Postulación no encontrada en este chat.")
        return dict(row)

    def add(self, chat, company, role, url="", cv="", applied=None):
        company, role = text(company, 160), text(role, 200)
        url = canonical_url(url)
        cv = text(cv, 240) if cv else ""
        now = time.time()
        applied = now if applied is None else float(applied)
        if not 0 < applied <= now + 60:
            raise ValueError("Fecha de postulación inválida.")
        fingerprint = hashlib.sha256(
            (company.casefold() + "\n" + role.casefold() + "\n" + url).encode()
        ).hexdigest()
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT * FROM tracked_applications WHERE chat=? AND fingerprint=?",
                (str(chat), fingerprint),
            ).fetchone()
            if existing:
                return {**dict(existing), "created_now": False}
            identity = uuid.uuid4().hex[:10]
            connection.execute(
                "INSERT INTO tracked_applications(id,chat,fingerprint,company,role,url,cv,status,applied,created,updated,next_at,next_action) VALUES(?,?,?,?,?,?,?,'applied',?,?,?,?,?)",
                (
                    identity,
                    str(chat),
                    fingerprint,
                    company,
                    role,
                    url,
                    cv,
                    applied,
                    now,
                    now,
                    applied + 7 * 86400,
                    "Hacer seguimiento",
                ),
            )
            item = self.find(connection, chat, identity)
            self.event(
                connection,
                item,
                "registered",
                "Postulación registrada" + ("; CV: " + cv if cv else ""),
            )
            return {**item, "created_now": True}

    def get(self, chat, identity):
        with self.connect() as connection:
            item = self.find(connection, chat, identity)
            item["events"] = [
                dict(r)
                for r in connection.execute(
                    "SELECT kind,detail,created FROM application_events WHERE application=? AND chat=? ORDER BY id DESC LIMIT 12",
                    (identity, str(chat)),
                )
            ]
            return item

    def items(self, chat, active=False):
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM tracked_applications WHERE chat=? ORDER BY updated DESC",
                (str(chat),),
            ).fetchall()
        return [dict(r) for r in rows if not active or r["status"] not in TERMINAL]

    def update(self, chat, identity, kind, value, note=""):
        note = text(note, 1000) if note else ""
        now = time.time()
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            item = self.find(connection, chat, identity)
            fields = {"updated": now}
            if kind == "status":
                status = ALIASES.get(value.casefold())
                if status is None:
                    raise ValueError("Estado inválido. " + ", ".join(STATUSES.values()))
                fields["status"] = status
                if status in TERMINAL:
                    fields.update(next_at=None, next_action="", interview_at=None)
                elif item["status"] in TERMINAL:
                    fields.update(next_at=now + 7 * 86400, next_action="Hacer seguimiento")
                if (
                    status not in TERMINAL
                    and status != item["status"]
                    and status in {"interview", "technical", "offer"}
                ):
                    fields.update(
                        next_at=now,
                        next_action={
                            "interview": "Coordinar fecha de entrevista",
                            "technical": "Preparar evaluación técnica",
                            "offer": "Revisar oferta",
                        }[status],
                    )
                if status != "interview":
                    fields["interview_at"] = None
                detail = f"{STATUSES[item['status']]} → {STATUSES[status]}"
            elif kind == "applied_date":
                applied = parse_date(value)
                if applied > now:
                    raise ValueError("La fecha de envío no puede ser futura.")
                fields["applied"] = applied
                if item["status"] not in TERMINAL and item["next_action"] == "Hacer seguimiento":
                    fields["next_at"] = applied + 7 * 86400
                detail = "Fecha de envío corregida: " + local_date(applied)
            elif kind in {"reminder", "interview"}:
                deadline = parse_date(value)
                if deadline <= now:
                    raise ValueError("Elegí una fecha futura.")
                if item["status"] in TERMINAL:
                    raise ValueError("Reabrí la postulación antes de programar una acción.")
                fields.update(next_at=deadline, next_action=note or "Hacer seguimiento")
                if kind == "interview":
                    fields.update(
                        status="interview",
                        interview_at=deadline,
                        next_at=max(now, deadline - 86400),
                        next_action="Preparar entrevista",
                    )
                detail = (
                    "Entrevista: " if kind == "interview" else "Próxima acción: "
                ) + local_date(deadline)
            elif kind == "contact":
                if item["status"] in TERMINAL:
                    raise ValueError("La postulación está cerrada o pausada.")
                fields.update(next_at=now + 7 * 86400, next_action="Hacer seguimiento")
                detail = "Seguimiento realizado; próximo control en 7 días"
            elif kind == "recruiter":
                if not re.fullmatch(r"[^\s@<>]+@[^\s@<>]+\.[^\s@<>]+", value) or len(value) > 254:
                    raise ValueError("Indicá un email válido del recruiter.")
                fields["recruiter"] = value.casefold()
                detail = "Contacto del recruiter actualizado"
            elif kind == "note":
                detail = text(value, 1000)
            else:
                raise ValueError("Actualización no permitida.")
            connection.execute(
                "UPDATE tracked_applications SET "
                + ",".join(k + "=?" for k in fields)
                + " WHERE id=? AND chat=?",
                (*fields.values(), identity, str(chat)),
            )
            self.event(connection, item, kind, detail + (" · " + note if note else ""))
            return self.find(connection, chat, identity)

    def delete(self, chat, identity):
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            self.find(connection, chat, identity)
            for table in ("application_events", "application_mail_suggestions"):
                connection.execute(
                    f"DELETE FROM {table} WHERE application=? AND chat=?", (identity, str(chat))
                )
            connection.execute(
                "DELETE FROM tracked_applications WHERE id=? AND chat=?", (identity, str(chat))
            )

    def due(self, chat, moment=None):
        moment = time.time() if moment is None else moment
        return sorted(
            [
                r
                for r in self.items(chat, active=True)
                if r["next_at"] is not None and r["next_at"] <= moment
            ],
            key=lambda r: r["next_at"],
        )


def draft(chat, identity):
    row = Applications().get(chat, identity)
    if row["status"] in TERMINAL:
        raise ValueError("La postulación está cerrada o pausada.")
    return f"Borrador para revisar — no enviado\n\nHola, ¿cómo estás?\n\nQuería consultar si hay novedades sobre mi postulación al puesto de {row['role']} en {row['company']}, enviada el {datetime.fromtimestamp(row['applied'], TZ):%d/%m/%Y}. Sigo interesado en la oportunidad y quedo disponible para ampliar información.\n\nMuchas gracias."
