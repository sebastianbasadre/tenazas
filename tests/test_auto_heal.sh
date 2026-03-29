#!/bin/bash
# ─────────────────────────────────────────────────────────────────
#  Tests para auto-heal.sh
#  Corre: bash tests/test_auto_heal.sh
#  Usa mocks de docker, curl y python3 via PATH override.
# ─────────────────────────────────────────────────────────────────

set -uo pipefail

PASS=0
FAIL=0
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
AUTO_HEAL="$SCRIPT_DIR/../auto-heal.sh"
MOCK_DIR="$(mktemp -d)"
TELEGRAM_CALLS="$MOCK_DIR/telegram_calls.txt"
DOCKER_CALLS="$MOCK_DIR/docker_calls.txt"
touch "$TELEGRAM_CALLS" "$DOCKER_CALLS"

# ── Helpers ───────────────────────────────────────────────────────
ok() { echo "  ✅ $1"; PASS=$((PASS+1)); }
fail() { echo "  ❌ $1"; FAIL=$((FAIL+1)); }

assert_contains() {
  local file="$1" pattern="$2" label="$3"
  if grep -q "$pattern" "$file" 2>/dev/null; then
    ok "$label"
  else
    fail "$label (no se encontró '$pattern' en $file)"
    echo "     Contenido: $(cat "$file" 2>/dev/null | head -5)"
  fi
}

assert_not_contains() {
  local file="$1" pattern="$2" label="$3"
  if ! grep -q "$pattern" "$file" 2>/dev/null; then
    ok "$label"
  else
    fail "$label (se encontró '$pattern' en $file cuando no debía)"
  fi
}

# ── Crear mocks en MOCK_DIR ───────────────────────────────────────
setup_mocks() {
  local scenario="$1"  # "all_ok" | "vpn_leak" | "gluetun_down" | "container_down"

  # Mock: python3 (solo para leer bot token)
  cat > "$MOCK_DIR/python3" << 'PYEOF'
#!/bin/bash
# Mock python3: si le piden el botToken, devuelve un token fake
if echo "$*" | grep -q "botToken"; then
  echo "FAKE_BOT_TOKEN"
else
  # Ejecutar python3 real para todo lo demás (ip-api parsing)
  /usr/bin/python3 "$@"
fi
PYEOF
  chmod +x "$MOCK_DIR/python3"

  # Mock: curl
  cat > "$MOCK_DIR/curl" << CURLEOF
#!/bin/bash
ARGS="\$*"
LOG_FILE="$MOCK_DIR/curl_calls.txt"
echo "\$ARGS" >> "\$LOG_FILE"

# Telegram API → siempre OK
if echo "\$ARGS" | grep -q "telegram"; then
  echo '{"ok":true}' >> "$TELEGRAM_CALLS"
  exit 0
fi

# ip-api geolocation
if echo "\$ARGS" | grep -q "ip-api"; then
  IP=\$(echo "\$ARGS" | grep -oP '(?<=json/)[^?]+')
  # IPs de Argentina de prueba
  if [[ "\$IP" == "200.45."* ]] || [[ "\$IP" == "AR_TEST_IP" ]]; then
    echo '{"countryCode":"AR"}'
  else
    echo '{"countryCode":"ES"}'
  fi
  exit 0
fi

# VPN proxy (gluetun)
if echo "\$ARGS" | grep -q "192.168.2.2:8888"; then
  case "$scenario" in
    vpn_leak)   echo "AR_TEST_IP" ;;
    gluetun_down) exit 1 ;;
    *)          echo "82.140.10.1" ;;  # IP española OK
  esac
  exit 0
fi

# ifconfig.me directo (IP pública real)
if echo "\$ARGS" | grep -q "ifconfig.me"; then
  echo "200.55.100.1"
  exit 0
fi

exit 0
CURLEOF
  chmod +x "$MOCK_DIR/curl"

  # Mock: docker
  cat > "$MOCK_DIR/docker" << DOCKEREOF
#!/bin/bash
ARGS="\$*"
echo "docker \$ARGS" >> "$DOCKER_CALLS"

case "\$ARGS" in
  "ps --filter name=^/gluetun$ --format {{.Status}}")
    case "$scenario" in
      gluetun_down) echo "" ;;  # no aparece en ps
      *)            echo "Up 2 hours (healthy)" ;;
    esac
    ;;
  "ps --filter name=^/sonarr$ --format {{.Status}}")
    case "$scenario" in
      container_down) echo "Exited (1) 1 hour ago" ;;
      *)              echo "Up 3 hours" ;;
    esac
    ;;
  "ps --filter name=^/"*"$ --format {{.Status}}")
    echo "Up 3 hours"
    ;;
  "restart gluetun")
    echo "gluetun"
    # Después del restart, simular que queda Up
    ;;
  "restart sonarr")
    echo "sonarr"
    ;;
  "restart "*)
    echo "\${ARGS#restart }"
    ;;
  *)
    ;;
