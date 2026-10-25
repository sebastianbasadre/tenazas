# Asistente de seguimiento de postulaciones

El seguimiento vive en Telegram, con datos separados por chat dentro del SQLite
local existente `state/assistant/jobs.sqlite`. No requiere un servicio adicional.
Complementa al buscador y a las propuestas de envío existentes.

## Empezar

```text
/postulaciones agregar Acme | Product Manager | https://jobs.example/123 | CV-producto-v2.pdf
/postulaciones
/postulaciones ver ID
/postulaciones fecha ID 2026-10-20 09:00
/postulaciones estado ID entrevista | Me contactó el recruiter
/postulaciones entrevista ID 2026-11-03 15:00 | Primera entrevista
/postulaciones recordar ID 2026-11-02 09:00 | Preparar ejemplos de proyectos
/postulaciones contacto ID | Envié seguimiento por mi cuenta
/postulaciones nota ID Revisar modalidad y equipo
/postulaciones recruiter ID recruiter@example.com
/postulaciones borrador ID
/postulaciones pendientes
```

Registrar indica una postulación que el usuario ya realizó. No envía el CV.
La fecha inicial es la del registro; `fecha` permite corregirla para envíos
anteriores. El CV es una referencia al archivo utilizado, no una nueva subida.
La próxima acción inicial se programa siete días después de la fecha de envío.

Estados: postulada, screening, entrevista, técnica, oferta, aceptada, rechazada,
retirada y pausada. Cerrar o pausar cancela las próximas acciones. Reabrir permite
volver a programarlas. Las entrevistas tienen fecha independiente y una acción
de preparación un día antes; no se crea un evento de calendario automáticamente.
Las fechas y los avisos se interpretan en America/Argentina/Buenos_Aires.

También se reconocen consultas exactas como «¿Cómo van mis postulaciones?» y
«postulaciones pendientes». El seguimiento no necesita consultar un LLM.

## Gmail con revisión

```text
/postulaciones correo
/postulaciones sugerencias
/postulaciones confirmar ID_SUGERENCIA
/postulaciones descartar ID_SUGERENCIA
```

La consulta se encola en el worker existente. Usa la cuenta Gmail configurada
localmente con Himalaya; no utiliza credenciales del conector de ChatGPT.
Solo el chat principal puede consultar la cuenta o confirmar sugerencias.
Se revisa INBOX de los últimos treinta días: máximo 150 cabeceras y veinte
cuerpos candidatos por ejecución. No incluye correo archivado ni garantiza
cobertura de todo el período; se declara el límite cuando se alcanza.

La asociación usa la empresa o el email de recruiter registrado, y el puesto
para resolver empresas con varias postulaciones activas. Los casos ambiguos o
anteriores a la postulación se omiten. Las reglas buscan indicios de entrevista,
prueba técnica, oferta, screening o rechazo. Son sugerencias, no resultados
confirmados. No se infieren fechas de entrevistas desde el correo.

El texto del correo nunca se ejecuta ni se manda a un LLM. Solo se guarda el
asunto acotado y el estado propuesto; no se guardan cuerpos ni adjuntos. Las
sugerencias se deduplican y requieren confirmación explícita. Si el usuario
modificó la postulación después de generarlas, se evita sobrescribir ese cambio.
Himalaya solo puede ejecutar `envelope list` y `message read --preview` por este
adaptador. Se rechazan envíos, borrados, cambios de flags y lecturas sin preview.
Errores de autenticación son errores, nunca una bandeja vacía exitosa.

La credencial anterior del NAS fue rechazada. El usuario configuró y verificó
una conexión dedicada con `tools/configure_job_gmail.py`, sin mostrar secretos.
La configuración exclusiva queda en
`state/assistant/job-gmail.toml`, modo 0600, fuera de Git. El asistente verifica
TLS e INBOX en modo readonly antes de guardar. No modifica la configuración
Himalaya que usan otras funciones de OpenClaw. Para renovar, en el NAS:

