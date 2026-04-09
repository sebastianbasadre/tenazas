#!/usr/bin/env python3
"""
build_multitool_dataset.py — Generate SFT dataset for multitool finetuning.

Generates JSONL with single-tool, multi-tool, no-tool, and ambiguous examples.
All in Spanish rioplatense, matching Tenazas' voice.

Usage:
    python3 build_multitool_dataset.py                    # stdout
    python3 build_multitool_dataset.py -o dataset.jsonl   # file
    python3 build_multitool_dataset.py --stats            # show counts
"""

import json
import random
import sys
from pathlib import Path

SYSTEM_MSG = "Respond only with valid XML tool calls."

# ── Examples ──────────────────────────────────────────────────────

SINGLE_TOOL = [
    # weather (15)
    ("cómo está el clima", "weather"),
    ("qué temperatura hace", "weather"),
    ("va a llover hoy", "weather"),
    ("hace frío afuera", "weather"),
    ("clima en buenos aires", "weather"),
    ("cómo está el tiempo", "weather"),
    ("pronóstico de hoy", "weather"),
    ("hace calor hoy", "weather"),
    ("decime el clima", "weather"),
    ("qué onda el clima", "weather"),
    ("está lindo afuera", "weather"),
    ("sale para salir o llueve", "weather"),
    ("necesito abrigo hoy", "weather"),
    ("cómo va a estar el tiempo mañana", "weather"),
    ("va a estar lindo", "weather"),

    # email (10)
    ("revisá mis mails", "email"),
    ("tengo correos nuevos", "email"),
    ("mostrame los emails", "email"),
    ("chequeá el inbox", "email"),
    ("cuántos mails tengo", "email"),
    ("hay emails sin leer", "email"),
    ("leeme los correos", "email"),
    ("emails recientes", "email"),
    ("fijate si tengo mails", "email"),
    ("mis correos", "email"),

    # calendar (10)
    ("qué tengo hoy en la agenda", "calendar"),
    ("mis eventos de hoy", "calendar"),
    ("qué reuniones tengo", "calendar"),
    ("agenda de mañana", "calendar"),
    ("tengo algo hoy", "calendar"),
    ("próximos eventos", "calendar"),
    ("mi calendario", "calendar"),
    ("qué hay en la agenda", "calendar"),
    ("tengo reuniones hoy", "calendar"),
    ("eventos de esta semana", "calendar"),

    # infra-monitor (8)
    ("estado de los containers", "infra-monitor"),
    ("cómo andan los containers", "infra-monitor"),
    ("docker status", "infra-monitor"),
    ("están todos los containers andando", "infra-monitor"),
    ("cuántos containers corren", "infra-monitor"),
    ("estado del docker", "infra-monitor"),
    ("cómo está la infra", "infra-monitor"),
    ("qué containers están corriendo", "infra-monitor"),

    # network-scanner (6)
    ("escaneá la red", "network-scanner"),
    ("cuántos dispositivos hay en la red", "network-scanner"),
    ("quién está conectado", "network-scanner"),
    ("scan de red", "network-scanner"),
    ("dispositivos en la red", "network-scanner"),
    ("nmap", "network-scanner"),

    # arr-monitor (6)
    ("cómo está el arr stack", "arr-monitor"),
    ("estado de sonarr", "arr-monitor"),
    ("andan las descargas", "arr-monitor"),
    ("estado del media stack", "arr-monitor"),
    ("cómo anda radarr", "arr-monitor"),
    ("estado de los torrents", "arr-monitor"),

    # self-evolve (5)
    ("propuestas de mejora", "self-evolve"),
    ("hay propuestas pendientes", "self-evolve"),
    ("self evolve", "self-evolve"),
    ("qué mejoras hay", "self-evolve"),
    ("propuestas del sistema", "self-evolve"),

    # reminders (5)
    ("mis recordatorios", "reminders"),
    ("qué recordatorios tengo", "reminders"),
    ("recordatorios activos", "reminders"),
    ("hay recordatorios pendientes", "reminders"),
    ("mostrame los reminders", "reminders"),

    # smart-home variants (10)
    ("prendé las luces", "smart-home-on"),
    ("apagá las luces", "smart-home-off"),
    ("apagá todo", "smart-home-off"),
    ("encendé las luces", "smart-home-on"),
    ("poné modo cine", "smart-home-cinema"),
    ("modo cinema", "smart-home-cinema"),
    ("poné modo relax", "smart-home-relax"),
    ("modo trabajo", "smart-home-work"),
    ("poneme las luces para laburar", "smart-home-work"),
    ("luces de relax", "smart-home-relax"),

    # tts (5)
    ("mandame un audio diciendo hola", "tts"),
    ("decí buen día en voz alta", "tts"),
    ("mandá audio de prueba", "tts"),
    ("hablá y decí test", "tts"),
    ("text to speech hola mundo", "tts"),
]

