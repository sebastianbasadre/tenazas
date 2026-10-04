# Estado privado: diseño y evidencia de staging

Trabajo exclusivo de `feature/tenazas-readonly-mcp`. **NO MERGE / NO DEPLOY**.
Los archivos realmente desplegados siguen intactos. El checkout candidato ahora
integra el writer y el resolver privado en los consumidores; todavía no están
instalados en producción. El ensayo integrado se describe en
[private-state-full-clone.md](private-state-full-clone.md); la evidencia histórica
de este documento corresponde al ensayo previo por componentes.

## Writer exacto y dos entradas

El NAS y el checkout definen `save_json()` en `workspace/runtime/runtime.py:88`:
crea el padre y usa `open(path, "w")` + `json.dump`. `update_state()` lo llama
en la línea 194 para `STATE_FILE`, definido en la línea 49.
`runtime_worker.py:41` llama `rt.update_state()`; la entrada CLI `runtime.py`
llama la misma función. Son el mismo writer, no `runtime_utils.save_json()`.

En producción existen **dos** caminos: `service_supervisor` programa
`worker_supervisor` cada 300 s; DSM Task 12 hace `docker exec` de `runtime.py`.
No se modificaron sus configuraciones ni se ejecutó Task 12 durante las pruebas.
`runtime_utils.write_atomic()` es otra función y no sustituye al writer anterior.

El candidato `tools/staging_private_json.py` exige Linux y UID/GID numéricos
explícitos; padre existente 0700 del mismo owner y target, si existe, 0600 y
sin enlaces. Abre un temporal aleatorio en el mismo directorio mediante
`O_CREAT|O_EXCL|O_NOFOLLOW`, **0600 desde creación**, comprueba `fstat`, escribe
JSON finito, hace `flush`, `fsync(file)`, `os.replace` con directory FD fijado y
`fsync(directory)`. No hace chmod/chown ni crea padres silenciosamente.

Los errores anteriores al reemplazo preservan el target y se propagan;
un fallo de fsync posterior produce `PublishedDurabilityError`: el reemplazo
ya ocurrió y su durabilidad no está confirmada. No se declara un rollback falso.
El reemplazo es atómico por archivo; no resuelve lost updates entre los dos
writers ni garantiza una transacción entre varios archivos del caché MCP.

## Hallazgos de Synology

NAS `sbasadre` = 1034:100; NAS `tenazas` = 1035:100. Los procesos OpenClaw
observados y las pruebas de sus cuatro imágenes/roles usan **1026:1026**.
No se deduce esa identidad del nombre NAS ni de `.Config.User` solamente.

En staging, mkdir solicitado con 0700 bajo la jerarquía compartida produjo
**0777 + ACL heredada everyone-write**. El preflight rechazó el resultado
antes de escribir datos. La preparación de directorios **vacíos de staging**
con ownership explícito y chmod 0700 eliminó esa ACL; la herramienta nativa
`synoacltool -get` confirmó “It's Linux mode”. Esto es provisioning, no el
mecanismo de privacidad del writer: los archivos nacen 0600 y nunca se corrigen
con chmod posterior.

`os.listxattr()` dentro del contenedor no muestra las ACL extendidas Synology.
El writer rechaza las ACL visibles, pero **no puede certificar las ocultas**.
Se necesita inspección nativa en el NAS y denegación real a otros UID.
La inspección se realizó con el binario nativo y bibliotecas RO, UID 1026,
sin red/capabilities, sobre montajes de staging RO. El UID host 1034 no puede
atravesar los directorios privados: no se relajaron permisos para inspeccionarlos.

El ensayo sintético del esquema antiguo mostró otra consecuencia: chmod del
padre recalculó el modo del hijo heredado de **0777 a 0000**, todavía con ACL.
El bootstrap normal rechazó ese target; el bootstrap explícito publicó un
inode nuevo 0600, UID/GID 1026. **No aplicar ese cambio al runtime completo**.

