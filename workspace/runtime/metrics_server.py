#!/usr/bin/env python3
"""
Standalone Prometheus metrics server for Tenazas.

Runs as a persistent background process (started from startup.sh).
Polls metrics_state.json (written by runtime.py each cycle) and
updates Prometheus Gauges so they can be scraped at :9105/metrics.

Also collects NAS host metrics (RAM, CPU, disk, temp), Ollama GPU
health, Telegram activity, and IOL portfolio data on slower intervals.

Usage: python3 metrics_server.py &
"""

import json
import logging
import os
import re
import subprocess
import time
import threading
from datetime import datetime, timedelta
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.error import URLError

log = logging.getLogger("metrics_server")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

BASE_DIR = Path(__file__).parent
METRICS_STATE_FILE = BASE_DIR / "logs" / "metrics_state.json"
LOGS_DIR = BASE_DIR / "logs"
POLL_INTERVAL = 5  # seconds

# Slower collection intervals (in seconds)
OLLAMA_CHECK_INTERVAL = 60
IOL_CHECK_INTERVAL = 300  # 5 min
LOG_PARSE_INTERVAL = 30

# Ollama GPU endpoint (configurable)
OLLAMA_GPU_URL = os.environ.get("OLLAMA_GPU_URL", "http://192.168.2.76:11434")
OLLAMA_GPU_TIMEOUT = 5

try:
    from prometheus_client import Counter, Gauge, start_http_server
    PROM_AVAILABLE = True
except ImportError:
    PROM_AVAILABLE = False

