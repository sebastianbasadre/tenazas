#!/usr/bin/env python3
"""
build_multitool_dataset.py — Generate SFT dataset for multitool finetuning.

Generates JSONL with single-tool, multi-tool, no-tool, and ambiguous examples.
All in Spanish rioplatense, matching Tenazas' voice.

Target: ~800 examples balanced across 12 tools + negatives.

Usage:
    python3 build_multitool_dataset.py                    # stdout
    python3 build_multitool_dataset.py -o dataset.jsonl   # file
    python3 build_multitool_dataset.py --stats            # show counts
"""

import json
import random
import re
import sys

SYSTEM_MSG = "Respond only with valid XML tool calls."

ALLOWED_TOOLS = {
    "weather", "email", "calendar", "infra-monitor", "network-scanner",
    "arr-monitor", "self-evolve", "reminders", "smart-home-on",
    "smart-home-off", "smart-home-cinema", "tts",
}

# ── Single-tool examples ─────────────────────────────────────────

WEATHER = [
    "cómo está el clima",
    "qué temperatura hace",
    "va a llover hoy",
    "hace frío afuera",
    "clima en buenos aires",
    "cómo está el tiempo",
    "pronóstico de hoy",
    "hace calor hoy",
    "decime el clima",
    "qué onda el clima",
    "está lindo afuera",
    "sale para salir o llueve",
    "necesito abrigo hoy",
    "cómo va a estar el tiempo mañana",
    "va a estar lindo",
    "qué dice el pronóstico",
    "necesito paraguas",
    "cómo va a estar a la tarde",
    "va a hacer frío esta noche",
    "cómo viene el clima para el finde",
    "está para ir a correr afuera",
    "llevo campera o no",
    "hay probabilidad de tormenta",
    "cómo va a estar mañana a la mañana",
    "decime la temperatura actual",
    "va a nevar",
    "está húmedo hoy",
    "hace viento afuera",
    "clima para esta semana",
    "cómo amanece mañana",
    "va a estar nublado",
    "qué temperatura hay ahora",
    "hace buen día",
    "sale día de pileta",
    "está para mate en la plaza",
    "cómo está el cielo",
    "hay alerta meteorológica",
    "voy a necesitar protector solar",
    "está para salir en bici",
    "cómo va a estar el sábado",
    "llueve a la noche",
    "cuántos grados hay",
    "se viene tormenta",
]

EMAIL = [
    "revisá mis mails",
    "tengo correos nuevos",
    "mostrame los emails",
    "chequeá el inbox",
    "cuántos mails tengo",
    "hay emails sin leer",
    "leeme los correos",
    "emails recientes",
    "fijate si tengo mails",
    "mis correos",
    "hay algo importante en el mail",
    "mails de hoy",
    "llegó algún mail",
    "chequeá el correo",
    "algún mail urgente",
    "últimos emails recibidos",
    "hay respuestas en el mail",
    "fijate el correo electrónico",
    "mails sin responder",
    "alguna novedad en el inbox",
    "correos del trabajo",
    "mails de esta semana",
    "hay mails pendientes",
    "revisá si llegó algo",
    "mi bandeja de entrada",
    "qué dice el último mail",
    "hay mails del banco",
    "correos de hoy a la mañana",
    "fijate si me respondieron",
    "mails no leídos",
    "cuántos correos nuevos hay",
    "hay algo en spam",
    "emails importantes",
    "últimos 5 correos",
]

CALENDAR = [
    "qué tengo hoy en la agenda",
    "mis eventos de hoy",
    "qué reuniones tengo",
    "agenda de mañana",
    "tengo algo hoy",
    "próximos eventos",
    "mi calendario",
    "qué hay en la agenda",
    "tengo reuniones hoy",
    "eventos de esta semana",
    "qué tengo agendado",
    "agenda para hoy",
    "hay reuniones mañana",
    "mis citas de hoy",
    "cuántas reuniones tengo",
    "qué hay en el calendario",
    "eventos próximos",
    "agenda de la semana",
    "tengo compromisos hoy",
    "qué día tengo libre",
    "reuniones de esta tarde",
    "hay algo agendado para el viernes",
    "cuándo es mi próxima reunión",
    "qué tengo a las 3",
    "agenda del lunes",
    "hay algo para esta tarde",
    "qué tengo el martes",
    "reuniones de mañana a la mañana",
    "eventos del miércoles",
    "tengo algo agendado a las 10",
    "hay reuniones esta semana",
    "qué hay para hoy a la tarde",
    "mis compromisos de mañana",
    "cuándo tengo libre",
    "hay algo en la agenda para el jueves",
    "mis meetings del día",
    "cuántas citas tengo hoy",
    "agenda de esta tarde",
    "reuniones pendientes",
    "próxima reunión",
    "qué tengo después del almuerzo",
    "calendario de la próxima semana",
    "tengo huecos libres hoy",
    "qué hay agendado para las 2",
]

