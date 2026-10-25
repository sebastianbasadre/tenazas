"""Read-only summaries, diagnostics and employment workflows for Telegram."""

import json
import re
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

from assistant_jobs import Jobs, ROOT
from runtime_utils import load_json

BASE = Path(__file__).parent


def daily(chat):
    from user_memory import get_profile
    from homelab_snapshot import _collect_containers
    from iol_dependency import status

    profile = get_profile()
    lines = [f"Buen día, {profile.get('name', 'Sebas')}. Resumen personal:"]
    try:
        from notification_poller import GCAL_SCRIPT

        result = subprocess.run([sys.executable, GCAL_SCRIPT, "1"], capture_output=True, text=True, timeout=15)
        if result.returncode:
            lines.append("Agenda: no pude consultar el calendario.")
        else:
            events = [line for line in result.stdout.splitlines() if "|" in line][:6]
            lines.append("Agenda:\n" + "\n".join(events) if events else "Agenda: sin eventos informados para hoy.")
    except Exception:
        lines.append("Agenda: no disponible.")
    tasks = load_json(ROOT / "assistant/todos.json", {}).get(str(chat), [])
    pending = [item for item in tasks if not item.get("done")]
    lines.append("Pendientes: " + ("; ".join(item["text"] for item in pending[:5]) or "ninguno registrado"))
    active = [item for item in Jobs().list(chat) if item["status"] in {"received", "running", "waiting_approval", "interrupted"}]
    lines.append(f"Trabajos para revisar: {len(active)}. Consultá /jobs.")
    containers = _collect_containers()
    if containers.get("error"):
        lines.append("Homelab: Docker no disponible para consulta.")
    else:
        lines.append(f"Homelab: {containers['running']}/{containers['total']} activos, {containers['unhealthy']} unhealthy, {containers['stopped']} detenidos.")
    opportunities = load_json(BASE / "jobs/opportunities.json", [])
    if isinstance(opportunities, dict):
        opportunities = opportunities.get("opportunities", [])
    opportunities = sorted(opportunities, key=lambda item: item.get("match_score", 0), reverse=True)
    lines.append("Empleo: " + ("; ".join(f"{item.get('title', '?')} · {item.get('company', '?')} ({item.get('match_score', '?')}%)" for item in opportunities[:3]) or "sin vacantes guardadas"))
    lines.append("Finanzas: IOL " + ("degradado; los datos pueden ser históricos" if status()["degraded"] else "disponible"))
    return "\n\n".join(lines)[:3500]


def weekly(chat):
    with Jobs().connect() as connection:
        rows = connection.execute("SELECT kind,status,created,updated FROM assistant_jobs WHERE chat=? AND created>=?", (str(chat), time.time() - 7 * 86400)).fetchall()
    finished = [row for row in rows if row["status"] in {"done", "failed"}]
    failures = Counter(row["kind"] for row in rows if row["status"] in {"failed", "interrupted"})
    durations = [row["updated"] - row["created"] for row in finished]
    metrics = load_json(BASE / "logs/inference_state.json", {})
    lines = ["Informe semanal de mejoras", f"Últimos 7 días: {len(rows)} trabajos; {sum(row['status'] == 'done' for row in rows)} terminados.",
             "Errores frecuentes: " + (", ".join(f"{name}: {count}" for name, count in failures.most_common(4)) or "sin fallos registrados"),
             f"Tiempo desde recepción hasta cierre: promedio {sum(durations) / len(durations):.1f}s, máximo {max(durations):.1f}s." if durations else "Todavía no hay tiempos suficientes para comparar."]
    if metrics.get("ts"):
        lines.append(f"Proxy GPU, ventana reciente de {metrics.get('latency_window_size', 0)} llamadas: p95 {metrics.get('latency_p95_seconds', 0):.2f}s; espera máxima {metrics.get('queue_wait_max_seconds', 0):.2f}s.")
    proposals = []
    if failures:
        proposals.append(f"Revisar primero {failures.most_common(1)[0][0]}: concentra más fallos de trabajos.")
    if metrics.get("queue_wait_max_seconds", 0) > 10:
        proposals.append("Mover análisis pesados fuera de las horas de chat: la espera GPU supera 10s.")
    if any(row["status"] == "interrupted" for row in rows):
        proposals.append("Revisar trabajos interrumpidos antes de repetir acciones externas.")
    if not proposals:
        proposals.append("Mantener la configuración actual y reunir otra semana de datos antes de cambiar modelos.")
    return "\n\n".join(lines + ["Propuestas:\n" + "\n".join(f"- {item}" for item in proposals)])


