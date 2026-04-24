#!/usr/bin/env python3
"""Tenazas multi-source learning - auto-discovers models."""
import json, os, glob, re
from datetime import datetime
from urllib.request import urlopen, Request

WORKSPACE = "/volume1/docker/openclaw/state/workspace"
MEMORY = WORKSPACE + "/memory/learnings.md"
LOGS_DIR = "/volume1/docker/openclaw/workspace/runtime/logs"
OLLAMA_URL = "http://192.168.2.6:11434"

now = datetime.now().strftime("%Y-%m-%d %H:%M")

GOOGLE_API_KEY = ""
BOT_TOKEN = ""
TELEGRAM_CHAT_ID = ""
try:
    with open("/volume1/docker/openclaw/docker-compose.yml") as f:
        for line in f:
            m = re.search(r"GOOGLE_API_KEY=(.+)", line)
            if m: GOOGLE_API_KEY = m.group(1).strip()
    with open("/volume1/docker/openclaw/state/openclaw.json") as f:
        _cfg = json.load(f)
        BOT_TOKEN = _cfg["channels"]["telegram"]["botToken"]
        TELEGRAM_CHAT_ID = str(_cfg["channels"]["telegram"].get("allowFrom", [""])[0])
except: pass

def discover_models():
    try:
        resp = urlopen(OLLAMA_URL + "/api/tags", timeout=5)
        data = json.loads(resp.read().decode())
        return [{"name": m["name"], "size": m["details"]["parameter_size"]} for m in data.get("models", [])]
    except:
        return []

def ask_ollama(prompt, model="qwen2.5:3b"):
    try:
        payload = json.dumps({"model": model, "prompt": prompt, "stream": False}).encode()
        req = Request(OLLAMA_URL + "/api/generate", data=payload, headers={"Content-Type": "application/json"})
        resp = urlopen(req, timeout=180)
        return json.loads(resp.read().decode()).get("response", "")
    except Exception as e:
        return "[Error %s]: %s" % (model, e)

def ask_gemini(prompt):
    if not GOOGLE_API_KEY: return "[Sin API key]"
    try:
        url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key=" + GOOGLE_API_KEY
        payload = json.dumps({"contents": [{"parts": [{"text": prompt}]}]}).encode()
        req = Request(url, data=payload, headers={"Content-Type": "application/json"})
        resp = urlopen(req, timeout=30)
        return json.loads(resp.read().decode())["candidates"][0]["content"]["parts"][0]["text"]
    except Exception as e:
        return "[Error Gemini]: %s" % e

def send_telegram(msg):
    if not BOT_TOKEN: return
    try:
        tg = json.dumps({"chat_id": TELEGRAM_CHAT_ID, "text": msg[:4000]}).encode()
        urlopen(Request("https://api.telegram.org/bot%s/sendMessage" % BOT_TOKEN,
                data=tg, headers={"Content-Type": "application/json"}), timeout=10)
    except: pass

# 1. Discover models
models = discover_models()
model_names = [m["name"] for m in models]
model_info = ", ".join(["%s (%s)" % (m["name"], m["size"]) for m in models])
print("=== Sesion de Aprendizaje %s ===" % now)
print("Modelos disponibles: %s" % model_info)

# Sort by size
size_order = {"1B": 1, "1.5B": 2, "3B": 3, "3.1B": 3, "4B": 4, "7B": 7, "7.6B": 7}
def model_size(m):
    return size_order.get(m["size"].upper(), 99)
models_sorted = sorted(models, key=model_size)
fast = models_sorted[0]["name"] if models_sorted else "qwen2.5:3b"
others = [m["name"] for m in models_sorted[1:]]

# 2. Collect data
conversations = []
errors = []
tools_used = []
for log in sorted(glob.glob(LOGS_DIR + "/*.log"))[-5:]:
    try:
        with open(log) as f:
            for line in f:
                low = line.lower()
                if ("message" in low and "telegram" in low) or \
                   ('"action": "incoming"' in low) or \
                   ('"text":' in low and '"chat_id"' in low):
                    conversations.append(line.strip()[:200])
                if "error" in low or "fail" in low:
                    if "bonjour" not in low and "probing" not in low:
                        errors.append(line.strip()[:150])
                if "tool" in low or "exec" in low or "command" in low:
                    tools_used.append(line.strip()[:150])
    except: pass

