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

try:
    from runtime_config import OLLAMA_GPU_TAGS_URL, OLLAMA_GPU_URL
except ImportError:
    from workspace.runtime.runtime_config import OLLAMA_GPU_TAGS_URL, OLLAMA_GPU_URL

log = logging.getLogger("metrics_server")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

BASE_DIR = Path(__file__).parent
METRICS_STATE_FILE = BASE_DIR / "logs" / "metrics_state.json"
LOGS_DIR = BASE_DIR / "logs"
AGENT_METRICS_STATE_FILE = LOGS_DIR / "agent_metrics_state.json"
POLL_INTERVAL = 5  # seconds

# Slower collection intervals (in seconds)
OLLAMA_CHECK_INTERVAL = 60
IOL_CHECK_INTERVAL = 300  # 5 min
LOG_PARSE_INTERVAL = 30
USD_ARS_API_TIMEOUT = 10

# Ollama GPU endpoint (configurable)
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

    # Smoking tracker
    G_SMOKING_TODAY = Gauge("tenazas_smoking_today", "Cigarettes smoked today")
    G_SMOKING_WEEK = Gauge("tenazas_smoking_week", "Cigarettes smoked this week")
    G_SMOKING_TOTAL = Gauge("tenazas_smoking_total", "Total cigarettes logged")
    G_SMOKING_DAY = Gauge("tenazas_smoking_day", "Cigarettes per day", ["date", "label"])
    G_SMOKING_HOUR = Gauge("tenazas_smoking_hour", "Cigarettes per hour per day", ["date", "hour"])

    # Skill feedback
    G_FEEDBACK_POSITIVE = Gauge("tenazas_feedback_positive_total", "Positive feedback count")
    G_FEEDBACK_NEGATIVE = Gauge("tenazas_feedback_negative_total", "Negative feedback count")
    G_FEEDBACK_IMPLICIT = Gauge("tenazas_feedback_implicit_neg_total", "Implicit negative (repeat) count")
    G_FEEDBACK_BY_SKILL = Gauge("tenazas_feedback_by_skill", "Feedback by skill", ["category", "feedback"])

    # Self-evolve proposals
    G_EVOLVE_TOTAL = Gauge("tenazas_evolve_proposals_total", "Total proposals in queue")
    G_EVOLVE_BY_TYPE = Gauge("tenazas_evolve_proposals", "Proposals by type and priority", ["type", "priority"])
    G_EVOLVE_BY_STATUS = Gauge("tenazas_evolve_status", "Proposals by status", ["status"])
    G_EVOLVE_RUNS = Gauge("tenazas_evolve_runs_total", "Total self-evolve runs")
    G_EVOLVE_LAST_RUN = Gauge("tenazas_evolve_last_run_ts", "Timestamp of last self-evolve run")
    G_EVOLVE_DETAIL = Gauge("tenazas_evolve_detail", "Proposal detail for table",
                            ["status", "priority", "type", "description"])

    # Guardrails
    G_GR_COOLDOWN = Gauge("guardrail_cooldown_total", "Heavy degraded by cooldown")
    G_GR_FREQUENCY = Gauge("guardrail_frequency_total", "Heavy degraded by frequency limit")
    G_GR_LATENCY = Gauge("guardrail_latency_total", "Heavy degraded by high latency")
    G_GR_VRAM_STORM = Gauge("guardrail_vram_storm_total", "Heavy degraded by VRAM swap storm")

    # ── Auto Training ──────────────────────────────────────────
    G_TRAINING_F1_BEFORE = Gauge("tenazas_training_f1_before", "F1 before last training run")
    G_TRAINING_F1_AFTER = Gauge("tenazas_training_f1_after", "F1 after last training run")
    G_TRAINING_SWAPPED = Gauge("tenazas_training_swapped", "1 if last run swapped model")
    G_TRAINING_RUNS = Gauge("tenazas_training_runs_total", "Total training pipeline runs")

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
    G_OLLAMA_GPU_VRAM_USED = Gauge("ollama_gpu_vram_used_bytes", "GPU VRAM currently used by loaded models")
    G_OLLAMA_GPU_VRAM_TOTAL = Gauge("ollama_gpu_vram_total_bytes", "GPU VRAM total (RTX 3090 = 24GB)")
    G_OLLAMA_GPU_LOADED_MODELS = Gauge("ollama_gpu_loaded_models", "Number of models currently loaded in GPU")

    # ── Telegram ────────────────────────────────────────────────
    G_TELEGRAM_TOTAL = Gauge("telegram_messages_total", "Total Telegram handler log entries")
    G_TELEGRAM_TODAY = Gauge("telegram_messages_today", "Telegram messages processed today")

    # ── IOL Portfolio ───────────────────────────────────────────
    G_IOL_PORTFOLIO_ARS = Gauge("iol_portfolio_value_ars", "IOL portfolio total value ARS")
    G_IOL_PORTFOLIO_PNL = Gauge("iol_portfolio_pnl_day_ars", "IOL portfolio day P&L ARS")
    G_IOL_PORTFOLIO_TOTAL = Gauge("iol_portfolio_total_ars", "IOL portfolio total from CLI")
    G_IOL_LAST_SUCCESS = Gauge("iol_portfolio_last_success_ts", "Timestamp of last successful IOL collection")
    G_IOL_ERRORS = Counter("iol_portfolio_errors_total", "Total IOL collection errors")

    # ── Network Scan Freshness ─────────────────────────────────
    G_NETWORK_SCAN_TS = Gauge("tenazas_network_last_scan_timestamp", "Timestamp of last network scan")

    # ── Device Discovery ───────────────────────────────────────
    G_DD_TOTAL = Gauge("device_discovery_total", "Total devices in inventory")
    G_DD_ONLINE = Gauge("device_discovery_online", "Devices seen in last scan")
    G_DD_MISSING = Gauge("device_discovery_missing", "Devices missing from last scan")
    G_DD_CLASSIFIED = Gauge("device_discovery_classified", "Devices with known category")
    G_DD_CONTROLLABLE = Gauge("device_discovery_controllable", "Devices with control method")
    G_DD_BY_CATEGORY = Gauge("device_discovery_by_category", "Devices by category", ["category"])
    G_DD_BY_VENDOR = Gauge("device_discovery_by_vendor", "Devices by vendor", ["vendor"])
    G_DD_LAST_SCAN = Gauge("device_discovery_last_scan_ts", "Timestamp of last inventory update")

    # ── B+A Roadmap (Sprint 5) ─────────────────────────────────
    G_BA_GOALS_FAILING = Gauge("tenazas_goals_failing", "Goals in fail state")
    G_BA_STAGING_PENDING = Gauge("tenazas_staging_pending", "Proposals in staging pending approval")
    G_BA_EXPERIMENTS_ACTIVE = Gauge("tenazas_experiments_active", "Active experiments running")
    G_BA_BACKEND_OFFLINE_24H = Gauge("tenazas_backend_offline_events_24h", "Backend offline outages in last 24h")
    G_BA_PREFERENCE_CONF = Gauge("tenazas_preference_confidence", "Preference confidence by category", ["category"])

    # ── OpenRouter ─────────────��───────────────────────────────
    G_OPENROUTER_BALANCE = Gauge("tenazas_openrouter_balance_usd", "Créditos disponibles en OpenRouter")
    G_OPENROUTER_USED = Gauge("tenazas_openrouter_used_usd", "Total gastado en OpenRouter")
    G_OPENROUTER_REQUESTS = Gauge("tenazas_openrouter_requests_total", "Requests realizadas via OpenRouter")
    G_OPENROUTER_LAST_CHECK = Gauge("tenazas_openrouter_last_check_ts", "Timestamp del último chequeo exitoso")

    # ── ROI Dashboard ──────────────────────────────────────────
    G_ROI_SKILL_EXECUTIONS = Gauge("tenazas_roi_skill_executions_total", "Total skill executions (period)")
    G_ROI_LLM_CHATS = Gauge("tenazas_roi_llm_chats_total", "Total LLM chat interactions (period)")
    G_ROI_AUTOMATION_RATE = Gauge("tenazas_roi_automation_rate", "Skill intercept rate 0-1")
    G_ROI_HOURS_SAVED = Gauge("tenazas_roi_hours_saved", "Estimated hours saved (period)")
    G_ROI_MINUTES_SAVED = Gauge("tenazas_roi_minutes_saved", "Estimated minutes saved (period)")
    G_ROI_ERRORS_AVOIDED = Gauge("tenazas_roi_errors_avoided_total", "Total errors avoided (all time)")
    G_ROI_CONTAINER_RESTARTS = Gauge("tenazas_roi_container_restarts", "Container restarts performed")
    G_ROI_VPN_FIXES = Gauge("tenazas_roi_vpn_fixes", "VPN disconnects fixed")
    G_ROI_BY_SKILL = Gauge("tenazas_roi_skill_usage", "Skill usage count", ["skill"])
    G_ROI_BY_SKILL_SUCCESS = Gauge("tenazas_roi_skill_success_rate", "Skill success rate 0-1", ["skill"])
    G_ROI_BY_SKILL_HOURS = Gauge("tenazas_roi_skill_hours_saved", "Hours saved per skill", ["skill"])
    G_ROI_PERIOD_DAYS = Gauge("tenazas_roi_period_days", "ROI tracking period in days")
    G_ROI_LAST_UPDATE = Gauge("tenazas_roi_last_update_ts", "Timestamp of last ROI snapshot")

    # Portfolio Reconciler
    G_RECONCILER_OK = Gauge("tenazas_reconciler_ok", "1 if portfolio data matches, 0 if issues found")
    G_RECONCILER_ISSUES = Gauge("tenazas_reconciler_issues_count", "Number of reconciliation issues")
    G_RECONCILER_BIN_DIFF = Gauge("tenazas_reconciler_binance_diff_pct", "Binance total divergence %")
    G_RECONCILER_IOL_DIFF = Gauge("tenazas_reconciler_iol_diff_pct", "IOL total divergence %")
    G_RECONCILER_LAST_RUN = Gauge("tenazas_reconciler_last_run_ts", "Last reconciliation timestamp")

    # ── Coordinator / Agent Routing ───────────────────────────
    G_AGENT_QUERIES = Gauge("tenazas_agent_queries_total", "Total coordinator-routed agent queries", ["agent"])
    G_AGENT_LATENCY_LAST = Gauge("tenazas_agent_latency_seconds", "Last agent latency in seconds", ["agent"])
    G_AGENT_LATENCY_AVG = Gauge("tenazas_agent_latency_avg_seconds", "Average agent latency in seconds", ["agent"])
    G_AGENT_OUTCOMES = Gauge("tenazas_agent_outcomes_total", "Agent outcomes by type", ["agent", "outcome"])
    G_COORD_ROUTES = Gauge("tenazas_coordinator_routes_total", "Coordinator routes by strategy", ["strategy"])
    G_COORD_FALLBACKS = Gauge("tenazas_coordinator_fallback_total", "Coordinator fallbacks by source", ["source"])
    G_COORD_HANDLED = Gauge("tenazas_coordinator_handled_total", "Coordinator handled vs unhandled totals", ["handled"])


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

    # Auto Training (from separate state file)
    try:
        training_file = BASE_DIR / "logs" / "training_metrics.json"
        with open(training_file) as f:
            tr = json.load(f)
        G_TRAINING_F1_BEFORE.set(tr.get("f1_before", 0))
        G_TRAINING_F1_AFTER.set(tr.get("f1_after", 0))
        G_TRAINING_SWAPPED.set(tr.get("swapped", 0))
        G_TRAINING_RUNS.set(tr.get("total_runs", 0))
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
    """Check Ollama GPU health, model count, and VRAM usage."""
    RTX_3090_VRAM = 24 * 1024 * 1024 * 1024  # 24 GB
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
        G_OLLAMA_GPU_VRAM_USED.set(0)
        G_OLLAMA_GPU_VRAM_TOTAL.set(RTX_3090_VRAM)
        G_OLLAMA_GPU_LOADED_MODELS.set(0)
        return
    # Query loaded models for VRAM usage
    try:
        req_ps = Request(f"{OLLAMA_GPU_URL}/api/ps")
        resp_ps = urlopen(req_ps, timeout=OLLAMA_GPU_TIMEOUT)
        ps_data = json.loads(resp_ps.read())
        loaded = ps_data.get("models", [])
        vram_used = sum(m.get("size_vram", 0) for m in loaded)
        G_OLLAMA_GPU_VRAM_USED.set(vram_used)
        G_OLLAMA_GPU_VRAM_TOTAL.set(RTX_3090_VRAM)
        G_OLLAMA_GPU_LOADED_MODELS.set(len(loaded))
    except Exception:
        G_OLLAMA_GPU_VRAM_USED.set(0)
        G_OLLAMA_GPU_VRAM_TOTAL.set(RTX_3090_VRAM)
        G_OLLAMA_GPU_LOADED_MODELS.set(0)


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
        ars_total_native = 0.0
        usd_total_native = 0.0
        for cuenta in cuentas:
            moneda = str(cuenta.get("moneda", "")).lower()
            total = float(cuenta.get("total", 0) or 0)
            if moneda == "dolar_estadounidense":
                usd_total_native += total
            else:
                ars_total_native += total

        portfolio = get_portfolio("argentina")
        activos = portfolio.get("activos", [])
        invertido = sum(float(a.get("valorizado", 0) or 0) for a in activos)
        implied_rate = 0.0
        if total_pesos > 0 and usd_total_native > 0:
            implied_rate = max((total_pesos - ars_total_native) / usd_total_native, 0.0)

        # Daily P&L in ARS equivalent
        pnl_day_ars = 0.0
        for a in activos:
            valorizado = float(a.get("valorizado", 0) or 0)
            cantidad = float(a.get("cantidad", 0) or 0)
            puntos = float(a.get("puntosVariacion", 0) or 0)
            variacion = float(a.get("variacionDiaria", 0) or 0)
            moneda = str(a.get("titulo", {}).get("moneda", "")).lower()
            day_pnl = puntos * cantidad if puntos else (
                valorizado * (variacion / 100.0) if variacion else 0.0
            )
            if moneda == "dolar_estadounidense":
                pnl_day_ars += day_pnl * implied_rate if implied_rate > 0 else 0.0
            else:
                pnl_day_ars += day_pnl

        G_IOL_PORTFOLIO_ARS.set(round(total_pesos if total_pesos else invertido, 2))
        G_IOL_PORTFOLIO_PNL.set(round(pnl_day_ars, 2))
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


