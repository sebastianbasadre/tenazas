#!/bin/bash
openclaw gateway &
OC_PID=$!

# Start Prometheus metrics server (persistent background process)
cd /home/tenazas && python3 -c "from runtime.metrics_exporter import start_server; start_server(); import time; time.sleep(999999)" &

sleep 20

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
