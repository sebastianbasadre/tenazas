#!/usr/bin/env python3
import json, subprocess, os, glob, sys
from datetime import datetime
from urllib.request import urlopen, Request

WORKSPACE = "/volume1/docker/openclaw/state/workspace"
MEMORY = f"{WORKSPACE}/memory/evolution-log.md"
now = datetime.now().strftime("%Y-%m-%d %H:%M")

# 1. Errors
errors = []
for log in glob.glob("/volume1/docker/openclaw/state/logs/*.log") + glob.glob("/tmp/openclaw/*.log"):
    try:
        with open(log) as f:
            for line in f:
                if any(w in line.lower() for w in ["error", "fail"]):
                    if not any(w in line.lower() for w in ["bonjour", "probing"]):
                        errors.append(line.strip()[:120])
    except:
        pass
errors = errors[-20:] if errors else ["Sin errores en los ultimos 7 dias."]

# 2. Skills
skills = []
skills_dir = f"{WORKSPACE}/skills"
for name in sorted(os.listdir(skills_dir)):
    path = os.path.join(skills_dir, name)
    if not os.path.isdir(path):
        continue
    has_skill = os.path.exists(os.path.join(path, "SKILL.md"))
    has_scripts = any(f.endswith(('.sh','.py')) for f in os.listdir(path))
    skills.append(f"- {name}: SKILL={'SI' if has_skill else 'NO'} Scripts={'SI' if has_scripts else 'NO'}")

no_skill = [s.split(':')[0].replace('- ','') for s in skills if 'SKILL=NO' in s]

# 3. Docker
try:
    r = subprocess.run(["sudo", "docker", "ps", "--format", "{{.Names}}"], capture_output=True, text=True, timeout=10)
    total = len(r.stdout.strip().split('\n'))
    r2 = subprocess.run(["sudo", "docker", "ps", "-a", "--format", "{{.Names}} {{.Status}}"], capture_output=True, text=True, timeout=10)
    downed = [l for l in r2.stdout.strip().split('\n') if 'Up' not in l and l.strip()]
    downed_str = ', '.join(downed) if downed else "Ninguno"
except:
    total = "?"
    downed_str = "Error"

# 4. Prompt
prompt = f"""Analiza este reporte semanal de Tenazas (asistente AI en Docker) y propone 1-3 mejoras concretas. Responde en espanol, maximo 200 palabras.

Errores: {'; '.join(errors[:5])}
Total skills: {len(skills)}
Skills sin SKILL.md: {', '.join(no_skill) if no_skill else 'Ninguno'}
Docker: {total} containers, Caidos: {downed_str}

Reglas: Solo mejoras accionables. Nunca auto-desplegar. Foco en confiabilidad."""

# 5. Call Ollama
payload = json.dumps({"model": "qwen2.5:3b", "prompt": prompt, "stream": False}).encode()
try:
    req = Request("http://127.0.0.1:11434/api/generate",
                  data=payload,
                  headers={"Content-Type": "application/json"})
    resp = urlopen(req, timeout=180)
    data = json.loads(resp.read().decode())
    analysis = data.get("response", "Ollama no respondio")
except Exception as e:
    analysis = f"Error Ollama: {e}"

# 6. Log
os.makedirs(os.path.dirname(MEMORY), exist_ok=True)
with open(MEMORY, "a") as f:
    f.write(f"\n## {now}\n{analysis}\n")

# 7. Send to Telegram
try:
    with open("/volume1/docker/openclaw/state/openclaw.json") as f:
        config = json.load(f)
    bot_token = config["channels"]["telegram"]["botToken"]
    msg = f"🧬 Self Evolution — {now} ART\n━━━━━━━━━━━━━\n{analysis}\n━━━━━━━━━━━━━\n📝 Esperando aprobacion de Sebas."
    tg_payload = json.dumps({"chat_id": "1565794551", "text": msg}).encode()
    tg_req = Request(f"https://api.telegram.org/bot{bot_token}/sendMessage",
                     data=tg_payload,
                     headers={"Content-Type": "application/json"})
    urlopen(tg_req, timeout=10)
except Exception as e:
    print(f"Telegram error: {e}")

# 8. Output
print(f"Self Evolution - {now} ART")
print("---")
print(analysis)
print("---")
