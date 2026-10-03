# Plataforma de búsqueda laboral

El agente `job_search` descubre oportunidades con el SearXNG configurado en
Tenazas, calcula compatibilidad usando `user_profile.json` y mantiene el estado
de cada postulación en `workspace/runtime/jobs/opportunities.json`.

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
