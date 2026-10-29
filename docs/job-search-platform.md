# Plataforma de búsqueda laboral

Revisión contra el código: **2026-10-28**.

El agente `job_search` descubre oportunidades con el SearXNG configurado en
Tenazas, calcula compatibilidad usando `user_profile.json` y mantiene el estado
de cada postulación en `workspace/runtime/jobs/opportunities.json`.

Además del agente, `startup.sh` inicia `job_search_watcher.py` con watchdog.
El intervalo es `JOB_SEARCH_INTERVAL_SECONDS` (default 600 s). Puede descubrir
vacantes, preparar documentos y notificar Telegram; no usarlo como una prueba
sin efectos sobre el estado del operador.

## Configuración

Completar en `workspace/runtime/user_profile.json`:

```json
{
  "city": "Buenos Aires",
  "country": "Argentina",
  "roles_target": ["Product Manager", "TAM", "DevOps"],
  "tools": ["Docker", "Ollama", "AWS"]
}
```

El matching de CV usa `qwen2.5:3b` en `agent_job_search.py`; comprobar que esté
instalado en el endpoint Ollama configurado. Los resultados de búsqueda y el
matching dependen de servicios externos y no garantizan elegibilidad ni vigencia.

## Datos y documentos

- `workspace/runtime/jobs/opportunities.json`: pipeline de oportunidades.
- `workspace/runtime/jobs/master_cv.txt`: CV maestro.
- `workspace/runtime/jobs/cv_template.txt` y `cv_template.pdf`: plantilla.
- `workspace/runtime/jobs/tailored/`: documentos preparados por oportunidad.
- Estado agregado: `.openclaw/state/agents/job_search_status.json` en contenedor;
  el fallback local usa `workspace/state/agents/`.

Las funciones `save_master_cv`, `save_cv_template`, `analyze_matches` y
`prepare_tailored_cv` dan soporte al flujo de documentos; no son subcomandos de
la CLI. Revisar contenido y datos personales antes de compartir cualquier CV.

## Uso

Desde Telegram o el asistente:

- `buscame trabajo`
- `buscar oportunidades de Product Manager remoto`
- `cómo van mis postulaciones`
- `tengo entrevistas pendientes`

Desde consola:

```powershell
python workspace/runtime/agent_job_search.py search "Product Manager remoto"
python workspace/runtime/agent_job_search.py status
python workspace/runtime/agent_job_search.py move ID applied --note "CV enviado"
```

Estados válidos: `detected`, `interested`, `applied`, `screening`, `interview`,
`offer`, `rejected`, `withdrawn` y `archived`.

La plataforma no envía postulaciones automáticamente. Esa acción requiere
revisión humana del aviso, la empresa y el CV correspondiente.