INFRA_MONITOR = [
    "estado de los containers",
    "cómo andan los containers",
    "docker status",
    "están todos los containers andando",
    "cuántos containers corren",
    "estado del docker",
    "cómo está la infra",
    "qué containers están corriendo",
    "cómo está el homelab",
    "estado del servidor",
    "cómo va el NAS",
    "hay algún problema de infra",
    "revisá la infraestructura",
    "cómo están los recursos del sistema",
    "uso de CPU del NAS",
    "temperatura del NAS",
    "cómo están los discos",
    "espacio disponible en el NAS",
    "memoria RAM del NAS",
    "cómo está el servidor de Ollama",
    "está bien la infra",
    "hay alertas de sistema",
    "status del homelab completo",
    "cómo está el hardware",
    "uso de disco del NAS",
    "performance del servidor",
    "cómo están los recursos",
    "revisá el estado general del sistema",
    "hay containers caídos",
    "cuánto espacio queda en disco",
    "se reinició algún container",
    "qué procesos están corriendo",
    "cómo está el uptime",
    "hay algún container en crash loop",
    "cuánta RAM se está usando",
    "el NAS está respondiendo lento",
    "cómo están los volúmenes",
    "hay errores de disco",
    "estado de Prometheus",
    "cómo está Grafana",
    "Portainer muestra algo raro",
    "el Ollama del NAS responde",
    "watchtower actualizó algo",
]

NETWORK_SCANNER = [
    "escaneá la red",
    "cuántos dispositivos hay en la red",
    "quién está conectado",
    "scan de red",
    "dispositivos en la red",
    "nmap",
    "qué dispositivos hay en la red",
    "mostrá los dispositivos conectados",
    "quién está en la red local",
    "hacé un scan de red",
    "qué IPs hay activas",
    "escaneá el homelab",
    "buscá dispositivos en la LAN",
    "qué equipos están conectados al router",
    "mostrá los hosts activos",
    "hay dispositivos desconocidos en la red",
    "escaneá 192.168.2.0/24",
    "qué está conectado a la red ahora",
    "listá los equipos de la red",
    "hacé un nmap",
    "detectá dispositivos nuevos en la red",
    "quién se conectó a la red hoy",
    "escaneá puertos del homelab",
    "qué dispositivos tiene la LAN",
    "mostrá la topología de red",
    "cuántos equipos hay en la red",
    "hay equipos nuevos en la red",
    "cuántas IPs activas hay",
    "ping sweep de la LAN",
    "escaneá la subred",
    "qué hay en la red local",
    "fijate los dispositivos conectados",
    "listá las IPs activas",
    "chequeá la red",
    "qué IPs responden a ping",
    "escaneá los puertos abiertos",
    "hay algo raro en la red",
    "quiénes están en el wifi",
    "topología de la LAN",
    "dispositivos wifi conectados",
    "discovery de la red",
    "ARP scan de la subred",
    "mostrá la tabla ARP",
    "qué hay en el segmento de red",
    "hosts nuevos en la LAN",
    "fijate si hay intrusos en la red",
]

ARR_MONITOR = [
    "cómo está el arr stack",
    "estado de sonarr",
    "andan las descargas",
    "estado del media stack",
    "cómo anda radarr",
    "estado de los torrents",
    "cómo están las descargas",
    "qué está descargando sonarr",
    "estado de radarr",
    "hay algo descargando ahora",
    "qué hay en la cola de descargas",
    "cuánto queda de descarga",
    "está funcionando el arr-stack",
    "hay errores en sonarr",
    "radarr está andando",
    "qué series se están bajando",
    "está activo qbittorrent",
    "hay descargas en progreso",
    "cuántos torrents activos",
    "cómo está la velocidad de descarga",
    "el indexer de prowlarr anda",
    "hay errores en radarr",
    "lidarr está descargando algo",
    "qué hay nuevo en sonarr",
    "prowlarr tiene indexers caídos",
    "velocidad de qbittorrent",
    "cuántos torrents hay en cola",
    "falló alguna descarga en sonarr",
    "estado del qbittorrent",
    "las series se están bajando bien",
    "hay películas descargando en radarr",
    "el torrent está seedeando",
    "qué música se está bajando en lidarr",
    "fijate si prowlarr tiene errores",
    "sabnzbd está funcionando",
    "cómo anda el torrent client",
    "hay algo trabado en la cola de torrents",
    "sonarr encontró episodios nuevos",
    "cuántas descargas completó radarr",
    "estado de los indexers",
    "requestrr está funcionando",
    "seerr tiene peticiones nuevas",
    "hay algo en la cola de sabnzbd",
    "errores en los indexers",
    "el tracker está respondiendo",
    "velocidad de seedeo",
    "ratio de los torrents",
    "hay algo trabado en radarr",
    "tautulli muestra algo",
    "cómo va la cola de seerr",
    "hay peticiones en seerr pendientes",
    "prowlarr está online",
    "el arr stack está completo",
]

