"""Run one allowlisted assistant task; stdout is one result object."""

import contextlib
import json
import signal
import sys
import threading

from assistant_jobs import Jobs
from runtime_config import load_config

CANCELLED = threading.Event()


def perform(job):
    import assistant_features as features
    payload, kind, chat = job["payload"], job["kind"], job["chat"]
    if kind == "daily":
        return {"text": features.daily(chat)}
    if kind == "weekly":
        return {"text": features.weekly(chat)}
    if kind == "application_digest":
        return features.application_digest(chat)
    if kind == "application_mail":
        from application_mail import scan
        return {"text": scan(chat)["text"]}
    if kind == "search":
        return {"text": features.search(payload.get("query", ""))}
    if kind == "cv":
        return features.prepare_cv(payload["opportunity_id"])
    if kind == "apply":
        return features.submit_application(payload)
    if kind == "documents":
        from document_library import query
        return {"text": query(payload["query"], chat)}
    if kind == "document_index":
        from document_library import ingest
        result = ingest(payload["path"], chat, payload["filename"])
        return {"text": f"Documento indexado: {payload['filename']} · {result['document']}\nConsultá /documentos tu pregunta."}
    if kind == "diagnose":
        return {"text": features.diagnose(payload.get("target", "homelab"))}
    if kind in {"routine_plan", "routine_execute"}:
        from verified_routines import plan, execute
        if kind == "routine_plan":
            return plan(payload["name"], load_config())
        if not payload.get("approval"):
            raise ValueError("Falta aprobación explícita")
        return execute(payload["prepared"], load_config(), CANCELLED.is_set,
                       lambda phase: Jobs().update(job["id"], phase=phase))
    raise ValueError("Tipo de trabajo no permitido")


def main():
    signal.signal(signal.SIGTERM, lambda *_: CANCELLED.set())
    job = Jobs().get(sys.argv[1])
    if not job or job["status"] != "running":
        raise SystemExit(2)
    try:
        with contextlib.redirect_stdout(sys.stderr):
            result = perform(job)
    except Exception as exc:
        result = {"ok": False, "text": str(exc)[:500]}
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