if PROM_AVAILABLE:
    # Use Gauges (not Counters) because we SET values from file
    # Runtime
    G_RUNS = Gauge("runtime_runs_total", "Total runtime cycles completed")
    G_DURATION = Gauge("runtime_last_duration_seconds", "Last cycle duration")
    G_FAILURES = Gauge("runtime_failures_total", "Total runtime failures")
    G_HEALTH = Gauge("runtime_health_score", "Runtime health score 0-100")

    # Infrastructure
    G_RESTARTS = Gauge("container_restarts_total", "Container restarts executed")
    G_VPN = Gauge("vpn_disconnects_total", "VPN disconnect events")
    G_OLLAMA = Gauge("ollama_failures_total", "Ollama unavailable events")
    G_FREEZES = Gauge("runtime_freeze_detected", "Freeze events detected")
    G_WATCHDOGS = Gauge("runtime_watchdog_triggered", "Watchdog timeout events")

    # RAG
    G_RAG_QUERIES = Gauge("rag_queries_total", "Total RAG queries executed")
    G_RAG_LOW_CONF = Gauge("rag_low_confidence_total", "RAG low confidence results")

    # Steps (top-level gauges for each step)
    G_STEP_UPDATE_STATE = Gauge("runtime_step_update_state_seconds", "update_state step duration")
    G_STEP_DETECT_EVENTS = Gauge("runtime_step_detect_events_seconds", "detect_events step duration")
    G_STEP_RUN_TASKS = Gauge("runtime_step_run_tasks_seconds", "run_tasks step duration")
    G_STEP_NMAP = Gauge("runtime_step_nmap_seconds", "network scan step duration")

    # Model failover
    G_FAILOVER_ACTIVE = Gauge("model_failover_active", "1 if using fallback model, 0 if primary")

    # Router V2
    G_RV2_V2_CALLS = Gauge("router_v2_calls_total", "Total Router V2 decisions")
    G_RV2_V1_CALLS = Gauge("router_v1_calls_total", "Total Router V1 decisions")
    G_RV2_V1_FALLBACK = Gauge("router_v1_fallback_from_v2_total", "V2 failed, V1 took over")
    G_RV2_INTERNAL_FALLBACKS = Gauge("router_v2_internal_fallbacks_total", "V2 non-primary model used")
    G_RV2_VRAM_SWAPS = Gauge("router_vram_swaps_total", "Models exceeding GPU VRAM selected")
    G_RV2_CLOUD_FALLBACKS = Gauge("router_cloud_fallbacks_total", "Fell through to Gemini cloud")
    G_RV2_LAST_LATENCY = Gauge("router_last_decision_latency_seconds", "Last routing decision latency")
    G_RV2_ROLE_FAST = Gauge("router_v2_role_fast_total", "Routing decisions for fast role")
    G_RV2_ROLE_REASON = Gauge("router_v2_role_reason_total", "Routing decisions for reason role")
    G_RV2_ROLE_HEAVY = Gauge("router_v2_role_heavy_total", "Routing decisions for heavy role")
    G_RV2_ROLE_CODE = Gauge("router_v2_role_code_total", "Routing decisions for code role")
    G_RV2_ROLE_VISION = Gauge("router_v2_role_vision_total", "Routing decisions for vision role")
    G_RV2_ROLE_EMBED = Gauge("router_v2_role_embed_total", "Routing decisions for embed role")

    # Guardrails
    G_GR_COOLDOWN = Gauge("guardrail_cooldown_total", "Heavy degraded by cooldown")
    G_GR_FREQUENCY = Gauge("guardrail_frequency_total", "Heavy degraded by frequency limit")
    G_GR_LATENCY = Gauge("guardrail_latency_total", "Heavy degraded by high latency")
    G_GR_VRAM_STORM = Gauge("guardrail_vram_storm_total", "Heavy degraded by VRAM swap storm")

    # ── NAS Host ────────────────────────────────────────────────
    G_NAS_RAM_USED = Gauge("nas_ram_used_bytes", "NAS RAM used (total - available)")
    G_NAS_RAM_TOTAL = Gauge("nas_ram_total_bytes", "NAS total RAM")
    G_NAS_CPU_PERCENT = Gauge("nas_cpu_load_percent", "NAS CPU load as percent of cores (1m avg)")
    G_NAS_DISK_USED = Gauge("nas_disk_used_bytes", "NAS root disk used bytes")
    G_NAS_DISK_TOTAL = Gauge("nas_disk_total_bytes", "NAS root disk total bytes")
    G_NAS_CPU_TEMP = Gauge("nas_cpu_temp_celsius", "NAS CPU temperature in Celsius")

    # ── Ollama GPU ──────────────────────────────────────────────
    G_OLLAMA_GPU_UP = Gauge("ollama_gpu_up", "1 if Ollama GPU responds, 0 otherwise")
    G_OLLAMA_GPU_LATENCY = Gauge("ollama_gpu_latency_seconds", "Ollama GPU health check latency")
    G_OLLAMA_GPU_MODELS = Gauge("ollama_gpu_models_count", "Number of models on GPU Ollama")

    G_OLLAMA_MODELS_TOTAL = Gauge("ollama_models_total", "Total models available on Ollama GPU")

    # ── Telegram ────────────────────────────────────────────────
    G_TELEGRAM_TOTAL = Gauge("telegram_messages_total", "Total Telegram handler log entries")
    G_TELEGRAM_TODAY = Gauge("telegram_messages_today", "Telegram messages processed today")

    # ── IOL Portfolio ───────────────────────────────────────────
    G_IOL_PORTFOLIO_ARS = Gauge("iol_portfolio_value_ars", "IOL portfolio total value ARS")
    G_IOL_PORTFOLIO_PNL = Gauge("iol_portfolio_pnl_day_ars", "IOL portfolio day P&L ARS")
    G_IOL_PORTFOLIO_TOTAL = Gauge("iol_portfolio_total_ars", "IOL portfolio total from CLI")
    G_IOL_LAST_SUCCESS = Gauge("iol_portfolio_last_success_ts", "Timestamp of last successful IOL collection")
    G_IOL_ERRORS = Counter("iol_portfolio_errors_total", "Total IOL collection errors")

    # ── OpenRouter ─────────────────────────────────────────────
    G_OPENROUTER_BALANCE = Gauge("tenazas_openrouter_balance_usd", "Créditos disponibles en OpenRouter")
    G_OPENROUTER_USED = Gauge("tenazas_openrouter_used_usd", "Total gastado en OpenRouter")
    G_OPENROUTER_REQUESTS = Gauge("tenazas_openrouter_requests_total", "Requests realizadas via OpenRouter")
    G_OPENROUTER_LAST_CHECK = Gauge("tenazas_openrouter_last_check_ts", "Timestamp del último chequeo exitoso")