SELF_EVOLVE = [
    "propuestas de mejora",
    "hay propuestas pendientes",
    "self evolve",
    "qué mejoras hay",
    "propuestas del sistema",
    "mostrá las propuestas",
    "qué propuestas generaste",
    "últimas propuestas de self-evolve",
    "corrió el self evolve",
    "hay mejoras propuestas",
    "qué sugerencias tenés",
    "propuestas sin revisar",
    "cuántas propuestas hay",
    "mostrá el reporte de mejoras",
    "qué detectó el self-evolve",
    "hay algo para mejorar",
    "propuestas de la última corrida",
    "qué aprendiste esta semana",
    "fijate las propuestas pendientes",
    "últimos análisis de mejora",
    "mostrá el learning report",
    "hay propuestas nuevas",
    "qué querés mejorar",
    "corrió el análisis semanal",
    "propuestas de optimización",
    "qué cambios proponés",
    "hay algo que se pueda mejorar",
    "mostrá las propuestas aceptadas",
    "cuántas propuestas rechazaste",
    "último reporte de self-evolve",
    "análisis de la semana",
    "qué detectaste que se puede mejorar",
    "propuestas de esta semana",
    "evolución del sistema",
    "qué aprendiste del routing",
    "propuestas de cambio",
    "informe de mejora continua",
    "estado del self-evolve",
    "hay algo nuevo en el aprendizaje",
]

REMINDERS = [
    "mis recordatorios",
    "qué recordatorios tengo",
    "recordatorios activos",
    "hay recordatorios pendientes",
    "mostrame los reminders",
    "recordame comprar leche",
    "acordate de llamar al plomero",
    "avisame a las 5",
    "tengo algo pendiente",
    "qué pendientes tengo",
    "qué me tengo que acordar",
    "hay algo que me olvido",
    "poneme un recordatorio",
    "agregá un reminder",
    "listá mis pendientes",
    "recordatorios del día",
    "tengo reminders para hoy",
    "qué tenía pendiente",
    "hay algo para acordarme",
    "mostrá mis recordatorios",
    "qué me falta hacer",
    "tengo cosas pendientes",
    "mis pendientes de hoy",
    "fijate si tengo recordatorios",
    "listá los reminders",
    "cuántos recordatorios tengo",
    "recordame pagar la tarjeta",
    "acordate de mandar el mail",
    "avisame mañana a las 10",
    "poneme un reminder para el lunes",
    "recordame sacar turno",
    "haceme acordar de comprar café",
    "avisame cuando sean las 3",
    "recordame que tengo que llamar a mamá",
    "qué me dijiste que recuerde",
    "recordatorios de hoy",
    "hay reminders activos",
    "mostrá mis recordatorios pendientes",
    "qué cosas tengo anotadas",
    "algún recordatorio para hoy",
    "qué me tenía que acordar",
    "tengo pendientes",
    "qué era lo que no me tenía que olvidar",
    "recordatorios sin completar",
    "listame lo que tengo pendiente",
    "fijate mis reminders",
    "hay algo que me olvidé",
    "cuántos pendientes tengo",
    "recordame ir al súper",
    "acordate del cumple de Juan",
    "no te olvides de avisarme lo del delivery",
    "reminder: pagar internet",
    "recordatorio: llevar la ropa al tinte",
    "anotá que tengo que ir al banco",
    "pendientes para mañana",
    "acordate que mañana vence el seguro",
    "agendame un recordatorio de renovar el dominio",
    "no me dejes olvidar lo del médico",
]