def diagnose(target):
    target = target.strip()
    if not target or target.lower() == "homelab":
        from homelab_snapshot import _collect_containers
        data = _collect_containers()
        return "Diagnóstico homelab:\n" + json.dumps(data, ensure_ascii=False) + "\nPara analizar una causa concreta: /diagnostico nombre_del_contenedor."
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", target):
        raise ValueError("Nombre de contenedor inválido")
    process = subprocess.run(["docker", "inspect", target], capture_output=True, text=True, timeout=10)
    if process.returncode:
        return f"No encontré el contenedor {target}. No ejecuté cambios."
    data = json.loads(process.stdout)[0]
    state = data["State"]
    evidence = {"running": state.get("Running"), "exit_code": state.get("ExitCode"), "oom_killed": state.get("OOMKilled"),
                "health": state.get("Health", {}).get("Status", "sin healthcheck"), "restart_count": data.get("RestartCount", 0)}
    if evidence["oom_killed"]:
        cause = "Docker registró una terminación por falta de memoria."
        action = "Revisar consumo y límites de memoria antes de reiniciar; guardar Compose y la imagen actual."
        rollback = "Restaurar los límites guardados y recrear con la imagen anterior si el ajuste no mejora el consumo."
    elif not evidence["running"] and evidence["exit_code"] == 0:
        cause = "Está detenido con salida normal; esta evidencia no demuestra un fallo."
        action = "Confirmar si debe estar activo. Si corresponde, iniciar sólo ese servicio."
        rollback = "Volver a detener el mismo servicio para recuperar su estado previo."
    elif evidence["health"] == "unhealthy":
        cause = "El healthcheck falla; puede fallar el servicio o una dependencia. La causa exacta requiere revisar ese probe."
        action = "Revisar el healthcheck y sus dependencias; conservar configuración e imagen antes de cualquier cambio."
        rollback = "Restaurar la configuración e imagen guardadas; comprobar otra vez el healthcheck."
    else:
        cause = "No hay una causa de fallo demostrada por este estado."
        action = "Comparar la hora del incidente con reinicios, memoria y errores de la dependencia."
        rollback = "No se aplicó ningún cambio."
    return f"Diagnóstico: {target}\nEvidencia: {json.dumps(evidence, ensure_ascii=False)}\nCausa probable: {cause}\nAcción propuesta: {action}\nRollback: {rollback}\nNo ejecuté cambios."


def search(query):
    from agent_job_search import discover, analyze_matches, _ranked_opportunities, _format_links, _format_cv_analysis
    result = discover(query)
    analysis = analyze_matches(limit=5)
    items = _ranked_opportunities()[:5]
    return f"Búsqueda laboral: {result.get('added', 0)} nuevas, {result.get('updated', 0)} actualizadas.\n" + (_format_links(items) if items else "Sin oportunidades guardadas.") + "\n" + _format_cv_analysis(analysis)


def prepare_cv(opportunity_id):
    from agent_job_search import prepare_tailored_cv_for_id, _load_opportunities
    from job_application_manager import stage
    item = next((item for item in _load_opportunities() if item.get("id") == opportunity_id), None)
    if not item:
        raise ValueError("La vacante seleccionada ya no está disponible")
    result = prepare_tailored_cv_for_id(opportunity_id)
    if result.get("error"):
        raise ValueError(result["error"])
    entry = stage(item, result)
    return {"text": f"CV preparado para {entry['title']} — {entry['company']}.\nFortalezas: {', '.join(result.get('strengths', []))}\nBrechas: {', '.join(result.get('gaps', []))}\nNo se envió ninguna postulación.",
            "file": result.get("path"), "application_id": entry["id"], "approval_required": True}


def submit_application(payload):
    from job_application_manager import _find, _save, submit
    if not payload.get("approval"):
        raise ValueError("Falta aprobación explícita")
    application = payload["prepared"]["application_id"]
    queue, entry = _find(application)
    if not entry or entry["status"] not in {"pending_approval", "approved"}:
        raise ValueError("La postulación ya fue procesada o no existe")
    entry.update({"status": "approved", "approved_at": payload["approval"]["at"]})
    _save(queue)
    result = submit(application)
    response = {"text": result["message"] + "\nEstado: " + result["status"], "ok": result["ok"]}
    if result["ok"] and result["status"] == "submitted":
        # A tracker failure must never turn a confirmed send into a failed send.
        try:
            from application_tracker import Applications
            tracked = Applications().add(payload["approval"]["chat"], entry["company"], entry["title"], entry.get("url", ""), entry.get("cv_filename", ""))
            response["text"] += "\nSeguimiento: /postulaciones ver " + tracked["id"]
        except Exception:
            response["text"] += "\nEl envío está confirmado; no pude registrar el seguimiento. Agregalo con /postulaciones agregar."
    return response


def application_digest(chat):
    from application_tracker import Applications, summary
    from application_mail import scan
    from runtime_config import get_default_telegram_chat_id

    due = Applications().due(chat)
    mail = {"new": 0, "text": ""}
    mail_failed = False
    if str(chat) == str(get_default_telegram_chat_id() or ""):
        try:
            mail = scan(chat)
        except Exception:
            mail_failed = True
            mail["text"] = "Gmail no disponible para esta revisión. Las postulaciones y sus estados se conservaron."
    parts = ["Seguimiento laboral", summary(chat, due_only=True)]
    if mail["text"]:
        parts.append(mail["text"])
    return {"text": "\n\n".join(parts)[:3800], "skip_delivery": not due and not mail["new"] and not mail_failed}
