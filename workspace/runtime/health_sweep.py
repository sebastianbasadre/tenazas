#!/usr/bin/env python3
"""
health_sweep.py — Full-stack health sweep for Tenazas.

Probes NAS, HomeLab, external APIs, internal services, and dashboard
cache freshness. Reports to Telegram and persists to SQLite.

100% additive — does NOT modify any existing module.
Stdlib only — no requests, httpx, aiohttp.

Usage:
  python3 health_sweep.py                # one-shot, notify Telegram
  python3 health_sweep.py --no-telegram  # one-shot, SQLite only
  python3 health_sweep.py --watch        # loop every 5 min
  python3 health_sweep.py --json         # one-shot, print JSON
"""

import argparse
import json
import logging
import os
import re
import socket
import sqlite3
import subprocess
import ssl
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

try:
    from .state_paths import private_state_path
except ImportError:
    from state_paths import private_state_path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

try:
    from .runtime_config import (
        CHROMADB_URL, GLUETUN_PROXY, GRAFANA_URL, HOMELAB_IP, PROMETHEUS_URL,
        get_default_telegram_chat_id,
    )
except ImportError:
    from runtime_config import (
        CHROMADB_URL, GLUETUN_PROXY, GRAFANA_URL, HOMELAB_IP, PROMETHEUS_URL,
        get_default_telegram_chat_id,
    )

# ── Paths ───────────────────────────────────────────────────────

_IN_CONTAINER = Path("/home/tenazas").exists()
BASE_DIR = Path(__file__).parent
CONFIG_PATH = Path("/home/tenazas/.openclaw/openclaw.json") if _IN_CONTAINER else BASE_DIR.parent / "state" / "openclaw.json"
STATE_DIR = Path("/home/tenazas/.openclaw/state") if _IN_CONTAINER else BASE_DIR.parent / "state"
DB_PATH = STATE_DIR / "health_sweeps.sqlite"
CHAT_ID = get_default_telegram_chat_id()
GRAFANA_AUTH = "YWRtaW46dGVuYXphcw=="  # admin:tenazas base64

# ── Config ──────────────────────────────────────────────────────

PROBE_TIMEOUT = 3       # seconds per network probe
DOCKER_TIMEOUT = 10     # seconds for local docker CLI calls on Synology
ARR_CURL_TIMEOUT = 5    # seconds for curl inside arr-stack containers
SWEEP_MAX = 30          # seconds total sweep budget
MAX_WORKERS = 8
WATCH_INTERVAL = 300    # 5 min

# Expected models on HomeLab Ollama.
# Keep this aligned with the models that are actually operationally required.
EXPECTED_MODELS = {"qwen2.5:7b", "qwen3:8b", "nomic-embed-text"}

# HomeLab IPs
HOMELAB_IPS = [HOMELAB_IP]
HOMELAB_OLLAMA_PORT = 11434

# ── Logging ─────────────────────────────────────────────────────

log = logging.getLogger("health_sweep")

# ── Helpers ─────────────────────────────────────────────────────

def _load_config() -> dict:
    try:
        return json.loads(CONFIG_PATH.read_text())
    except Exception:
        return {}


def _http_get(url: str, timeout: float = PROBE_TIMEOUT,
              headers: dict | None = None) -> tuple[int, str, float]:
    """GET url → (status, body, latency_ms). Raises on failure."""
    req = Request(url)
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    t0 = time.monotonic()
    resp = urlopen(req, timeout=timeout, context=ctx)
    lat = (time.monotonic() - t0) * 1000
    body = resp.read().decode("utf-8", errors="replace")
    return resp.status, body, lat


def _tcp_ping(host: str, port: int, timeout: float = 2.0) -> float:
    """TCP connect → latency_ms. Raises on failure."""
    t0 = time.monotonic()
    sock = socket.create_connection((host, port), timeout=timeout)
    lat = (time.monotonic() - t0) * 1000
    sock.close()
    return lat


def _run_command(args: list[str], timeout: float = DOCKER_TIMEOUT) -> tuple[subprocess.CompletedProcess[str], float]:
    """Run a local command and capture elapsed time in ms."""
    t0 = time.monotonic()
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    lat = (time.monotonic() - t0) * 1000
    return result, lat


def _docker_ps_names() -> tuple[set[str], float]:
    """Return running docker container names and elapsed time in ms."""
    result, lat = _run_command(["docker", "ps", "--format", "{{.Names}}"])
    if result.returncode != 0:
        raise RuntimeError(f"docker ps exit {result.returncode}")
    names = {n.strip() for n in result.stdout.strip().splitlines() if n.strip()}
    return names, lat


def _sanitize_detail(detail: dict) -> dict:
    """Strip secrets from detail dict before persisting."""
    s = json.dumps(detail)
    # Match key-value patterns where the KEY is a sensitive field name
    s = re.sub(r'("(?:bot_?token|api_?key|secret_?key|password|bearer|credentials)":\s*")([\w\-:.]+)',
               r'\1***', s, flags=re.IGNORECASE)
    try:
        return json.loads(s)
    except Exception:
        return {"sanitized": True}


def _normalize_model_name(name: str) -> str:
    """Normalize Ollama tag names so `foo` and `foo:latest` compare equally."""
    return name[:-7] if name.endswith(":latest") else name


