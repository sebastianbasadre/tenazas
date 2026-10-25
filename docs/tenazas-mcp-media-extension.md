# Ampliación del MCP: biblioteca Plex y Download Station

Estado al 24 de octubre de 2026: Plex, Download Station y SABnzbd verificados
y publicados en el NAS. Instalación VM completada y validada por HTTPS local.
Validación final desde ChatGPT pendiente de reautenticación. Discovery, OAuth y túnel
no se modificaron por esta ampliación.

## Despliegue actual de medios

NAS: `/volume1/tenazas-mcp-media/releases/media-20261025-v1`, código separado y
propiedad de root; `275A180` permanece intacto. El oneshot
`tenazas-mcp-media-collect.service` se ejecuta cada 60 segundos mediante timer
estático, sin habilitar arranque automático. Publica una proyección saneada en
`/volume1/tenazas-mcp-media/cache/state.json`, root:docker 0640, con reemplazo
atómico y directorio 02750. Credenciales, URLs de descarga, rutas de medios,
contraseñas de extracción y usuarios Plex quedan fuera de la proyección.

El drop-in fuente `zz-media.conf` selecciona el nuevo release y `--media-file`.
Conserva usuario, grupo, bind, puerto y sandbox. `/media` usa el puerto privado
19105 existente; no se publicaron puertos nuevos. El handler sirve memoria
prevalidada, sin ejecutar colectores por petición. Rollback NAS root-only:
`/volume1/tenazas-mcp-media/rollback/rollback.sh`.

VM instalada: `/opt/tenazas-mcp/releases/media-20261025-v1`.
El usuario ejecutó `/home/mcpadmin/tenazas-media-review-v1/install.py` con sudo
y el hash del bundle verificado; devolvió INSTALLED.
Rollback root-only: `/var/backups/tenazas-mcp-media/media-20261025-v1/rollback.sh`.
El instalador compara hashes efectivos, copia el release anterior a uno nuevo
y aplica cambios acotados. No utiliza los helpers antiguos switch/rollback.
El nuevo proceso VM consume solamente `/media`, sin credenciales del NAS, y
escribe `/var/lib/tenazas-mcp-media/state.json` como tenazas-mcp, 0600, con lock
y reemplazo atómico. Conserva las fechas originales del NAS.
El gateway mantiene TLS, bearer, bind privado, argv y hardening existentes.

Se agregan `get_media_library` y `get_downloads`, ambas de solo lectura.
`get_status` también incluye `media_library` y `downloads`, permitiendo usar
la consulta existente sin esperar al redescubrimiento de herramientas del cliente.

### Evidencia real y alcance

Plex se consulta directamente por loopback con el token vigente leído de
Preferences.xml y comprobando ProcessedMachineIdentifier. El token guardado en
Tautulli no pudo leer las bibliotecas locales; el nativo sí. No se modificó
Tautulli ni se reinició Plex.
Los conteos del API y una lectura independiente paginada de todos los elementos
coincidieron: **856 películas, 25 series, 1583 episodios**, en tres bibliotecas
movie/show. Una biblioteca de música está excluida. Cero reproducciones activas.
Recientes: límite 20 y truncamiento explícito. Dos generaciones NAS verificadas
con fechas crecientes y las tres fuentes disponibles:
`5be4ea5f3957422799fc426020c72066` y `18055036770645698fb43846f5d27844`.

Download Station: acceso local por synowebapi, API instalada
SYNO.DownloadStation2.Task, versión 2, método list, runner=sbasadre, paginado.
No necesita contraseña ni SID nuevos. Alcance: tareas visibles para ese usuario,
sin afirmar cobertura de todos los usuarios. Una tarea finalizada verificada.
Los estados se contrastaron con TASK_STATUS_INT del JavaScript de la interfaz
instalada: 5 finalizada, 8 seeding, 6 comprobación. Esto sí es lectura efectiva,
distinta del API discovery inicial.

SABnzbd: clave existente en memoria y POST local exclusivamente queue/history.
Cola e historial vacíos verificados. Velocidad global de cola cuando existe;
velocidad por tarea desconocida queda null. ETA calculada se marca estimada;
velocidad cero o tamaño desconocido implica null.

