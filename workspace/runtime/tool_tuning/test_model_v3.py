#!/usr/bin/env python3
"""
test_model_v3.py — Validate tenazas-multitool model output.

Tests deterministic XML tool classification against expected outputs.

Usage:
    python test_model_v3.py [--host http://192.168.2.6:11434] [--model tenazas-multitool]
"""

import argparse
import json
import re
import sys
from urllib.request import urlopen, Request

ALLOWED_TOOLS = {
    "weather", "email", "calendar", "infra-monitor", "network-scanner",
    "arr-monitor", "self-evolve", "reminders", "smart-home-on",
    "smart-home-off", "smart-home-cinema", "tts",
}

# Test cases: (query, expected_tools_set, description)
TESTS = [
    # Single tool
    ("clima hoy", {"weather"}, "weather single"),
    ("qué hay en mi agenda", {"calendar"}, "calendar single"),
    ("leeme los mails", {"email"}, "email single"),
    ("prendé las luces", {"smart-home-on"}, "smart-home-on"),
    ("apagá las luces", {"smart-home-off"}, "smart-home-off"),
    ("poné modo cine", {"smart-home-cinema"}, "smart-home-cinema"),
    ("escaneá la red", {"network-scanner"}, "network-scanner"),
    ("estado de los containers", {"infra-monitor"}, "infra-monitor"),
    ("cómo están las descargas", {"arr-monitor"}, "arr-monitor"),
    ("tus propuestas de mejora", {"self-evolve"}, "self-evolve"),
    ("recordame comprar leche", {"reminders"}, "reminders"),
    ("decí hola en voz alta", {"tts"}, "tts"),

    # Multi-tool
    ("clima y agenda", {"weather", "calendar"}, "weather+calendar"),
    ("prendé las luces y decime el clima", {"smart-home-on", "weather"}, "lights+weather"),
    ("estado de infra y descargas", {"infra-monitor", "arr-monitor"}, "infra+arr"),
    ("leeme los mails y la agenda", {"email", "calendar"}, "email+calendar"),

    # None (should return empty tools)
    ("buenos días", set(), "greeting → none"),
    ("gracias", set(), "thanks → none"),
    ("contame un chiste", set(), "chat → none"),
    ("quién sos", set(), "identity → none"),
]


def query_ollama(host: str, model: str, prompt: str) -> str:
    """Query Ollama and return raw response text."""
    payload = json.dumps({
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0, "top_k": 1, "top_p": 0},
    }).encode()
    req = Request(f"{host}/api/generate", data=payload,
                  headers={"Content-Type": "application/json"})
    resp = urlopen(req, timeout=30)
    data = json.loads(resp.read())
    return data.get("response", "").strip()


def parse_tools(text: str) -> set[str]:
    """Extract tool names from XML response."""
    return set(re.findall(r"<tool>([^<]+)</tool>", text))


def validate_xml(text: str) -> list[str]:
    """Check response is valid strict XML."""
    errors = []
    text = text.strip()

    # Must start with <tools>
    if not text.startswith("<tools>"):
        errors.append(f"doesn't start with <tools>: {text[:50]!r}")

    # Check for text outside XML
    cleaned = re.sub(r"</?tools>|<tool>[^<]*</tool>", "", text).strip()
    if cleaned:
        errors.append(f"text outside XML: {cleaned[:50]!r}")

    # Check for markdown
    if "```" in text or "**" in text:
        errors.append("contains markdown")

    # Check tool names
    tools = parse_tools(text)
    invalid = tools - ALLOWED_TOOLS
    if invalid:
        errors.append(f"invalid tools: {sorted(invalid)}")

    return errors


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--host", default="http://192.168.2.6:11434")
    p.add_argument("--model", default="tenazas-multitool")
    p.add_argument("--verbose", "-v", action="store_true")
    args = p.parse_args()

    print(f"[test] Model: {args.model} @ {args.host}")
    print(f"[test] Running {len(TESTS)} tests...\n")

    passed = 0
    failed = 0
    xml_errors = 0

    for query, expected, desc in TESTS:
        try:
            raw = query_ollama(args.host, args.model, query)
        except Exception as e:
            print(f"  FAIL  {desc}: connection error: {e}")
            failed += 1
            continue

        # Validate XML structure
        xml_errs = validate_xml(raw)
        if xml_errs:
            xml_errors += 1
            print(f"  XML   {desc}: {xml_errs[0]}")
            if args.verbose:
                print(f"        raw: {raw!r}")
            failed += 1
            continue

        # Check tool match
        got = parse_tools(raw)
        if got == expected:
            passed += 1
            if args.verbose:
                print(f"  OK    {desc}: {sorted(got) if got else '(none)'}")
        else:
            failed += 1
            print(f"  FAIL  {desc}")
            print(f"        expected: {sorted(expected) if expected else '(none)'}")
            print(f"        got:      {sorted(got) if got else '(none)'}")
            if args.verbose:
                print(f"        raw: {raw!r}")

    # Summary
    total = passed + failed
    pct = (passed / total * 100) if total else 0
    print(f"\n[test] Results: {passed}/{total} passed ({pct:.0f}%)")
    if xml_errors:
        print(f"[test] XML errors: {xml_errors}")
    if failed:
        print(f"[test] FAILED: {failed} tests")
        sys.exit(1)
    else:
        print("[test] ALL PASSED")


if __name__ == "__main__":
    main()