def _notify_telegram(msg: str):
    """Send message to Telegram."""
    try:
        cfg = _load_config()
        token = cfg["channels"]["telegram"]["botToken"]
        payload = json.dumps({
            "chat_id": CHAT_ID,
            "text": msg[:4090],
            "parse_mode": "Markdown",
        }).encode()
        urlopen(Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=payload,
            headers={"Content-Type": "application/json"},
        ), timeout=10)
    except Exception as e:
        # Retry without Markdown
        try:
            cfg = _load_config()
            token = cfg["channels"]["telegram"]["botToken"]
            payload = json.dumps({
                "chat_id": CHAT_ID,
                "text": msg[:4090],
            }).encode()
            urlopen(Request(
                f"https://api.telegram.org/bot{token}/sendMessage",
                data=payload,
                headers={"Content-Type": "application/json"},
            ), timeout=10)
        except Exception:
            log.error("notify failed: %s", e)


# ── Probe result ────────────────────────────────────────────────

def _ok(probe: str, latency_ms: float = 0, detail: dict | None = None) -> dict:
    return {"probe": probe, "status": "ok", "latency_ms": round(latency_ms, 1),
            "detail": detail or {}}

def _warn(probe: str, msg: str, latency_ms: float = 0, detail: dict | None = None) -> dict:
    return {"probe": probe, "status": "warn", "latency_ms": round(latency_ms, 1),
            "detail": {**(detail or {}), "message": msg}}

def _fail(probe: str, msg: str, latency_ms: float = 0, detail: dict | None = None) -> dict:
    return {"probe": probe, "status": "fail", "latency_ms": round(latency_ms, 1),
            "detail": {**(detail or {}), "message": msg}}


# ════════════════════════════════════════════════════════════════
#  SECTION 1: NAS
# ════════════════════════════════════════════════════════════════

def _probe_docker_socket() -> dict:
    """Check Docker daemon is alive via docker ps."""
    try:
        names, lat = _docker_ps_names()
        return _ok("docker_socket", lat, {"containers_up": len(names)})
    except Exception as e:
        return _fail("docker_socket", str(e))


def _probe_containers() -> dict:
    """Check expected containers are running."""
    try:
        cfg = _load_config()
        expected = cfg.get("expected_containers", [
            "openclaw", "gluetun", "qbittorrent", "sonarr", "radarr",
            "lidarr", "prowlarr", "sabnzbd", "seerr", "requestrr",
            "tautulli", "chromadb", "piper", "prometheus", "grafana",
            "portainer", "watchtower",
        ])
        running, lat = _docker_ps_names()
        running = {n.lower() for n in running}
        missing = [c for c in expected if c.lower() not in running]
        if missing:
            return _warn("containers", f"missing: {', '.join(missing)}", lat,
                         {"expected": len(expected), "up": len(expected) - len(missing),
                          "missing": missing})
        return _ok("containers", lat, {"expected": len(expected), "up": len(expected)})
    except Exception as e:
        return _fail("containers", str(e))


def _probe_chromadb() -> dict:
    try:
        status, body, lat = _http_get(f"{CHROMADB_URL}/api/v2/heartbeat")
        return _ok("chromadb", lat) if status == 200 else _fail("chromadb", f"status {status}", lat)
    except Exception as e:
        return _fail("chromadb", str(e))


def _probe_prometheus() -> dict:
    try:
        status, body, lat = _http_get(f"{PROMETHEUS_URL}/-/healthy")
        if status != 200:
            return _fail("prometheus", f"healthy={status}", lat)
        s2, _, l2 = _http_get(f"{PROMETHEUS_URL}/-/ready")
        if s2 != 200:
            return _warn("prometheus", "healthy but not ready", lat)
        return _ok("prometheus", lat)
    except Exception as e:
        return _fail("prometheus", str(e))


def _probe_grafana() -> dict:
    try:
        status, body, lat = _http_get(f"{GRAFANA_URL}/api/health")
        if status == 200:
            try:
                data = json.loads(body)
                db = data.get("database", "unknown")
                if db != "ok":
                    return _warn("grafana", f"db={db}", lat)
            except Exception:
                pass
            return _ok("grafana", lat)
        return _fail("grafana", f"status {status}", lat)
    except Exception as e:
        return _fail("grafana", str(e))


def _probe_piper() -> dict:
    """Check Piper TTS container is running."""
    try:
        r, lat = _run_command(
            ["docker", "inspect", "--format", "{{.State.Running}}", "piper"])
        if "true" in r.stdout.lower():
            return _ok("piper", lat)
        return _fail("piper", "not running", lat)
    except Exception as e:
        return _fail("piper", str(e))


def _probe_vpn() -> dict:
    """Check VPN via ip-api.com — country must be ES."""
    try:
        import urllib.request
        proxy = urllib.request.ProxyHandler({"http": GLUETUN_PROXY})
        opener = urllib.request.build_opener(proxy)
        t0 = time.monotonic()
        resp = opener.open("http://ip-api.com/json", timeout=PROBE_TIMEOUT)
        lat = (time.monotonic() - t0) * 1000
        data = json.loads(resp.read())
        country = data.get("countryCode", "??")
        if country == "ES":
            return _ok("vpn", lat, {"country": country, "ip": data.get("query", "?")})
        return _fail("vpn", f"LEAK — country={country}", lat,
                     {"country": country, "ip": data.get("query", "?"),
                      "suggestion": "docker restart gluetun"})
    except Exception as e:
        return _fail("vpn", str(e), detail={"suggestion": "docker restart gluetun"})