def update_from_file():
    """Read metrics_state.json and update Prometheus Gauges."""
    try:
        with open(METRICS_STATE_FILE) as f:
            state = json.load(f)

        # Runtime
        G_RUNS.set(state.get("runs", 0))
        G_DURATION.set(state.get("last_duration", 0))
        G_FAILURES.set(state.get("failures", 0))
        G_HEALTH.set(state.get("health_score", 0))

        # Infrastructure
        G_RESTARTS.set(state.get("restarts", 0))
        G_VPN.set(state.get("vpn_disconnects", 0))
        G_OLLAMA.set(state.get("ollama_failures", 0))
        G_FREEZES.set(state.get("freezes", 0))
        G_WATCHDOGS.set(state.get("watchdogs", 0))

        # RAG
        G_RAG_QUERIES.set(state.get("rag_queries", 0))
        G_RAG_LOW_CONF.set(state.get("rag_low_confidence", 0))

        # Steps
        steps = state.get("steps", {})
        G_STEP_UPDATE_STATE.set(steps.get("update_state", 0))
        G_STEP_DETECT_EVENTS.set(steps.get("detect_events", 0))
        G_STEP_RUN_TASKS.set(steps.get("run_tasks", 0))
        G_STEP_NMAP.set(steps.get("nmap", 0))

        # Failover
        G_FAILOVER_ACTIVE.set(state.get("failover_active", 0))

        # Router V2
        rv2 = state.get("router_v2", {})
        G_RV2_V2_CALLS.set(rv2.get("v2_calls", 0))
        G_RV2_V1_CALLS.set(rv2.get("v1_calls", 0))
        G_RV2_V1_FALLBACK.set(rv2.get("v1_fallback_from_v2", 0))
        G_RV2_INTERNAL_FALLBACKS.set(rv2.get("internal_fallbacks", 0))
        G_RV2_VRAM_SWAPS.set(rv2.get("vram_swaps", 0))
        G_RV2_CLOUD_FALLBACKS.set(rv2.get("cloud_fallbacks", 0))
        G_RV2_LAST_LATENCY.set(rv2.get("last_latency", 0))
        G_RV2_ROLE_FAST.set(rv2.get("role_fast", 0))
        G_RV2_ROLE_REASON.set(rv2.get("role_reason", 0))
        G_RV2_ROLE_HEAVY.set(rv2.get("role_heavy", 0))
        G_RV2_ROLE_CODE.set(rv2.get("role_code", 0))
        G_RV2_ROLE_VISION.set(rv2.get("role_vision", 0))
        G_RV2_ROLE_EMBED.set(rv2.get("role_embed", 0))

    except (FileNotFoundError, json.JSONDecodeError):
        pass

    # Guardrails (from separate state file)
    try:
        guardrail_file = BASE_DIR / "guardrail_state.json"
        with open(guardrail_file) as f:
            gr = json.load(f)
        degs = gr.get("degradations", {})
        G_GR_COOLDOWN.set(degs.get("cooldown", 0))
        G_GR_FREQUENCY.set(degs.get("frequency", 0))
        G_GR_LATENCY.set(degs.get("latency", 0))
        G_GR_VRAM_STORM.set(degs.get("vram_storm", 0))
    except (FileNotFoundError, json.JSONDecodeError):
        pass


# ── NAS Host Metrics ────────────────────────────────────────────


def collect_nas_metrics():
    """Read NAS RAM, CPU load, disk, and temperature from /proc and /sys."""
    # RAM from /proc/meminfo
    try:
        with open("/proc/meminfo") as f:
            mem = {}
            for line in f:
                parts = line.split()
                if len(parts) >= 2:
                    mem[parts[0].rstrip(":")] = int(parts[1]) * 1024  # kB→bytes
        total = mem.get("MemTotal", 0)
        available = mem.get("MemAvailable", 0)
        G_NAS_RAM_TOTAL.set(total)
        G_NAS_RAM_USED.set(total - available)
    except Exception:
        pass

    # CPU load from /proc/loadavg (1-min avg / nproc → percent)
    try:
        with open("/proc/loadavg") as f:
            load_1m = float(f.read().split()[0])
        cpus = os.cpu_count() or 1
        G_NAS_CPU_PERCENT.set(round(load_1m / cpus * 100, 1))
    except Exception:
        pass

    # Disk usage for root filesystem (which is /volume1 on Synology)
    try:
        st = os.statvfs("/")
        total = st.f_frsize * st.f_blocks
        free = st.f_frsize * st.f_bavail
        G_NAS_DISK_TOTAL.set(total)
        G_NAS_DISK_USED.set(total - free)
    except Exception:
        pass

    # CPU temperature from hwmon
    try:
        for hwmon in Path("/sys/class/hwmon").iterdir():
            temp_file = hwmon / "temp1_input"
            if temp_file.exists():
                millideg = int(temp_file.read_text().strip())
                G_NAS_CPU_TEMP.set(millideg / 1000.0)
                break
    except Exception:
        pass


