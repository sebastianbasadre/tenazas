"""NAS fixed-command SSH endpoint. One validated JSON request; never accepts shell commands."""

import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

from application_tracker import STATUSES, Applications, draft, local_date
from mcp_applications_contract import validate

OWNER = "personal"
BASE = Path(__file__).resolve().parent


def connect():
    context = Applications().connect()
    return context


def queue_mail():
    with connect() as connection:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS application_mcp_jobs(id TEXT PRIMARY KEY,status TEXT NOT NULL,created REAL NOT NULL,result TEXT)"
        )
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            "UPDATE application_mcp_jobs SET status='interrupted' WHERE status IN ('queued','running') AND created<?",
            (time.time() - 600,),
        )
        row = connection.execute(
            "SELECT id,status FROM application_mcp_jobs WHERE status IN ('queued','running') ORDER BY created LIMIT 1"
        ).fetchone()
        if row:
            return dict(row)
        identity = uuid.uuid4().hex[:10]
        connection.execute(
            "INSERT INTO application_mcp_jobs VALUES(?,'queued',?,NULL)", (identity, time.time())
        )
    try:
        subprocess.Popen(
            [sys.executable, str(BASE / "application_mcp_runner.py"), "--mail-worker", identity],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
        )
    except OSError:
        with connect() as connection:
            connection.execute(
                "UPDATE application_mcp_jobs SET status='failed',result=? WHERE id=?",
                (json.dumps({"code": "MAIL_WORKER_UNAVAILABLE"}), identity),
            )
        raise ValueError("No pude iniciar la lectura de Gmail.") from None
    return {"id": identity, "status": "queued"}


def mail_worker(identity):
    with connect() as connection:
        cursor = connection.execute(
            "UPDATE application_mcp_jobs SET status='running' WHERE id=? AND status='queued'",
            (identity,),
        )
        if cursor.rowcount != 1:
            return
    try:
        from application_mail import scan

        value = scan(OWNER)
        result = {k: value[k] for k in ("new", "truncated") if k in value}
        result["checked_at"] = time.time()
        status = "done"
    except Exception:
        result, status = {"code": "GMAIL_UNAVAILABLE"}, "failed"
    with connect() as connection:
        connection.execute(
            "UPDATE application_mcp_jobs SET status=?,result=? WHERE id=?",
            (status, json.dumps(result), identity),
        )


def public(row):
    value = {k: v for k, v in row.items() if k not in {"chat", "fingerprint", "created_now"}}
    value.update(
        applied_date=local_date(row.get("applied")),
        next_action_date=local_date(row.get("next_at")),
        interview_date=local_date(row.get("interview_at")),
    )
    return value


def dispatch(name, arguments):
    validate(name, arguments)
    store = Applications()
    if name == "get_job_applications":
        rows = store.items(OWNER)
        status = arguments.get("status")
        if status is not None:
            if status not in STATUSES:
                raise ValueError("Estado inválido.")
            rows = [r for r in rows if r["status"] == status]
        offset = arguments.get("offset", 0)
        return {
            "applications": [public(r) for r in rows[offset : offset + 30]],
            "total": len(rows),
            "offset": offset,
            "next_offset": offset + 30 if offset + 30 < len(rows) else None,
            "overdue": len(store.due(OWNER)),
            "timezone": "America/Argentina/Buenos_Aires",
        }
    if name == "get_job_application":
        return public(store.get(OWNER, arguments["id"]))
    if name == "register_job_application":
        return public(store.add(OWNER, **arguments))
    if name == "update_job_application":
        return public(
            store.update(
                OWNER,
                arguments["id"],
                arguments["kind"],
                arguments.get("value", ""),
                arguments.get("note", ""),
            )
        )
    if name == "draft_job_followup":
        return {"draft": draft(OWNER, arguments["id"]), "sent": False}
    if name == "delete_job_application":
        store.delete(OWNER, arguments["id"])
        return {"deleted": arguments["id"]}
    if name == "refresh_job_mail":
        return queue_mail()
    if name == "resolve_job_mail_suggestion":
        from application_mail import resolve

        return {"result": resolve(OWNER, arguments["id"], arguments["accept"])}
    if name == "get_job_mail_review":
        from application_mail import pending

        with connect() as connection:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS application_mcp_jobs(id TEXT PRIMARY KEY,status TEXT NOT NULL,created REAL NOT NULL,result TEXT)"
            )
            if arguments.get("job_id"):
                row = connection.execute(
                    "SELECT * FROM application_mcp_jobs WHERE id=?", (arguments["job_id"],)
                ).fetchone()
            else:
                row = connection.execute(
                    "SELECT * FROM application_mcp_jobs ORDER BY created DESC LIMIT 1"
                ).fetchone()
        job = dict(row) if row else None
        if job:
            job["result"] = json.loads(job["result"]) if job["result"] else None
            if job["status"] in {"running", "queued"} and time.time() - job["created"] > 600:
                job["status"] = "interrupted"
        return {"scan": job, "suggestions": pending(OWNER), "suggestion_limit": 10}
    raise ValueError("Operación no permitida.")


def main():
    os.umask(0o077)
    if len(sys.argv) == 3 and sys.argv[1] == "--mail-worker":
        import re

        if not re.fullmatch(r"[a-f0-9]{10}", sys.argv[2]):
            return 1
        mail_worker(sys.argv[2])
        return 0
    try:
        raw = sys.stdin.buffer.read(16385)
        if len(raw) > 16384:
            raise ValueError("Solicitud demasiado grande.")
        request = json.loads(raw)
        if not isinstance(request, dict) or set(request) != {"name", "arguments"}:
            raise ValueError("Solicitud inválida.")
        value = {"ok": True, "data": dispatch(request["name"], request["arguments"])}
    except ValueError as error:
        # Only our validation messages; never serialize source exceptions.
        value = {
            "ok": False,
            "data": {
                "code": "INVALID_REQUEST",
                "message": str(error) if type(error) is ValueError else "Solicitud inválida.",
            },
        }
    except Exception:
        value = {"ok": False, "data": {"code": "APPLICATION_SERVICE_UNAVAILABLE"}}
    print(json.dumps(value, ensure_ascii=False, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