def _probe_ollama_nas() -> dict:
    """Check NAS CPU Ollama fallback."""
    try:
        status, body, lat = _http_get("http://127.0.0.1:11434/api/tags")
        return _ok("ollama_nas", lat) if status == 200 else _ok("ollama_nas", lat, {"status": status, "expected_optional": True})
    except Exception:
        return _ok("ollama_nas", 0, {"status": "offline", "expected_optional": True})


def _probe_arr_connectivity() -> dict:
    """Check arr-stack services can talk to each other via gluetun network.

    Prowlarr must reach Sonarr (:8989) and Radarr (:7878) on localhost
    inside the shared gluetun network namespace. If this fails, the
    containers appear 'Up' but indexers and requests don't work.
    """
    try:
        t0 = time.monotonic()
        # From Prowlarr, curl Sonarr and Radarr via localhost (shared gluetun net)
        checks = {
            "sonarr": ("prowlarr", "http://localhost:8989/ping"),
            "radarr": ("prowlarr", "http://localhost:7878/ping"),
        }
        failed = []
        for name, (container, url) in checks.items():
            r, _ = _run_command(
                ["docker", "exec", container, "curl", "-sf", "-o", "/dev/null",
                 "-w", "%{http_code}", "--max-time", str(ARR_CURL_TIMEOUT), url],
                timeout=ARR_CURL_TIMEOUT + 3)
            code = r.stdout.strip()
            if code != "200":
                failed.append(f"{name}({code})")

        lat = (time.monotonic() - t0) * 1000
        if failed:
            return _fail("arr_connectivity",
                         f"broken: {', '.join(failed)} — restart gluetun + arr-stack",
                         lat, {"failed": failed,
                               "suggestion": "docker restart gluetun && sleep 15 && docker restart sonarr radarr prowlarr seerr requestrr"})
        return _ok("arr_connectivity", lat, {"checked": list(checks.keys())})
    except Exception as e:
        return _fail("arr_connectivity", str(e))


def sweep_nas() -> dict:
    probes = [
        _probe_docker_socket, _probe_containers, _probe_chromadb,
        _probe_prometheus, _probe_grafana, _probe_piper,
        _probe_vpn, _probe_ollama_nas, _probe_arr_connectivity,
    ]
    results = _run_probes(probes)
    return {"section": "nas", "results": results}


# ════════════════════════════════════════════════════════════════
#  SECTION 2: HOMELAB
# ════════════════════════════════════════════════════════════════

def _probe_ollama_gpu() -> dict:
    """Check Ollama GPU — try both IPs, validate expected models."""
    for ip in HOMELAB_IPS:
        try:
            url = f"http://{ip}:{HOMELAB_OLLAMA_PORT}/api/tags"
            status, body, lat = _http_get(url)
            if status == 200:
                data = json.loads(body)
                model_names = {
                    _normalize_model_name(m.get("name", ""))
                    for m in data.get("models", [])
                    if m.get("name")
                }
                missing = EXPECTED_MODELS - model_names
                detail = {"ip": ip, "models": len(model_names),
                          "model_list": sorted(model_names)[:10]}
                if missing:
                    detail["missing_models"] = sorted(missing)
                    return _warn("ollama_gpu", f"missing: {', '.join(sorted(missing))}", lat, detail)
                return _ok("ollama_gpu", lat, detail)
        except Exception:
            continue
    return _fail("ollama_gpu", "unreachable on both IPs",
                 detail={"ips_tried": HOMELAB_IPS})


def _probe_ollama_ps() -> dict:
    """Check what models are loaded in VRAM."""
    for ip in HOMELAB_IPS:
        try:
            url = f"http://{ip}:{HOMELAB_OLLAMA_PORT}/api/ps"
            status, body, lat = _http_get(url)
            if status == 200:
                data = json.loads(body)
                loaded = data.get("models", [])
                names = [m.get("name", "?") for m in loaded]
                vram_total = sum(m.get("size", 0) for m in loaded)
                return _ok("ollama_vram", lat, {
                    "loaded": names, "count": len(names),
                    "vram_used_gb": round(vram_total / 1e9, 1)})
        except Exception:
            continue
    return _fail("ollama_vram", "unreachable")


def _probe_homelab_tcp() -> dict:
    """TCP ping to Ollama port on both IPs."""
    results = {}
    for ip in HOMELAB_IPS:
        try:
            lat = _tcp_ping(ip, HOMELAB_OLLAMA_PORT)
            results[ip] = round(lat, 1)
        except Exception:
            results[ip] = None
    reachable = {ip: lat for ip, lat in results.items() if lat is not None}
    if not reachable:
        return _fail("homelab_tcp", "both IPs unreachable", detail={"pings": results})
    if len(reachable) < len(HOMELAB_IPS):
        unreachable = [ip for ip, lat in results.items() if lat is None]
        return _warn("homelab_tcp", f"routing issue: {unreachable[0]} unreachable",
                     list(reachable.values())[0], {"pings": results})
    return _ok("homelab_tcp", min(reachable.values()), {"pings": results})