## Evidencia y alcance

En `docs/evidence/private-writer-staging/`:

- `cycles.json`: diez ciclos, cinco por `service_supervisor.start ->
  worker_supervisor.main -> runtime_worker.run` y cinco por docker exec de la
  CLI en un contenedor exclusivo. Después de cada ciclo: UID/GID, modo, inode,
  JSON, mismo UID lector/writer y ACL nativa del estado y caché. Caché dedicado
  0700/0600; UID 1027 denegado; seis herramientas leen con montaje RO y la
  escritura devuelve EROFS. Sondas sintéticas y tareas/acciones deshabilitadas.
- `subdir-writers.json`: otros diez ciclos de ambos entrypoints con el estado
  en `/home/tenazas/runtime/private-state/state.json`. No hay alias plano;
  un audit hook bloquea aperturas de la ruta antigua y el control negativo
  comprueba que el bloqueo está activo. Resultado 0600, 1026:1026, JSON válido.
- `reader-contracts.json`: diez contratos sobre fuentes capturadas del NAS;
  nueve módulos prueban sus expresiones reales de lectura con el `load_json`
  real y JSON sentinel. La sonda real de `health_sweep` reproduce primero la
  omisión silenciosa del archivo trasladado y luego comprueba la ruta corregida.
  **No son ejecuciones completas de esos agentes/servicios.**
- `reader-roles.json`: imágenes exactas de OpenClaw, observador 24x7, watchdog
  y job-runner, en contenedores de prueba aislados. Usuario `tenazas` resulta
  1026:1026, puede leer el subdirectorio privado a través de su bind y no escribir
  en el montaje RO. Ningún proceso productivo fue ejecutado/reiniciado por esto.
- `consumer-inventory.json`, `mounts.json`, `dsm-tasks.json`, `n8n.json`:
  inventario estático, snapshot de procesos y montajes, 32 definiciones DSM
  decodificadas sólo en memoria y contrastadas con crontab. Task 12 referencia
  `runtime.py`; ninguna contiene literalmente `state.json`. Nueve workflows
  n8n, **cero activos**, comprobados con SQLite RO. No se publican comandos,
  parámetros de nodos ni valores de credenciales.
- `suite.json`: **160 específicos pasados**; suite **2848 pasados, 114 fallos
  heredados, 6 errores de colección heredados, 9 omitidos, 29 subtests**.
  Comparación contra `59aa27d...`: mismos IDs y mensajes normalizados de los
  114 fallos, mismas trazas de los seis errores, cero fallos nuevos. La suite
  completa no está verde; ver también `mcp-readonly-validation.md`.
- `https.json`: **12/12** pruebas HTTPS, autenticación válida/inválida,
  rotación/revocación, exactamente seis tools, RO, cero marcador de secreto
  sintético en output/audit. No es OAuth/ChatGPT end-to-end.
- `cutover-rollback.json`: rehearsal de permisos y bootstrap sintético;
  rollback de compatibilidad mantiene 0700/0600/1026 y el esquema original,
  pero el writer antiguo no tiene atomicidad. No es aún un rollback de toda
  la migración de rutas ni una prueba del servicio OpenClaw completo.

El inventario inicial recorrió 10157 archivos; declaró exclusiones, 2303
skips y 13 archivos sin permiso. Las búsquedas literales no demuestran ausencia
de rutas construidas dinámicamente, plugins, comandos guardados o consumidores
no ejercitados. **El gate “ningún reader/writer oculto se rompe” sigue abierto.**
Falta aplicar las referencias propuestas a un clon completo y ejercitar allí
los servicios/entrypoints configurados con tripwire, incluyendo jobs diferidos.
No convertir contratos de lectura ni un snapshot en aprobación de cutover.

## Migración exacta propuesta, aún NO autorizada

1. Cerrar el gate anterior: inventario clasificado, clon completo con rutas
   migradas, pruebas funcionales de consumidores, dos writers y rollback de
   rutas. Registrar los SHA exactos de fuentes NAS y candidato; ninguna
   modificación de Task 12/scheduler/compose como efecto secundario.
