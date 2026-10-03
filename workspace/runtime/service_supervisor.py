"""Supervise services, health probes and the periodic runtime in one process."""

import logging
import os
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from logging.handlers import RotatingFileHandler
from pathlib import Path
from urllib.request import urlopen

from runtime_utils import write_atomic

BASE = Path(__file__).resolve().parent
LOGS = BASE / "logs"
STATE = LOGS / "service_state.json"
STOP = threading.Event()
STARTED_AT = time.time()


@dataclass
class Service:
    name: str
    command: list
    probe: str = ""
    interval: int = 0
    process: object = None
    started: float = 0
    next_start: float = 0
    restarts: int = 0
    failures: int = 0
    probe_failures: int = 0
    last_probe: float = 0
    last_success: float = 0
    logger: object = field(default=None, repr=False)


def enabled(name, default=True):
    return os.getenv(name, "true" if default else "false").lower() == "true"


def services():
    py = sys.executable
    definitions = [
        Service(
            "inference_gate", [py, str(BASE / "inference_gate.py")], "http://127.0.0.1:18902/health"
        ),
        Service("gateway", ["openclaw", "gateway"], "http://127.0.0.1:18789/health"),
        Service("metrics", [py, str(BASE / "metrics_server.py")], "http://127.0.0.1:9105/metrics"),
        Service("model_failover", [py, str(BASE / "model_failover.py")]),
        Service("telegram_router", [py, str(BASE / "telegram_router.py")]),
        Service("assistant_worker", [py, str(BASE / "assistant_worker.py")]),
        Service("job_search_watcher", [py, str(BASE / "job_search_watcher.py")]),
        Service(
            "portfolio_exporter",
            ["node", str(BASE / "portfolio-exporter.mjs")],
            "http://127.0.0.1:9101/metrics",
        ),
        Service(
            "network_exporter",
            [py, str(BASE / "network_exporter.py")],
            "http://127.0.0.1:9108/metrics",
        ),
        Service(
            "finance_exporter",
            [py, str(BASE / "finance_exporter.py")],
            "http://127.0.0.1:9109/metrics",
        ),
        Service(
            "eval_exporter",
            [py, str(BASE / "tool_tuning/eval_exporter.py")],
            "http://127.0.0.1:9107/metrics",
        ),
        Service(
            "api_exporter",
            [py, str(BASE / "api_exporter_service.py")],
            "http://127.0.0.1:9106/metrics",
        ),
    ]
    if enabled("ENABLE_LOCAL_JOB_RUNNER", False):
        definitions.append(
            Service(
                "job_runner", [py, str(BASE / "job_runner.py")], "http://127.0.0.1:18891/health"
            )
        )
    if enabled("ENABLE_RUNTIME_SCHEDULER"):
        definitions.append(
            Service("runtime_cycle", [py, str(BASE / "worker_supervisor.py")], interval=300)
        )
    if enabled("ENABLE_PORTFOLIO_RECONCILER_WATCH"):
        definitions.append(
            Service("portfolio_reconciler", [py, str(BASE / "portfolio_reconciler.py"), "--watch"])
        )
    if enabled("ENABLE_IOL_REFRESH_LOOP") or enabled("ENABLE_STARTUP_IOL_REFRESH"):
        definitions.append(
            Service(
                "iol_refresh",
                [py, "/home/tenazas/tools/iol_refresh.py"],
                interval=900 if enabled("ENABLE_IOL_REFRESH_LOOP") else 86400,
            )
        )
    return definitions


def _logger(name):
    logger = logging.getLogger("service." + name)
    logger.setLevel(logging.INFO)
    handler = RotatingFileHandler(LOGS / (name + ".service.log"), maxBytes=5_000_000, backupCount=3)
    handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    logger.addHandler(handler)
    return logger


def _drain(process, logger):
    for line in process.stdout:
        logger.info(line.rstrip()[:8000])
    process.stdout.close()