def _probe_gpu_exporter() -> dict:
    """Check nvidia-gpu-exporter on :9835 (if it exists)."""
    for ip in HOMELAB_IPS:
        try:
            status, body, lat = _http_get(f"http://{ip}:9835/metrics")
            if status == 200:
                detail = {"ip": ip}
                # Try to parse GPU temp and VRAM
                for line in body.split("\n"):
                    if "gpu_temperature" in line.lower() and not line.startswith("#"):
                        try:
                            detail["gpu_temp_c"] = float(line.split()[-1])
                        except Exception:
                            pass
                    if "memory_used" in line.lower() and not line.startswith("#"):
                        try:
                            detail["vram_used_bytes"] = float(line.split()[-1])
                        except Exception:
                            pass
                return _ok("gpu_exporter", lat, detail)
        except Exception:
            continue
    return _ok("gpu_exporter", 0,
               {"status": "not_installed", "optional": True,
                "suggestion": "Install nvidia-gpu-exporter on HomeLab"})


def sweep_homelab() -> dict:
    probes = [_probe_ollama_gpu, _probe_ollama_ps, _probe_homelab_tcp, _probe_gpu_exporter]
    results = _run_probes(probes)
    return {"section": "homelab", "results": results}


# ════════════════════════════════════════════════════════════════
#  SECTION 3: EXTERNAL APIs
# ════════════════════════════════════════════════════════════════

def _probe_binance_ping() -> dict:
    try:
        import urllib.request
        proxy = urllib.request.ProxyHandler({"https": GLUETUN_PROXY})
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        handler = urllib.request.HTTPSHandler(context=ctx)
        opener = urllib.request.build_opener(proxy, handler)
        t0 = time.monotonic()
        resp = opener.open(Request("https://api.binance.com/api/v3/ping"),
                           timeout=PROBE_TIMEOUT)
        lat = (time.monotonic() - t0) * 1000
        body = resp.read().decode()
        if body.strip() == "{}":
            return _ok("binance_ping", lat)
        return _warn("binance_ping", f"unexpected: {body[:50]}", lat)
    except Exception as e:
        return _fail("binance_ping", str(e))


def _probe_binance_ticker() -> dict:
    try:
        import urllib.request
        proxy = urllib.request.ProxyHandler({"https": GLUETUN_PROXY})
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        handler = urllib.request.HTTPSHandler(context=ctx)
        opener = urllib.request.build_opener(proxy, handler)
        url = "https://api.binance.com/api/v3/ticker/24hr?symbol=BTCUSDT"
        t0 = time.monotonic()
        resp = opener.open(Request(url, headers={"Cache-Control": "no-cache, no-store"}),
                           timeout=PROBE_TIMEOUT)
        lat = (time.monotonic() - t0) * 1000
        data = json.loads(resp.read())
        price = data.get("lastPrice")
        if price:
            return _ok("binance_ticker", lat, {"btc_usd": price})
        return _warn("binance_ticker", "no lastPrice", lat)
    except Exception as e:
        return _fail("binance_ticker", str(e))


def _probe_iol() -> dict:
    try:
        status, body, lat = _http_get("https://api.invertironline.com/", timeout=PROBE_TIMEOUT)
        # 401/403 = auth/resource denied but API reachable, 200 = also fine.
        if status in (200, 401, 403):
            return _ok("iol_api", lat, {"status": status})
        if status >= 500:
            return _fail("iol_api", f"server error {status}", lat)
        return _ok("iol_api", lat, {"status": status})
    except HTTPError as e:
        if e.code in (401, 403):
            return _ok("iol_api", 0, {"status": e.code, "auth_required": True})
        if e.code >= 500:
            return _fail("iol_api", f"server error {e.code}", 0)
        return _ok("iol_api", 0, {"status": e.code})
    except Exception as e:
        return _fail("iol_api", str(e))


def _probe_gemini() -> dict:
    """Legacy Gemini check.

    Gemini is no longer treated as an operational dependency by default.
    Keep the probe available for operators who explicitly want it, but
    downgrade missing keys/quota exhaustion to optional info so it does not
    pollute the health signal when Kimi/OpenRouter or local Ollama are the
    intended cloud/local paths.
    """
    try:
        cfg = _load_config()
        enabled = str(
            os.environ.get("TENAZAS_ENABLE_GEMINI_HEALTHCHECK",
                           cfg.get("enable_gemini_healthcheck", "false"))
        ).strip().lower() in {"1", "true", "yes", "on"}
        if not enabled:
            return _ok("gemini", 0, {"status": "disabled", "optional": True,
                                     "provider": "gemini"})

        # Try tenazas.json first for Gemini key
        _tcfg_path = Path("/home/tenazas/.openclaw/tenazas.json") if _IN_CONTAINER else BASE_DIR.parent / "state" / "tenazas.json"
        api_key = ""
        try:
            tcfg = json.loads(_tcfg_path.read_text()) if _tcfg_path.exists() else {}
            api_key = tcfg.get("google_api_key", "")
        except Exception:
            pass
        if not api_key:
            api_key = os.environ.get("GOOGLE_API_KEY", "")
        if not api_key:
            return _ok("gemini", 0, {"status": "no_api_key", "optional": True,
                                     "provider": "gemini"})

        url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={api_key}"
        payload = json.dumps({
            "contents": [{"parts": [{"text": "ok"}]}],
            "generationConfig": {"maxOutputTokens": 5},
        }).encode()
        t0 = time.monotonic()
        resp = urlopen(Request(url, data=payload,
                               headers={"Content-Type": "application/json"}),
                       timeout=5)
        lat = (time.monotonic() - t0) * 1000
        data = json.loads(resp.read())
        if "candidates" in data:
            return _ok("gemini", lat)
        return _ok("gemini", lat, {"status": "unexpected_response",
                                   "optional": True, "provider": "gemini"})
    except URLError as e:
        msg = str(e)
        if "429" in msg or "RESOURCE_EXHAUSTED" in msg:
            return _ok("gemini", 0, {"status": "quota_exhausted", "optional": True,
                                     "provider": "gemini",
                                     "suggestion": "Wait or check billing"})
        return _fail("gemini", msg)
    except Exception as e:
        return _fail("gemini", str(e))


