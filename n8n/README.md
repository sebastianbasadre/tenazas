# n8n

El repositorio incluye workflows y un script de importación. `data/` se usa para
persistencia local y está excluido de Git.

El servicio requiere activación explícita:

```bash
docker compose --profile migration up -d n8n
# Después del preflight y respaldo:
docker exec n8n /workspace/import-workflows.sh
```

La versión declarada en Compose es `2.19.5`. Se usa como scheduler/orquestador que
llama al runner de Tenazas mediante HTTP y `X-Tenazas-Job-Token`; no reemplaza la
lógica de routing de Telegram.

El runner escucha en `127.0.0.1` por defecto. El target de n8n,
`http://host.docker.internal:18891`, requiere un bind del host alcanzable desde la
red Docker. Resolver conectividad y restricciones antes de activar schedules.

Importar no equivale a completar un cutover: comprobar cada workflow, su estado,
las notificaciones y el scheduler anterior para evitar duplicación.

- [Runbook de cutover](../docs/n8n-cutover-runbook.md)
- [Allowlist y timeouts](../docs/n8n-phase1-jobs.md)
- [Checklist DSM](../docs/dsm-task-checklist.md)
- [Plan de migración](../docs/platform-migration-plan.md)

Revisión: 2026-10-28, basada en código; no confirma workflows activos en el NAS.
