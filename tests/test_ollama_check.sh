#!/bin/bash
# ─────────────────────────────────────────────────────────────────
#  Tests para ollama-check.sh
#  Corre: bash tests/test_ollama_check.sh
# ─────────────────────────────────────────────────────────────────

set -uo pipefail

PASS=0
FAIL=0
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
OLLAMA_CHECK="$SCRIPT_DIR/../ollama-check.sh"
MOCK_DIR="$(mktemp -d)"
STATUS_FILE="/tmp/ollama-notebook.status"

ok()   { echo "  ✅ $1"; PASS=$((PASS+1)); }
fail() { echo "  ❌ $1"; FAIL=$((FAIL+1)); }

assert_output() {
  local actual="$1" expected="$2" label="$3"
  if echo "$actual" | grep -q "$expected"; then
    ok "$label"
  else
    fail "$label (esperado: '$expected', obtenido: '$actual')"
  fi
}

# ── Mock curl ────────────────────────────────────────────────────
setup_curl_mock() {
  local notebook_status="$1"  # "ok" | "offline"
  local nas_status="${2:-offline}"

  cat > "$MOCK_DIR/curl" << CURLEOF
#!/bin/bash
ARGS="\$*"

# Notebook Ollama
if echo "\$ARGS" | grep -q "192.168.2.76:11434"; then
  [ "$notebook_status" = "ok" ] && exit 0 || exit 1
fi

# NAS Ollama
if echo "\$ARGS" | grep -q "127.0.0.1:11434"; then
  [ "$nas_status" = "ok" ] && exit 0 || exit 1
fi

exit 1
CURLEOF
  chmod +x "$MOCK_DIR/curl"
}

run_check() {
  rm -f "$STATUS_FILE"
  PATH="$MOCK_DIR:$PATH" bash "$OLLAMA_CHECK" "$@" 2>/dev/null
}

# Versión sin borrar cache (para tests de cache)
run_check_cached() {
  PATH="$MOCK_DIR:$PATH" bash "$OLLAMA_CHECK" "$@" 2>/dev/null
}

# ════════════════════════════════════════════════════════════════
echo ""
echo "── Test 1: Notebook online — route system → notebook ──"
setup_curl_mock "ok"
OUTPUT=$(run_check --route system)
assert_output "$OUTPUT" "BACKEND=notebook" "BACKEND es notebook"
assert_output "$OUTPUT" "qwen2.5:3b" "Modelo es qwen2.5:3b para tareas system"
assert_output "$OUTPUT" "192.168.2.76" "Endpoint apunta a notebook"

# ════════════════════════════════════════════════════════════════
echo ""
echo "── Test 2: Notebook online — route reason → 7b ──"
setup_curl_mock "ok"
OUTPUT=$(run_check --route reason)
assert_output "$OUTPUT" "BACKEND=notebook" "BACKEND es notebook"
assert_output "$OUTPUT" "qwen2.5:7b" "Modelo es qwen2.5:7b para razonamiento"

# ════════════════════════════════════════════════════════════════
echo ""
echo "── Test 3: Notebook offline, NAS offline → cloud ──"
setup_curl_mock "offline" "offline"
OUTPUT=$(run_check --route system)
assert_output "$OUTPUT" "BACKEND=cloud" "BACKEND es cloud cuando todo offline"
assert_output "$OUTPUT" "gemini" "Modelo es Gemini Flash"

# ════════════════════════════════════════════════════════════════
echo ""
echo "── Test 4: Notebook offline, NAS online → nas-cpu ──"
setup_curl_mock "offline" "ok"
OUTPUT=$(run_check --route system)
assert_output "$OUTPUT" "BACKEND=nas-cpu" "BACKEND es nas-cpu como fallback"

# ════════════════════════════════════════════════════════════════
echo ""
echo "── Test 5: Cache — no hace ping dos veces en 120s ──"
# Escribir cache fresco directamente (evita depender del primer run para crearlo)
echo "ok" > "$STATUS_FILE"
# Mock dice offline — si el cache funciona, el script debe ignorar el mock
setup_curl_mock "offline"
OUTPUT=$(run_check_cached --status)

# Debug en caso de fallo
if [ "$OUTPUT" != "ok" ]; then
  CACHE_AGE=$(( $(date +%s) - $(stat -c %Y "$STATUS_FILE" 2>/dev/null || echo 0) ))
  echo "  [debug] cache_age=${CACHE_AGE}s, TTL=120s, contenido=$(cat $STATUS_FILE 2>/dev/null)"
fi

if [ "$OUTPUT" = "ok" ]; then
  ok "Cache funciona — devuelve 'ok' aunque mock dice offline"
else
  fail "Cache no funciona — debería devolver 'ok' del cache (obtuvo: '$OUTPUT')"
fi

# ════════════════════════════════════════════════════════════════
echo ""
echo "── Test 6: Cache expirado — re-verifica ──"
setup_curl_mock "offline"
# Escribir cache con timestamp viejo (200s atrás)
echo "ok" > "$STATUS_FILE"
touch -d "200 seconds ago" "$STATUS_FILE" 2>/dev/null || \
  python3 -c "import os,time; os.utime('$STATUS_FILE', (time.time()-200, time.time()-200))"

OUTPUT=$(run_check --status)
if [ "$OUTPUT" = "offline" ]; then
  ok "Cache expirado → re-verifica y detecta offline"
else
  fail "Cache expirado debería re-verificar (obtuvo: '$OUTPUT')"
fi

# ════════════════════════════════════════════════════════════════
echo ""
echo "── Test 7: --json output es JSON válido ──"
setup_curl_mock "ok"
JSON_OUT=$(run_check --json 2>/dev/null)
if python3 -c "import json,sys; json.loads(sys.stdin.read())" <<< "$JSON_OUT" 2>/dev/null; then
  ok "Output de --json es JSON válido"
else
  fail "Output de --json no es JSON válido"
fi

# ════════════════════════════════════════════════════════════════
# Cleanup
rm -rf "$MOCK_DIR"
rm -f "$STATUS_FILE"

echo ""
echo "═══════════════════════════════"
echo "Resultado: $PASS passed, $FAIL failed"
echo "═══════════════════════════════"
[ "$FAIL" -eq 0 ] && exit 0 || exit 1
