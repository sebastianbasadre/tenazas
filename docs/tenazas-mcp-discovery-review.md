# Discovery y freshness: propuesta local para revisión

Estado original: propuesta local, sin commit ni despliegue. El usuario autoriz�
el 25 de octubre de 2026 versionar y subir todos los pendientes. El commit no
constituye autorizaci�n adicional de despliegue; se conservan abajo la base y
las conclusiones originales de revisi�n.
Base: `feature/tenazas-mcp-snapshot`, HEAD
`7c8cc113a07637512e42c93eac152825970918f3`. Los dos handoffs se preservan.
Esta propuesta sustituye, únicamente en el código local, la lectura de las tres
métricas 9105 para `/network`. No cambia código ni configuración de exporters.

## Problema y resultado esperado

Discovery convertía un timeout en una lista vacía y marcaba todo el inventario
missing. El timestamp de `runtime.py` se escribía antes de discovery. El feed
combinaba ese timestamp con conteos de otro paso: no demostraba un resultado válido.

La causa de los timeouts de nmap sigue pendiente. Sus comandos, timeouts y la
cadencia de 1800 segundos permanecen iguales. Con ciclos cada 300 segundos, los
escaneos observados se separan aproximadamente 35 minutos; TTL permanece en 300.
Un éxito puede quedar stale a los 301 segundos sin que el scanner haya fallado.

## Cambios propuestos

- `device_discovery.py`: errores tipados `timeout`, `execution_error` y
  `parsing_error`. Código de salida no cero, salida parcial, resumen ausente,
  conteos inconsistentes o hosts duplicados no actualizan el inventario.
  El resumen completo de un escaneo exitoso vacío sí es válido.
- Inventario publicado mediante reemplazo atómico. Ante los fallos anteriores se
  conserva su contenido, incluidos `last_seen` y `missing_since`.
- `network_discovery_result.py`: contrato estricto y publicación atómica de una
  señal agregada; límite 4096 bytes, sin dispositivos, IP/MAC, stderr o secretos.
  Lock no bloqueante para serializar intentos. `running` se publica antes de nmap:
  una interrupción mantiene el feed no disponible hasta completar otro intento.
- `runtime.py`: actualiza el archivo del timestamp legado después de discovery
  exitoso. El exporter sigue leyendo el mismo archivo, sin modificación alguna.
  El router interactivo sigue siendo otro escritor legado: ese archivo ya no es
  la autoridad del feed MCP.
- El feed requiere `--network-result-file`, sin fallback al exporter ni lectura
  del inventario privado. Paquete NAS actualizado con el módulo compartido;
  paquete VM y producer sin cambios.
- CI local propuesta: incluye el nuevo módulo, pruebas y chequeos de sintaxis.

## Señal separada y coherencia

Ruta propuesta en el NAS:
`/volume1/docker/openclaw/workspace/runtime/network_discovery_result.json`.
Dentro del contenedor corresponde a `RESULT_FILE`, junto a `device_discovery.py`.

Campos: `schema_version=1`, `attempt_id` UUID, `status`, `attempted_at` UTC Z,
`finished_at` UTC Z o null y `last_success` o null. `last_success` contiene
`attempt_id`, `observed_at`, `total`, `online` y `missing`. Un éxito exige que
identificador y fecha coincidan con el intento completado, y total=online+missing.
Los conteos se calculan del inventario producido por ese mismo discovery, bajo
el mismo lock. Un fallo conserva exactamente el último `last_success` y usa otro
identificador; no puede atribuirle su nueva fecha de intento a los datos antiguos.

Estados `running`, `timeout`, `execution_error`, `parsing_error` y
`publication_error` producen `/network` 503 con error fijo al refrescar la fuente.
Una señal ausente, corrupta, con campos extra, generaciones mezcladas, fechas
futuras o conteos inválidos también falla cerrada. No se crea un éxito ficticio
durante el despliegue. Un éxito vacío deja online=0 y missing igual al inventario
histórico; si nunca hubo dispositivos, los tres conteos son cero.

El HTTP sigue sirviendo documentos en memoria. Los cambios de estado se reflejan
en el próximo refresh de la fuente, actualmente cada 30 segundos; no se promete
invalidación instantánea entre refreshes. El producer conserva el último contenido
válido de red y su `observed_at` ante HTTP 503, marcando `available=False`. La
generación global del snapshot puede avanzar por otras fuentes; la observación
de red no se renueva.

El JSON agregado se publica 0644, su lock 0600 y el inventario atómico 0600.
El usuario del exporter debe seguir pudiendo leer su inventario. La cuenta
`sbasadre` de la fuente necesita solamente lectura de la señal y acceso de
traversal a sus directorios; debe verificarse antes de aplicar nada.

Inventario y señal son archivos separados: no constituyen una transacción de dos
archivos. Si un fallo de disco ocurre después de publicar el inventario y antes
de publicar success, el feed sigue no disponible y conserva la observación
anterior. No debe inferirse éxito ni repararse automáticamente la señal. En ese
caso corresponde revisión del operador; no se garantiza restauración del
inventario anterior frente a un fallo de almacenamiento o caída del proceso.

Los escaneos de un único target no publican success de toda la LAN y no marcan
missing a dispositivos que no fueron parte del target.

## Verificación local