MULTI_TOOL = [
    # 2 tools (60)
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

    ("fijate mis mails y el calendario", ["email", "calendar"]),
    ("correos y agenda de hoy", ["email", "calendar"]),
    ("emails y eventos", ["email", "calendar"]),
    ("chequeá mails y reuniones", ["email", "calendar"]),
    ("tengo mails y qué hay en la agenda", ["email", "calendar"]),
    ("revisá correos y calendario", ["email", "calendar"]),
    ("inbox y agenda", ["email", "calendar"]),
    ("mails nuevos y eventos de hoy", ["email", "calendar"]),

    ("prendé las luces y decime el clima", ["smart-home-on", "weather"]),
    ("encendé las luces y fijate el tiempo", ["smart-home-on", "weather"]),
    ("luces y clima", ["smart-home-on", "weather"]),
    ("poneme las luces y decime la temperatura", ["smart-home-on", "weather"]),

    ("escaneá la red y decime el estado del sistema", ["network-scanner", "infra-monitor"]),
    ("nmap y docker status", ["network-scanner", "infra-monitor"]),
    ("dispositivos y containers", ["network-scanner", "infra-monitor"]),
    ("scan de red y estado de infra", ["network-scanner", "infra-monitor"]),
    ("red y containers", ["network-scanner", "infra-monitor"]),

    ("poné modo cine y decime el clima", ["smart-home-cinema", "weather"]),
    ("modo cinema y temperatura", ["smart-home-cinema", "weather"]),
    ("luces de cine y pronóstico", ["smart-home-cinema", "weather"]),

    ("mandame audio y fijate el clima", ["tts", "weather"]),
    ("audio de prueba y agenda de hoy", ["tts", "calendar"]),

    ("clima y estado de los containers", ["weather", "infra-monitor"]),
    ("temperatura y docker status", ["weather", "infra-monitor"]),
    ("cómo está el clima y la infra", ["weather", "infra-monitor"]),

    ("mails y clima", ["email", "weather"]),
    ("correos y temperatura", ["email", "weather"]),
    ("chequeá mails y cómo está el tiempo", ["email", "weather"]),
    ("emails y pronóstico", ["email", "weather"]),

    ("propuestas de mejora y estado de containers", ["self-evolve", "infra-monitor"]),
    ("self evolve y docker status", ["self-evolve", "infra-monitor"]),

    ("recordatorios y agenda", ["reminders", "calendar"]),
    ("reminders y eventos de hoy", ["reminders", "calendar"]),
    ("mis recordatorios y reuniones", ["reminders", "calendar"]),

    ("apagá las luces y decime la agenda", ["smart-home-off", "calendar"]),
    ("modo relax y fijate el clima", ["smart-home-relax", "weather"]),
    ("luces de trabajo y mails", ["smart-home-work", "email"]),

    ("estado del arr stack y containers", ["arr-monitor", "infra-monitor"]),
    ("sonarr y docker status", ["arr-monitor", "infra-monitor"]),
    ("descargas y containers", ["arr-monitor", "infra-monitor"]),

    ("escaneá la red y fijate el clima", ["network-scanner", "weather"]),
    ("dispositivos y temperatura", ["network-scanner", "weather"]),

    ("emails y recordatorios", ["email", "reminders"]),
    ("correos y reminders", ["email", "reminders"]),

    ("agenda y propuestas", ["calendar", "self-evolve"]),
    ("eventos y mejoras pendientes", ["calendar", "self-evolve"]),

    ("mails y estado del arr", ["email", "arr-monitor"]),
    ("correos y descargas", ["email", "arr-monitor"]),

    ("clima y descargas", ["weather", "arr-monitor"]),
    ("temperatura y estado del torrent", ["weather", "arr-monitor"]),

    ("prendé las luces y poné modo cine no mejor relax", ["smart-home-relax"]),

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

    ("containers, descargas y red", ["infra-monitor", "arr-monitor", "network-scanner"]),
    ("docker, arr stack y dispositivos", ["infra-monitor", "arr-monitor", "network-scanner"]),

    ("clima, mails y containers", ["weather", "email", "infra-monitor"]),
    ("agenda, clima y propuestas", ["calendar", "weather", "self-evolve"]),
    ("emails, agenda y recordatorios", ["email", "calendar", "reminders"]),
]

