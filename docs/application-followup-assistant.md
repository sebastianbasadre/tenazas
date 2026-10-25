# Seguimiento de postulaciones desde ChatGPT

Interfaz elegida: **ChatGPT mediante el MCP existente de Tenazas**. La extensión
nueva de Telegram se retiró: comandos, consultas naturales, tareas y avisos.
Los datos y la conexión Gmail se conservan en el NAS. El bot preexistente sigue
funcionando para sus otras tareas.

## Uso previsto en ChatGPT

- «Registrá que me postulé a Acme como Product Manager; usé CV-producto-v2.pdf».
- «¿Cómo van mis postulaciones y qué tengo pendiente?».
- «Pasá la postulación de Acme a entrevista».
- «Tengo entrevista el 3 de noviembre a las 15:00».
- «Revisá Gmail por respuestas de estas postulaciones».
- «Confirmá esta sugerencia de entrevista» o «Descartala».
- «Preparame un mensaje de seguimiento».

Registrar guarda una aplicación ya realizada; no envía CVs ni postula por el
usuario. Los borradores son texto para revisar; no se crean ni envían correos.
Las fechas se interpretan en Buenos Aires y se devuelven también formateadas.
Las próximas acciones se consultan en el chat; esta integración no programa
notificaciones fuera de ChatGPT ni tareas nativas de ChatGPT.

## Herramientas MCP

| Herramienta | Alcance |
|---|---|
| get_job_applications | Listado paginado de 30 y cantidad de acciones vencidas |
| get_job_application | Detalle, CV de referencia, entrevista e historial |
| register_job_application | Alta idempotente por empresa, puesto y URL |
| update_job_application | Estado, nota, contacto, fecha o próxima acción |
| draft_job_followup | Borrador de texto, sin enviar |
| refresh_job_mail | Inicia una lectura Gmail acotada, devuelve ID de trabajo |
| get_job_mail_review | Estado de lectura y hasta diez sugerencias pendientes |
| resolve_job_mail_suggestion | Confirmar o descartar explícitamente una sugerencia |
| delete_job_application | Borrado con confirm=true; conserva correos y CVs originales |

Los argumentos no aceptan principal, chat, comandos, rutas, credenciales ni
cuentas de correo. El principal local es personal. Se mantiene el aislamiento
frente a registros de otros propietarios. Esta instancia está destinada al
usuario personal autorizado de Tenazas, no a varios usuarios independientes.
El registro y los eventos viven en el SQLite local de OpenClaw.

## Gmail

Se usa la cuenta validada y el archivo privado job-gmail.toml (0600) del NAS.
Himalaya solo ejecuta envelope list y message read --preview, manteniendo flags.
Se revisan hasta 150 cabeceras de INBOX de los últimos treinta días y veinte
cuerpos candidatos. No incluye mensajes archivados y se informa el truncamiento.

Las coincidencias por empresa o recruiter deben identificar una única aplicación;
se usa el puesto para desambiguar. Los correos viejos o ambiguos se omiten. El
texto nunca se ejecuta ni se manda a un LLM local. Se guardan únicamente asuntos
acotados y propuestas, sin cuerpos ni adjuntos. Confirmar es una operación aparte;
si cambió la postulación desde la propuesta, se evita sobrescribirla.
La lectura falla explícitamente ante errores de Gmail, sin fingir una lista vacía.

Para renovar la credencial se conserva tools/configure_job_gmail.py: es interactivo,
verifica TLS y selecciona INBOX readonly antes de guardar. La credencial se ingresa
solo en la terminal NAS. La configuración general de correo no se modifica.

## Transporte y autorización

La VM llama a un comando SSH fijo del NAS. Se generó una clave exclusiva, sin
acceso a shell: authorized_keys restringe la IP origen a 192.168.2.91, bloquea
PTY/forwarding y fuerza application_mcp_runner.py dentro del contenedor como
usuario tenazas. El cliente verifica la clave de host conocida del NAS. No se
abrieron puertos nuevos y no se copiaron credenciales Gmail a la VM.

Las consultas usan read:tenazas. Altas, cambios, confirmación de sugerencias y
borrado requieren además write:applications, comprobado sobre el JWT validado
por el proxy ANTES de llegar al gateway. Los permisos de lectura existentes no
habilitan escrituras. Las ocho herramientas de infraestructura/medios mantienen
su contrato de solo lectura. El gateway anuncia las nueve nuevas herramientas
solo cuando se configura explícitamente la capacidad SSH.

La lectura Gmail es asíncrona y se consulta por ID; no pasa por el worker de
Telegram. Solo hay una lectura en curso. Los trabajos interrumpidos se identifican
sin repetir efectos de negocio. No se admiten acciones de envío de correo.

## Despliegue y validación

La parte NAS está activa y se probó el comando fijo desde la VM. Se verificó
que un comando SSH alternativo se ignora. Se preservó la base antes de migrar el
propietario anterior a personal y desactivar la extensión de Telegram: backup
state/assistant/backups/chatgpt-99a6a5fa dentro del home de OpenClaw. Había cero
postulaciones para migrar; no se insertaron ejemplos en producción.

El instalador VM está preparado en /home/mcpadmin/tenazas-applications-mcp-v1.
Usa parches contextuales estrictos sobre las copias efectivas, releases nuevos,
backup root-only y rollback. Conserva TLS, configuración OAuth, argumentos,
sandbox, túnel y estado de arranque. Detiene el gateway antes de cambiar el proxy
para que el proxy anterior de lectura nunca alcance las herramientas nuevas de
escritura. No sobrescribe releases de medios ni el release NAS 275A180.

Bundle SHA256: 4442a703548705c4ee18dfd611aed557933652320766f1640a9641579a57bcd7.
Instalador SHA256: c4b733a928388699d3f001e73746a97de9b29227f4ea8b9aa1ef166edacf581c.

Instalado en la VM el 25 de octubre de 2026: jobs-chatgpt-v1. Verificadas las
17 herramientas, listado real de postulaciones (cero registros), trabajo de
correo finalizado sin errores y consulta de infraestructura correcta.

Pendiente: registrar/autorizar write:applications en Auth0 y reconectar el MCP
en ChatGPT. La interfaz conectada sigue pidiendo renovar la autenticaci�n;
no se declara completada la prueba desde ChatGPT.

Las pruebas cubren argumentos, aislamiento de propietario, altas y estados,
Gmail sin cambios de flags ni envíos, revisión explícita, claves SSH fijas,
anotaciones, rechazo de todas las escrituras con token de solo lectura y permiso
específico antes de reenviar. Los datos, correos y CVs reales no se usan como fixtures.

Referencia de permisos Auth0: https://auth0.com/docs/get-started/apis/add-api-permissions.

## Refresh del catalogo de ChatGPT

Renovar OAuth no sustituye el refresh de herramientas. Tras desplegar cambios,
abrir la conexion Tenazas en https://chatgpt.com/plugins, seleccionar Refresh,
verificar que aparecen 17 herramientas (incluido get_job_applications) y abrir
una conversacion nueva con el plugin seleccionado. Si siguen apareciendo seis,
comparar la URL configurada con la ruta publica del proxy actualizado; la prueba
interna del gateway no demuestra que el endpoint publico use ese gateway.
No se da por validada la interfaz ChatGPT hasta poder llamar a la herramienta.

Referencia oficial: https://developers.openai.com/plugins/deploy/connect-chatgpt#refresh-metadata.
