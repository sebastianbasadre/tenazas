#!/bin/bash
set -u

# ── Helpers ──────────────────────────────────────────────────────

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

# Watchdog wrapper: restarts a process with exponential backoff
# Usage: watchdog "label" command args...
watchdog() {
  local label="$1"
  shift
  local fails=0
  while true; do
    cd /home/tenazas && "$@"
    local exit_code=$?
    fails=$((fails + 1))
    local ts
    ts=$(TZ=America/Argentina/Buenos_Aires date +"%H:%M:%S")
    echo "[watchdog] $label exited (code=$exit_code, restarts=$fails) at $ts" >> /tmp/${label}_watchdog.log

    if [ $fails -ge 5 ]; then sleep 60
    elif [ $fails -ge 3 ]; then sleep 30
    else sleep 5; fi
  done
}

# ── Main process ─────────────────────────────────────────────────

openclaw gateway &
OC_PID=$!

# Kill zombie MCP server instances from prior runs
pkill -f "iol_broker/mcp_server.py" 2>/dev/null || true
sleep 1

# ── Git sandbox for proposal_executor (idempotent) ───────────────

if [ ! -d /home/tenazas/.git ]; then
  cd /home/tenazas
  git init --initial-branch main 2>/dev/null
  git config user.email "tenazas@local"
  git config user.name "tenazas"

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

# ── Background services ─────────────────────────────────────────

run_bg_cd "Metrics server iniciado en :9105" "/home/tenazas" python3 /home/tenazas/runtime/metrics_server.py
run_bg_cd "Model failover monitor iniciado" "/home/tenazas" python3 /home/tenazas/runtime/model_failover.py
run_bg "API metrics exporter iniciado en :9106" python3 -c "from runtime.api_call_counter import start_exporter; start_exporter(); import time; [time.sleep(3600) for _ in iter(int, 1)]"

# Telegram router with auto-restart watchdog
watchdog "telegram_router" python3 /home/tenazas/runtime/telegram_router.py &
echo "Telegram router iniciado (with watchdog auto-restart)"

run_bg_cd "Portfolio exporter iniciado en :9101" "/home/tenazas" node /home/tenazas/runtime/portfolio-exporter.mjs
run_bg_cd "Network exporter iniciado en :9108" "/home/tenazas" python3 /home/tenazas/runtime/network_exporter.py
run_bg_cd "Finance exporter iniciado en :9109" "/home/tenazas" python3 /home/tenazas/runtime/finance_exporter.py

# Portfolio reconciler (delayed start, watch mode)
(sleep 120 && cd /home/tenazas && python3 /home/tenazas/runtime/portfolio_reconciler.py --watch) &
echo "Portfolio reconciler iniciado (watch mode, cada 10 min)"

# Multitool eval exporter
if ! pgrep -f "eval_exporter.py" >/dev/null 2>&1; then
  run_bg_cd "Eval exporter iniciado en :9107" "/home/tenazas" python3 /home/tenazas/runtime/tool_tuning/eval_exporter.py
fi

# ── IOL token management ─────────────────────────────────────────

sleep 20
python3 /home/tenazas/tools/iol_refresh.py --force 2>&1 || echo "[startup] IOL refresh failed (non-critical)"
echo "[startup] IOL token initialized"

# IOL auto-refresh loop (token expires in 20 min, refresh every 15)
(while true; do sleep 900; python3 /home/tenazas/tools/iol_refresh.py >> /tmp/iol_refresh.log 2>&1; done) &
echo "[startup] IOL auto-refresh loop iniciado (cada 15 min)"

# ── Startup checks ──────────────────────────────────────────────

NODE_VER=$(node --version)
echo "[startup] Node version: $NODE_VER"
NODE_MAJOR=$(printf '%s\n' "${NODE_VER#v}" | cut -d. -f1)
if ! [[ "$NODE_MAJOR" =~ ^[0-9]+$ ]] || [ "$NODE_MAJOR" -lt 24 ]; then
  echo "[startup] WARN: Node < 24 detectado. OpenClaw 2026.4.x recomienda Node 24."
fi

# ── Startup notification ─────────────────────────────────────────

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