def _probe_telegram_bot() -> dict:
    try:
        cfg = _load_config()
        token = cfg.get("channels", {}).get("telegram", {}).get("botToken", "")
        if not token:
            return _warn("telegram_bot", "no token configured")
        status, body, lat = _http_get(f"https://api.telegram.org/bot{token}/getMe")
        data = json.loads(body)
        if data.get("ok"):
            return _ok("telegram_bot", lat, {"username": data.get("result", {}).get("username", "?")})
        return _fail("telegram_bot", "getMe returned ok=false", lat)
    except Exception as e:
        return _fail("telegram_bot", str(e))


def _probe_openmeteo() -> dict:
    try:
        url = "https://api.open-meteo.com/v1/forecast?latitude=-34.75&longitude=-58.4&current=temperature_2m"
        status, body, lat = _http_get(url)
        if status == 200:
            data = json.loads(body)
            temp = data.get("current", {}).get("temperature_2m")
            return _ok("openmeteo", lat, {"temp_c": temp})
        return _fail("openmeteo", f"status {status}", lat)
    except Exception as e:
        return _fail("openmeteo", str(e))


def sweep_apis() -> dict:
    probes = [
        _probe_binance_ping, _probe_binance_ticker, _probe_iol,
        _probe_gemini, _probe_telegram_bot, _probe_openmeteo,
    ]
    results = _run_probes(probes)
    return {"section": "apis", "results": results}


# ════════════════════════════════════════════════════════════════
#  SECTION 4: INTERNAL
# ════════════════════════════════════════════════════════════════

def _probe_freeze_detector() -> dict:
    """Check last heartbeat is recent."""
    hb_paths = [
        BASE_DIR / "logs" / "heartbeat.json",
        BASE_DIR.parent / "state" / "heartbeat.json",
        Path("/tmp/tenazas_heartbeat"),
    ]
    for p in hb_paths:
        try:
            if p.exists():
                mtime = p.stat().st_mtime
                age = time.time() - mtime
                if age < 600:  # 10 min (2 cron cycles)
                    return _ok("freeze_detector", 0, {"age_s": round(age), "path": str(p)})
                return _warn("freeze_detector", f"heartbeat stale ({round(age)}s)", 0,
                             {"age_s": round(age)})
        except Exception:
            continue
    return _ok("freeze_detector", 0, {"status": "no_heartbeat_file", "optional": True})


def _probe_rag() -> dict:
    """Smoke-test ChromaDB is reachable for RAG."""
    try:
        # Heartbeat confirms ChromaDB is alive (collection mgmt is via Python client)
        status, body, lat = _http_get(f"{CHROMADB_URL}/api/v2/heartbeat")
        if status != 200:
            return _fail("rag", f"ChromaDB heartbeat status {status}", lat)
        return _ok("rag", lat)
    except Exception as e:
        return _fail("rag", str(e))


def _probe_memory_stores() -> dict:
    """Check memory store sizes."""
    memory_dir = BASE_DIR / "logs"
    stores = {}
    total_bytes = 0
    bloat_warn = []
    for name in ["memory_log.json", "shared_memory.json", "patterns.json",
                 "events.json", "state.json"]:
        p = private_state_path(BASE_DIR) if name == "state.json" else memory_dir / name
        if name == "state.json" and not p.is_file():
            raise RuntimeError("Private runtime state unavailable")
        if name != "state.json" and not p.exists():
            p = BASE_DIR / name
        if p.exists():
            sz = p.stat().st_size
            stores[name] = sz
            total_bytes += sz
            if sz > 2 * 1024 * 1024 * 1024:  # 2 GB
                bloat_warn.append(f"{name}={sz // (1024*1024)}MB")

    if bloat_warn:
        return _warn("memory_stores", f"bloat: {', '.join(bloat_warn)}", 0,
                     {"sizes": stores, "total_bytes": total_bytes})
    return _ok("memory_stores", 0, {"sizes": stores, "total_bytes": total_bytes})


def _probe_orchestrator() -> dict:
    """Smoke-test: import orchestrator and classify a dummy query."""
    try:
        t0 = time.monotonic()
        sys.path.insert(0, str(BASE_DIR))
        from orchestrator import classify_query
        result = classify_query("hola que tal")
        lat = (time.monotonic() - t0) * 1000
        cat = result if isinstance(result, str) else result.get("category", str(result))
        if cat == "chat":
            return _ok("orchestrator", lat, {"classified_as": cat})
        return _warn("orchestrator", f"expected chat, got {cat}", lat)
    except Exception as e:
        return _fail("orchestrator", str(e))


