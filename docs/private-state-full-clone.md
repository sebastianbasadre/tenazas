# Gate de clon integrado, exclusivamente staging

Rama `feature/tenazas-readonly-mcp`. **NO PUSH / NO MERGE / NO DEPLOY**.

El candidato cambia diez consumidores al resolver fijo
`runtime/private-state/state.json`, sin fallback a la ruta antigua. El writer
canónico `runtime.save_json()` usa `private_json.save_json()` únicamente para
ese archivo, con UID/GID explícitos 1026:1026. Los demás JSON conservan su API.
`tools/staging_private_json.py` reexporta el mismo writer para sus pruebas de
inyección de fallos. Estos son cambios del checkout candidato, no de producción.

La validación integrada usa el `startup.sh`, `service_supervisor.py` y
`worker_supervisor.py` realmente desplegados, capturados como código solamente:
269 archivos de runtime/tools/scripts y 31 fuentes de skills. Se conservan
manifiestos SHA-256 de ese código externo al checkout. Se superponen solamente
los diez consumidores modificados y los dos módulos nuevos del candidato.
La imagen de instrumentación agrega strace a la imagen de revisión; no retaguea
ni reemplaza imágenes productivas.

El clon es un contenedor separado, sin puertos publicados, con red `none`, sin
socket Docker, sin claves SSH, sin configuraciones, tokens ni bases reales.
Los probes CLI de dispositivos son fixtures que rechazan comandos desconocidos.
Las funciones de negocio, ambos writers, OpenClaw y los supervisores son reales.
La dependencia Ollama devuelve embeddings deterministas en loopback; Chroma es
un servidor real separado que comparte solamente el namespace de red del clon
y tiene almacenamiento sintético. Los endpoints constantes de las skills RAG
se sustituyen exclusivamente en la copia del fixture por direcciones loopback.
Esto valida consumo de estado e integración local; no valida modelos GPU ni
servicios externos reales.

Se prepara un baseline completo antes de migrar. La ruta antigua se reemplaza
en el clon por un archivo regular 0000 de root con contenido deliberadamente
inválido: no contiene un JSON alternativo. Esto evita que filtros de archivos
regulares omitan el tripwire por su tipo, usando metadata cacheada de DirEntry.
strace sigue descendientes Python, Node y shell. Las invocaciones por Docker
exec reciben trazas propias. El filtro registra syscalls de archivos, sin
execve, contenido de archivos ni argumentos de autenticación de los procesos.
El control negativo registra tanto stat como open a la ruta antigua, incluso
cuando el consumidor podría convertir el resultado en un fallback silencioso.
Ese control tiene un prefijo separado y se excluye del período de observación.
`tools/private_state_trace.py` considera bloqueante todo acceso absoluto a la
ruta antigua y todo `state.json` relativo cuyo dirfd privado no esté resuelto.

El período contado debe comenzar con fuentes fijadas al SHA candidato. Debe
registrar varios ciclos reales de 300 s, jobs inmediatos y jobs diferidos,
health_sweep, snapshots, alertas, memoria, indexación/búsqueda documental y
mantenimiento. Un consumidor nuevo reinicia toda la observación. Un worker
que declara `ok: true` pero contiene un error interno no constituye éxito de
esa función: esos fallos se registran separadamente con comparación del baseline.

Después de cada ciclo: JSON parseable, padre 0700, archivo 0600, UID/GID
1026:1026, sin temporales, ACL nativa Synology en modo Linux, lectura denegada
a otro UID, trazas sin accesos antiguos y evidencia de ambos writers.
No se deduce el UID del contenedor a partir del usuario NAS `tenazas` 1035.

El rollback se hace con todos los procesos del clon detenidos. Restaura el
código completo, startup y configuración de baseline, devuelve **el estado
más reciente** a la ruta anterior y conserva JSON de memoria, documentos,
SQLite/WAL y jobs creados durante la prueba. Se comparan hashes lógicos de las
bases y de los JSON antes y después de restaurar, se arranca el baseline
completo y se vuelven a ejecutar ambos writers. No se restaura una base vieja
que descarte el trabajo realizado durante staging.

La preparación y los controles preliminares no cierran el gate. El resultado
final debe incluir el SHA observado, duración/ciclos reales, errores internos,
trazas nativas, ACL y el rollback completo. Las evidencias del ensayo se guardan
en el directorio aislado `full-clone-20261004` y en una copia local independiente.
El cutover del NAS y Tunnel/OAuth siguen siendo gates posteriores separados.