OLLAMA_MODELS_URL = OLLAMA_GPU_TAGS_URL
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


# ── Smoking Tracker ──────────────────────────────────────────

SMOKING_METRICS_PATH = Path(__file__).parent / "smoking" / "metrics.json"


FEEDBACK_LOG_PATH = Path(__file__).parent / "learning" / "feedback_log.jsonl"


def update_feedback_metrics():
    """Read feedback_log.jsonl and update Prometheus gauges."""
    try:
        if not FEEDBACK_LOG_PATH.exists():
            return
        pos = neg = implicit = 0
        by_skill = {}
        for line in FEEDBACK_LOG_PATH.read_text().splitlines():
            try:
                e = json.loads(line)
                fb = e.get("feedback", "")
                cat = e.get("category", "unknown")
                if fb == "positive":
                    pos += 1
                    by_skill.setdefault(cat, {"pos": 0, "neg": 0})["pos"] += 1
                elif fb == "negative":
                    neg += 1
                    by_skill.setdefault(cat, {"pos": 0, "neg": 0})["neg"] += 1
                elif fb == "negative_repeat":
                    implicit += 1
                    by_skill.setdefault(cat, {"pos": 0, "neg": 0})["neg"] += 1
            except Exception:
                continue
        G_FEEDBACK_POSITIVE.set(pos)
        G_FEEDBACK_NEGATIVE.set(neg)
        G_FEEDBACK_IMPLICIT.set(implicit)
        for cat, counts in by_skill.items():
            G_FEEDBACK_BY_SKILL.labels(category=cat, feedback="positive").set(counts["pos"])
            G_FEEDBACK_BY_SKILL.labels(category=cat, feedback="negative").set(counts["neg"])
    except Exception:
        pass