SMART_HOME_ON = [
    "prendé las luces",
    "encendé las luces",
    "poné modo relax",
    "modo trabajo",
    "poneme las luces para laburar",
    "luces de relax",
    "dale luz",
    "prendé la luz del living",
    "encendé todo",
    "poneme luz",
    "prendé las luces de la habitación",
    "luces para estudiar",
    "poneme luces suaves",
    "encendé las luces del escritorio",
    "luces para leer",
    "activá las luces",
    "poneme iluminación",
    "necesito luz",
    "está oscuro, prendé",
    "luces al máximo",
    "poneme las luces normales",
    "encendé la lámpara",
    "prendé las luces del comedor",
    "poneme luces para cocinar",
    "subí las luces",
    "poneme las luces brillantes",
    "luces de día",
    "encendé las luces blancas",
    "poneme las luces cálidas",
    "prendé las luces de arriba",
    "prendé que está re oscuro",
    "dame luz en el escritorio",
    "poneme ambiente para cenar",
    "luces para yoga",
    "activá la iluminación",
    "necesito las luces prendidas",
    "poneme las luces lindas",
    "encendé todo que no veo nada",
    "luces ambiente",
    "poneme luces de fondo",
    "subí la intensidad",
    "luces al 100",
    "poneme las luces fuertes",
    "encendé la luz de la cocina",
    "luz para la oficina",
    "poneme las luces de la sala",
    "prendé las de acá",
    "encendé la del baño",
]

SMART_HOME_OFF = [
    "apagá las luces",
    "apagá todo",
    "apagá las luces del living",
    "apagá la luz",
    "oscurecé todo",
    "me voy a dormir, apagá",
    "apagá las luces de la habitación",
    "apagá las luces del escritorio",
    "bajá las luces al mínimo",
    "apagá la lámpara",
    "desactivá las luces",
    "sacá las luces",
    "ya no necesito luz",
    "apagá las luces del comedor",
    "dejá todo apagado",
    "apagá las de arriba",
    "matá las luces",
    "a oscuras",
    "apagá que me voy",
    "cerrá las luces",
    "apagá las de abajo",
    "apagá la del baño",
    "no necesito más luz",
    "apagá las luces de la sala",
    "apagá las luces de la cocina",
    "dejá oscuro",
    "apagá las luces que voy a dormir",
    "sin luz",
    "apagá las del pasillo",
    "cortá la luz",
    "que quede todo apagado",
]

SMART_HOME_CINEMA = [
    "poné modo cine",
    "modo cinema",
    "poneme las luces para ver una peli",
    "luces de cine",
    "vamos a ver una película",
    "modo película",
    "poneme ambiente de cine",
    "luces tenues para ver algo",
    "preparame para ver tele",
    "modo home theater",
    "poneme las luces para Netflix",
    "ambiente de película",
    "poneme las luces bajitas para ver",
    "modo series",
    "luces de fondo para la tele",
    "poneme luces para ver YouTube",
    "modo película con las luces",
    "bajá las luces para la peli",
    "poneme las luces para el streaming",
]

TTS = [
    "mandame un audio diciendo hola",
    "decí buen día en voz alta",
    "mandá audio de prueba",
    "hablá y decí test",
    "text to speech hola mundo",
    "leeme eso en voz alta",
    "decí eso por el parlante",
    "reproducí eso con la voz",
    "mandá audio con eso",
    "leé esto en voz alta",
    "convertí eso a voz",
    "que tenazas hable",
    "respondé con audio",
    "mandá un mensaje de voz",
    "decilo por el speaker",
    "quiero escucharlo, no leerlo",
    "generá audio con esa respuesta",
    "usá la voz para responder",
    "texto a voz",
    "sintetizá voz con eso",
    "que lo diga en voz alta",
    "respuesta en audio",
    "leé el resumen en voz",
    "convertí el texto a audio",
    "mandá voz en lugar de texto",
    "decí eso en audio",
    "habla en vez de escribir",
    "mandame un voice note",
    "leeme el reporte con la voz",
    "pasá eso a audio",
    "necesito que lo digas",
    "con voz, no texto",
    "audio del resumen",
    "voice message con eso",
    "leeme los mails en voz alta",
    "decí el estado en audio",
    "mandá nota de voz",
    "piper test",
    "probá la voz",
]

# ── Multi-tool examples ──────────────────────────────────────────

