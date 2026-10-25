"""Persistent, owner-scoped jobs for Telegram's personal-assistant features."""

import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

ROOT = Path("/home/tenazas/.openclaw/state") if Path("/home/tenazas").exists() else Path(__file__).parent.parent / "state"
DB = ROOT / "assistant/jobs.sqlite"
KINDS = {"daily", "weekly", "search", "cv", "apply", "documents", "document_index", "diagnose", "routine_plan", "routine_execute"}
LABELS = {"received": "recibido", "running": "trabajando", "waiting_approval": "esperando aprobación",
          "done": "terminado", "failed": "falló", "cancel_requested": "cancelando",
          "cancelled": "cancelado", "interrupted": "interrumpido: requiere revisión"}


class Jobs:
    def __init__(self, path=None):
        self.path = Path(path or DB)

    @contextmanager
    def connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("""CREATE TABLE IF NOT EXISTS assistant_jobs (
            id TEXT PRIMARY KEY, chat TEXT NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL,
            status TEXT NOT NULL, phase TEXT NOT NULL DEFAULT '', created REAL NOT NULL,
            updated REAL NOT NULL, run_after REAL NOT NULL DEFAULT 0, result TEXT,
            schedule_key TEXT UNIQUE, delivery TEXT NOT NULL DEFAULT 'pending')""")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    @staticmethod
    def decode(row):
        if row is None:
            return None
        item = dict(row)
        item["payload"] = json.loads(item["payload"])
        item["result"] = json.loads(item["result"]) if item["result"] else {}
        return item

    def create(self, chat, kind, payload=None, schedule_key=None, run_after=0):
        if kind not in KINDS:
            raise ValueError("Tipo de trabajo no permitido")
        data = json.dumps(payload or {})
        if len(data) > 16000:
            raise ValueError("Solicitud demasiado larga")
        job_id = uuid.uuid4().hex[:12]
        with self.connect() as connection:
            connection.execute("INSERT OR IGNORE INTO assistant_jobs (id,chat,kind,payload,status,created,updated,schedule_key,run_after) VALUES (?,?,?,?,'received',?,?,?,?)",
                               (job_id, str(chat), kind, data, time.time(), time.time(), schedule_key, run_after))
            row = connection.execute("SELECT * FROM assistant_jobs WHERE schedule_key=?" if schedule_key else
                                     "SELECT * FROM assistant_jobs WHERE id=?", (schedule_key or job_id,)).fetchone()
            return self.decode(row)

    def get(self, job_id, chat=None):
        with self.connect() as connection:
            sql = "SELECT * FROM assistant_jobs WHERE id=?"
            args = [job_id]
            if chat is not None:
                sql += " AND chat=?"
                args.append(str(chat))
            return self.decode(connection.execute(sql, args).fetchone())

    def list(self, chat, limit=12):
        with self.connect() as connection:
            return [self.decode(row) for row in connection.execute(
                "SELECT * FROM assistant_jobs WHERE chat=? ORDER BY created DESC LIMIT ?", (str(chat), limit))]

    def update(self, job_id, **values):
        if not values or not set(values) <= {"status", "phase", "result", "kind", "payload", "delivery"}:
            raise ValueError("Actualización no permitida")
        values = {key: json.dumps(value) if key in {"payload", "result"} else value for key, value in values.items()}
        values["updated"] = time.time()
        with self.connect() as connection:
            connection.execute("UPDATE assistant_jobs SET " + ",".join(key + "=?" for key in values) + " WHERE id=?",
                               [*values.values(), job_id])

    def claim(self):
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM assistant_jobs WHERE status='received' AND run_after<=? ORDER BY created LIMIT 1", (time.time(),)).fetchone()
            if row:
                connection.execute("UPDATE assistant_jobs SET status='running',phase='iniciando',updated=? WHERE id=?", (time.time(), row["id"]))
            return self.decode(row)

    def cancel(self, job_id, chat):
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM assistant_jobs WHERE id=? AND chat=?", (job_id, str(chat))).fetchone()
            if not row or row["status"] not in {"received", "running", "waiting_approval"}:
                return False
            status = "cancel_requested" if row["status"] == "running" else "cancelled"
            connection.execute("UPDATE assistant_jobs SET status=?,updated=? WHERE id=?", (status, time.time(), job_id))
            return True

    def approve(self, job_id, chat):
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            item = self.decode(connection.execute("SELECT * FROM assistant_jobs WHERE id=? AND chat=? AND status='waiting_approval'", (job_id, str(chat))).fetchone())
            if not item or item["kind"] not in {"cv", "routine_plan"}:
                return False
            payload = {**item["payload"], "approval": {"chat": str(chat), "at": time.time()}, "prepared": item["result"]}
            kind = "apply" if item["kind"] == "cv" else "routine_execute"
            connection.execute("UPDATE assistant_jobs SET status='received',kind=?,payload=?,delivery='pending',updated=? WHERE id=?",
                               (kind, json.dumps(payload), time.time(), job_id))
            return True

    def recover(self):
        with self.connect() as connection:
            connection.execute("UPDATE assistant_jobs SET status='interrupted',phase='No se reintentó automáticamente tras el reinicio',updated=? WHERE status IN ('running','cancel_requested')", (time.time(),))
            connection.execute("UPDATE assistant_jobs SET delivery='uncertain' WHERE delivery='sending'")

    def finish(self, job_id, status, result):
        with self.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT status FROM assistant_jobs WHERE id=?", (job_id,)).fetchone()
            if row and row["status"] == "cancel_requested":
                status = "cancelled"
            connection.execute("UPDATE assistant_jobs SET status=?,result=?,phase='',updated=? WHERE id=?",
                               (status, json.dumps(result), time.time(), job_id))

    def deliveries(self):
        with self.connect() as connection:
            return [self.decode(row) for row in connection.execute("SELECT * FROM assistant_jobs WHERE delivery='pending' AND status IN ('done','failed','waiting_approval','cancelled','interrupted') ORDER BY updated LIMIT 10")]