def _probe_metrics_exporter() -> dict:
    """Check :9105/metrics returns valid Prometheus format."""
    try:
        status, body, lat = _http_get("http://127.0.0.1:9105/metrics")
        if status != 200:
            return _fail("metrics_exporter", f"status {status}", lat)
        # Validate it has Prometheus-style lines
        metric_lines = [l for l in body.split("\n")
                        if l and not l.startswith("#")]
        if len(metric_lines) > 0:
            return _ok("metrics_exporter", lat, {"metric_count": len(metric_lines)})
        return _warn("metrics_exporter", "no metrics found", lat)
    except Exception as e:
        return _fail("metrics_exporter", str(e))


def sweep_internal() -> dict:
    probes = [
        _probe_freeze_detector, _probe_rag, _probe_memory_stores,
        _probe_orchestrator, _probe_metrics_exporter,
    ]
    results = _run_probes(probes)
    return {"section": "internal", "results": results}


# ════════════════════════════════════════════════════════════════
#  SECTION 5: DASHBOARD CACHE DETECTION
# ════════════════════════════════════════════════════════════════

def _probe_grafana_cache() -> dict:
    """Detect stale Grafana cache by comparing cached vs fresh Prometheus queries."""
    try:
        base = f"{GRAFANA_URL}/api/datasources/proxy/uid/afhjgavdgav40d/api/v1/query"
        query = "?query=up"
        auth = {"Authorization": f"Basic {GRAFANA_AUTH}"}

        # Fresh request
        status1, body_fresh, lat1 = _http_get(
            base + query, headers={**auth, "Cache-Control": "no-cache, no-store"})
        if status1 == 401:
            return _ok("grafana_cache", lat1, {"status": 401, "auth_required": True})
        data_fresh = json.loads(body_fresh)

        # Potentially cached request (no cache-bust headers)
        status2, body_cached, lat2 = _http_get(base + query, headers=auth)
        if status2 == 401:
            return _ok("grafana_cache", lat2, {"status": 401, "auth_required": True})
        data_cached = json.loads(body_cached)

        # Extract timestamps
        try:
            ts_fresh = float(data_fresh["data"]["result"][0]["value"][0])
            ts_cached = float(data_cached["data"]["result"][0]["value"][0])
        except (KeyError, IndexError, TypeError):
            return _warn("grafana_cache", "could not extract timestamps", lat1)

        drift = abs(ts_fresh - ts_cached)
        now = time.time()
        freshness = abs(now - ts_fresh)

        detail = {"drift_s": round(drift, 1), "freshness_s": round(freshness, 1)}

        if drift > 120:
            return _fail("grafana_cache", f"CACHE STALE — drift {round(drift)}s", lat1,
                         {**detail, "suggestion": "docker restart grafana"})
        if freshness > 120:
            return _warn("grafana_cache",
                         f"Prometheus not scraping — data {round(freshness)}s old", lat1,
                         {**detail, "suggestion": "Check Prometheus scrape config"})
        return _ok("grafana_cache", lat1, detail)
    except Exception as e:
        if "401" in str(e) or "Unauthorized" in str(e):
            return _ok("grafana_cache", 0, {"status": 401, "auth_required": True})
        return _warn("grafana_cache", str(e))


def _probe_chromadb_frozen() -> dict:
    """Detect frozen ChromaDB by checking heartbeat nanosecond timestamp changes."""
    try:
        status, body, lat = _http_get(f"{CHROMADB_URL}/api/v2/heartbeat")
        if status != 200:
            return _warn("chromadb_frozen", f"heartbeat status {status}", lat)

        data = json.loads(body)
        count = data.get("nanosecond heartbeat", 0)

        # Read previous counts from SQLite
        prev_counts = _get_recent_chroma_counts(3)
        detail = {"current_count": count, "previous_counts": prev_counts}

        if len(prev_counts) >= 2 and all(c == count for c in prev_counts):
            # Check if there was Telegram activity
            activity_file = STATE_DIR / "telegram_last_activity.json"
            had_activity = False
            try:
                if activity_file.exists():
                    act = json.loads(activity_file.read_text())
                    last_ts = act.get("last_message_ts", 0)
                    if time.time() - last_ts < 3600:
                        had_activity = True
            except Exception:
                pass

            if had_activity:
                return _warn("chromadb_frozen",
                             f"collection stuck at {count} chunks with recent activity",
                             lat, {**detail, "suggestion": "docker restart chromadb && python3 ingest.py"})

        return _ok("chromadb_frozen", lat, detail)
    except Exception as e:
        return _warn("chromadb_frozen", str(e))


def sweep_dashboards() -> dict:
    probes = [_probe_grafana_cache, _probe_chromadb_frozen]
    results = _run_probes(probes)
    return {"section": "dashboards", "results": results}


# ════════════════════════════════════════════════════════════════
#  PARALLEL EXECUTION
# ════════════════════════════════════════════════════════════════