2. Ruta raw propuesta: host
   `/volume1/docker/openclaw/workspace/runtime/private-state/state.json`,
   contenedor `/home/tenazas/runtime/private-state/state.json`. Crear solamente
   ese subdirectorio **vacío**, preparar 0700 y owner 1026:1026 y comprobar ACL
   nativa/denegación a UID 1035 y otro UID. No chmod/chown recursivo del runtime,
   ni cambio de ACL de sus hermanos, ni alias/fallback al estado plano.
3. Preparar un resolver compartido de ruta fija para `runtime`,
   `background_agents`, `goal_engine`, `devin_lite`, `infra_diagnostics`,
   `benchmark_continuous`, `world_model`, `agent_security`,
   `workers/diagnostics_worker` y la sonda de `health_sweep`; además de cualquier
   consumidor adicional que revele el gate. Integrar el writer privado sólo
   en el estado raw; no cambiar tácitamente los otros save_json del runtime.
   Estos cambios productivos todavía **no están implementados/aplicados**.
4. Sólo con autorización nueva y ventana de mantenimiento, impedir ambas
   rutas de escritura antes de tomar la última instantánea y respaldo privado
   0600. Una ventana con OpenClaw detenido impediría ambos writers sin editar
   Task 12; sus docker exec fallarían durante esa ventana. Ese downtime y los
   fallos esperados del scheduler necesitan aprobación específica, no automática.
5. Publicar esa instantánea con el writer seguro en el subdirectorio ya validado.
   Conservar esquema raw, comprobar JSON/mode/UID/GID/ACL, activar las referencias
   aprobadas conjuntamente y retirar el archivo plano sólo tras validar todos
   sus consumidores. El respaldo permanece privado; nunca JSON raw en logs.
6. Producer separado: raw privado **RO** + inventario explícito de credenciales
   en memoria/stdin -> caché MCP dedicado. Propuesta:
   `/volume1/@tenazas-private/mcp-cache`, bajo un namespace protegido de root
   para impedir sustitución por un usuario del directorio compartido. Docker
   puede bind-montar el hijo 1026:1026 aunque el padre host sea root:root 0700.
   Verificar ACL/ancestral namespace antes de fijar esa ruta final.
   Caché/`incidents` 0700, JSON 0600, sin ACL heredada, sin archivos adicionales.
7. Gateway: sólo ese caché **RO**, imagen mínima inmutable, 1026:1026, rootfs RO,
   sin Docker socket, SSH, raw state, inventario o collector. No ingress aún.
   Verificar montajes observados, UID 1026 autorizado y 1027/1035 denegados,
   secreto/cookies/Authorization ausentes del caché/output y autenticación.
8. Revisar diff y evidencia antes de una autorización distinta para cutover.
   Secure MCP Tunnel + OAuth/ChatGPT end-to-end continúan como gate independiente.

## Rollback propuesto

Ante fallo, no iniciar MCP/ingress; conservar raw/caché privados. Recuperar la
última instantánea válida mediante publicación privada atómica y detener el
cutover si fsync/ACL/ownership no pueden demostrarse. Mantener las rutas privadas
y su resolver en la variante de rollback: no devolver datos al plano 0777.
Si se necesita recuperar lógica previa, preparar y probar de antemano una
variante que lea/escriba la ruta privada con umask 077 y no recree herencia.
El ensayo de truncado legacy conservó 0600; no equivale a durabilidad atómica.
Si algún servicio requiere volver a una ruta/permisos inseguros, mantenerlo
detenido y revisar el rollback; no relajar ACL/ownership automáticamente.

La prueba final RO confirmó fuente runtime/supervisores sin cambios, estado
productivo todavía 0777 owner 1034:100 y OpenClaw con el mismo StartedAt.
No se efectuaron push, merge, deploy ni cambios productivos en esta tarea.
