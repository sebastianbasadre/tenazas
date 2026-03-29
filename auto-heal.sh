#!/bin/bash
# ─────────────────────────────────────────────────────────────────
#  Tenazas Auto-Heal — NAS Host Script
#  Corre en el host Synology, sin LLM.
#  Cron recomendado: cada 15 min via DSM Task Scheduler
#  docker exec openclaw bash /home/tenazas/auto-heal.sh
# ─────────────────────────────────────────────────────────────────

set -uo pipefail

# ── CONFIG ────────────────────────────────────────────────────────
OPENCLAW_CONFIG="/home/tenazas/.openclaw/openclaw.json"
GLUETUN_PROXY="http://192.168.2.2:8888"
OLLAMA_NOTEBOOK="http://192.168.2.76:11434"
TELEGRAM_CHAT="1565794551"
LOCK_FILE="/tmp/auto-heal.lock"

ARR_CONTAINERS=(gluetun qbittorrent sonarr radarr lidarr prowlarr seerr requestrr tautulli sabnzbd)

# No reiniciar nunca estos containers
SKIP_CONTAINERS=(openclaw watchtower)

# Sleeps configurables para tests (HEAL_SLEEP_VPN=0 HEAL_SLEEP_CONTAINER=0)
HEAL_SLEEP_VPN=${HEAL_SLEEP_VPN:-30}
HEAL_SLEEP_CONTAINER=${HEAL_SLEEP_CONTAINER:-15}

LOG_PREFIX="[auto-heal $(TZ=America/Argentina/Buenos_Aires date '+%H:%M:%S')]"

# ── LOCK: evitar ejecuciones paralelas ───────────────────────────
if [ -f "$LOCK_FILE" ]; then
  LOCK_AGE=$(( $(date +%s) - $(cat "$LOCK_FILE" 2>/dev/null || echo 0) ))
  if [ "$LOCK_AGE" -lt 600 ]; then
    echo "$LOG_PREFIX Ya hay una ejecución en curso (lock age: ${LOCK_AGE}s). Saliendo."
    exit 0
  fi
fi
date +%s > "$LOCK_FILE"
trap 'rm -f "$LOCK_FILE"' EXIT

# ── FUNCIONES ─────────────────────────────────────────────────────
log() { echo "$LOG_PREFIX $*"; }