EVOLVE_QUEUE_PATH = Path(__file__).parent / "learning" / "improvement_queue.json"
EVOLVE_LOG_PATH = Path("/home/tenazas/.openclaw/workspace/memory/evolution-log.md")


def update_evolve_metrics():
    """Read improvement_queue.json and evolution-log.md for self-evolve metrics."""
    try:
        # Proposals from queue
        if EVOLVE_QUEUE_PATH.exists():
            proposals = json.loads(EVOLVE_QUEUE_PATH.read_text())
            G_EVOLVE_TOTAL.set(len(proposals))

            # By type + priority
            by_type = {}
            by_status = {}
            for p in proposals:
                key = (p.get("type", "unknown"), p.get("priority", "unknown"))
                by_type[key] = by_type.get(key, 0) + 1
                st = p.get("status", "unknown")
                by_status[st] = by_status.get(st, 0) + 1

            for (t, prio), count in by_type.items():
                G_EVOLVE_BY_TYPE.labels(type=t, priority=prio).set(count)
            for st, count in by_status.items():
                G_EVOLVE_BY_STATUS.labels(status=st).set(count)

        # Per-proposal detail (for Grafana table)
        G_EVOLVE_DETAIL._metrics.clear()  # reset to avoid stale entries
        for i, p in enumerate(proposals[-20:]):  # last 20
            G_EVOLVE_DETAIL.labels(
                status=p.get("status", "?"),
                priority=p.get("priority", "?"),
                type=p.get("type", "?"),
                description=p.get("description", "")[:80],
            ).set(1 if p.get("status") == "processed" else 0)

        # Run count from evolution-log.md (count ## headers)
        if EVOLVE_LOG_PATH.exists():
            text = EVOLVE_LOG_PATH.read_text()
            runs = text.count("\n## ")
            G_EVOLVE_RUNS.set(runs)

            # Last run timestamp from last ## header
            import re
            dates = re.findall(r"## (\d{4}-\d{2}-\d{2} \d{2}:\d{2})", text)
            if dates:
                from datetime import datetime
                last = datetime.strptime(dates[-1], "%Y-%m-%d %H:%M")
                G_EVOLVE_LAST_RUN.set(last.timestamp())
    except Exception:
        pass


