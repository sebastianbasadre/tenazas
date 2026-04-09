#!/bin/bash
openclaw gateway &
OC_PID=$!

# Start Prometheus metrics server (polls metrics_state.json, serves :9105)
cd /home/tenazas && python3 /home/tenazas/runtime/metrics_server.py &

# Start model failover monitor (watches for 429, switches to Ollama, pre-loads VRAM)
cd /home/tenazas && python3 /home/tenazas/runtime/model_failover.py &

# Start API call counter exporter (:9106/metrics — Gemini + IOL usage tracking)
python3 -c "from runtime.api_call_counter import start_exporter; start_exporter(); import time; [time.sleep(3600) for _ in iter(int, 1)]" &
echo "API metrics exporter iniciado en :9106"

# Start Telegram deterministic router (intercepts skills before LLM)
cd /home/tenazas && python3 /home/tenazas/runtime/telegram_router.py &
echo "Telegram router iniciado (deterministic skill interception)"

sleep 20

# Verify Node version
NODE_VER=$(node --version)
echo "[startup] Node version: $NODE_VER"
if [[ "$NODE_VER" < "v24" ]]; then
  echo "[startup] WARN: Node < 24 detectado. OpenClaw 2026.4.x recomienda Node 24."
fi

LOCKFILE="/tmp/tenazas-startup.lock"
if [ ! -f "$LOCKFILE" ] || [ $(($(date +%s) - $(cat "$LOCKFILE"))) -gt 300 ]; then
  date +%s > "$LOCKFILE"

  BOT_TOKEN=$(python3 -c "import json; c=json.load(open('/home/tenazas/.openclaw/openclaw.json')); print(c['channels']['telegram']['botToken'])")
  CHAT_ID="1565794551"
  TIMESTAMP=$(TZ=America/Argentina/Buenos_Aires date +"%H:%M")

  curl -sf "https://api.telegram.org/bot${BOT_TOKEN}/sendMessage" \
    -d chat_id="${CHAT_ID}" \
    -d text="🦀 Tenazas Online — ${TIMESTAMP} ART" > /dev/null 2>&1
fi

wait $OC_PID
