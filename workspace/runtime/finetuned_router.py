import re, json, logging, requests
from typing import Optional
logger = logging.getLogger(__name__)
FINETUNED_MODEL = "tenazas-qwen25-7b-v6"
OLLAMA_URL = "http://192.168.2.6:11434/api/generate"
TIMEOUT_SECS = 15
TEMPERATURE = 0.1
SYSTEM_PROMPT = """Sos un clasificador de intenciones para un asistente domotico personal. Dado un mensaje en espanol, responde UNICAMENTE con un JSON: {"intent": "...", "args": {...}}. Intents validos: lights_on, lights_off, cinema_mode, weather, media_search, infra_check, chat. Responde SOLO el JSON, sin texto extra, sin backticks."""
KNOWN_INTENTS = {
    "lights_on","lights_off","cinema_mode","weather","media_search","infra_check","chat",
    "smart-home-on","smart-home-off","smart-home-cinema","email","calendar",
    "arr-monitor","infra-monitor","self-evolve","tts","reminders",
}
# Map v4 tool names → intent names for normalizer compatibility
_TOOL_TO_INTENT = {
    "smart-home-on": "lights_on", "smart-home-off": "lights_off",
    "smart-home-cinema": "cinema_mode", "arr-monitor": "infra_check",
    "infra-monitor": "infra_check",
}
KEYWORD_RULES = [
    (["cinema","pelicula","movie","cine"],"cinema_mode"),
    (["apag","turn off","luces off"],"lights_off"),
    (["luz","luces","light","encend","prender"],"lights_on"),
    (["clima","lluvia","temperatura","weather"],"weather"),
    (["container","docker","infra","caido"],"infra_check"),
    (["busca","buscar","serie","episodio"],"media_search"),
]
def _keyword_intent(text):
    text = text.lower()
    for keywords, intent in KEYWORD_RULES:
        for k in keywords:
            if k in text: return intent
    return None
def _parse_tolerant(raw):
    raw = raw.strip()
    try:
        parsed = json.loads(raw)
        # Accept both "intent" and "tool" keys (v4 uses "tool")
        if "tool" in parsed:
            tool = parsed["tool"]
            parsed["intent"] = _TOOL_TO_INTENT.get(tool, tool)
            parsed.setdefault("args", {})
            return {**parsed, "_parsed_by":"model_json"}
        if "intent" in parsed:
            return {**parsed, "_parsed_by":"model_json"}
    except: pass
    match = re.search(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)?\}", raw, re.DOTALL)
    if match:
        try:
            parsed = json.loads(match.group(0))
            if "tool" in parsed:
                tool = parsed["tool"]
                parsed["intent"] = _TOOL_TO_INTENT.get(tool, tool)
                parsed.setdefault("args", {})
                return {**parsed, "_parsed_by":"model_json_extracted"}
            if "intent" in parsed:
                return {**parsed, "_parsed_by":"model_json_extracted"}
        except: pass
    intent = _keyword_intent(raw)
    if intent: return {"intent":intent,"args":{},"_parsed_by":"keyword_fallback"}
    return {"intent":None,"args":{},"_parsed_by":"no_match"}
def route(message):
    raw_output = ""
    try:
        resp = requests.post(OLLAMA_URL, json={"model":FINETUNED_MODEL,"stream":False,"prompt":message,"options":{"temperature":TEMPERATURE,"num_predict":128}}, timeout=TIMEOUT_SECS)
        resp.raise_for_status()
        raw_output = resp.json().get("response","")
        result = _parse_tolerant(raw_output)
    except requests.exceptions.Timeout:
        result = {"intent":_keyword_intent(message),"args":{},"_parsed_by":"ollama_error","_error":"timeout"}
    except Exception as e:
        result = {"intent":_keyword_intent(message),"args":{},"_parsed_by":"ollama_error","_error":str(e)}
    if result.get("intent") and result["intent"] not in KNOWN_INTENTS:
        result["intent"] = _keyword_intent(message)
        result["_parsed_by"] = "keyword_fallback"
    result["_raw"] = raw_output
    result.setdefault("args",{})
    result.setdefault("_error",None)
    return result
if __name__ == "__main__":
    import sys
    msgs = sys.argv[1:] or ["prende las luces del living","modo cine","clima de hoy","hola"]
    for msg in msgs:
        r = route(msg)
        print(f"[{r['_parsed_by']:25s}] {msg!r:40s} -> {r['intent']} {r['args']}")