EVOLVE_HTML_PATH = Path(__file__).parent / "learning" / "proposals_dashboard.html"


def update_evolve_html():
    """Generate HTML for Grafana Text panel with proposal checklist."""
    try:
        queue = json.loads(EVOLVE_QUEUE_PATH.read_text()) if EVOLVE_QUEUE_PATH.exists() else []
        if not queue:
            EVOLVE_HTML_PATH.write_text("<p>Sin propuestas.</p>")
            return

        lines = ['<table style="width:100%;font-size:13px;border-collapse:collapse">']
        lines.append('<tr style="border-bottom:1px solid #444"><th>Estado</th>'
                     '<th>Prioridad</th><th>Tipo</th><th>Descripcion</th></tr>')

        status_icons = {
            "processed": "&#9989;",   # green check
            "approved": "&#128308;",  # yellow pending
            "proposed": "&#9898;",    # white circle
            "failed": "&#10060;",     # red X
            "rejected": "&#10060;",
        }
        prio_colors = {
            "high": "#ff4444",
            "medium": "#ffaa00",
            "low": "#888888",
        }

        for p in queue:
            st = p.get("status", "proposed")
            icon = status_icons.get(st, "&#9898;")
            prio = p.get("priority", "?")
            pcolor = prio_colors.get(prio, "#888")
            typ = p.get("type", "?")
            desc = p.get("description", "")[:80]
            lines.append(
                f'<tr style="border-bottom:1px solid #333">'
                f'<td style="text-align:center">{icon}</td>'
                f'<td style="color:{pcolor};text-align:center">{prio}</td>'
                f'<td>{typ}</td>'
                f'<td>{desc}</td></tr>'
            )

        lines.append('</table>')
        EVOLVE_HTML_PATH.parent.mkdir(parents=True, exist_ok=True)
        EVOLVE_HTML_PATH.write_text("\n".join(lines))
    except Exception:
        pass