def start(service):
    service.started = time.monotonic()
    service.probe_failures = 0
    service.last_probe = service.started
    service.process = subprocess.Popen(
        service.command,
        cwd="/home/tenazas",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        errors="replace",
        start_new_session=True,
    )
    threading.Thread(target=_drain, args=(service.process, service.logger), daemon=True).start()
    service.logger.info("started pid=%s", service.process.pid)


def stop(service):
    process = service.process
    if process is None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=55 if service.name == "assistant_worker" else 5)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait(timeout=2)
    service.process = None


def check(service, current):
    process = service.process
    if process is not None and process.poll() is not None:
        code = process.returncode
        elapsed = current - service.started
        stop(service)
        if code == 0:
            service.last_success = time.time()
        if service.interval and code == 0:
            service.failures = 0
            service.next_start = current + max(1, service.interval - elapsed)
        else:
            service.restarts += 1
            service.failures = 1 if elapsed >= 300 else service.failures + 1
            delay = min(60, 5 * 2 ** min(service.failures - 1, 4))
            if service.interval:
                delay = max(delay, min(service.interval, 300))
            service.next_start = current + delay
            service.logger.warning("exited code=%s restart=%s", code, service.restarts)
    if service.process is None and current >= service.next_start:
        start(service)
    if service.process and service.probe and current - service.last_probe >= 30:
        service.last_probe = current
        try:
            with urlopen(service.probe, timeout=2) as response:
                if response.status != 200:
                    raise OSError("probe failed")
            service.probe_failures = 0
            service.last_success = time.time()
            if current - service.started >= 300:
                service.failures = 0
        except Exception:
            service.probe_failures += 1
            if current - service.started >= 90 and service.probe_failures >= 3:
                service.logger.warning("health probe failed; restarting service")
                stop(service)
                service.restarts += 1
                service.next_start = current + 5
    if service.process and service.name == "job_search_watcher" and current - service.started > 900:
        from runtime_utils import load_json

        entry = load_json(BASE / "logs/worker_heartbeat.json", {}).get("job_search_watcher", {})
        if time.time() - entry.get("ts", 0) > 900:
            service.logger.warning("job search heartbeat expired; restarting")
            stop(service)
            service.restarts += 1
            service.next_start = current + 5


def snapshot(definitions):
    memory = {}
    try:
        host = dict(line.split(":", 1) for line in Path("/proc/meminfo").read_text().splitlines())
        memory["host_total_bytes"] = int(host["MemTotal"].split()[0]) * 1024
        memory["host_available_bytes"] = int(host["MemAvailable"].split()[0]) * 1024
        rss = 0
        for proc in Path("/proc").iterdir():
            if not proc.name.isdigit():
                continue
            try:
                values = dict(line.split(":", 1) for line in (proc / "status").read_text().splitlines() if ":" in line)
                rss += int(values.get("VmRSS", "0").split()[0]) * 1024
            except (OSError, ValueError):
                continue
        memory["process_rss_sum_bytes"] = rss
    except (OSError, ValueError, KeyError):
        pass
    return {
        "ts": time.time(),
        "supervisor_started_at": STARTED_AT,
        "memory": memory,
        "services": {
            service.name: {
                "pid": service.process.pid if service.process else None,
                "running": bool(service.process and service.process.poll() is None),
                "restarts": service.restarts,
                "probe_failures": service.probe_failures,
                "last_success_ts": service.last_success,
                "next_start_monotonic": service.next_start,
            }
            for service in definitions
        },
    }


def main():
    LOGS.mkdir(parents=True, exist_ok=True)
    definitions = services()
    for service in definitions:
        service.logger = _logger(service.name)
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: STOP.set())
    try:
        while not STOP.is_set():
            for service in definitions:
                if STOP.is_set():
                    break
                check(service, time.monotonic())
            write_atomic(STATE, snapshot(definitions))
            STOP.wait(5)
    finally:
        for service in reversed(definitions):
            stop(service)


if __name__ == "__main__":
    main()
