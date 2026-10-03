#!/usr/bin/env python3
"""
job_runner.py - Small authenticated HTTP runner for n8n-triggered jobs.

This avoids n8n's Execute Command node and keeps the runnable surface
limited to an explicit allowlist.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
import subprocess
import time
import uuid
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from job_store import JobStore

LOG_PATH = Path(os.getenv("TENAZAS_JOB_RUNNER_LOG", str(Path(__file__).parent / "logs/job_runner.log")))
LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    filename=str(LOG_PATH),
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)
log = logging.getLogger("job_runner")

HOST = os.getenv("TENAZAS_JOB_RUNNER_HOST", "127.0.0.1")
PORT = int(os.getenv("TENAZAS_JOB_RUNNER_PORT", "18891"))
TOKEN = os.getenv("TENAZAS_JOB_RUNNER_TOKEN", "")
EXEC_CONTAINER = os.getenv("TENAZAS_JOB_EXEC_CONTAINER", "")
STORE = JobStore(os.getenv("TENAZAS_JOB_STORE", "/home/tenazas/job-state/jobs.sqlite"))
INSECURE_DEFAULT_TOKENS = {"", "tenazas-local-job-runner", "change-me", "replace-me"}


@dataclass(frozen=True)
class JobSpec:
    command: list[str]
    timeout: int


JOBS: dict[str, JobSpec] = {
    "worker_supervisor": JobSpec(
        ["python3", "/home/tenazas/runtime/worker_supervisor.py"],
        240,
    ),
    "health_sweep": JobSpec(
        ["python3", "/home/tenazas/runtime/health_sweep.py"],
        240,
    ),
    "gpu_keepalive": JobSpec(
        ["python3", "/home/tenazas/runtime/gpu_scheduler.py", "--keepalive"],
        90,
    ),
    "autofix_agent": JobSpec(
        ["python3", "/home/tenazas/runtime/autofix_agent.py"],
        1200,
    ),
    "proposal_executor": JobSpec(
        ["python3", "/home/tenazas/runtime/proposal_executor.py", "--once", "--json"],
        300,
    ),
    "portfolio_reconciler": JobSpec(
        ["python3", "/home/tenazas/runtime/portfolio_reconciler.py"],
        180,
    ),
    "iol_refresh": JobSpec(
        ["python3", "/home/tenazas/tools/iol_refresh.py"],
        120,
    ),
    "notification_calendar": JobSpec(
        ["python3", "/home/tenazas/runtime/notification_poller.py", "--calendar"],
        90,
    ),
    "notification_email": JobSpec(
        ["python3", "/home/tenazas/runtime/notification_poller.py", "--email"],
        180,
    ),
    "finance_daily": JobSpec(
        ["python3", "/home/tenazas/runtime/finance_daily.py"],
        180,
    ),
}


def _json_bytes(payload: dict) -> bytes:
    return json.dumps(payload, ensure_ascii=True).encode("utf-8")


def run_job(job_name: str, idempotency_key=None) -> dict:
    spec = JOBS[job_name]
    key = idempotency_key or str(uuid.uuid4())
    existing = STORE.claim(job_name, key, spec.timeout)
    if existing is not None:
        return existing
    try:
        result = _execute_job(job_name)
    except Exception as exc:
        result = {"ok": False, "job": job_name, "error": str(exc)[:300], "exit_code": 1}
    STORE.finish(key, result)
    return result


def _execute_job(job_name: str) -> dict:
    spec = JOBS.get(job_name)
    if spec is None:
        raise KeyError(job_name)

    started = time.time()
    try:
        proc = subprocess.run(
            (
                [
                    "docker",
                    "exec",
                    EXEC_CONTAINER,
                    "python3",
                    "/home/tenazas/runtime/job_entrypoint.py",
                    job_name,
                ]
                if EXEC_CONTAINER
                else spec.command
            ),
            capture_output=True,
            text=True,
            timeout=spec.timeout + 15 if EXEC_CONTAINER else spec.timeout,
            check=False,
        )
        elapsed_ms = int((time.time() - started) * 1000)
        stdout = proc.stdout[-4000:]
        stderr = proc.stderr[-4000:]
        result = {
            "ok": proc.returncode == 0,
            "job": job_name,
            "exit_code": proc.returncode,
            "elapsed_ms": elapsed_ms,
            "stdout": stdout,
            "stderr": stderr,
        }
        log.info("job=%s exit_code=%s elapsed_ms=%s", job_name, proc.returncode, elapsed_ms)
        return result
    except subprocess.TimeoutExpired as exc:
        elapsed_ms = int((time.time() - started) * 1000)
        stdout = exc.stdout.decode(errors="replace") if isinstance(exc.stdout, bytes) else exc.stdout or ""
        stderr = exc.stderr.decode(errors="replace") if isinstance(exc.stderr, bytes) else exc.stderr or ""
        stdout, stderr = stdout[-4000:], stderr[-4000:]
        log.warning("job=%s timed_out elapsed_ms=%s timeout=%s", job_name, elapsed_ms, spec.timeout)
        return {
            "ok": False,
            "job": job_name,
            "exit_code": 124,
            "elapsed_ms": elapsed_ms,
            "stdout": stdout,
            "stderr": stderr,
            "error": f"timed out after {spec.timeout}s",
        }


class Handler(BaseHTTPRequestHandler):
    server_version = "TenazasJobRunner/1.0"

    def _send(self, status: int, payload: dict) -> None:
        payload.setdefault("request_id", getattr(self, "request_id", ""))
        body = _json_bytes(payload)
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self) -> bool:
        if TOKEN in INSECURE_DEFAULT_TOKENS:
            self._send(
                HTTPStatus.SERVICE_UNAVAILABLE,
                {"ok": False, "error": "TENAZAS_JOB_RUNNER_TOKEN not securely configured"},
            )
            return False
        provided = self.headers.get("X-Tenazas-Job-Token", "")
        if not secrets.compare_digest(provided, TOKEN):
            self._send(HTTPStatus.UNAUTHORIZED, {"ok": False, "error": "unauthorized"})
            return False
        return True

    def do_GET(self) -> None:
        self.request_id = self.headers.get("X-Request-Id", str(uuid.uuid4()))
        parsed = urlparse(self.path)
        if parsed.path == "/health":
            self._send(
                HTTPStatus.OK,
                {
                    "ok": True,
                    "service": "job_runner",
                    "jobs": sorted(JOBS.keys()),
                },
            )
            return
        if parsed.path == "/status":
            if self._authorized():
                self._send(HTTPStatus.OK, STORE.summary())
            return
        self._send(HTTPStatus.NOT_FOUND, {"ok": False, "error": "not_found"})

    def do_POST(self) -> None:
        self.request_id = self.headers.get("X-Request-Id", str(uuid.uuid4()))
        parsed = urlparse(self.path)
        if not parsed.path.startswith("/run/"):
            self._send(HTTPStatus.NOT_FOUND, {"ok": False, "error": "not_found"})
            return
        if not self._authorized():
            return

        job_name = parsed.path.rsplit("/", 1)[-1]
        log.info(
            "request_id=%s job=%s remote=%s", self.request_id, job_name, self.client_address[0]
        )
        try:
            key = self.headers.get("Idempotency-Key")
            if key is not None and (not key or len(key) > 200):
                self._send(
                    HTTPStatus.BAD_REQUEST, {"ok": False, "error": "invalid idempotency key"}
                )
                return
            result = run_job(job_name, key) if key else run_job(job_name)
        except KeyError:
            self._send(HTTPStatus.NOT_FOUND, {"ok": False, "error": f"unknown_job:{job_name}"})
            return

        status = (
            HTTPStatus.CONFLICT
            if result.get("conflict")
            else HTTPStatus.OK
            if result["ok"]
            else HTTPStatus.BAD_GATEWAY
        )
        self._send(status, result)

    def log_message(self, fmt: str, *args) -> None:
        log.info("%s - %s", self.address_string(), fmt % args)


def main() -> None:
    if TOKEN in INSECURE_DEFAULT_TOKENS:
        raise SystemExit("TENAZAS_JOB_RUNNER_TOKEN must be set to a non-default secret")
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    log.info("job_runner listening on %s:%s jobs=%s", HOST, PORT, ",".join(sorted(JOBS.keys())))
    server.serve_forever()


if __name__ == "__main__":
    main()