def _run_probes(probes: list) -> list:
    """Run probe functions in parallel with ThreadPoolExecutor."""
    results = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(fn): fn.__name__ for fn in probes}
        for future in as_completed(futures, timeout=SWEEP_MAX):
            name = futures[future]
            try:
                results.append(future.result())
            except Exception as e:
                results.append(_fail(name, f"probe crashed: {e}"))
    return results


# ════════════════════════════════════════════════════════════════
#  SCORING
# ════════════════════════════════════════════════════════════════

def _score_section(results: list) -> int:
    """Score a section 0-100 based on probe results."""
    if not results:
        return 0
    total = len(results)
    ok_count = sum(1 for r in results if r["status"] == "ok")
    warn_count = sum(1 for r in results if r["status"] == "warn")
    # ok=100%, warn=50%, fail=0%
    score = ((ok_count * 100) + (warn_count * 50)) / total
    return round(score)


def _score_overall(sections: list[dict]) -> int:
    """Weighted overall score."""
    weights = {"nas": 30, "homelab": 25, "apis": 20, "internal": 15, "dashboards": 10}
    total_weight = 0
    weighted_sum = 0
    for s in sections:
        w = weights.get(s["section"], 10)
        total_weight += w
        weighted_sum += w * _score_section(s["results"])
    return round(weighted_sum / total_weight) if total_weight else 0


# ════════════════════════════════════════════════════════════════
#  SQLite PERSISTENCE
# ════════════════════════════════════════════════════════════════

def _init_db():
    """Create SQLite tables if needed."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS sweeps (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            section TEXT NOT NULL,
            probe TEXT NOT NULL,
            status TEXT NOT NULL,
            latency_ms REAL,
            detail_json TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS scores (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            overall INTEGER,
            nas INTEGER,
            homelab INTEGER,
            apis INTEGER,
            internal INTEGER,
            dashboards INTEGER
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS chroma_counts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT NOT NULL,
            count INTEGER
        )
    """)
    conn.commit()
    conn.close()


def _persist(sections: list[dict], overall: int):
    """Save sweep results to SQLite."""
    _init_db()
    conn = sqlite3.connect(str(DB_PATH))
    ts = datetime.now().astimezone().isoformat()
    section_scores = {}

    for s in sections:
        section_scores[s["section"]] = _score_section(s["results"])
        for r in s["results"]:
            detail = _sanitize_detail(r.get("detail", {}))
            conn.execute(
                "INSERT INTO sweeps (ts, section, probe, status, latency_ms, detail_json) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (ts, s["section"], r["probe"], r["status"],
                 r["latency_ms"], json.dumps(detail)))

            # Track ChromaDB counts
            if r["probe"] == "chromadb_frozen" and "current_count" in r.get("detail", {}):
                conn.execute("INSERT INTO chroma_counts (ts, count) VALUES (?, ?)",
                             (ts, r["detail"]["current_count"]))

    conn.execute(
        "INSERT INTO scores (ts, overall, nas, homelab, apis, internal, dashboards) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (ts, overall,
         section_scores.get("nas", 0), section_scores.get("homelab", 0),
         section_scores.get("apis", 0), section_scores.get("internal", 0),
         section_scores.get("dashboards", 0)))

    conn.commit()
    conn.close()


def _get_previous_score() -> int | None:
    """Get the last overall score."""
    try:
        conn = sqlite3.connect(str(DB_PATH))
        row = conn.execute(
            "SELECT overall FROM scores ORDER BY id DESC LIMIT 1").fetchone()
        conn.close()
        return row[0] if row else None
    except Exception:
        return None


def _get_recent_chroma_counts(n: int = 3) -> list[int]:
    """Get last N ChromaDB counts from SQLite."""
    try:
        conn = sqlite3.connect(str(DB_PATH))
        rows = conn.execute(
            "SELECT count FROM chroma_counts ORDER BY id DESC LIMIT ?", (n,)
        ).fetchall()
        conn.close()
        return [r[0] for r in rows]
    except Exception:
        return []


# ════════════════════════════════════════════════════════════════
#  FORMATTING
# ════════════════════════════════════════════════════════════════

_SECTION_LABELS = {
    "nas": "NAS",
    "homelab": "HOMELAB",
    "apis": "APIs externas",
    "internal": "Interno",
    "dashboards": "Dashboards",
}

_STATUS_ICONS = {"ok": "✅", "warn": "⚠️", "fail": "🔴"}

TELEGRAM_SUPPRESSED_PROBES = {
    "binance_ping",
    "binance_ticker",
    "iol_api",
}


def _filter_telegram_sections(sections: list[dict]) -> list[dict]:
    """Remove private finance probes from Telegram heartbeat output."""
    filtered = []
    for section in sections:
        results = [
            r for r in section.get("results", [])
            if r.get("probe") not in TELEGRAM_SUPPRESSED_PROBES
        ]
        if results:
            filtered.append({**section, "results": results})
    return filtered