Resultado de la versión final: **373 passed, 0 failed, 0 skipped**, en Ubuntu WSL
local con Python 3.14.4 y pytest 9.1.1; incluye 28 casos del módulo nuevo.
Ruff 0.16.10 pasó completo en el módulo nuevo, fuente, pruebas modificadas y
builder; pasó E9/F821/F822/F823 en los dos archivos legacy del writer.
`git diff --check` pasó. No se usaron NAS ni VM para estas pruebas.

Todas las ejecuciones de nmap en las pruebas son mocks; no se ejecutan scanners.
Los HTTP de integración usan exclusivamente loopback. Casos incluidos:
éxito con dispositivos, éxito vacío con/sin historial, timeout, ejecución no cero,
binario ausente, parsing inválido y recuperación tras cada fallo. Se comprueba
retención del inventario y fechas, HTTP 503/200, retención del snapshot por el
producer, TTL 300 y stale tras 301 segundos. También se comprueban señales
inválidas, límite de tamaño, lock contra solapamiento y modos Linux.

Comando reproducible en un entorno local aislado con Python >=3.11,
pytest 9.1.1, Ruff 0.16.10 y PyJWT[crypto] 2.15.1 para las pruebas OAuth existentes:

```sh
python -m pytest tests/test_mcp*.py tests/test_network_discovery_result.py -o addopts= -q
python -m ruff check --select E9,F821,F822,F823 workspace/runtime/device_discovery.py workspace/runtime/runtime.py
python -m ruff check workspace/runtime/network_discovery_result.py workspace/runtime/mcp_sanitized_sources.py tests/test_network_discovery_result.py tests/test_mcp_sanitized_sources.py tests/test_mcp_nas_artifacts.py tools/mcp_build_packages.py
```

## Despliegue propuesto: requiere autorización posterior

1. Revisar y aprobar el diff exacto y sus hashes. Revalidar read-only el lanzador
   efectivo de discovery, los mounts, UID/GID, permisos de lectura de la fuente
   bajo su sandbox y ausencia de escritores externos del inventario. La señal
   debe corresponder a la ruta del mismo escritor, no a una copia de otro runtime.
2. Antes de cambiar NAS, acordar cómo pausar exclusivamente los lanzadores de
   discovery para evitar cargar versiones mezcladas. Esa acción necesita
   autorización concreta: no detener exporters, OAuth, túnel u otros servicios.
   Si no existe aislamiento para el escritor, detener el despliegue para revisión.
3. Capturar en backup privado del NAS los tres archivos de writer
   (`device_discovery.py`, `runtime.py`, módulo nuevo si ya existe), inventario,
   timestamp legado, estado previo de la señal, fuente instalada y unidad fuente.
   Registrar hashes, propietarios, modos y ACL. No descargar inventario privado.
4. Preparar versiones revisadas sin sobrescribir runtime en uso. Separar la
   actualización del writer de la del paquete `nas-sources`: el builder de fuentes
   no incluye `device_discovery.py` ni `runtime.py`. Verificar manifiestos y miembros;
   confirmar que el módulo compartido tiene el mismo hash en ambos destinos.
5. Aplicar el writer coherente durante la ventana aprobada. Aplicar luego el paquete
   de fuentes y su argumento `--network-result-file`, conservando bind, puerto,
   sandbox y estado sin boot enablement. Verificar la unidad DSM y, solamente con
   autorización específica, recargar/reiniciar la fuente independiente.
6. Reanudar exclusivamente los lanzadores acordados y esperar su próximo ciclo
   natural; no provocar un escaneo. Hasta un resultado válido, `/network` debe ser
   503. Verificar fechas y conteos sanitizados de la señal y el feed, sin inventario.
   No fabricar datos a partir del inventario ya marcado missing.
7. Verificar en la VM, bajo autorización separada, que el producer conserva datos
   y fecha ante 503 y recupera availability ante success. No cambiar TTL ni la
   configuración de fuentes. El release VM ya instalado no requiere este diff.
8. Mantener activación de staging y E2E como gates separados. Para probar red fresh
   con TTL 300, usar la ventana posterior a un éxito natural; comprobar también
   stale tras el TTL. No usar `switch` ni `rollback` del helper instalado, porque
   todavía actúan sobre el túnel. Las seis herramientas pendientes siguen siendo
   get_status, get_containers, get_models, get_storage, get_network_health y
   get_recent_alerts, mediante el OAuth/túnel existentes sin cambiarlos.

## Rollback propuesto: requiere autorización posterior

1. Pausar solamente los mismos lanzadores aprobados y la fuente independiente,
   esperar a los escritores y comprobar que no hay intento activo. No borrar locks.
2. Restaurar los archivos del writer, fuente y unidad desde el backup verificado,
   con propietario, modo y ACL originales. Restaurar inventario/timestamp juntos
   únicamente si fueron alterados en esa ventana y el operador aprueba esa pérdida
   de observaciones posteriores. Conservar la señal de diagnóstico fuera de la
   ruta activa; restaurar su predecesora si existía, sin fabricar success.
3. Verificar hashes e importaciones, reponer solamente los estados de ejecución
   autorizados y conservar boot disabled. No usar los helpers VM switch/rollback.
4. El rollback del código no corrige la semántica defectuosa del feed anterior.
   Mantener staging bloqueado y la fuente de red no publicada hasta revisión del
   operador. No promover a producción ni afirmar freshness desde los conteos
   heredados. OAuth, túnel, firewall y exporters permanecen fuera de la operación.
