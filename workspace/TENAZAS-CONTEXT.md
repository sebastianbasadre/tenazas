# Contexto de Tenazas

Revisión: **2026-10-28**. Contexto del repositorio para colaboradores y asistentes;
no es una instrucción para ejecutar acciones en equipos reales.

Tenazas es el asistente personal de Sebastián sobre OpenClaw en un NAS Synology.
La interacción principal es Telegram. Usa runtime Python, Ollama en HomeLab,
modelos cloud según el flujo, servicios de observabilidad y herramientas personales.
El estilo del asistente es español rioplatense, directo y breve.

## Fuentes vigentes

- [README](../README.md): alcance y acceso a las guías.
- [Arquitectura](../docs/architecture.md): procesos, agentes y persistencia.
- [Instalación](../docs/setup.md): requisitos y límites del bootstrap.
- [Infraestructura](../INFRA.md): servicios, puertos y dependencias externas.
- [Modelos](../MODELS.md): configuración de los distintos selectores.
- [Contrato de routing](runtime/INTERFACE_CONTRACT.md): labels y APIs.
- [Desarrollo](../docs/development.md): entorno y validación.

## Decisiones que requieren atención

El coordinador se activa con `USE_AGENT_COORDINATOR` o la configuración Telegram
leída por Tenazas; el default es desactivado. No asumir que la implementación nueva
está habilitada en el NAS.

n8n usa el perfil Compose `migration`. DSM, loops internos y workflows pueden
coexistir en un despliegue existente; inventariarlos antes de migrar schedules.

La configuración del gateway tiene schema estricto. Las plantillas para helpers
no reemplazan una configuración OpenClaw validada. Las credenciales y chat IDs se
obtienen del estado y entorno del operador, sin fijarlos en esta documentación.

Las propuestas aceptadas pueden ejecutarse mediante el executor con sus controles;
los candidatos no deben confundirse con cambios ya desplegados. Ver
[staging](staging/README.md).

Los planes de `.planning/` y los reportes fechados registran decisiones e historia.
No usarlos como confirmación del inventario o métricas actuales.