def update_smoking_metrics():
    """Read smoking metrics.json and update Prometheus gauges."""
    try:
        if not SMOKING_METRICS_PATH.exists():
            return
        data = json.loads(SMOKING_METRICS_PATH.read_text())
        G_SMOKING_TODAY.set(data.get("today", 0))
        G_SMOKING_WEEK.set(data.get("week", 0))
        G_SMOKING_TOTAL.set(data.get("total", 0))

        # Per-day breakdown
        for d_str, d_data in data.get("by_day", {}).items():
            label = d_data.get("label", d_str)
            G_SMOKING_DAY.labels(date=d_str, label=label).set(d_data.get("count", 0))

            # Per-hour per-day
            for h, count in d_data.get("hours", {}).items():
                G_SMOKING_HOUR.labels(date=d_str, hour=h).set(count)
    except Exception:
        pass


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


# ── Device Discovery ──────────────────────────────────────────

INVENTORY_FILE = BASE_DIR / "device_inventory.json"

def update_ba_metrics():
    """Update B+A roadmap metrics: goals, staging, experiments, offline, preferences."""
    if not PROM_AVAILABLE:
        return
    try:
        # Goals failing
        try:
            goals_file = BASE_DIR / "homelab_goals_state.json"
            if not goals_file.exists():
                goals_file = Path("/home/tenazas/.openclaw/state/homelab_goals.json")
            if goals_file.exists():
                goals = json.loads(goals_file.read_text())
                if isinstance(goals, list):
                    failing = sum(1 for g in goals if g.get("status") == "fail")
                    G_BA_GOALS_FAILING.set(failing)
        except Exception:
            pass

        # Staging pending
        try:
            STATE = Path("/home/tenazas/.openclaw/state") if Path("/home/tenazas").exists() else BASE_DIR.parent / "state"
            cand_file = STATE / "improvement_candidates.json"
            if cand_file.exists():
                cands = json.loads(cand_file.read_text())
                if isinstance(cands, list):
                    pending = sum(1 for c in cands if c.get("status") in ("proposed", "staged"))
                    G_BA_STAGING_PENDING.set(pending)
        except Exception:
            pass

        # Experiments active
        try:
            exp_dir = STATE / "experiments"
            if exp_dir.exists():
                active = 0
                for f in exp_dir.glob("*.json"):
                    try:
                        e = json.loads(f.read_text())
                        if e.get("status") == "active":
                            active += 1
                    except Exception:
                        pass
                G_BA_EXPERIMENTS_ACTIVE.set(active)
        except Exception:
            pass

        # Backend offline events 24h
        try:
            mining_dir = STATE / "error_mining"
            today = datetime.now().strftime("%Y-%m-%d")
            mining_file = mining_dir / f"daily_{today}.json"
            if mining_file.exists():
                report = json.loads(mining_file.read_text())
                offline = sum(1 for e in report.get("errors", [])
                              if e.get("type") == "backend_offline")
                G_BA_BACKEND_OFFLINE_24H.set(offline)
        except Exception:
            pass

        # Preference confidence (top categories only)
        try:
            prefs_file = STATE / "preferences.json"
            if prefs_file.exists():
                prefs = json.loads(prefs_file.read_text())
                for cat, conf in list(prefs.get("confidence", {}).items())[:20]:
                    G_BA_PREFERENCE_CONF.labels(category=cat).set(conf)
        except Exception:
            pass

    except Exception as e:
        log.warning("B+A metrics update failed: %s", e)


