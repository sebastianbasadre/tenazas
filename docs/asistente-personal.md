# Asistente personal de Tenazas

Los comandos de Telegram usan una cola SQLite persistente y un worker supervisado.
El scheduler interno conserva la responsabilidad de las ejecuciones periódicas;
los nueve workflows de n8n siguen desactivados.

| Función | Uso |
| --- | --- |
| Resumen personal | `/resumen`: agenda, pendientes, trabajos, homelab, vacantes e IOL |
| Seguimiento | `/jobs`, `/jobs id`, `/cancelar id`, `/aprobar id` |
| Memoria | `/memoria ver`, `/memoria poner campo valor`, `/memoria borrar campo`, `/memoria limpiar` |
| Empleo | `/empleo buscar texto`, `/empleo lista`, `/cv número`, `/postulaciones` |
| Documentos | Adjuntar PDF, DOCX, TXT, MD o CSV con leyenda `/documento`; `/documentos pregunta`, `/documentos lista`, `/documentos borrar id` |
| Diagnóstico | `/diagnostico contenedor`: evidencia, causa probable, acción y rollback propuestos |
| Rutinas | `/rutina cine`: preparar plan; ejecutar con `/aprobar id` |
| Mejoras | `/semanal`: errores, tiempos de trabajos y propuestas |
| Pendientes | `/tareas agregar texto`, `/tareas`, `/tareas terminar id` |

Horario predeterminado: resumen diario a las 09:00 y semanal los viernes a las
18:00, Argentina. Cambiar con `/resumen horario 08:00` o
`/semanal horario domingo 19:00`; desactivar con `/resumen horario off` o
`/semanal horario off`. No envía retrospectivamente un horario anterior a la
instalación. Cada fecha tiene una clave única que evita duplicar el cron anterior.

Los trabajos pasan por recibido, trabajando, esperando aprobación y terminado,
con estados separados para fallo, cancelación e interrupción. Un reinicio no
repite automáticamente una acción externa. Una entrega Telegram de resultado
incierto tampoco se reenvía automáticamente; el resultado queda en `/jobs id`.
Cancelar pide detener el proceso, con hasta 45 segundos para terminar o intentar
rollback. Ante interrupción de una acción externa, revisar sus efectos antes de
repetirla.

Preparar el CV selecciona una vacante concreta y crea una postulación pendiente.
Enviar exige aprobación explícita. El envío depende de los adaptadores de portales
existentes y su configuración; un portal sin integración informa el bloqueo.

La memoria editable es el perfil del usuario principal que utiliza el chat.
Proyectos e intereses usan listas JSON, por ejemplo
`/memoria poner projects ["Tenazas", "Homelab"]`. Los documentos y trabajos
se restringen al chat propietario. Borrar el perfil no borra documentos ni
postulaciones: se administran por separado.

La búsqueda documental devuelve extractos con archivo y página o fragmento.
Usa búsqueda textual FTS; no realiza OCR ni interpreta imágenes. Límite de
20 MB por archivo. PDF necesita texto extraíble y `pdftotext` disponible.

La rutina inicial es cine: TV Q70, Marantz en entrada TV y escena Hue.
Lee el estado antes de pedir aprobación y vuelve a comprobarlo antes de ejecutar.
Verifica cada dispositivo y, ante fallo, intenta restaurar lo que cambió,
indicando si pudo verificar la restauración. La conectividad de TV no demuestra
por sí sola que la pantalla esté mostrando imagen. El diagnóstico sólo propone
acciones; no reinicia contenedores.

El informe semanal agrupa los trabajos de siete días. Los tiempos incluyen la
espera de aprobación. Las métricas del proxy son su ventana reciente y están
identificadas como tales; no se presentan como siete días completos.

Piper y watchtower no son requisitos predeterminados del health sweep. Piper
detenido informa voz no disponible sin fallar el chequeo; incluir `piper` en
`expected_containers` lo vuelve obligatorio. Esto no inicia servicios detenidos.