# ── Ollama GPU Health ───────────────────────────────────────────


def collect_ollama_gpu():
    """Check Ollama GPU health and model count."""
    try:
        start = time.monotonic()
        req = Request(f"{OLLAMA_GPU_URL}/api/tags")
        resp = urlopen(req, timeout=OLLAMA_GPU_TIMEOUT)
        latency = time.monotonic() - start
        data = json.loads(resp.read())
        models = data.get("models", [])
        G_OLLAMA_GPU_UP.set(1)
        G_OLLAMA_GPU_LATENCY.set(round(latency, 3))
        G_OLLAMA_GPU_MODELS.set(len(models))
    except Exception:
        G_OLLAMA_GPU_UP.set(0)
        G_OLLAMA_GPU_LATENCY.set(0)
        G_OLLAMA_GPU_MODELS.set(0)


# ── Telegram Activity ──────────────────────────────────────────


def collect_telegram_metrics():
    """Count Telegram messages from telegram_handler.log."""
    log_file = LOGS_DIR / "telegram_handler.log"
    try:
        total = 0
        today_count = 0
        today_str = datetime.now().strftime("%Y-%m-%d")
        with open(log_file) as f:
            for line in f:
                total += 1
                if today_str in line:
                    today_count += 1
        G_TELEGRAM_TOTAL.set(total)
        G_TELEGRAM_TODAY.set(today_count)
    except FileNotFoundError:
        G_TELEGRAM_TOTAL.set(0)
        G_TELEGRAM_TODAY.set(0)


# ── IOL Portfolio ──────────────────────────────────────────────


def collect_iol_metrics():
    """Fetch IOL portfolio value and P&L. Logs errors instead of silencing."""
    import sys
    try:
        # Add skills path for iol_broker import
        skills_path = "/home/tenazas/.openclaw/workspace/skills"
        if skills_path not in sys.path:
            sys.path.insert(0, skills_path)
        from iol_broker.iol_portfolio import get_portfolio, get_account_state

        account = get_account_state()
        cuentas = account.get("cuentas", [])
        total_pesos = float(account.get("totalEnPesos", 0) or 0)

        portfolio = get_portfolio("argentina")
        activos = portfolio.get("activos", [])
        invertido = sum(float(a.get("valorizado", 0) or 0) for a in activos)

        # P&L = sum of (valorizado - cantidad * ppc) for each activo
        pnl = 0
        for a in activos:
            valorizado = float(a.get("valorizado", 0) or 0)
            cantidad = float(a.get("cantidad", 0) or 0)
            ppc = float(a.get("ppc", 0) or 0)
            pnl += valorizado - (cantidad * ppc)

        G_IOL_PORTFOLIO_ARS.set(round(total_pesos if total_pesos else invertido, 2))
        G_IOL_PORTFOLIO_PNL.set(round(pnl, 2))
        G_IOL_LAST_SUCCESS.set(time.time())
    except Exception as e:
        G_IOL_ERRORS.inc()
        log.warning("IOL collect failed: %s: %s", type(e).__name__, e)


# ── IOL Portfolio via CLI ──────────────────────────────────────


IOL_CLI_PATH = "/home/tenazas/.openclaw/workspace/skills/iol_broker/iol_handler_cli.py"
IOL_CLI_INTERVAL = 900  # 15 min


def update_iol_metrics():
    """Run iol_handler_cli.py 'portafolio', parse Total (ARS), update gauge."""
    try:
        result = subprocess.run(
            ["python3", IOL_CLI_PATH, "portafolio"],
            capture_output=True, text=True, timeout=30
        )
        output = result.stdout
        if result.returncode != 0 or not output.strip():
            log.warning("IOL CLI failed: rc=%d stderr=%s", result.returncode, result.stderr.strip())
            return
        # Parse "💰 Total (ARS): $6,851,543.49"
        m = re.search(r"Total \(ARS\):\s*\$([0-9,]+\.?[0-9]*)", output)
        if m:
            value = float(m.group(1).replace(",", ""))
            G_IOL_PORTFOLIO_TOTAL.set(value)
            log.info("IOL portfolio updated: $%s → %.2f", m.group(1), value)
        else:
            log.warning("IOL CLI: no Total (ARS) found in output: %s", output[:200])
    except subprocess.TimeoutExpired:
        log.warning("IOL CLI timeout (30s)")
    except Exception as e:
        log.warning("IOL CLI error: %s", e)