def update_device_discovery_metrics():
    """Read device_inventory.json and update device discovery gauges."""
    if not PROM_AVAILABLE:
        return
    try:
        if not INVENTORY_FILE.exists():
            return
        with open(INVENTORY_FILE) as f:
            devices = json.load(f)
        if not isinstance(devices, list):
            return

        total = len(devices)
        missing = sum(1 for d in devices if d.get("missing_since"))
        online = total - missing
        classified = sum(1 for d in devices if d.get("category", "unknown") != "unknown")
        controllable = sum(1 for d in devices if d.get("control"))

        G_DD_TOTAL.set(total)
        G_DD_ONLINE.set(online)
        G_DD_MISSING.set(missing)
        G_DD_CLASSIFIED.set(classified)
        G_DD_CONTROLLABLE.set(controllable)

        # By category
        by_cat = {}
        for d in devices:
            cat = d.get("category", "unknown")
            by_cat[cat] = by_cat.get(cat, 0) + 1
        for cat, count in by_cat.items():
            G_DD_BY_CATEGORY.labels(category=cat).set(count)

        # By vendor (only known vendors)
        by_vendor = {}
        for d in devices:
            vendor = d.get("vendor")
            if vendor:
                by_vendor[vendor] = by_vendor.get(vendor, 0) + 1
        for vendor, count in by_vendor.items():
            G_DD_BY_VENDOR.labels(vendor=vendor).set(count)

        # Last scan timestamp from most recent last_seen
        latest = 0
        for d in devices:
            ls = d.get("last_seen", "")
            if ls:
                try:
                    ts = datetime.fromisoformat(ls).timestamp()
                    if ts > latest:
                        latest = ts
                except (ValueError, TypeError):
                    pass
        if latest > 0:
            G_DD_LAST_SCAN.set(latest)

    except Exception as e:
        log.warning("Device discovery metrics failed: %s", e)


