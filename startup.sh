#!/bin/bash
set -u

run_bg() {
  local label="$1"
  shift
  "$@" &
  echo "$label"
}

run_bg_cd() {
  local label="$1"
  local workdir="$2"
  shift 2
  (
    cd "$workdir" || exit 1
    "$@"
  ) &
  echo "$label"
}

resolve_chat_id() {
  python3 -c "import json; c=json.load(open('/home/tenazas/.openclaw/openclaw.json')); allowed=c['channels']['telegram'].get('allowFrom', []); print(allowed[0] if allowed else '')"
}

openclaw gateway &
OC_PID=$!

# Kill zombie MCP server instances from prior runs
pkill -f "iol_broker/mcp_server.py" 2>/dev/null || true
sleep 1

# Initialize git repo for proposal_executor sandbox (idempotent)
if [ ! -d /home/tenazas/.git ]; then
  cd /home/tenazas
  git init --initial-branch main 2>/dev/null
  git config user.email "tenazas@local"
  git config user.name "tenazas"

  # Ignore everything except runtime source and tests
  cat > .gitignore << 'GITEOF'
# Ignore everything by default
*

# Allow runtime source
!runtime/
runtime/*
!runtime/*.py
!runtime/proposal_executors/
runtime/proposal_executors/*
!runtime/proposal_executors/*.py
!runtime/devices/
runtime/devices/*
!runtime/devices/*.py
!runtime/tool_tuning/
runtime/tool_tuning/*
!runtime/tool_tuning/*.py

# Allow tests
!tests/
tests/*
!tests/*.py
!tests/simulation/
tests/simulation/*
!tests/simulation/**/*.py

# Allow root scripts
!.gitignore
!startup.sh
!auto-heal.sh
!ollama-check.sh

# Always ignore state/logs/data
runtime/logs/
runtime/state/
runtime/smoking/
runtime/learning/
runtime/__pycache__/
runtime/proposal_executors/__pycache__/
runtime/devices/__pycache__/
runtime/tool_tuning/__pycache__/
tests/__pycache__/
state/
logs/
learning/
*.json
*.jsonl
*.sqlite
GITEOF

  git add -A 2>/dev/null
  git commit -m "init: baseline for proposal_executor sandbox" 2>/dev/null
  echo "[startup] Git repo initialized for proposal_executor"
fi

# Start Prometheus metrics server (polls metrics_state.json, serves :9105)
run_bg_cd "Metrics server iniciado en :9105" "/home/tenazas" python3 /home/tenazas/runtime/metrics_server.py

# Start model failover monitor (watches for 429, switches to Ollama, pre-loads VRAM)
run_bg_cd "Model failover monitor iniciado" "/home/tenazas" python3 /home/tenazas/runtime/model_failover.py

# Start API call counter exporter (:9106/metrics — Gemini + IOL usage tracking)
run_bg "API metrics exporter iniciado en :9106" python3 -c "from runtime.api_call_counter import start_exporter; start_exporter(); import time; [time.sleep(3600) for _ in iter(int, 1)]"

# Start Telegram deterministic router (with auto-restart watchdog)
(
  FAILS=0
  while true; do
    cd /home/tenazas && python3 /home/tenazas/runtime/telegram_router.py
    EXIT_CODE=$?
    FAILS=$((FAILS + 1))
    TS=$(TZ=America/Argentina/Buenos_Aires date +"%H:%M:%S")
    echo "[watchdog] telegram_router exited (code=$EXIT_CODE, restarts=$FAILS) at $TS" >> /tmp/telegram_watchdog.log

    # Backoff: 5s first restart, 30s after 3, 60s after 5
    if [ $FAILS -ge 5 ]; then
      sleep 60
    elif [ $FAILS -ge 3 ]; then
      sleep 30
    else
      sleep 5
    fi

    # Reset fail counter if it's been running for a while (>10 min since last restart)
    # The counter resets naturally on container restart via startup.sh
  done
) &
echo "Telegram router iniciado (with watchdog auto-restart)"

# Start portfolio exporter (:9101/metrics — Binance portfolio-level: total, drawdown, allocation, liquidity)
run_bg_cd "Portfolio exporter iniciado en :9101" "/home/tenazas" node /home/tenazas/runtime/portfolio-exporter.mjs

# Start network exporter (:9108/metrics — device inventory + Deco WiFi)
run_bg_cd "Network exporter iniciado en :9108" "/home/tenazas" python3 /home/tenazas/runtime/network_exporter.py

# Start finance exporter (:9109/metrics — IOL + Binance portfolio history)
run_bg_cd "Finance exporter iniciado en :9109" "/home/tenazas" python3 /home/tenazas/runtime/finance_exporter.py

# Start portfolio reconciler (validates exporter vs live API every 10 min)
(sleep 120 && cd /home/tenazas && python3 /home/tenazas/runtime/portfolio_reconciler.py --watch) &
echo "Portfolio reconciler iniciado (watch mode, cada 10 min)"

# Start multitool eval exporter (:9107/metrics — F1, precision, recall, per-tool)
if ! pgrep -f "eval_exporter.py" >/dev/null 2>&1; then
  cd /home/tenazas && nohup python3 /home/tenazas/runtime/tool_tuning/eval_exporter.py \
    >/tmp/eval_exporter.log 2>&1 &
  echo "Eval exporter iniciado en :9107"
fi

sleep 20

# IOL token refresh at startup
python3 /home/tenazas/tools/iol_refresh.py --force 2>&1 || echo "[startup] IOL refresh failed (non-critical)"
echo "[startup] IOL token initialized"

# IOL token auto-refresh cada 15 minutos (token dura 20 min)
(while true; do
  sleep 900
  python3 /home/tenazas/tools/iol_refresh.py >> /tmp/iol_refresh.log 2>&1
done) &
echo "[startup] IOL auto-refresh loop iniciado (cada 15 min)"

# Verify Node version
NODE_VER=$(node --version)
echo "[startup] Node version: $NODE_VER"
NODE_MAJOR=$(printf '%s\n' "${NODE_VER#v}" | cut -d. -f1)
if ! [[ "$NODE_MAJOR" =~ ^[0-9]+$ ]] || [ "$NODE_MAJOR" -lt 24 ]; then
  echo "[startup] WARN: Node < 24 detectado. OpenClaw 2026.4.x recomienda Node 24."
fi

LOCKFILE="/tmp/tenazas-startup.lock"
if [ ! -f "$LOCKFILE" ] || [ $(($(date +%s) - $(cat "$LOCKFILE"))) -gt 300 ]; then
  date +%s > "$LOCKFILE"

  BOT_TOKEN=$(python3 -c "import json; c=json.load(open('/home/tenazas/.openclaw/openclaw.json')); print(c['channels']['telegram']['botToken'])")
  CHAT_ID="${OPENCLAW_TELEGRAM_CHAT_ID:-$(resolve_chat_id)}"
  TIMESTAMP=$(TZ=America/Argentina/Buenos_Aires date +"%H:%M")

  if [ -n "$BOT_TOKEN" ] && [ -n "$CHAT_ID" ]; then
    curl -sf "https://api.telegram.org/bot${BOT_TOKEN}/sendMessage" \
      -d chat_id="${CHAT_ID}" \
      -d text="🦀 Tenazas Online — ${TIMESTAMP} ART" > /dev/null 2>&1
  fi
fi

wait $OC_PID
