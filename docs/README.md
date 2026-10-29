# Documentación de Tenazas

Revisión: **2026-10-28**. La referencia técnica es el código de la rama revisada;
los inventarios y reportes históricos no prueban el estado actual de producción.

## Instalación y desarrollo

| Documento | Contenido |
|---|---|
| [Presentación](../README.md) | Alcance y componentes |
| [Instalación](setup.md) | Requisitos, configuración, mounts y arranque |
| [Arquitectura](architecture.md) | Telegram, agentes, workers, modelos y estado |
| [Desarrollo](development.md) | Entorno Python, pruebas y revisión de cambios |
| [Infraestructura](../INFRA.md) | Servicios, puertos, métricas y dependencias externas |
| [Modelos](../MODELS.md) | Catálogo y cadenas de fallback del código |
| [Contrato de routing](../workspace/runtime/INTERFACE_CONTRACT.md) | Labels y APIs reales |

## Operación

| Documento | Contenido |
|---|---|
| [HomeLab Ollama](homelab-ollama-runbook.md) | Arranque Windows y diferencias entre scripts/modelos |
| [Migración de plataforma](platform-migration-plan.md) | Arquitectura objetivo y fases propuestas |
| [Jobs n8n](n8n-phase1-jobs.md) | Allowlist, timeouts y schedules de referencia |
| [Cutover n8n](n8n-cutover-runbook.md) | Conectividad, activación gradual y rollback |
| [Checklist DSM](dsm-task-checklist.md) | Tareas programadas y wrappers |
| [Directorio n8n](../n8n/README.md) | Workflows y persistencia |
| [Búsqueda laboral](job-search-platform.md) | Perfil, oportunidades y CV |
| [Plan de mejoras](improvement-execution-plan.md) | Hardening y pendientes |

## Contexto e historia

- [Workspace](../workspace/README.md) y [contexto resumido](../workspace/TENAZAS-CONTEXT.md).
- [Staging](../workspace/staging/README.md): candidatos y revisión.
- [Instrucciones para Claude Code](../CLAUDE.md): incluye secciones generadas e históricas.
- [Notas de schema](../tools/SCHEMA_NOTES.md): observaciones de mayo de 2026.
- [Reporte 2026-05-21](../tools/REPORT_20260521.md): cambios observados en esa fecha.
- [Estado de planificación](../.planning/STATE.md): historial de milestones.

Los documentos de `.planning/`, reportes fechados y secciones generadas de
`CLAUDE.md` conservan su contexto histórico. Para decisiones de instalación o
routing usar las guías vigentes y verificar los archivos fuente enlazados.