Cada fuente conserva disponibilidad, observación, alcance y último dato propios.
TTL 300 segundos. Fallos no renuevan observaciones; listas vacías exitosas son
válidas. Antes del paso VM pasaron 243 pruebas relevantes en Linux, sin skips.
Instalación privilegiada completada. El harness tools/mcp_media_verify.py
verificó las ocho consultas por HTTPS local, validación TLS activa, anotaciones
de solo lectura, rechazo de mutación y datos frescos. get_status incluye las
dos nuevas secciones. Gateway activo, timer VM activo cada 30 segundos,
oneshot exitoso y arranque automático sin habilitar (gateway disabled, nuevas
unidades static). OAuth y túnel permanecieron intactos.
Se verificaron dos generaciones distintas a través del gateway HTTPS VM:
`c841325ce9d640698ee6847f6f524d59` (Plex observado 2026-10-25T00:35:50.871994Z)
y `e81c710532a347e5ac852cb293fca303` (2026-10-25T00:36:52.565741Z).
Las observaciones de Download Station y SABnzbd también avanzaron en esos
ciclos, con las tres fuentes disponibles y frescas.
La conexión ChatGPT volvió a devolver UNAUTHORIZED / Reauthentication required
al intentar get_status después de instalar: la validación final desde ChatGPT
requiere que el usuario reconecte Tenazas MCP. No se declara E2E completado.

Bundle VM SHA256:
`5c0d3f07fb619129aeb3172d5329255e71ee63f7487a9c5b1a50c20533803e6a`.
Instalador utilizado en el despliegue (copia histórica en VM) SHA256:
`e00ca4a3b43c97e57585daa63ed150426caa0c623d4a1dbca4c05c3f5b867298`.

Las secciones siguientes conservan el contrato y diagnóstico inicial, distinto
del acceso directo a Plex finalmente utilizado.

## 1. Verificar las fuentes instaladas

Ejecutar en el NAS:

```sh
sudo /usr/local/bin/python3.13 -ISB \
  /volume1/docker/openclaw/tools/mcp_media_probe.py --probe
```

La sonda busca el contenedor Tautulli y su mount `/config`, lee solamente los
parámetros necesarios de `config.ini` y envía la API key en el cuerpo de una
petición al servicio loopback o a la única IP privada del contenedor obtenida de
Docker inspect si no hay puerto publicado. No recibe claves por argumentos, no las guarda
en nuevos archivos ni las imprime. Permite exclusivamente `get_server_identity`,
`get_libraries` y `get_activity`. Publica presencia de identidad Plex, cantidad
de bibliotecas por tipo y número de sesiones; nunca títulos, usuarios, tokens,
direcciones Plex, enlaces ni respuestas crudas. No consulta la configuración
mediante la API de Tautulli, porque esa respuesta podría contener secretos.

Si Tautulli usa `network_mode=container:...`, se sigue esa relación hasta el
contenedor dueño de la red (por ejemplo Gluetun) para consultar sus puertos ya
publicados. La configuración y la clave siguen leyéndose del mount de Tautulli,
nunca del contenedor VPN. Se rechazan ciclos o dueños de red detenidos.

Para DSM consulta `SYNO.API.Info` sin iniciar sesión. Publica exclusivamente
descriptores de las API de autenticación y Download Station seleccionadas.
La ruta y versión instalada prevalecen sobre ejemplos de documentación antigua.
Una API anunciada no demuestra que ya se puedan leer tareas autenticadas.
HTTPS mantiene verificación de certificados; `--ca-file` permite indicar una CA
local. No hay opción para desactivar TLS. Redirecciones y proxies ambientales
están deshabilitados.

La sonda no publica puertos ni elige entre múltiples IP privadas: en ese caso
devuelve un diagnóstico para revisar la ruta. Si no se pueden autodetectar mount/puerto, la sonda acepta `--tautulli-config`
y `--tautulli-url` loopback. `--dsm-url` permite indicar el origen DSM local.
Los mensajes de error no imprimen cuerpos, URLs con credenciales ni excepciones
originales. `checked_at` es la hora de comprobación, no una observación de un
inventario de medios completo.

### Diagnóstico de ruta realizado el 24 de octubre de 2026

Se verificó por SSH que Tautulli comparte la red de Gluetun, que publica
8181/tcp, y que la ruta derivada de su configuración es
`http://127.0.0.1:8181` sin prefijo. TCP conecta y HTTP responde.
Las tres operaciones permitidas devolvieron cabeceras HTTP 200 aproximadamente
15 segundos después de enviar la petición con la clave local. El límite original
de 8 segundos producía `SOURCE_UNREACHABLE_OR_TLS_ERROR`; esto no demuestra un
problema de TLS ni una clave incorrecta.

La sonda admite ahora `--timeout SECONDS` (1–60; predeterminado 8).
Una ejecución diagnóstica con 25 segundos pudo leer las respuestas, pero terminó
en `PLEX_IDENTITY_UNAVAILABLE`: Tautulli todavía no está verificado como fuente
Plex. No se modificaron servicios ni se desactivó la verificación TLS.
Download Station sigue en `API_DISCOVERED`, sin lectura autenticada verificada.