existing = ""
try:
    with open(MEMORY) as f:
        existing = f.read()[-1500:]
except: pass

skills = [n for n in sorted(os.listdir(WORKSPACE + "/skills"))
          if os.path.isdir(os.path.join(WORKSPACE + "/skills", n))]

NL = chr(10)
context = "Tenazas (asistente AI, Docker, Synology NAS):\n"
context += "Mensajes: %d, Errores: %d, Tools: %d\n" % (len(conversations), len(errors), len(tools_used))
context += "Skills: %s\n" % ", ".join(skills)
context += "Modelos disponibles: %s + Gemini Flash\n" % ", ".join(model_names)
context += "Ultimos mensajes: %s\n" % NL.join(conversations[-10:])
context += "Errores: %s\n" % NL.join(errors[-5:])
context += "Previos: %s" % existing[-300:]

print("Datos: %d msgs, %d errores, %d tools\n" % (len(conversations), len(errors), len(tools_used)))

# 3. Fast model analiza
print("[1] %s — analisis de conversaciones..." % fast)
r_fast = ask_ollama(context + "\nAnaliza las conversaciones. Que pidio Sebas, que fallo, que aprendiste de el. 100 palabras, espanol, directo.", fast)
print("-> %s\n" % r_fast[:250])

# 4. Decide que modelos consultar
others_desc = ", ".join(["%s (%s)" % (m["name"], m["size"]) for m in models_sorted[1:]])
decision = ask_ollama("Basandote en: %s\nModelos locales extra: %s\nGemini Flash disponible (externo)\nElegi cuales consultar. Responde SOLO los nombres separados por coma, o NINGUNO." % (r_fast[:300], others_desc), fast).strip()
print("[2] Decision: %s\n" % decision)

# 5. Consultar modelos elegidos
responses = {fast: r_fast}
sources = fast

for mn in model_names:
    if mn != fast and mn.lower() in decision.lower():
        print("[+] %s — consultando..." % mn)
        r = ask_ollama("%s\nSkills: %s\nPropone 1 mejora concreta. 80 palabras, espanol." % (r_fast[:300], ", ".join(skills)), mn)
        responses[mn] = r
        sources += " + %s" % mn
        print("-> %s\n" % r[:200])

if "gemini" in decision.lower():
    print("[+] Gemini Flash — contexto externo...")
    r_gemini = ask_gemini("Contexto: %s\n1 recomendacion de integracion moderna para un asistente AI en Docker. 80 palabras, espanol." % r_fast[:300])
    responses["Gemini"] = r_gemini
    sources += " + Gemini"
    print("-> %s\n" % r_gemini[:200])

# Si no eligio nada extra, usar al menos uno mas
if len(responses) == 1 and others:
    extra = others[0]
    print("[+] %s — perspectiva adicional..." % extra)
    r = ask_ollama(context + "\nQue mejora concreta le harias a este setup? 80 palabras, espanol.", extra)
    responses[extra] = r
    sources += " + %s" % extra
    print("-> %s\n" % r[:200])

# 6. Sintesis
all_resp = NL.join(["%s: %s" % (k, v[:200]) for k, v in responses.items()])
synthesis = ask_ollama("Sintesis de aprendizaje. Fuentes:\n%s\n\nReporte final, 200 palabras max, espanol:\n1. Que aprendi de las conversaciones con Sebas\n2. Que mejora propongo\n3. Que modelo aporto que\nDirecto." % all_resp, fast)

# 7. Save
os.makedirs(os.path.dirname(MEMORY), exist_ok=True)
with open(MEMORY, "a") as f:
    f.write("\n## %s | Fuentes: %s\n%s\n" % (now, sources, synthesis))

# 8. Telegram
msg = "🧠 Aprendizaje — %s ART\nModelos: %s\n━━━━━━━━━━━━━\n%s\n━━━━━━━━━━━━━" % (now, sources, synthesis[:3500])
send_telegram(msg)

print("\n=== Resultado (%s) ===\n%s" % (sources, synthesis))
