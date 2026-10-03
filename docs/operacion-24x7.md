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

El 403 de IOL se corrigio usando el transporte HTTP estandar versionado y
reautenticando con las credenciales existentes. Se verificaron HTTP 200 para
autenticacion, refresh forzado, portafolio y estado de cuenta. El cliente local
de la skill tiene backup y usa `iol_http.py`; el estado persistente de dependencia
marca finanzas como degradado si falla IOL o la lectura tiene mas de 15 minutos.
Los snapshots se etiquetan como historicos. La metrica `tenazas_iol_degraded`
y su alerta permiten comprobarlo sin confundir datos antiguos con datos actuales.

## Verificacion posterior de pendientes

Las claves historicas se probaron contra sus emisores, sin escribirlas ni
imprimirlas: Google devuelve 400/API_KEY_INVALID y OpenRouter 401. Ninguna
autentica hoy. Esto no acredita la accion administrativa de revocacion;
para eso hace falta el registro o la consola del proveedor. La clave antigua
de cifrado de n8n tampoco revoca credenciales contenidas en backups antiguos.

El audit de destinos confirma proxy para runtime GPU, gateway y autofix. Se
corrigio el OLLAMA_API_BASE heredado que apuntaba directo a la PC. El upstream
del propio proxy y el fallback CPU tienen destinos directos intencionales.
Los logs del servidor GPU detectaron seis llamadas /api/chat directas desde
192.168.2.43 durante las pruebas; las unitarias ahora bloquean conexiones a
11434/18902 salvo marker live_inference. No se cerro el acceso GPU de otros
clientes. Los headers de cliente identifican gateway, Telegram, router
entrenado y empleo; otros consumidores quedan como unlabelled.

La observacion ampliada registra RAM, latencia, epochs/reinicios del supervisor
y estados SQLite. Genera `state/observations/24x7-report.json` cada minuto y
al terminar. La ventana con estas metricas comenzo el 3/10 a las 14:49 ART:
completa 24 horas el 4/10 aproximadamente a las 14:49 ART. Los reinicios
manuales de despliegue se distinguen de reinicios automaticos de servicios.

Verificacion directa final: `/status` devuelve 28 totales, 26 activos, 2
detenidos y 0 unhealthy; el objetivo de contenedores devuelve ok. `/model`
identifica Qwen3-fast 8B tanto en gateway como en Telegram. La consulta de la
base de n8n confirma nueve workflows inactivos y el supervisor tiene scheduler.
IOL reporta auth/portafolio/cuenta HTTP 200 y `tenazas_iol_degraded 0`.
La suite ampliada aprobo 409 pruebas y 18 subtests en Linux.

Herramientas de comprobacion: `check_followup.py`, `audit_inference_clients.py`,
`verify_historical_keys.py` y `summarize_observation.py`. Esta ultima no ejecuta
acciones: informa ciclos unicos, fallos, jobs interrumpidos, RAM y latencia.
`period_complete` solo indica tiempo transcurrido; `coverage_complete` verifica
continuidad de muestras. Ninguno sustituye revisar los fallos del informe.