esac
exit 0
DOCKEREOF
  chmod +x "$MOCK_DIR/docker"
}

# Prepara el entorno para correr el script con mocks
run_heal() {
  local scenario="$1"
  setup_mocks "$scenario"
  > "$TELEGRAM_CALLS"
  > "$DOCKER_CALLS"
  rm -f /tmp/auto-heal.lock

  # Inyectar mocks en PATH y forzar config path
  OPENCLAW_CONFIG="$MOCK_DIR/fake_config.json" \
  HEAL_SLEEP_VPN=0 \
  HEAL_SLEEP_CONTAINER=0 \
  PATH="$MOCK_DIR:$PATH" \
    bash "$AUTO_HEAL" 2>&1
}

# Config fake para que python3 mock devuelva el token
cat > "$MOCK_DIR/fake_config.json" << 'EOF'
{"channels": {"telegram": {"botToken": "FAKE_BOT_TOKEN"}}}
EOF

# ════════════════════════════════════════════════════════════════
echo ""
echo "── Test 1: Todo OK — sin acciones, sin notificación ──"
run_heal "all_ok" > "$MOCK_DIR/out_all_ok.txt" 2>&1
assert_not_contains "$TELEGRAM_CALLS" "ok" "No envía Telegram cuando todo está OK"
assert_contains "$MOCK_DIR/out_all_ok.txt" "Todo OK" "Imprime 'Todo OK'"

# ════════════════════════════════════════════════════════════════
echo ""
echo "── Test 2: VPN Leak (IP argentina) ──"
run_heal "vpn_leak" > "$MOCK_DIR/out_vpn.txt" 2>&1
assert_contains "$DOCKER_CALLS" "restart gluetun" "Ejecuta docker restart gluetun"
assert_contains "$TELEGRAM_CALLS" "ok" "Envía notificación Telegram"
assert_contains "$MOCK_DIR/out_vpn.txt" "AR" "Detecta IP argentina en el log"

# ════════════════════════════════════════════════════════════════
echo ""
echo "── Test 3: Gluetun container DOWN ──"
run_heal "gluetun_down" > "$MOCK_DIR/out_gluetun.txt" 2>&1
assert_contains "$DOCKER_CALLS" "restart gluetun" "Ejecuta docker restart gluetun"
assert_contains "$TELEGRAM_CALLS" "ok" "Envía notificación Telegram"

# ════════════════════════════════════════════════════════════════
echo ""
echo "── Test 4: Container arr-stack caído (sonarr) ──"
run_heal "container_down" > "$MOCK_DIR/out_container.txt" 2>&1
assert_contains "$DOCKER_CALLS" "restart sonarr" "Ejecuta docker restart sonarr"
assert_contains "$TELEGRAM_CALLS" "ok" "Envía notificación Telegram"

# ════════════════════════════════════════════════════════════════
echo ""
echo "── Test 5: Lock file — no doble ejecución ──"
setup_mocks "all_ok"
> "$TELEGRAM_CALLS"
> "$DOCKER_CALLS"
date +%s > /tmp/auto-heal.lock  # lock reciente — NO usar run_heal (borra el lock)
OPENCLAW_CONFIG="$MOCK_DIR/fake_config.json" \
HEAL_SLEEP_VPN=0 \
HEAL_SLEEP_CONTAINER=0 \
PATH="$MOCK_DIR:$PATH" \
  bash "$AUTO_HEAL" > "$MOCK_DIR/out_lock.txt" 2>&1
assert_contains "$MOCK_DIR/out_lock.txt" "lock" "Detecta lock y sale sin correr"
rm -f /tmp/auto-heal.lock

# ════════════════════════════════════════════════════════════════
echo ""
echo "── Test 6: Ollama check escribe status file ──"
rm -f /tmp/ollama-notebook.status
run_heal "all_ok" > /dev/null 2>&1
# Mock curl no responde al puerto 11434, así que queda offline
if [ -f /tmp/ollama-notebook.status ]; then
  ok "Archivo /tmp/ollama-notebook.status creado"
else
  fail "Archivo /tmp/ollama-notebook.status no fue creado"
fi

# ════════════════════════════════════════════════════════════════
# Cleanup
rm -rf "$MOCK_DIR"

echo ""
echo "═══════════════════════════════"
echo "Resultado: $PASS passed, $FAIL failed"
echo "═══════════════════════════════"
[ "$FAIL" -eq 0 ] && exit 0 || exit 1