ROI_STATE_FILE = LOGS_DIR / "roi_state.json"


def update_roi_metrics():
    """Read roi_state.json and update ROI Prometheus gauges."""
    if not PROM_AVAILABLE:
        return
    try:
        if not ROI_STATE_FILE.exists():
            return
        data = json.loads(ROI_STATE_FILE.read_text())
        totals = data.get("totals", {})

        G_ROI_SKILL_EXECUTIONS.set(totals.get("skill_executions", 0))
        G_ROI_LLM_CHATS.set(totals.get("llm_chats", 0))
        G_ROI_AUTOMATION_RATE.set(totals.get("automation_rate", 0))
        G_ROI_HOURS_SAVED.set(totals.get("hours_saved", 0))
        G_ROI_MINUTES_SAVED.set(totals.get("minutes_saved", 0))
        G_ROI_ERRORS_AVOIDED.set(totals.get("errors_avoided", 0))

        detail = data.get("errors_avoided_detail", {})
        G_ROI_CONTAINER_RESTARTS.set(detail.get("container_restarts", 0))
        G_ROI_VPN_FIXES.set(detail.get("vpn_fixes", 0))

        G_ROI_PERIOD_DAYS.set(data.get("period_days", 7))

        gen_at = data.get("generated_at", "")
        if gen_at:
            try:
                G_ROI_LAST_UPDATE.set(datetime.fromisoformat(gen_at).timestamp())
            except (ValueError, TypeError):
                pass

        # Per-skill labeled metrics
        for skill, info in data.get("skills", {}).items():
            clean = skill.replace("skill:", "")
            G_ROI_BY_SKILL.labels(skill=clean).set(info.get("count", 0))
            G_ROI_BY_SKILL_SUCCESS.labels(skill=clean).set(info.get("success_rate", 0))

        for skill, hours in data.get("hours_saved_detail", {}).items():
            clean = skill.replace("skill:", "")
            G_ROI_BY_SKILL_HOURS.labels(skill=clean).set(hours)

    except Exception as e:
        log.warning("ROI metrics update failed: %s", e)


