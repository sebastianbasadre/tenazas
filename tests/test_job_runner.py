import sys
import tempfile
import unittest
from http import HTTPStatus
from pathlib import Path
from unittest.mock import patch


RUNTIME_DIR = Path(__file__).resolve().parents[1] / "workspace" / "runtime"
if str(RUNTIME_DIR) not in sys.path:
    sys.path.insert(0, str(RUNTIME_DIR))

import job_runner


class JobRunnerTests(unittest.TestCase):
    def test_unknown_job_raises_keyerror(self):
        with self.assertRaises(KeyError):
            job_runner.run_job("does_not_exist")

    def test_run_job_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "ok.py"
            script.write_text("print('ok from test')\n", encoding="ascii")
            original = job_runner.JOBS
            job_runner.JOBS = {"test_ok": job_runner.JobSpec(["python3", str(script)], 5)}
            try:
                result = job_runner.run_job("test_ok")
            finally:
                job_runner.JOBS = original

        self.assertTrue(result["ok"])
        self.assertEqual(result["exit_code"], 0)
        self.assertIn("ok from test", result["stdout"])


class _HandlerHarness:
    def __init__(self, path: str, headers: dict[str, str] | None = None):
        self.path = path
        self.headers = headers or {}
        self.status = None
        self.sent_headers = []
        self.body = b""
        self.wfile = self
        self.client_address = ("127.0.0.1", 12345)
        self._send = job_runner.Handler._send.__get__(self, _HandlerHarness)
        self._authorized = job_runner.Handler._authorized.__get__(self, _HandlerHarness)

    def send_response(self, status: int) -> None:
        self.status = status

    def send_header(self, name: str, value: str) -> None:
        self.sent_headers.append((name, value))

    def end_headers(self) -> None:
        return None

    def write(self, data: bytes) -> int:
        self.body += data
        return len(data)

    def address_string(self) -> str:
        return "127.0.0.1"


class JobRunnerHandlerTests(unittest.TestCase):
    def test_health_endpoint_lists_jobs(self):
        harness = _HandlerHarness("/health")
        job_runner.Handler.do_GET(harness)
        self.assertEqual(harness.status, HTTPStatus.OK)
        self.assertIn(b'"service": "job_runner"', harness.body)
        self.assertIn(b"finance_daily", harness.body)

    def test_post_rejects_when_token_missing(self):
        harness = _HandlerHarness("/run/finance_daily")
        with patch.object(job_runner, "TOKEN", ""):
            job_runner.Handler.do_POST(harness)
        self.assertEqual(harness.status, HTTPStatus.SERVICE_UNAVAILABLE)
        self.assertIn(b"TENAZAS_JOB_RUNNER_TOKEN not securely configured", harness.body)

    def test_post_rejects_default_token(self):
        harness = _HandlerHarness(
            "/run/finance_daily",
            headers={"X-Tenazas-Job-Token": "tenazas-local-job-runner"},
        )
        with patch.object(job_runner, "TOKEN", "tenazas-local-job-runner"):
            job_runner.Handler.do_POST(harness)
        self.assertEqual(harness.status, HTTPStatus.SERVICE_UNAVAILABLE)

    def test_post_rejects_invalid_token(self):
        harness = _HandlerHarness(
            "/run/finance_daily",
            headers={"X-Tenazas-Job-Token": "wrong"},
        )
        with patch.object(job_runner, "TOKEN", "secret"):
            job_runner.Handler.do_POST(harness)
        self.assertEqual(harness.status, HTTPStatus.UNAUTHORIZED)
        self.assertIn(b"unauthorized", harness.body)

    def test_post_unknown_job_returns_404(self):
        harness = _HandlerHarness(
            "/run/does_not_exist",
            headers={"X-Tenazas-Job-Token": "secret"},
        )
        with patch.object(job_runner, "TOKEN", "secret"):
            job_runner.Handler.do_POST(harness)
        self.assertEqual(harness.status, HTTPStatus.NOT_FOUND)
        self.assertIn(b"unknown_job:does_not_exist", harness.body)

    def test_post_runs_job_and_returns_payload(self):
        harness = _HandlerHarness(
            "/run/finance_daily",
            headers={"X-Tenazas-Job-Token": "secret"},
        )
        with patch.object(job_runner, "TOKEN", "secret"):
            with patch.object(job_runner, "run_job", return_value={
                "ok": True,
                "job": "finance_daily",
                "exit_code": 0,
                "elapsed_ms": 12,
                "stdout": "ok",
                "stderr": "",
            }):
                job_runner.Handler.do_POST(harness)
        self.assertEqual(harness.status, HTTPStatus.OK)
        self.assertIn(b'"job": "finance_daily"', harness.body)
        self.assertIn(b'"ok": true', harness.body)


if __name__ == "__main__":
    unittest.main()
