#!/usr/bin/env python3
"""
Self-Debugger — Analyzes Tenazas runtime errors and log anomalies.

Reads log files from logs/ directory. Identifies:
- Repeated errors
- Timeouts
- Module failures

Safety: Read-only analysis. Never modifies logs or infrastructure.

Log: workspace/runtime/logs/self_debug.log
"""

import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

BASE_DIR = Path(__file__).parent
LOGS_DIR = BASE_DIR / "logs"
TELEMETRY_LOG = LOGS_DIR / "self_debug.log"

# Config
MAX_LOG_LINES = 1000
ERROR_KEYWORDS = ["error", "fail", "timeout", "exception", "traceback", "refused", "denied"]
LOOKBACK_HOURS = 24
MAX_ERRORS_PER_REPORT = 20


def _now():
    return datetime.now().astimezone()


def _log(entry: dict):
    TELEMETRY_LOG.parent.mkdir(parents=True, exist_ok=True)
    entry["ts"] = _now().isoformat()
    with open(TELEMETRY_LOG, "a") as f:
        f.write(json.dumps(entry, default=str) + "\n")


def _load_log_lines(log_path: Path, max_lines: int = MAX_LOG_LINES) -> list:
    """Load recent lines from a JSON-line log file."""
    lines = []
    try:
        with open(log_path) as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        lines.append(json.loads(line))
                    except json.JSONDecodeError:
                        # Non-JSON line — store as raw
                        lines.append({"raw": line, "ts": ""})
    except FileNotFoundError:
        pass
    return lines[-max_lines:]


def _is_error_entry(entry: dict) -> bool:
    """Check if a log entry represents an error."""
    # Explicit failure markers
    if entry.get("ok") is False:
        return True

    # Check for error field
    if entry.get("error"):
        return True

    # Check string fields for error keywords
    text = json.dumps(entry).lower()
    return any(kw in text for kw in ERROR_KEYWORDS)


def _classify_error(entry: dict) -> str:
    """Classify an error entry into a category."""
    text = json.dumps(entry).lower()

    if "timeout" in text:
        return "timeout"
    if "refused" in text or "connection" in text:
        return "connection_error"
    if "permission" in text or "denied" in text:
        return "permission_error"
    if "not found" in text or "filenotfound" in text:
        return "not_found"
    if "json" in text and ("decode" in text or "parse" in text):
        return "parse_error"
    if "import" in text or "module" in text:
        return "module_error"

    return "general_error"


def _extract_error_message(entry: dict) -> str:
    """Extract a human-readable error message from a log entry."""
    if entry.get("error"):
        return str(entry["error"])[:200]
    if entry.get("stderr"):
        return str(entry["stderr"])[:200]
    if entry.get("detail"):
        return str(entry["detail"])[:200]
    if entry.get("raw"):
        return str(entry["raw"])[:200]

    # Build from available fields
    parts = []
    if entry.get("action"):
        parts.append(f"action={entry['action']}")
    if entry.get("target"):
        parts.append(f"target={entry['target']}")
    if entry.get("type"):
        parts.append(f"type={entry['type']}")
    return " ".join(parts)[:200] if parts else "unknown error"


# ── Diagnosis Functions ─────────────────────────────────────────


def diagnose(log_path: Path) -> dict:
    """Analyze a single log file for errors and patterns.

    Args:
        log_path: absolute path to a JSON-line log file

    Returns:
        {
            "errors": list of error dicts,
            "root_cause": str,
            "suggestion": str,
        }
    """
    if not isinstance(log_path, Path):
        log_path = Path(log_path)

    lines = _load_log_lines(log_path)
    if not lines:
        return {
            "errors": [],
            "root_cause": "No log data found",
            "suggestion": "Verify the log file exists and has content",
        }

    errors = []
    error_categories = Counter()

    for entry in lines:
        if _is_error_entry(entry):
            category = _classify_error(entry)
            error_categories[category] += 1
            errors.append({
                "category": category,
                "message": _extract_error_message(entry),
                "ts": entry.get("ts", ""),
                "action": entry.get("action", ""),
                "target": entry.get("target", ""),
            })

    errors = errors[-MAX_ERRORS_PER_REPORT:]  # Keep most recent

    # Determine root cause from most common category
    if not error_categories:
        return {
            "errors": [],
            "root_cause": "No errors detected",
            "suggestion": "Log file is healthy",
        }

    top_category, top_count = error_categories.most_common(1)[0]
    total_errors = sum(error_categories.values())

    root_cause_map = {
        "timeout": "Timeouts frecuentes — posible problema de red o servicio lento",
        "connection_error": "Errores de conexion — servicio destino no responde",
        "permission_error": "Errores de permisos — revisar configuracion de acceso",
        "not_found": "Archivos o recursos no encontrados — verificar paths",
        "parse_error": "Errores de parseo JSON — datos corruptos o formato inesperado",
        "module_error": "Errores de modulo/import — dependencia faltante",
        "general_error": "Errores generales — revisar logs para mas detalle",
    }

    suggestion_map = {
        "timeout": "Verificar conectividad de red y timeouts de servicios",
        "connection_error": "Verificar que los servicios destino estan corriendo",
        "permission_error": "Revisar permisos de usuario y grupos (docker, filesystem)",
        "not_found": "Verificar que los archivos/paths existen y son accesibles",
        "parse_error": "Verificar formato de archivos JSON y respuestas de API",
        "module_error": "Verificar que las dependencias estan instaladas",
        "general_error": "Revisar manualmente los errores mas recientes",
    }

    result = {
        "errors": errors,
        "root_cause": root_cause_map.get(top_category, f"Categoria principal: {top_category}"),
        "suggestion": suggestion_map.get(top_category, "Revisar logs manualmente"),
        "summary": {
            "total_lines": len(lines),
            "total_errors": total_errors,
            "error_rate": round(total_errors / max(len(lines), 1), 4),
            "categories": dict(error_categories.most_common()),
        },
    }

    _log({
        "type": "diagnose",
        "log_file": str(log_path),
        "total_errors": total_errors,
        "top_category": top_category,
    })

    return result


def scan_all_logs() -> dict:
    """Scan all log files in the logs/ directory.

    Returns:
        {
            "scanned": int,
            "files_with_errors": int,
            "reports": {filename: diagnosis_dict},
            "overall_health": "healthy" | "degraded" | "unhealthy",
        }
    """
    if not LOGS_DIR.exists():
        return {
            "scanned": 0,
            "files_with_errors": 0,
            "reports": {},
            "overall_health": "healthy",
        }

    reports = {}
    files_with_errors = 0

    for log_file in sorted(LOGS_DIR.glob("*.log")):
        # Skip our own log to avoid recursion
        if log_file.name == "self_debug.log":
            continue

        result = diagnose(log_file)
        if result["errors"]:
            files_with_errors += 1
        reports[log_file.name] = result

    scanned = len(reports)

    # Overall health
    if files_with_errors == 0:
        health = "healthy"
    elif files_with_errors <= scanned / 3:
        health = "degraded"
    else:
        health = "unhealthy"

    summary = {
        "scanned": scanned,
        "files_with_errors": files_with_errors,
        "reports": reports,
        "overall_health": health,
    }

    _log({
        "type": "scan_all",
        "scanned": scanned,
        "files_with_errors": files_with_errors,
        "health": health,
    })

    return summary
