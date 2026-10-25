"""Owner-scoped Telegram commands; long operations are always queued."""

import json
import re
import uuid
from pathlib import Path

from assistant_jobs import Jobs, LABELS, ROOT
from runtime_utils import StateLock, load_json, write_atomic

COMMANDS = {"/resumen", "/semanal", "/jobs", "/cancelar", "/aprobar", "/memoria", "/empleo",
            "/cv", "/postulaciones", "/documentos", "/diagnostico", "/rutina", "/tareas"}
HELP = ("/resumen · resumen personal\n/semanal · errores y mejoras\n/jobs [id] · trabajos\n"
        "/cancelar id · cancelar\n/aprobar id · aprobar una propuesta\n"
        "/memoria [ver|poner campo valor|borrar campo|limpiar] · preferencias\n"
        "/empleo [lista|buscar texto] · vacantes\n/cv número · preparar CV y postulación\n"
        "/postulaciones [ayuda|agregar|estado|pendientes|correo] · seguimiento laboral\n/documentos [lista|borrar id|pregunta] · archivos y fuentes\n"
        "Adjuntá archivos con la leyenda /documento.\n"
        "/diagnostico contenedor · evidencia y rollback\n/rutina cine · plan para revisar\n"
        "/tareas [agregar texto|terminar id] · pendientes")


def enqueue(chat, kind, payload=None):
    job = Jobs().create(chat, kind, payload)
    return f"Recibido · {job['id']}\nVoy a trabajar en {kind}. Seguimiento: /jobs {job['id']}\nCancelar: /cancelar {job['id']}"


def memory(argument):
    from user_memory import get_profile, update_profile, forget_profile_field, save_profile, clear_context
    parts = argument.split(maxsplit=2)
    action = parts[0].lower() if parts else "ver"
    if action == "ver":
        return "Memoria editable:\n" + json.dumps(get_profile(), ensure_ascii=False, indent=2)[:3600]
    if action == "limpiar":
        save_profile({})
        clear_context()
        return "Borré el perfil aprendido y el contexto reciente. Los documentos y postulaciones se gestionan por separado."
    if action == "borrar" and len(parts) == 2:
        return "Preferencia borrada." if forget_profile_field(parts[1]) else "No encontré esa preferencia."
    if action == "poner" and len(parts) == 3:
        key = parts[1]
        allowed = {"name", "city", "country", "timezone", "language", "tone_preference", "projects", "interests", "roles_target", "notes", "job_search"}
        if key not in allowed:
            return "Campo no editable. Permitidos: " + ", ".join(sorted(allowed))
        try:
            value = json.loads(parts[2])
        except ValueError:
            value = parts[2]
        if key in {"projects", "interests", "roles_target", "notes"} and not (isinstance(value, list) and all(isinstance(item, str) for item in value)):
            return 'Ese campo necesita una lista JSON, por ejemplo ["Tenazas", "Homelab"].'
        if key == "job_search" and not isinstance(value, bool):
            return "job_search necesita true o false."
        if key not in {"projects", "interests", "roles_target", "notes", "job_search"} and not isinstance(value, str):
            return "Ese campo necesita texto."
        if len(json.dumps(value)) > 2000:
            return "Preferencia demasiado larga."
        update_profile(key, value)
        return f"Actualicé {key}."
    return "Usá /memoria ver, /memoria poner campo valor, /memoria borrar campo o /memoria limpiar."


def todos(chat, argument):
    path = ROOT / "assistant/todos.json"
    with StateLock(path):
        data = load_json(path, {})
        items = data.setdefault(str(chat), [])
        if argument.startswith("agregar "):
            text = argument[8:].strip()
            if not text or len(text) > 500:
                return "Indicá una tarea de hasta 500 caracteres."
            items.append({"id": uuid.uuid4().hex[:8], "text": text, "done": False})
            write_atomic(path, data)
        elif argument.startswith("terminar "):
            selected = argument[9:].strip()
            match = next((item for item in items if item["id"] == selected), None)
            if not match:
                return "Tarea no encontrada."
            match["done"] = True
            write_atomic(path, data)
        return "Pendientes:\n" + ("\n".join(f"{item['id']} · {item['text']}" for item in items if not item["done"]) or "Ninguno registrado.")