MULTI_TOOL = [
    # weather + calendar (10)
    ("decime el clima y qué tengo hoy", ["weather", "calendar"]),
    ("clima y agenda de hoy", ["weather", "calendar"]),
    ("cómo está el tiempo y mis eventos", ["weather", "calendar"]),
    ("fijate el clima y la agenda", ["weather", "calendar"]),
    ("pronóstico y reuniones de hoy", ["weather", "calendar"]),
    ("temperatura y calendario", ["weather", "calendar"]),
    ("clima y eventos de mañana", ["weather", "calendar"]),
    ("qué onda el clima y qué tengo", ["weather", "calendar"]),
    ("hace calor y tengo reuniones", ["weather", "calendar"]),
    ("decime el tiempo y mis citas", ["weather", "calendar"]),

    # email + calendar (8)
    ("fijate mis mails y el calendario", ["email", "calendar"]),
    ("correos y agenda de hoy", ["email", "calendar"]),
    ("emails y eventos", ["email", "calendar"]),
    ("chequeá mails y reuniones", ["email", "calendar"]),
    ("tengo mails y qué hay en la agenda", ["email", "calendar"]),
    ("revisá correos y calendario", ["email", "calendar"]),
    ("inbox y agenda", ["email", "calendar"]),
    ("mails nuevos y eventos de hoy", ["email", "calendar"]),

    # smart-home-on + weather (5)
    ("prendé las luces y decime el clima", ["smart-home-on", "weather"]),
    ("encendé las luces y fijate el tiempo", ["smart-home-on", "weather"]),
    ("luces y clima", ["smart-home-on", "weather"]),
    ("poneme las luces y decime la temperatura", ["smart-home-on", "weather"]),
    ("modo relax y fijate el clima", ["smart-home-on", "weather"]),

    # network-scanner + infra-monitor (5)
    ("escaneá la red y decime el estado del sistema", ["network-scanner", "infra-monitor"]),
    ("nmap y docker status", ["network-scanner", "infra-monitor"]),
    ("dispositivos y containers", ["network-scanner", "infra-monitor"]),
    ("scan de red y estado de infra", ["network-scanner", "infra-monitor"]),
    ("red y containers", ["network-scanner", "infra-monitor"]),

    # smart-home-cinema + weather (3)
    ("poné modo cine y decime el clima", ["smart-home-cinema", "weather"]),
    ("modo cinema y temperatura", ["smart-home-cinema", "weather"]),
    ("luces de cine y pronóstico", ["smart-home-cinema", "weather"]),

    # tts + weather / calendar (4)
    ("mandame audio y fijate el clima", ["tts", "weather"]),
    ("audio de prueba y agenda de hoy", ["tts", "calendar"]),
    ("leeme el clima en voz alta y la agenda", ["tts", "weather", "calendar"]),
    ("decí el pronóstico por audio", ["tts", "weather"]),

    # weather + infra-monitor (3)
    ("clima y estado de los containers", ["weather", "infra-monitor"]),
    ("temperatura y docker status", ["weather", "infra-monitor"]),
    ("cómo está el clima y la infra", ["weather", "infra-monitor"]),

    # email + weather (4)
    ("mails y clima", ["email", "weather"]),
    ("correos y temperatura", ["email", "weather"]),
    ("chequeá mails y cómo está el tiempo", ["email", "weather"]),
    ("emails y pronóstico", ["email", "weather"]),

    # self-evolve + infra-monitor (3)
    ("propuestas de mejora y estado de containers", ["self-evolve", "infra-monitor"]),
    ("self evolve y docker status", ["self-evolve", "infra-monitor"]),
    ("mejoras pendientes y cómo está la infra", ["self-evolve", "infra-monitor"]),

    # reminders + calendar (4)
    ("recordatorios y agenda", ["reminders", "calendar"]),
    ("reminders y eventos de hoy", ["reminders", "calendar"]),
    ("mis recordatorios y reuniones", ["reminders", "calendar"]),
    ("qué tengo pendiente y qué reuniones hay", ["reminders", "calendar"]),

    # smart-home-off + calendar / weather (3)
    ("apagá las luces y decime la agenda", ["smart-home-off", "calendar"]),
    ("apagá todo y decime el clima", ["smart-home-off", "weather"]),
    ("apagá las luces y mostrá los eventos", ["smart-home-off", "calendar"]),

    # smart-home-on + email (2)
    ("luces para laburar y mails", ["smart-home-on", "email"]),
    ("prendé las luces y chequeá el correo", ["smart-home-on", "email"]),

    # arr-monitor + infra-monitor (4)
    ("estado del arr stack y containers", ["arr-monitor", "infra-monitor"]),
    ("sonarr y docker status", ["arr-monitor", "infra-monitor"]),
    ("descargas y containers", ["arr-monitor", "infra-monitor"]),
    ("cómo andan los torrents y los containers", ["arr-monitor", "infra-monitor"]),

    # network-scanner + weather (3)
    ("escaneá la red y fijate el clima", ["network-scanner", "weather"]),
    ("dispositivos y temperatura", ["network-scanner", "weather"]),
    ("nmap y pronóstico", ["network-scanner", "weather"]),

    # email + reminders (3)
    ("emails y recordatorios", ["email", "reminders"]),
    ("correos y reminders", ["email", "reminders"]),
    ("fijate recordatorios y mails", ["email", "reminders"]),

    # calendar + self-evolve (2)
    ("agenda y propuestas", ["calendar", "self-evolve"]),
    ("eventos y mejoras pendientes", ["calendar", "self-evolve"]),

    # email + arr-monitor (3)
    ("mails y estado del arr", ["email", "arr-monitor"]),
    ("correos y descargas", ["email", "arr-monitor"]),
    ("emails y cómo andan los torrents", ["email", "arr-monitor"]),

    # weather + arr-monitor (2)
    ("clima y descargas", ["weather", "arr-monitor"]),
    ("temperatura y estado del torrent", ["weather", "arr-monitor"]),

    # reminders + email (2)
    ("pendientes y emails sin leer", ["reminders", "email"]),
    ("hay reminders y correos nuevos", ["reminders", "email"]),

    # reminders + weather (2)
    ("recordame algo y decime el clima", ["reminders", "weather"]),
    ("pendientes y pronóstico de hoy", ["reminders", "weather"]),

    # smart-home-off + smart-home-on context (1)
    ("prendé las luces y poné modo cine no mejor relax", ["smart-home-on"]),

    # network-scanner + arr-monitor (2)
    ("escaneá la red y fijate las descargas", ["network-scanner", "arr-monitor"]),
    ("dispositivos y estado de sonarr", ["network-scanner", "arr-monitor"]),

    # self-evolve + calendar (2)
    ("propuestas y agenda del día", ["self-evolve", "calendar"]),
    ("mejoras y reuniones de hoy", ["self-evolve", "calendar"]),

    # tts + reminders (2)
    ("leeme los recordatorios en voz alta", ["tts", "reminders"]),
    ("decí mis pendientes por audio", ["tts", "reminders"]),

    # smart-home-cinema + calendar (2)
    ("modo cine y después qué tengo", ["smart-home-cinema", "calendar"]),
    ("poné cine y decime si tengo algo después", ["smart-home-cinema", "calendar"]),

    # arr-monitor + weather (2)
    ("cómo andan las descargas y el clima", ["arr-monitor", "weather"]),
    ("torrents y temperatura afuera", ["arr-monitor", "weather"]),

    # self-evolve + email (2)
    ("propuestas y correos", ["self-evolve", "email"]),
    ("mejoras del sistema y mails", ["self-evolve", "email"]),

    # smart-home-off + infra-monitor (2)
    ("apagá las luces y mostrá el estado de la infra", ["smart-home-off", "infra-monitor"]),
    ("apagá todo y docker status", ["smart-home-off", "infra-monitor"]),

    # 3 tools (20)
    ("clima, agenda y mails", ["weather", "calendar", "email"]),
    ("decime el clima, mis eventos y los correos", ["weather", "calendar", "email"]),
    ("temperatura, reuniones y emails", ["weather", "calendar", "email"]),
    ("fijate clima, calendario y mails", ["weather", "calendar", "email"]),
    ("cómo está el tiempo, qué tengo y emails nuevos", ["weather", "calendar", "email"]),
    ("pronóstico, agenda y correos", ["weather", "calendar", "email"]),
    ("clima y agenda y inbox", ["weather", "calendar", "email"]),

    ("escaneá la red, docker status y clima", ["network-scanner", "infra-monitor", "weather"]),
    ("red, containers y temperatura", ["network-scanner", "infra-monitor", "weather"]),
    ("nmap, docker y pronóstico", ["network-scanner", "infra-monitor", "weather"]),

    ("prendé las luces, decime el clima y la agenda", ["smart-home-on", "weather", "calendar"]),
    ("luces, clima y eventos de hoy", ["smart-home-on", "weather", "calendar"]),
    ("encendé luces, temperatura y reuniones", ["smart-home-on", "weather", "calendar"]),

    ("mails, recordatorios y agenda", ["email", "reminders", "calendar"]),
    ("correos, reminders y eventos", ["email", "reminders", "calendar"]),
    ("emails, agenda y recordatorios", ["email", "calendar", "reminders"]),
    ("recordatorios, agenda y mails", ["reminders", "calendar", "email"]),

    ("containers, descargas y red", ["infra-monitor", "arr-monitor", "network-scanner"]),
    ("docker, arr stack y dispositivos", ["infra-monitor", "arr-monitor", "network-scanner"]),

    ("clima, mails y containers", ["weather", "email", "infra-monitor"]),
    ("agenda, clima y propuestas", ["calendar", "weather", "self-evolve"]),

    # Additional 2-tool combos for balance
    ("reminders y estado del arr", ["reminders", "arr-monitor"]),
    ("pendientes y descargas", ["reminders", "arr-monitor"]),
    ("tts y estado de infra", ["tts", "infra-monitor"]),
    ("leeme el estado por audio", ["tts", "infra-monitor"]),
    ("propuestas y descargas", ["self-evolve", "arr-monitor"]),
    ("mejoras y torrents", ["self-evolve", "arr-monitor"]),
    ("apagá las luces y leeme los mails", ["smart-home-off", "email"]),
    ("apagá y decime los recordatorios", ["smart-home-off", "reminders"]),
    ("modo cine y apagá el resto", ["smart-home-cinema", "smart-home-off"]),
    ("red y descargas", ["network-scanner", "arr-monitor"]),
    ("scan de red y estado de sonarr", ["network-scanner", "arr-monitor"]),
    ("email y self evolve", ["email", "self-evolve"]),
    ("mails y propuestas de mejora", ["email", "self-evolve"]),
    ("tts y email", ["tts", "email"]),
    ("leeme los correos en audio", ["tts", "email"]),
    ("recordatorios e infra", ["reminders", "infra-monitor"]),
    ("pendientes y containers", ["reminders", "infra-monitor"]),
    ("smart-home y agenda", ["smart-home-on", "calendar"]),
    ("prendé las luces y decime mis eventos", ["smart-home-on", "calendar"]),
    ("luces y recordatorios", ["smart-home-on", "reminders"]),
    ("encendé las luces y mis pendientes", ["smart-home-on", "reminders"]),
    ("self-evolve y red", ["self-evolve", "network-scanner"]),
    ("mejoras y scan de red", ["self-evolve", "network-scanner"]),
    ("tts y arr-monitor", ["tts", "arr-monitor"]),
    ("decime las descargas en audio", ["tts", "arr-monitor"]),
    ("cinema y descargas", ["smart-home-cinema", "arr-monitor"]),
    ("modo cine y cómo van los torrents", ["smart-home-cinema", "arr-monitor"]),
    ("apagá y decime el pronóstico", ["smart-home-off", "weather"]),
    ("smart-home-off y clima", ["smart-home-off", "weather"]),
    ("calendario y red", ["calendar", "network-scanner"]),
    ("agenda y dispositivos conectados", ["calendar", "network-scanner"]),
]

