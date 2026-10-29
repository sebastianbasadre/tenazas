# Revisión documental — 2026-10-28

Base revisada: `19882c295c0ce4bd543ca2e6c6913ad14c62f14c`.

Alcance: documentación mantenida en raíz, `docs/`, workspace y n8n. Los reportes
fechados conservan sus resultados originales con una nota histórica. No se
reescribieron planes de milestones ni sus verificaciones pasadas.

Se contrastaron Compose, Dockerfile, startup, configuración, catálogos y roles,
contratos/dispatch, agentes, runner, wrappers, exporters y runner de pruebas.
Se corrigieron rutas inexistentes, modelos antiguos, salud/reinicio Docker,
intervalos Prometheus y conectividad entre n8n y el runner en localhost.

Se agregaron índice, instalación, arquitectura y desarrollo. La instalación
explicita sus dependencias externas y el límite de la plantilla de configuración.

No se ejecutaron servicios del NAS, workflows, acciones de broker, Telegram,
training ni despliegues. La documentación requiere verificación operativa en el
host para confirmar estado, credenciales, modelos y schedules activos.

Los prompts GSD del repositorio se inspeccionaron: requieren un engine externo
que no estaba disponible en las rutas locales/globales consultadas. Esta revisión
queda registrada aquí sin alterar el estado de milestones históricos.