NO_TOOL = [
    ("buen día", []),
    ("hola", []),
    ("cómo andás", []),
    ("gracias", []),
    ("ok dale", []),
    ("buenas tardes", []),
    ("joya", []),
    ("genial", []),
    ("chau", []),
    ("nos vemos", []),
    ("todo bien", []),
    ("qué es kubernetes", []),
    ("explicame qué hace docker compose", []),
    ("cuánto es 2+2", []),
    ("contame un chiste", []),
    ("quién sos", []),
    ("qué podés hacer", []),
    ("hablame de python", []),
    ("cuánto gasté este mes", []),
    ("postulé a una empresa", []),
]

AMBIGUOUS_CONSERVATIVE = [
    # Ambiguous queries resolved conservatively (no tools)
    ("buen día, cómo andás", []),
    ("qué onda", []),
    ("todo tranqui", []),
    ("dale arrancamos", []),
    ("qué hacemos", []),
    ("necesito ayuda", []),
    ("tengo un problema", []),
    ("se me rompió algo", []),
    ("podés hacer algo", []),
    ("qué sabés hacer", []),
    ("estoy al pedo", []),
    ("aburrido", []),
    ("vamos a ver", []),
    ("qué pasa", []),
    ("decime algo", []),
    ("hablemos", []),
    ("nada puntual", []),
    ("fijate vos", []),
    ("lo que quieras", []),
    ("sorprendeme", []),
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

    for user_msg, tool in SINGLE_TOOL:
        entries.append(_make_entry(user_msg, [tool]))

    for user_msg, tools in MULTI_TOOL:
        entries.append(_make_entry(user_msg, tools))

    for user_msg, tools in NO_TOOL:
        entries.append(_make_entry(user_msg, tools))

    for user_msg, tools in AMBIGUOUS_CONSERVATIVE:
        entries.append(_make_entry(user_msg, tools))

    return entries


def write_jsonl(entries: list[dict], output_path: str | None = None):
    """Write dataset as JSONL to file or stdout."""
    out = open(output_path, "w") if output_path else sys.stdout
    for entry in entries:
        out.write(json.dumps(entry, ensure_ascii=False) + "\n")
    if output_path:
        out.close()


if __name__ == "__main__":
    if "--stats" in sys.argv:
        print(f"Single-tool: {len(SINGLE_TOOL)}")
        print(f"Multi-tool:  {len(MULTI_TOOL)}")
        print(f"No-tool:     {len(NO_TOOL)}")
        print(f"Ambiguous:   {len(AMBIGUOUS_CONSERVATIVE)}")
        print(f"Total:       {len(SINGLE_TOOL) + len(MULTI_TOOL) + len(NO_TOOL) + len(AMBIGUOUS_CONSERVATIVE)}")
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