def handle(text, chat):
    command, _, argument = text.strip().partition(" ")
    command = command.split("@", 1)[0].lower()
    if command not in COMMANDS:
        return None
    if not chat:
        return "Este comando necesita un chat autorizado."
    argument = argument.strip()
    store = Jobs()
    if command in {"/resumen", "/semanal"}:
        kind = "daily" if command == "/resumen" else "weekly"
        if argument.startswith("horario "):
            from assistant_schedule import configure
            weekdays = {"lunes": 0, "martes": 1, "miércoles": 2, "miercoles": 2, "jueves": 3, "viernes": 4, "sábado": 5, "sabado": 5, "domingo": 6}
            fields = argument[8:].split()
            try:
                clock = fields[-1]
                day = weekdays[fields[0].lower()] if kind == "weekly" and len(fields) == 2 else None
                configure(kind, clock, day)
                return "Horario actualizado (Argentina)." if clock != "off" else "Envío automático desactivado; sigue disponible bajo demanda."
            except (ValueError, KeyError, IndexError):
                return "Usá /resumen horario 09:00 o /semanal horario viernes 18:00. Para pausar: horario off."
        return enqueue(chat, kind)
    if command == "/jobs":
        if argument:
            job = store.get(argument, chat)
            if not job:
                return "Trabajo no encontrado en este chat."
            result = job["result"].get("text", "")
            suffix = f"\n/aprobar {job['id']} o /cancelar {job['id']}" if job["status"] == "waiting_approval" else ""
            return f"{job['id']} · {LABELS[job['status']]} · {job['kind']}\n{job['phase']}\n{result}"[:3500] + suffix
        return "Trabajos:\n" + ("\n".join(f"{item['id']} · {LABELS[item['status']]} · {item['kind']}" for item in store.list(chat)) or "Sin trabajos registrados.")
    if command == "/cancelar":
        return "Cancelación solicitada. Si ya hubo efectos externos, revisá el resultado y el rollback." if store.cancel(argument, chat) else "No encontré un trabajo cancelable en este chat."
    if command == "/aprobar":
        return "Aprobación registrada. Seguí el trabajo con /jobs " + argument if store.approve(argument, chat) else "No encontré una propuesta pendiente de aprobación en este chat."
    if command == "/memoria":
        return memory(argument)
    if command == "/tareas":
        return todos(chat, argument)
    if command == "/empleo":
        if argument.startswith("buscar"):
            return enqueue(chat, "search", {"query": argument[6:].strip()})
        from agent_job_search import _ranked_opportunities
        items = _ranked_opportunities()[:10]
        return "Vacantes guardadas (usá /cv número):\n" + ("\n".join(f"{index}. {item.get('title', '?')} · {item.get('company', '?')} ({item.get('match_score', 0)}%)\n{item.get('url', '')}" for index, item in enumerate(items, 1)) or "Sin vacantes. Usá /empleo buscar tu rol.")
    if command == "/cv":
        from agent_job_search import _ranked_opportunities
        items = _ranked_opportunities()
        if not argument.isdigit() or not 1 <= int(argument) <= len(items):
            return "Usá /cv número, según /empleo lista."
        return enqueue(chat, "cv", {"opportunity_id": items[int(argument) - 1]["id"]})
    if command == "/postulaciones":
        if argument == "propuestas":
            from runtime_config import get_default_telegram_chat_id
            if str(chat) != str(get_default_telegram_chat_id() or ""):
                return "Las propuestas anteriores están disponibles para el chat principal."
            from job_application_manager import _load
            return "Propuestas de envío (no son postulaciones confirmadas):\n" + ("\n".join(f"{item.get('title', '?')} · {item.get('company', '?')}: {item.get('status', '?')}" for item in _load()[-15:]) or "Ninguna registrada.")
        from application_tracker import handle as track_applications
        return track_applications(argument, chat)
    if command == "/documentos":
        from document_library import list_documents, delete
        if not argument or argument == "lista":
            return "Documentos:\n" + ("\n".join(f"{item[0]} · {item[1]}" for item in list_documents(chat)) or "Adjuntá un archivo con la leyenda /documento.")
        if argument.startswith("borrar "):
            return "Documento e índice borrados." if delete(argument[7:].strip(), chat) else "No encontré ese documento en este chat."
        return enqueue(chat, "documents", {"query": argument})
    if command == "/diagnostico":
        return enqueue(chat, "diagnose", {"target": argument or "homelab"})
    if command == "/rutina":
        return enqueue(chat, "routine_plan", {"name": argument or "cine"})


def upload(token, document, chat):
    from document_library import ALLOWED, LIBRARY
    from telegram_transport import telegram_request
    from urllib.request import urlopen
    if not re.fullmatch(r"-?\d+", str(chat)):
        return "Chat inválido."
    filename = Path(document.get("file_name", "documento.txt").replace("\\", "/")).name
    filename = re.sub(r"[\x00-\x1f]", " ", filename)[:160]
    if Path(filename).suffix.lower() not in ALLOWED:
        return "Formatos: PDF, DOCX, TXT, MD y CSV."
    maximum = 20 * 1024 * 1024
    if document.get("file_size", 0) > maximum:
        return "Máximo 20 MB por documento."
    info = telegram_request(token, "getFile", {"file_id": document["file_id"]})
    if not info or not info.get("ok"):
        return "No pude descargar el archivo."
    folder = LIBRARY / str(chat)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (uuid.uuid4().hex[:12] + "-" + filename)
    try:
        with urlopen(f"https://api.telegram.org/file/bot{token}/{info['result']['file_path']}", timeout=30) as response:
            data = response.read(maximum + 1)
        if len(data) > maximum:
            raise ValueError("Máximo 20 MB")
        path.write_bytes(data)
        return enqueue(chat, "document_index", {"path": str(path), "filename": filename})
    except Exception as exc:
        path.unlink(missing_ok=True)
        return "No pude indexar el documento: " + type(exc).__name__