# ── Negative examples (no tools) ────────────────────────────────

NEGATIVES = [
    # Greetings
    "hola",
    "buen día",
    "buenos días",
    "buenas tardes",
    "buenas noches",
    "cómo estás",
    "cómo andás",
    "qué tal",
    "qué onda",
    "todo bien",
    "buen día, cómo andás",
    "hola Tenazas",
    "hola, qué tal",
    "buenas, cómo va",
    "ey",
    "qué hacés",
    "cómo va todo",
    # Farewells
    "chau",
    "nos vemos",
    "hasta mañana",
    "buenas noches, nos vemos",
    "me voy, chau",
    "hablamos después",
    # Acknowledgments
    "gracias",
    "genial",
    "perfecto",
    "ok",
    "dale",
    "joya",
    "ok dale",
    "de una",
    "buenísimo",
    "listo",
    "entendido",
    "sí",
    "no",
    "puede ser",
    "bien ahí",
    "piola",
    "muy bien",
    "ok entendido",
    "ya fue",
    "como quieras",
    "todo tranqui",
    "copado",
    "bárbaro",
    "excelente",
    # Chat / questions
    "contame un chiste",
    "quién sos",
    "qué podés hacer",
    "hablame de python",
    "qué es kubernetes",
    "explicame qué hace docker compose",
    "cuánto es 2+2",
    "cuánto gasté este mes",
    "postulé a una empresa",
    "qué es el modelo GPT",
    "explicame qué es un LLM",
    "cuánto es 15 por ciento de 200",
    "qué día es hoy",
    "qué hora es",
    "contame algo",
    "qué sabés hacer",
    "ayuda",
    "cuáles son tus skills",
    "hablame",
    "hablemos",
    "ayudame",
    "tengo un problema",
    "se me rompió algo",
    "contame algo interesante",
    "no sé",
    "fijate vos",
    "lo que quieras",
    "sorprendeme",
    "decime algo",
    "qué pasa",
    "vamos a ver",
    "necesito ayuda",
    "qué hacemos",
    "dale arrancamos",
    "aburrido",
    "estoy al pedo",
    "nada puntual",
    "podés hacer algo",
    "hablá",
    "respondeme",
    "estoy acá",
    "todo joya",
    "me fui a dormir",
    "ya está",
    "no importa",
    "dejá",
    "olvidate",
    "paso",
    "mirá",
    "escuchá",
    "pensé que",
    "no creo",
    "capaz que sí",
    "veremos",
    "no sé qué hacer",
    "estoy pensando",
    "después veo",
    "mañana sigo",
    "volví",
    "recién llego",
    "estoy laburando",
    "no molestes",
    "ahora no",
    "un momento",
    "esperá",
    "ya vuelvo",
    "dame un segundo",
    "cómo era eso",
    "repetí lo último",
    "explicame de nuevo",
    "no entendí",
    "otra vez",
    "seguí",
    "y después",
    "nada más",
    "eso era todo",
    "algo más",
    "listo por hoy",
    "terminamos",
    "gracias por todo",
    "buen laburo",
    "bien hecho",
    "me salvaste",
    "sos un genio",
    "muy útil",
    "todo claro",
]

