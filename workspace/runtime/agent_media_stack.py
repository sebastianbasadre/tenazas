#!/usr/bin/env python3
"""Media Stack Agent -- arr-stack and downloads health."""

import json
import subprocess
from pathlib import Path

from agent_protocol import Observation, Recommendation
from runtime_utils import now, save_json

BASE_DIR = Path(__file__).parent
STATE_DIR = Path("/home/tenazas/.openclaw/state") if Path("/home/tenazas").exists() else BASE_DIR.parent / "state"
AGENT_DIR = STATE_DIR / "agents"
STATUS_FILE = AGENT_DIR / "media_stack_status.json"
LOG_FILE = BASE_DIR / "logs" / "agent_media_stack.log"
ARR = {"gluetun", "qbittorrent", "sonarr", "radarr", "lidarr", "prowlarr",
       "seerr", "requestrr", "tautulli", "sabnzbd"}
SERVICE_CHECKS = {
    "sonarr": ("sonarr", "http://localhost:8989/ping"),
    "radarr": ("radarr", "http://localhost:7878/ping"),
    "lidarr": ("lidarr", "http://localhost:8686/ping"),
    "prowlarr": ("prowlarr", "http://localhost:9696/ping"),
    "sabnzbd": ("sabnzbd", "http://localhost:8080/"),
    "qbittorrent": ("qbittorrent", "http://localhost:8080/"),
    "seerr": ("seerr", "http://localhost:5055/"),
    "tautulli": ("tautulli", "http://localhost:8181/"),
}
SERVICE_TIMEOUT = 5

OWNERSHIP = {"media", "arr", "arr-stack", "sonarr", "radarr", "lidarr",
             "prowlarr", "qbittorrent", "torrent", "download", "descarga",
             "descargas", "sabnzbd", "seerr"}
PREFERRED_ROLE = "fast"


def _log(entry: dict):
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    entry["ts"] = now().isoformat()
    try:
        with open(LOG_FILE, "a") as f:
            f.write(json.dumps(entry, default=str) + "\n")
    except OSError:
        pass


def owns(topic: str) -> bool:
    q = topic.lower()
    return any(k in q for k in OWNERSHIP)


def _curl_from_container(container: str, url: str) -> dict:
    try:
        result = subprocess.run(
            ["docker", "exec", container, "curl", "-sf", "-o", "/dev/null",
             "-w", "%{http_code}", "--max-time", str(SERVICE_TIMEOUT), url],
            capture_output=True, text=True, timeout=SERVICE_TIMEOUT + 3,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        return {"container": container, "url": url, "ok": False, "error": str(exc)}

    code = (result.stdout or "").strip()
    return {
        "container": container,
        "url": url,
        "ok": result.returncode == 0 and code.startswith(("2", "3")),
        "code": code or None,
        "error": (result.stderr or "").strip()[:200],
    }


def _service_health(running: set[str]) -> list[dict]:
    checks = []
    for name, (container, url) in SERVICE_CHECKS.items():
        if container not in running:
            continue
        checks.append({"service": name, **_curl_from_container(container, url)})
    return checks


def assess() -> dict:
    containers = []
    try:
        from docker_wrapper import docker_ps
        containers = docker_ps()
    except Exception:
        containers = []
    arr = [c for c in containers if str(c.get("name", "")).lower() in ARR]
    seen = {str(c.get("name", "")).lower() for c in arr}
    missing = sorted(ARR - seen)
    running = {str(c.get("name", "")).lower() for c in arr if c.get("up")}
    down = [c for c in arr if not c.get("up")]
    service_checks = _service_health(running)
    unhealthy_services = [c for c in service_checks if not c.get("ok")]
    status = {"agent": "media_stack", "ts": now().isoformat(), "containers": len(arr),
              "expected": len(ARR), "up": len(arr) - len(down), "missing": missing,
              "down": down, "service_checks": service_checks,
              "unhealthy_services": unhealthy_services}
    save_json(str(STATUS_FILE), status)
    _log({"action": "assess", "containers": len(arr), "down": len(down),
          "missing": len(missing), "unhealthy_services": len(unhealthy_services)})
    return status


def handle(query: str, correlation_id: str = None):
    status = assess()
    if status["containers"]:
        summary = f"Media stack: {status['up']}/{status['expected']} containers up"
    else:
        summary = "Media stack: sin datos de Docker"
    if status["containers"] and status.get("missing"):
        summary += f" ({len(status['missing'])} missing)"
    if status["down"]:
        names = ", ".join(c.get("name", "?") for c in status["down"][:5])
        summary += f" ({names} down)"
    if status.get("unhealthy_services"):
        names = ", ".join(c.get("service", "?") for c in status["unhealthy_services"][:5])
        summary += f" ({names} health check failed)"
    has_issues = bool(status["down"] or status.get("missing") or status.get("unhealthy_services"))
    recs = [Recommendation("investigate", "arr_stack", "medium", False,
                           "Media stack has missing, down, or unhealthy services")] if has_issues else []
    obs = Observation("media_stack", "infra", summary, 0.75, status)
    _log({"action": "handle", "query": query[:80], "correlation_id": correlation_id})
    return obs, recs
