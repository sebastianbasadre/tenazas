"""Supervised subprocess jobs; cancellation never retries external side effects."""

import json
import os
import signal
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

from assistant_jobs import Jobs, LABELS, ROOT
from assistant_schedule import tick
from process_lock import ProcessLock
from runtime_config import get_allowed_chat_ids, get_default_telegram_chat_id, get_telegram_bot_token
from telegram_transport import send_message, send_document

STOP = threading.Event()
BASE = Path(__file__).parent


def parent_death_guard():
    """A killed supervisor must not leave an external-action child running."""
    import ctypes
    parent = os.getppid()
    if ctypes.CDLL(None).prctl(1, signal.SIGKILL, 0, 0, 0) != 0:
        raise OSError("Could not install parent death signal")
    if os.getppid() != parent or parent == 1:
        os.kill(os.getpid(), signal.SIGKILL)


def terminate(process):
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=45)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=5)


def run_job(job, store):
    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
        process = subprocess.Popen([sys.executable, str(BASE / "assistant_tasks.py"), job["id"]],
                                   stdout=output, stderr=errors, start_new_session=True,
                                   preexec_fn=parent_death_guard if sys.platform == "linux" else None)
        started = time.monotonic()
        stopped = False
        reason = ""
        try:
            while process.poll() is None:
                status = store.get(job["id"])["status"]
                reason = "cancelled" if status == "cancel_requested" else "interrupted" if STOP.is_set() else "failed" if time.monotonic() - started > 600 else ""
                if output.tell() > 2_000_000 or errors.tell() > 5_000_000:
                    reason = "failed"
                if reason:
                    terminate(process)
                    stopped = True
                    break
                STOP.wait(.5)
            output.seek(0)
            try:
                result = json.loads(output.read(2_000_000))
            except (ValueError, UnicodeDecodeError):
                result = {"ok": False, "text": "El proceso terminó sin un resultado válido. No se reintentó."}
            if stopped:
                status = reason
                result["text"] = f"{LABELS[status]}. " + result.get("text", "")
                if job["kind"] in {"apply", "routine_execute"}:
                    result["text"] += "\nRevisá los efectos externos y el rollback antes de repetir."
            else:
                status = "waiting_approval" if result.get("approval_required") else "failed" if result.get("ok") is False or process.returncode else "done"
            store.finish(job["id"], status, result)
        finally:
            if process.poll() is None:
                terminate(process)


def deliver(job, store):
    allowed = get_allowed_chat_ids() or {get_default_telegram_chat_id()}
    if job["chat"] not in allowed:
        store.update(job["id"], delivery="blocked")
        return
    token = get_telegram_bot_token()
    if not token:
        return
    # Claim before network I/O. Ambiguous delivery is reviewed, never blindly resent.
    store.update(job["id"], delivery="sending")
    text = f"Trabajo {job['id']} · {LABELS[job['status']]}\n" + job["result"].get("text", job["phase"])
    if job["status"] == "waiting_approval":
        text += f"\nAprobar: /aprobar {job['id']}\nCancelar: /cancelar {job['id']}"
    try:
        for start in range(0, len(text), 3900):
            response = send_message(token, job["chat"], text[start:start + 3900])
            if not response or not response.get("ok"):
                raise RuntimeError("Telegram rechazó el mensaje")
        file = job["result"].get("file")
        if file:
            path = Path(file).resolve()
            if not path.is_relative_to((BASE / "jobs/tailored").resolve()):
                raise ValueError("Adjunto fuera del directorio de CV")
            response = send_document(token, job["chat"], path, caption="CV preparado para revisión")
            if not response or not response.get("ok"):
                raise RuntimeError("Telegram rechazó el archivo")
        store.update(job["id"], delivery="sent")
    except Exception:
        store.update(job["id"], delivery="uncertain")


def main():
    lock = ProcessLock(ROOT / "assistant/worker.lock")
    if not lock.acquire():
        raise SystemExit(1)
    store = Jobs()
    store.recover()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: STOP.set())
    try:
        while not STOP.is_set():
            tick()
            job = store.claim()
            if job:
                run_job(job, store)
            for item in store.deliveries():
                if STOP.is_set():
                    break
                deliver(item, store)
            STOP.wait(2)
    finally:
        lock.release()


if __name__ == "__main__":
    main()
