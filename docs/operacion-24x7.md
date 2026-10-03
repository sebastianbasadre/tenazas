# Tenazas 24/7

## Operacion

`service_supervisor.py` mantiene gateway, Telegram, exporters, busqueda laboral y
el control de inferencia. Reinicia procesos que terminan, aplica backoff y verifica
HTTP en los servicios que lo soportan. El ciclo de workers se agenda cada 300 s.
Un lock del sistema operativo evita solapamientos; el estado del supervisor evita
repetir el ciclo en el mismo intervalo si tambien lo dispara DSM.

Los cuatro agentes de fondo conservan sus intervalos: infraestructura 5 min,
patrones 60 min, metricas 30 min y seguridad 15 min. Los agentes por dominio del
coordinador se invocan bajo demanda y sus procesos se cancelan al vencer el plazo.
Busqueda laboral sigue su watcher cada 10 min. Finanzas conserva sus aprobaciones
y reconciliacion de solo lectura. Las propuestas de cambios conservan el flujo
existente de revision y pruebas; registrar un agente no autoriza acciones nuevas.

`tenazas-job-runner` recibe trabajos permitidos desde n8n en la red Docker interna.
Mantiene un registro SQLite persistente, excluye trabajos simultaneos por nombre y
reutiliza resultados cuando se repite `Idempotency-Key`. Los trabajos interrumpidos
requieren revision: no se repiten efectos externos automaticamente tras un crash.
Este registro no reemplaza una cola distribuida; Redis/PostgreSQL queda para cuando
la carga justifique varios workers independientes.

`tenazas-recovery-watchdog` reinicia exclusivamente OpenClaw y el runner cuando
Docker los marca unhealthy. Limita recuperacion a tres reinicios por hora y espera
al menos dos minutos entre intentos. Un healthcheck por si solo no reinicia Docker.

## Modelos configurados

| Trabajo | Modelo | Motivo |
|---|---|---|
| Chat, resumenes, empleo y razonamiento habitual | `qwen3-fast:8b` | Ya instalado; alias local sin thinking para respuesta rapida |
| Carga reducida / alternativa GPU | `qwen3-fast:4b` | Menor carga de memoria |
| Analisis dificil | `deepseek-r1:14b` | Bajo demanda; guardrails de frecuencia y cooldown |
| Codigo | `qwen2.5-coder:14b` | Especialista instalado; rama y pruebas antes de aplicar |
| Imagenes | `qwen2.5vl:7b` | Modelo con vision instalado; no reemplazar por texto solamente |
| RAG | `nomic-embed-text` | Mantiene el espacio de embeddings existente |
| Seleccion de herramientas | `tenazas-qwen25-7b-v8` con el pipeline existente | Preserva router entrenado y evaluaciones |
| GPU inaccesible | `qwen2.5:3b` en NAS | Ollama CPU separado, 4 GB y CPUs 0/1 |

Las verificaciones de infraestructura y seguridad normalmente usan Python y datos
reales; no requieren una llamada LLM por cada ciclo. Un agente define su tarea y
permisos: puede compartir un modelo con otros agentes.

El proxy `inference_gate.py` en 127.0.0.1:18902 serializa las llamadas que lo usan y
prioriza Telegram. Otros clientes que consulten HomeLab directamente no quedan
sujetos a ese limite. Qwen 32B sigue instalado, pero no es el modelo automatico de
trabajo continuo: deja poco margen de VRAM para el contexto y los otros procesos.

`TENAZAS_ALLOW_CLOUD=false` desactiva seleccion automatica cloud en el runtime y
autofix; OpenClaw tiene fallback local CPU. Las consultas explicitas de APIs de
otras herramientas y servicios no se convierten en llamadas gratuitas por esta
opcion. Los modelos recomendados son una base basada en lo instalado; la calidad
por dominio debe medirse con tus evaluaciones, no solo por tamano.

Fuentes de capacidades: [Qwen2.5 Coder](https://arxiv.org/abs/2409.12186),
[Ollama Qwen3](https://ollama.com/library/qwen3),
[DeepSeek R1](https://ollama.com/library/deepseek-r1).

## n8n y acceso

Editor: https://192.168.2.2:5679. El certificado es local y autofirmado; para evitar
el aviso del navegador, confiar en el certificado local o reemplazarlo por uno de
tu CA. El puerto 5678 queda accesible solo desde localhost del NAS. n8n no monta
docker.sock y los workflows usan una credencial cifrada HTTP Header Auth, sin
leer variables de entorno del editor.

Los nueve workflows se importan conservando sus IDs. Se mantienen desactivados
hasta asignarles la tarea que actualmente realiza DSM o el proceso continuo.
El ciclo de agentes ya tiene scheduler interno: no activar otro en paralelo.

Para actualizar la credencial desde la configuracion local, sin imprimirla:

```sh
docker exec n8n node /workspace/bootstrap-runner-credentials.js
docker exec n8n sh /workspace/import-workflows.sh
```

## Datos, pruebas y recuperacion

Los CV, oportunidades, backups SQLite de n8n y logs se conservan en el NAS y dejan
de versionarse. Un clon limpio necesita restaurar datos personales desde backup
antes de usar la busqueda laboral. Nunca poner credenciales en el build context.

Hay manifests de dependencias y CI Linux. El scanner actual bloquea coincidencias
en archivos versionados; el reporte historico es informativo. Tres commits
historicos contienen candidatos a secretos. Las claves antiguas detectadas no
coinciden con las configuraciones locales actuales; esto no verifica su revocacion
en los proveedores. No se reescribio ni se forzo el historial remoto.

Backup de despliegue: `state/deployments/20261003-24x7/`, incluyendo configuracion
anterior y codigo. Restaurar la configuracion y el codigo correspondiente juntos;
recrear los contenedores con la configuracion Compose de esa version.

La observacion pasiva de 24 horas escribe un JSONL con estado de servicios,
workers y ultimos exitos de agentes. El inicio de esa observacion no demuestra
24 horas de estabilidad. Las pruebas de fallas se ejecutan aisladas; la recuperacion
de un exporter se verifica durante el despliegue.

## Validacion del despliegue (2026-10-03)

OpenClaw, runner y Ollama CPU reportan healthy. Los cinco workers completaron
el ciclo real. Las inferencias de Qwen3-fast 8B por el proxy GPU y Qwen2.5 3B
en CPU respondieron correctamente. Tras terminar deliberadamente el exporter
de metricas, el supervisor lo reinicio y su endpoint volvio a responder.
Se inicio el contenedor de observacion de 24 horas; el periodo sigue pendiente.

El refresco de IOL recibe HTTP 403 del proveedor. No se considera reparado:
necesita revisar el acceso con IOL. Los fallos de tareas periodicas esperan al
menos cinco minutos antes del siguiente intento, evitando un ciclo de reintentos.