## 2. Contratos independientes

Preparar dos consultas MCP: `get_media_library` y `get_downloads`. Cada una
necesita su propia observación, TTL, disponibilidad y último dato válido. La
indisponibilidad de Download Station no debe ocultar la biblioteca ni afectar
las seis consultas existentes. No ampliar la seguridad de `public_text` usada
por otros contratos para permitir títulos: los nuevos títulos necesitan una
validación específica, Unicode, límites y exclusión de URLs/secretos.

Biblioteca: películas por biblioteca; series y episodios como conteos separados;
incorporaciones recientes con tipo y fecha; reproducciones activas sin nombres
de usuarios, IP, sesión Plex ni reproductores identificables. El listado reciente
debe declarar límite/truncamiento. Las bibliotecas de música o fotos no se cuentan
como películas. Hay que comprobar los conteos contra Plex: `get_libraries` puede
reflejar la caché de Tautulli. Consultar esa caché ahora no renueva la antigüedad
de sus datos; por eso la sonda marca `library_counts_live_verified=false`.

Descargas: estado, título, progreso, bytes/segundo y ETA cuando exista. Un ETA
calculado se identifica como estimación; con velocidad cero o tamaño desconocido
es null. Distinguir cola, descarga, pausa, comprobación, finalización, seeding y
error según los estados reales de la versión instalada. Paginar y comprobar
total/cantidad; no presentar una página parcial como lista completa. El alcance
de la cuenta DSM debe declararse: sus tareas visibles no prueban que se estén
viendo las de todos los usuarios.

Una respuesta exitosa sin tareas es una lista vacía válida. Un fallo de
autenticación o de transporte es fuente inaccesible, nunca una lista vacía.
Datos fuera de TTL son desactualizados; conservar su observación original y no
marcarlos como actuales al servir una caché. No reenviar mensajes de error crudos
de DSM: pueden incluir destinos, rutas, enlaces o credenciales.

## 3. Credenciales y publicación

Guardar la cuenta/sesión DSM y la clave Tautulli solamente en archivos privados
del NAS, cargados por el colector local. Solicitar la contraseña interactivamente
en el NAS cuando sea necesario; no pegarla en ChatGPT ni incluirla en comandos.
Una cuenta dedicada con acceso al paquete no implica que la API nativa restrinja
sus permisos a lectura: el colector y MCP deben imponer su propia lista estricta
de operaciones permitidas. Login/logout son únicamente administración de la
sesión; no se permiten create/delete/pause/resume ni terminación de reproducciones.

Publicar solamente proyecciones saneadas mediante reemplazo atómico. El proceso
que responde MCP consume cachés, no credenciales NAS ni respuestas de los
servicios originales. Las nuevas fuentes se empaquetan en una versión nueva;
no sobrescribir el release `275A180` ni su manifiesto. Revisar los artefactos
efectivos NAS/VM antes de activar para no modificar solamente la copia de trabajo.

## 4. Pruebas y criterio de terminado

Primero fixtures/mocks: películas vs. series/episodios, varias bibliotecas,
actividad vacía, paginación de tareas, tamaño desconocido, velocidad cero,
errores, sesión expirada, fuente caída, TTL vencido, última observación conservada
y exclusión de credenciales/enlaces en todos los campos publicados. Probar que
ninguna consulta permite mutar biblioteca, sesiones ni descargas.

Luego comparar con Plex y Download Station en el NAS. Verificar dos ciclos de
recolección con timestamps crecientes y leer ambas herramientas desde ChatGPT.
La lista de herramientas debe anunciar las dos consultas como solo lectura.

Terminado únicamente cuando ChatGPT pueda responder «¿cuántas películas tengo?»
y «¿qué está bajando ahora?» con datos comparados contra las interfaces reales,
alcance explícito y antigüedad. Una respuesta de prueba local no sustituye esta
validación de punta a punta.

## Referencias primarias

- https://github.com/Tautulli/Tautulli/wiki/Tautulli-API-Reference
- https://github.com/Tautulli/Tautulli/blob/master/plexpy/config.py
- https://global.download.synology.com/download/Document/Software/DeveloperGuide/Package/DownloadStation/All/enu/Synology_Download_Station_Web_API.pdf

El instalador versionado aplica la extensión de medios sobre el código fuente
NAS fijado por hash, conservando su implementación de discovery. Así el commit
de medios no necesita incorporar los cambios de discovery aún sin versionar.
Esta mejora de empaquetado no modifica el despliegue activo.