```sh
sudo /usr/local/bin/docker exec -it -u tenazas openclaw python3 /home/tenazas/tools/configure_job_gmail.py
```

La contraseña de aplicación se ingresa solo en esa terminal. Google requiere
verificación en dos pasos para generarla: https://support.google.com/accounts/answer/185833.

## Avisos y propuestas anteriores

```text
/postulaciones avisos 09:00
/postulaciones avisos off
/postulaciones propuestas
/postulaciones importar
/postulaciones borrar ID
```

Los avisos son opt-in. El scheduler existente revisa las postulaciones activas
una vez por fecha y chat después del horario elegido. El digest consulta Gmail
para el chat principal y avisa si hay acciones vencidas, sugerencias nuevas o
un fallo de lectura. Sin novedades no se envía el digest. Desactivar avisos
también bloquea entregas que ya estuvieran encoladas. El flujo de entrega
existente conserva su política de no repetir envíos de resultado incierto.

`propuestas` muestra la cola anterior del chat principal. `importar` solo incorpora
entradas cuyo envío ya está confirmado como `submitted`; no convierte aprobaciones,
CVs preparados ni rechazos de una propuesta en aplicaciones realizadas.
Los envíos exitosos futuros de ambos flujos de aprobación se registran con su CV.
Si falla el registro después de un envío confirmado, ese envío sigue informado
como exitoso, evitando inducir una segunda postulación.

`borrar` elimina la postulación, eventos y sugerencias de ese chat. No elimina
correos, CVs originales, documentos ni el registro de envío anterior.

## Validación y operación

Pruebas con SQLite temporal, clientes de correo simulados y transporte Telegram
simulado: aislamiento por chat, altas concurrentes, duplicados, fechas, cierre,
historial, cancelación de avisos, clasificación ambigua, correos antiguos,
confirmación obsoleta, fallos de autenticación y ausencia de efectos externos.
Se valida además el parser real de Himalaya y lectura Gmail en el contenedor.

El despliegue usa el mount de runtime existente; se recargan únicamente
telegram_router y assistant_worker bajo su supervisor. Antes se comprueban
trabajos activos y se crea un backup coherente de SQLite mediante su API backup.
Las tablas nuevas son aditivas; no se modifican las colas anteriores al importar.
El rollback restaura únicamente los módulos de esta extensión y recarga esos
dos procesos. Los datos del tracker pueden conservarse; no es necesario
restaurar la base completa y perder trabajos recientes.

La suite ampliada del buscador anterior detectó cuatro fallos en
test_job_search_platform.py (alias TAM y búsquedas), fuera del seguimiento nuevo.
No se alteró agent_job_search.py ni se declaran resueltos por esta extensión.

## Resultado del despliegue

Se ejecutaron 75 pruebas del seguimiento, el asistente, las aprobaciones y el
router en Linux: todas pasaron, sin skips. En Windows: 74 pasaron y una prueba
específica de Linux se omitió. Ruff y la validación de sintaxis pasaron.

En el contenedor se verificaron los comandos slash y la consulta natural sin
emitir mensajes de Telegram de prueba. Se leyó una página real de 50 cabeceras
Gmail y un mensaje laboral mediante preview; sus flags permanecieron iguales.
La configuración dedicada quedó 0600 y no se mostraron credenciales ni cuerpos.

Backup previo: `state/assistant/backups/followup-20261025T012919Z-36c51e`, dentro
del home de OpenClaw. Contiene snapshot consistente de SQLite, los ocho módulos
anteriores y un manifiesto de hashes/commit. No había trabajos activos al recargar.
El supervisor permaneció con el mismo PID y recargó solo router y worker.

La importación inicial encontró cero envíos confirmados; no se inventaron
postulaciones ni se incluyeron las propuestas pendientes. El usuario puede
registrarlas manualmente o consultar la cola con `/postulaciones propuestas`.
Avisos automáticos sin activar; el usuario elige horario mediante el comando
correspondiente. El resto de rutinas, el MCP y sus servicios no se modificaron.
