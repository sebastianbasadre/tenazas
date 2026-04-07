#!/usr/bin/env python3
"""
API Call Counter — tracks external API usage for Gemini and IOL.

Persists counts to state/api_calls.json (atomic writes, thread-safe).
Exposes Prometheus-compatible metrics on :9106/metrics.

Usage:
    from api_call_counter import count_call, get_monthly_total, start_exporter

    count_call("gemini", model="gemini-2.0-flash", status="ok")
    count_call("iol", endpoint="/api/v2/portafolio/argentina", status="ok")

    total = get_monthly_total("iol")  # calls this month
    start_exporter(port=9106)         # background HTTP server
"""

import json
import logging
import os
import threading
import time
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path

log = logging.getLogger("api_call_counter")

# ── Config ────────────────────────────────────────────────────────

STATE_DIR = Path("/home/tenazas/.openclaw/state")
STATE_FILE = STATE_DIR / "api_calls.json"
IOL_MONTHLY_LIMIT = 25000

_lock = threading.Lock()
_state = {}  # loaded lazily


# ── Persistence ───────────────────────────────────────────────────


def _load():
    """Load state from disk. Returns empty dict if missing/corrupt."""
    global _state
    try:
        with open(STATE_FILE) as f:
            _state = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        _state = {}


def _save():
    """Atomic write: write to .tmp then rename."""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STATE_FILE.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(_state, f, indent=2)
    os.replace(str(tmp), str(STATE_FILE))


# ── Core API ──────────────────────────────────────────────────────


def count_call(service: str, *, model: str = "", endpoint: str = "", status: str = "ok"):
    """Register an API call.

    Args:
        service: "gemini" or "iol"
        model: model name (for gemini), e.g. "gemini-2.0-flash"
        endpoint: API path (for iol), e.g. "/api/v2/portafolio/argentina"
        status: "ok", "error", or "rate_limit"
    """
    with _lock:
        if not _state:
            _load()

        # Total counter: service__model_or_endpoint__status
        detail = model or endpoint or "unknown"
        key = f"{service}__{detail}__{status}"
        _state[key] = _state.get(key, 0) + 1

        # Monthly counter: YYYY-MM__service
        month = datetime.now().strftime("%Y-%m")
        month_key = f"{month}__{service}"
        _state[month_key] = _state.get(month_key, 0) + 1

        _save()


def get_monthly_total(service: str, month: str = None) -> int:
    """Get total calls for a service in a given month.

    Args:
        service: "gemini" or "iol"
        month: "YYYY-MM" format, defaults to current month
    """
    with _lock:
        if not _state:
            _load()
        if month is None:
            month = datetime.now().strftime("%Y-%m")
        return _state.get(f"{month}__{service}", 0)


# ── Prometheus Exporter ───────────────────────────────────────────


def _build_metrics() -> str:
    """Build Prometheus text exposition from current state."""
    with _lock:
        if not _state:
            _load()
        snapshot = dict(_state)

    lines = []
    month = datetime.now().strftime("%Y-%m")

    # tenazas_api_calls_total{service, detail, status} counter
    lines.append("# HELP tenazas_api_calls_total Total API calls by service")
    lines.append("# TYPE tenazas_api_calls_total counter")
    for key, val in sorted(snapshot.items()):
        parts = key.split("__")
        if len(parts) == 3 and not parts[0].startswith("20"):
            service, detail, status = parts
            # Determine label name based on service
            detail_label = "model" if service == "gemini" else "endpoint"
            lines.append(
                f'tenazas_api_calls_total{{service="{service}",{detail_label}="{detail}",status="{status}"}} {val}'
            )

    # tenazas_api_calls_monthly{service, month} gauge
    lines.append("# HELP tenazas_api_calls_monthly Monthly API call totals")
    lines.append("# TYPE tenazas_api_calls_monthly gauge")
    for key, val in sorted(snapshot.items()):
        parts = key.split("__")
        if len(parts) == 2 and parts[0].startswith("20"):
            m, service = parts
            lines.append(
                f'tenazas_api_calls_monthly{{service="{service}",month="{m}"}} {val}'
            )

    # IOL quota gauges
    iol_monthly = snapshot.get(f"{month}__iol", 0)
    quota_used = min(iol_monthly / IOL_MONTHLY_LIMIT, 1.0) if IOL_MONTHLY_LIMIT else 0
    quota_remaining = max(IOL_MONTHLY_LIMIT - iol_monthly, 0)

    lines.append("# HELP tenazas_iol_quota_used IOL monthly quota fraction (0-1, limit 25000)")
    lines.append("# TYPE tenazas_iol_quota_used gauge")
    lines.append(f"tenazas_iol_quota_used {quota_used:.6f}")

    lines.append("# HELP tenazas_iol_quota_remaining IOL monthly calls remaining")
    lines.append("# TYPE tenazas_iol_quota_remaining gauge")
    lines.append(f"tenazas_iol_quota_remaining {quota_remaining}")

    # Gemini monthly for convenience
    gemini_monthly = snapshot.get(f"{month}__gemini", 0)
    lines.append("# HELP tenazas_gemini_calls_monthly Gemini calls this month")
    lines.append("# TYPE tenazas_gemini_calls_monthly gauge")
    lines.append(f"tenazas_gemini_calls_monthly {gemini_monthly}")

    lines.append("")
    return "\n".join(lines)


class _MetricsHandler(BaseHTTPRequestHandler):
    """Serve /metrics endpoint."""

    def do_GET(self):
        if self.path == "/metrics":
            body = _build_metrics().encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; version=0.0.4; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        """Silence HTTP access logs."""
        pass


def start_exporter(port: int = 9106):
    """Start Prometheus metrics HTTP server on a daemon thread."""
    server = HTTPServer(("0.0.0.0", port), _MetricsHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    log.info("API call metrics exporter on :%d", port)