# ── Hard negatives (confusing but NOT tool calls) ────────────────

HARD_NEGATIVES = [
    "me decis tus skills ?",
    "me decís qué hacés?",
    "qué hacés?",
    "podes hablar con otros modelos?",
    "hablás con otros modelos?",
    "podés contarme algo?",
    "decime quién sos",
    "quién sos?",
    "qué sos?",
    "qué podés hacer?",
    "qué sabés hacer",
    "cuáles son tus funciones?",
    "cuantas luces estan encendidas",
    "audita mi portafolio",
    "auditá mi portafolio",
    "auditá esto",
    "analizá esto",
    "revisá esto",
    "analiza mi portfolio",
    "revisá mi portfolio",
    "analizá mi portafolio",
    "portafolio",
    "contame un chiste",
    "sabés chistes?",
]


def _format_tools_xml(tools: list[str]) -> str:
    """Format tool list as XML string."""
    if not tools:
        return "<tools></tools>"
    inner = "".join(f"<tool>{t}</tool>" for t in tools)
    return f"<tools>{inner}</tools>"


def _make_entry(user_msg: str, tools: list[str]) -> dict:
    return {
        "messages": [
            {"role": "system", "content": SYSTEM_MSG},
            {"role": "user", "content": user_msg},
            {"role": "assistant", "content": _format_tools_xml(tools)},
        ]
    }