# ── Ollama Models (via 192.168.2.6) ──────────────────────────


OLLAMA_MODELS_URL = "http://192.168.2.6:11434/api/tags"
OLLAMA_MODELS_INTERVAL = 300  # 5 min


def update_ollama_metrics():
    """Count models on Ollama at 192.168.2.6, update gauge."""
    try:
        req = Request(OLLAMA_MODELS_URL)
        resp = urlopen(req, timeout=5)
        data = json.loads(resp.read())
        models = data.get("models", [])
        G_OLLAMA_MODELS_TOTAL.set(len(models))
        log.info("Ollama models updated: %d", len(models))
    except Exception as e:
        G_OLLAMA_MODELS_TOTAL.set(0)
        log.warning("Ollama models check failed: %s", e)


# ── OpenRouter Usage ─────────────────────────────────────────


OPENROUTER_CHECK_INTERVAL = 60  # seconds — cache to avoid hammering API
_openrouter_cache = {"ts": 0, "data": None}


def collect_openrouter_metrics():
    """Fetch OpenRouter balance/usage. Fails silently if offline/unauth."""
    import sys
    now = time.monotonic()

    # Cache: skip if last successful check was < 60s ago
    if _openrouter_cache["data"] is not None and (now - _openrouter_cache["ts"]) < OPENROUTER_CHECK_INTERVAL:
        return

    try:
        runtime_path = str(BASE_DIR)
        if runtime_path not in sys.path:
            sys.path.insert(0, runtime_path)
        from openrouter_usage import get_balance

        b = get_balance()
        balance = b.get("balance_usd", 0.0)
        usage = b.get("usage_usd", 0.0)
        rate_limit = b.get("rate_limit", {})
        requests_count = rate_limit.get("requests", 0)

        G_OPENROUTER_BALANCE.set(balance)
        G_OPENROUTER_USED.set(usage)
        G_OPENROUTER_REQUESTS.set(requests_count)
        G_OPENROUTER_LAST_CHECK.set(time.time())

        _openrouter_cache["ts"] = now
        _openrouter_cache["data"] = b
        log.info("OpenRouter updated: balance=$%.4f used=$%.4f", balance, usage)
    except Exception as e:
        log.warning("OpenRouter check failed: %s", e)


# ── Background Thread for IOL CLI ─────────────────────────────


def _iol_cli_loop():
    """Background thread: run IOL CLI every 15 min."""
    while True:
        update_iol_metrics()
        time.sleep(IOL_CLI_INTERVAL)


# ── Main Loop ──────────────────────────────────────────────────


if __name__ == "__main__":
    if not PROM_AVAILABLE:
        print("prometheus_client not available")
        exit(1)

    start_http_server(9105)
    print("Metrics server on :9105 (polling metrics_state.json + NAS + Ollama + IOL)")

    # Background thread for IOL CLI (slow, subprocess-based)
    iol_thread = threading.Thread(target=_iol_cli_loop, daemon=True)
    iol_thread.start()

    last_ollama_check = 0
    last_iol_check = 0
    last_log_parse = 0
    last_models_check = 0
    last_openrouter_check = 0

    while True:
        now = time.monotonic()

        # Always: state file + NAS host metrics (fast, local reads)
        update_from_file()
        collect_nas_metrics()

        # Every 60s: Ollama GPU health (via OLLAMA_GPU_URL)
        if now - last_ollama_check >= OLLAMA_CHECK_INTERVAL:
            collect_ollama_gpu()
            last_ollama_check = now

        # Every 30s: Telegram log parsing
        if now - last_log_parse >= LOG_PARSE_INTERVAL:
            collect_telegram_metrics()
            last_log_parse = now

        # Every 5 min: IOL portfolio (Python API), Ollama models (via .6)
        if now - last_iol_check >= IOL_CHECK_INTERVAL:
            collect_iol_metrics()
            last_iol_check = now

        if now - last_models_check >= OLLAMA_MODELS_INTERVAL:
            update_ollama_metrics()
            last_models_check = now

        # Every 60s: OpenRouter balance/usage (has internal 60s cache too)
        if now - last_openrouter_check >= OPENROUTER_CHECK_INTERVAL:
            collect_openrouter_metrics()
            last_openrouter_check = now

        time.sleep(POLL_INTERVAL)