def format_telegram(sections: list[dict], overall: int) -> str:
    """Format sweep results as Telegram message."""
    now = datetime.now().strftime("%d/%m %H:%M")
    lines = [f"🧠 *Tenazas Health Sweep — {now}*", ""]
    display_sections = _filter_telegram_sections(sections)
    display_overall = _score_overall(display_sections)

    for s in display_sections:
        label = _SECTION_LABELS.get(s["section"], s["section"])
        results = s["results"]
        ok = sum(1 for r in results if r["status"] == "ok")
        total = len(results)
        score = _score_section(results)

        # Section icon based on worst status
        statuses = [r["status"] for r in results]
        if "fail" in statuses:
            icon = "🔴"
        elif "warn" in statuses:
            icon = "⚠️"
        else:
            icon = "✅"

        line = f"{icon} {label} ({ok}/{total})"

        # Add details for non-ok probes
        problems = [r for r in results if r["status"] != "ok"]
        if problems:
            details = []
            for p in problems:
                msg = p.get("detail", {}).get("message", "")
                details.append(f"{p['probe']}: {msg}" if msg else p["probe"])
            line += " — " + "; ".join(details[:3])

        lines.append(line)

        # Add suggestions
        for p in problems:
            suggestion = p.get("detail", {}).get("suggestion")
            if suggestion:
                lines.append(f"   ↳ `{suggestion}`")

    lines.append("")

    # Score with delta
    prev = _get_previous_score()
    delta = ""
    if prev is not None:
        diff = display_overall - prev
        if diff != 0:
            delta = f" ({'↑' if diff > 0 else '↓'} {abs(diff)} vs anterior)"
    lines.append(f"Score: {display_overall}/100{delta}")

    return "\n".join(lines)


# ════════════════════════════════════════════════════════════════
#  PROMETHEUS EXPORT
# ════════════════════════════════════════════════════════════════

_health_gauges = {}


def _export_prometheus(sections: list[dict], overall: int):
    """Export health scores to Prometheus if available."""
    try:
        from prometheus_client import Gauge
        global _health_gauges
        if not _health_gauges:
            _health_gauges["overall"] = Gauge(
                "tenazas_health_score", "Health sweep score",
                ["section"])
        g = _health_gauges["overall"]
        g.labels(section="overall").set(overall)
        for s in sections:
            g.labels(section=s["section"]).set(_score_section(s["results"]))
    except ImportError:
        pass


# ════════════════════════════════════════════════════════════════
#  MAIN ENTRY POINTS
# ════════════════════════════════════════════════════════════════

def sweep_section(name: str) -> dict:
    """Run a single section sweep."""
    runners = {
        "nas": sweep_nas,
        "homelab": sweep_homelab,
        "apis": sweep_apis,
        "internal": sweep_internal,
        "dashboards": sweep_dashboards,
    }
    fn = runners.get(name)
    if not fn:
        return {"section": name, "results": [_fail(name, f"unknown section: {name}")]}
    return fn()


def sweep() -> dict:
    """Run full health sweep across all sections."""
    _init_db()

    t0 = time.monotonic()
    sections = []

    # Run all sections in parallel (each section runs its probes in parallel too)
    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = {
            pool.submit(sweep_nas): "nas",
            pool.submit(sweep_homelab): "homelab",
            pool.submit(sweep_apis): "apis",
            pool.submit(sweep_internal): "internal",
            pool.submit(sweep_dashboards): "dashboards",
        }
        for future in as_completed(futures, timeout=SWEEP_MAX):
            try:
                sections.append(future.result())
            except Exception as e:
                name = futures[future]
                sections.append({"section": name, "results": [_fail(name, str(e))]})

    # Sort by canonical order
    order = ["nas", "homelab", "apis", "internal", "dashboards"]
    sections.sort(key=lambda s: order.index(s["section"]) if s["section"] in order else 99)

    overall = _score_overall(sections)
    elapsed = (time.monotonic() - t0) * 1000

    result = {
        "ts": datetime.now().astimezone().isoformat(),
        "elapsed_ms": round(elapsed),
        "overall_score": overall,
        "sections": sections,
    }

    # Persist
    _persist(sections, overall)

    # Prometheus
    _export_prometheus(sections, overall)

    return result


def run(notify: bool = True, as_json: bool = False):
    """Run sweep and optionally notify Telegram."""
    result = sweep()

    if as_json:
        print(json.dumps(result, indent=2, default=str))
        return result

    msg = format_telegram(result["sections"], result["overall_score"])
    print(msg)

    if notify:
        _notify_telegram(msg)
        log.info("sweep done — score=%d, elapsed=%dms",
                 result["overall_score"], result["elapsed_ms"])

    return result


def watch(interval: int = WATCH_INTERVAL, notify: bool = True):
    """Loop sweep every N seconds."""
    log.info("watch mode — interval=%ds", interval)
    while True:
        try:
            run(notify=notify)
        except Exception as e:
            log.error("sweep error: %s", e)
        time.sleep(interval)


# ════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Tenazas Health Sweep")
    parser.add_argument("--no-telegram", action="store_true",
                        help="Skip Telegram notification")
    parser.add_argument("--watch", action="store_true",
                        help="Loop every 5 min")
    parser.add_argument("--json", action="store_true",
                        help="Output JSON")
    parser.add_argument("--section", type=str,
                        help="Run single section (nas|homelab|apis|internal|dashboards)")
    args = parser.parse_args()

    if args.section:
        result = sweep_section(args.section)
        print(json.dumps(result, indent=2, default=str))
    elif args.watch:
        watch(notify=not args.no_telegram)
    else:
        run(notify=not args.no_telegram, as_json=args.json)