def update_agent_metrics():
    """Read agent_metrics_state.json and update Prometheus gauges."""
    if not PROM_AVAILABLE:
        return
    try:
        if not AGENT_METRICS_STATE_FILE.exists():
            return
        data = json.loads(AGENT_METRICS_STATE_FILE.read_text())
        per_agent = data.get("per_agent", {})
        coord = data.get("coordinator", {})

        G_AGENT_QUERIES._metrics.clear()
        G_AGENT_LATENCY_LAST._metrics.clear()
        G_AGENT_LATENCY_AVG._metrics.clear()
        G_AGENT_OUTCOMES._metrics.clear()
        G_COORD_ROUTES._metrics.clear()
        G_COORD_FALLBACKS._metrics.clear()
        G_COORD_HANDLED._metrics.clear()

        for agent, info in per_agent.items():
            G_AGENT_QUERIES.labels(agent=agent).set(info.get("queries_total", 0))
            G_AGENT_LATENCY_LAST.labels(agent=agent).set(info.get("latency_last", 0))
            G_AGENT_LATENCY_AVG.labels(agent=agent).set(info.get("latency_avg", 0))
            for outcome, count in info.get("outcomes", {}).items():
                G_AGENT_OUTCOMES.labels(agent=agent, outcome=outcome).set(count)

        for strategy, count in coord.get("routes", {}).items():
            G_COORD_ROUTES.labels(strategy=strategy).set(count)
        for source, count in coord.get("fallbacks", {}).items():
            G_COORD_FALLBACKS.labels(source=source).set(count)
        for handled, count in coord.get("handled", {}).items():
            G_COORD_HANDLED.labels(handled=handled).set(count)
    except Exception as e:
        log.warning("Agent metrics update failed: %s", e)


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

        # Every 30s: Smoking + Self-evolve + Feedback + Device Discovery (local reads)
        update_smoking_metrics()
        update_evolve_metrics()
        update_evolve_html()
        update_feedback_metrics()
        update_device_discovery_metrics()
        update_ba_metrics()
        update_roi_metrics()
        update_agent_metrics()

        # Network scan freshness (read timestamp file written by router_runtime)
        try:
            ts_file = Path("/home/tenazas/runtime/network_scan_last_ts")
            if ts_file.exists():
                G_NETWORK_SCAN_TS.set(float(ts_file.read_text().strip()))
        except Exception:
            pass

        # Portfolio reconciler state (reads reconcile_state.json)
        try:
            _state_dir = (Path("/home/tenazas/.openclaw/state")
                          if Path("/home/tenazas").exists()
                          else BASE_DIR.parent / "state")
            _reconcile_file = _state_dir / "reconcile_state.json"
            if _reconcile_file.exists():
                _rstate = json.loads(_reconcile_file.read_text())
                G_RECONCILER_OK.set(1.0 if _rstate.get("ok") else 0.0)
                G_RECONCILER_ISSUES.set(float(len(_rstate.get("issues", []))))
                G_RECONCILER_BIN_DIFF.set(
                    _rstate.get("checks", {}).get("binance", {}).get("diff_pct", 0))
                G_RECONCILER_IOL_DIFF.set(
                    _rstate.get("checks", {}).get("iol", {}).get("diff_pct", 0))
                G_RECONCILER_LAST_RUN.set(time.time())
        except Exception:
            pass

        time.sleep(POLL_INTERVAL)