def generate_dataset() -> list[dict]:
    """Generate the full dataset as a list of dicts."""
    entries = []

    # Single-tool
    tool_lists = [
        (WEATHER, "weather"),
        (EMAIL, "email"),
        (CALENDAR, "calendar"),
        (INFRA_MONITOR, "infra-monitor"),
        (NETWORK_SCANNER, "network-scanner"),
        (ARR_MONITOR, "arr-monitor"),
        (SELF_EVOLVE, "self-evolve"),
        (REMINDERS, "reminders"),
        (SMART_HOME_ON, "smart-home-on"),
        (SMART_HOME_OFF, "smart-home-off"),
        (SMART_HOME_CINEMA, "smart-home-cinema"),
        (TTS, "tts"),
    ]
    for queries, tool in tool_lists:
        for q in queries:
            entries.append(_make_entry(q, [tool]))

    # Multi-tool
    for user_msg, tools in MULTI_TOOL:
        entries.append(_make_entry(user_msg, tools))

    # Negatives
    for q in NEGATIVES:
        entries.append(_make_entry(q, []))

    # Hard negatives
    for q in HARD_NEGATIVES:
        entries.append(_make_entry(q, []))

    return entries


def write_jsonl(entries: list[dict], output_path: str | None = None):
    """Write dataset as JSONL to file or stdout."""
    out = open(output_path, "w", encoding="utf-8") if output_path else sys.stdout
    for entry in entries:
        out.write(json.dumps(entry, ensure_ascii=False) + "\n")
    if output_path:
        out.close()


def count_tools(entries: list[dict]) -> dict:
    """Count tool mentions across all entries."""
    counts = {}
    negatives = 0
    for e in entries:
        assistant = e["messages"][2]["content"]
        tools = re.findall(r"<tool>([^<]+)</tool>", assistant)
        if not tools:
            negatives += 1
        for t in tools:
            counts[t] = counts.get(t, 0) + 1
    counts["(negatives)"] = negatives
    return counts


if __name__ == "__main__":
    if "--stats" in sys.argv:
        entries = generate_dataset()
        counts = count_tools(entries)
        print(f"{'Tool':<20s} {'Count':>5s}")
        print("-" * 27)
        for t in sorted(counts, key=counts.get, reverse=True):
            print(f"  {t:<20s} {counts[t]:>4d}")
        print(f"\nTotal rows: {len(entries)}")

        # Validate
        invalid = set(counts.keys()) - ALLOWED_TOOLS - {"(negatives)"}
        if invalid:
            print(f"\nINVALID TOOLS: {sorted(invalid)}")
            sys.exit(1)
        else:
            print(f"\nAll {len(counts) - 1} tools valid. 0 invalid.")
        sys.exit(0)

    output = None
    for i, arg in enumerate(sys.argv):
        if arg == "-o" and i + 1 < len(sys.argv):
            output = sys.argv[i + 1]

    entries = generate_dataset()
    random.seed(42)
    random.shuffle(entries)
    write_jsonl(entries, output)
    if output:
        print(f"Wrote {len(entries)} examples to {output}", file=sys.stderr)