notify() {
  local msg="$1"
  local token
  token=$(python3 -c "
import json, sys
try:
    c = json.load(open('$OPENCLAW_CONFIG'))
    print(c['channels']['telegram']['botToken'])
except Exception as e:
    sys.exit(1)
" 2>/dev/null) || { log "ERROR: No se pudo leer el bot token"; return 1; }

  curl -sf "https://api.telegram.org/bot${token}/sendMessage" \
    --data-urlencode "chat_id=${TELEGRAM_CHAT}" \
    --data-urlencode "text=${msg}" \
    > /dev/null 2>&1
}

container_status() {
  docker ps --filter "name=^/$1$" --format '{{.Status}}' 2>/dev/null || echo ""
}

is_up() {
  [[ "$(container_status "$1")" == Up* ]]
}

# ── ACUMULADOR DE ACCIONES ────────────────────────────────────────
ACTIONS=()
TIMESTAMP=$(TZ=America/Argentina/Buenos_Aires date +"%H:%M")

# ═══════════════════════════════════════════════════════════════════
# 1. VPN CHECK — Gluetun
# ═══════════════════════════════════════════════════════════════════
log "Verificando VPN (gluetun)..."

VPN_IP=$(curl -sf --max-time 10 -x "$GLUETUN_PROXY" https://ifconfig.me 2>/dev/null || echo "")

if [ -z "$VPN_IP" ]; then
  log "Proxy gluetun no responde. Verificando container..."
  if ! is_up "gluetun"; then
    log "Gluetun DOWN — reiniciando..."
    docker restart gluetun >> /tmp/auto-heal-docker.log 2>&1
    sleep "$HEAL_SLEEP_VPN"
    if is_up "gluetun"; then
      VPN_IP=$(curl -sf --max-time 10 -x "$GLUETUN_PROXY" https://ifconfig.me 2>/dev/null || echo "sin_ip")
      ACTIONS+=("🔧 gluetun reiniciado (estaba caído) → VPN IP: ${VPN_IP}")
    else
      ACTIONS+=("❌ gluetun — restart fallido, sigue caído")
    fi
  else
    # Container up pero proxy no responde — puede estar inicializando
    log "Gluetun up pero proxy no responde aún (inicializando?)"
  fi
else
  log "VPN IP obtenida: $VPN_IP — verificando país..."
  COUNTRY=$(curl -sf --max-time 5 "http://ip-api.com/json/${VPN_IP}?fields=countryCode" 2>/dev/null \
    | python3 -c "import json,sys; print(json.load(sys.stdin).get('countryCode','??'))" 2>/dev/null || echo "??")

  log "País VPN: $COUNTRY"

  if [ "$COUNTRY" = "AR" ]; then
    log "⚠️ VPN LEAK — IP $VPN_IP es Argentina. Reiniciando gluetun..."
    docker restart gluetun >> /tmp/auto-heal-docker.log 2>&1
    sleep "$HEAL_SLEEP_VPN"

    VPN_IP_NEW=$(curl -sf --max-time 10 -x "$GLUETUN_PROXY" https://ifconfig.me 2>/dev/null || echo "")
    if [ -n "$VPN_IP_NEW" ]; then
      COUNTRY_NEW=$(curl -sf --max-time 5 "http://ip-api.com/json/${VPN_IP_NEW}?fields=countryCode" 2>/dev/null \
        | python3 -c "import json,sys; print(json.load(sys.stdin).get('countryCode','??'))" 2>/dev/null || echo "??")
      if [ "$COUNTRY_NEW" != "AR" ]; then
        ACTIONS+=("🔒 VPN leak resuelto: ${VPN_IP} (AR) → ${VPN_IP_NEW} (${COUNTRY_NEW})")
      else
        ACTIONS+=("❌ VPN sigue en AR después del restart — IP: ${VPN_IP_NEW}")
      fi
    else
      ACTIONS+=("⚠️ Gluetun reiniciado por leak AR — proxy aún no responde")
    fi
  else
    log "VPN OK → $VPN_IP ($COUNTRY)"
  fi
fi

# ═══════════════════════════════════════════════════════════════════
# 2. ARR-STACK CONTAINER CHECK
# ═══════════════════════════════════════════════════════════════════
log "Verificando arr-stack containers..."

for CONTAINER in "${ARR_CONTAINERS[@]}"; do
  # Saltar gluetun si ya fue procesado arriba
  if [ "$CONTAINER" = "gluetun" ]; then
    continue
  fi

  STATUS=$(container_status "$CONTAINER")

  if [ -z "$STATUS" ]; then
    log "$CONTAINER — no encontrado, skipping"
    continue
  fi

  if [[ "$STATUS" != Up* ]]; then
    log "$CONTAINER DOWN (status: '$STATUS') — reiniciando..."
    if docker restart "$CONTAINER" >> /tmp/auto-heal-docker.log 2>&1; then
      sleep "$HEAL_SLEEP_CONTAINER"
      STATUS_AFTER=$(container_status "$CONTAINER")
      if [[ "$STATUS_AFTER" == Up* ]]; then
        ACTIONS+=("✅ $CONTAINER reiniciado OK")
      else
        ACTIONS+=("❌ $CONTAINER — restart fallido (estado: ${STATUS_AFTER:-unknown})")
      fi
    else
      ACTIONS+=("❌ $CONTAINER — docker restart falló")
    fi
  fi
done

# ═══════════════════════════════════════════════════════════════════
# 3. OLLAMA CHECK — Notebook
# ═══════════════════════════════════════════════════════════════════
log "Verificando Ollama notebook ($OLLAMA_NOTEBOOK)..."

OLLAMA_STATUS=$(curl -sf --max-time 5 "$OLLAMA_NOTEBOOK/api/tags" > /dev/null 2>&1 && echo "ok" || echo "offline")

if [ "$OLLAMA_STATUS" = "offline" ]; then
  log "Notebook Ollama offline — registrando estado"
  echo "offline" > /tmp/ollama-notebook.status
else
  log "Ollama OK"
  echo "ok" > /tmp/ollama-notebook.status
fi

# ═══════════════════════════════════════════════════════════════════
# 4. REPORTE
# ═══════════════════════════════════════════════════════════════════
if [ ${#ACTIONS[@]} -gt 0 ]; then
  MSG="🦀 Auto-Heal — ${TIMESTAMP} ART"$'\n'
  for ACTION in "${ACTIONS[@]}"; do
    MSG+="$ACTION"$'\n'
  done

  if [ "$OLLAMA_STATUS" = "offline" ]; then
    MSG+="⚠️ Notebook offline — modo Cloud/NAS CPU"
  fi

  notify "$MSG"
  log "Acciones ejecutadas: ${#ACTIONS[@]}. Notificado por Telegram."
elif [ "$OLLAMA_STATUS" = "offline" ]; then
  notify "⚠️ Notebook offline — ${TIMESTAMP} ART. Tenazas en modo Cloud/NAS CPU."
  log "Notebook offline, notificado."
else
  log "Todo OK. Sin acciones."
fi
