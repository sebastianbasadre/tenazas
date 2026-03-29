#!/bin/bash
# ─────────────────────────────────────────────────────────────────
#  Tenazas Test Runner
#  Corre: bash tests/run_tests.sh
#  O desde el container: docker exec openclaw bash /home/tenazas/tests/run_tests.sh
# ─────────────────────────────────────────────────────────────────

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
TOTAL_PASS=0
TOTAL_FAIL=0
TIMESTAMP=$(date +"%Y-%m-%d %H:%M:%S")

echo ""
echo "╔══════════════════════════════════════╗"
echo "║  Tenazas Test Suite — $TIMESTAMP  ║"
echo "╚══════════════════════════════════════╝"

run_suite() {
  local name="$1"
  local cmd="$2"
  local output

  echo ""
  echo "┌─────────────────────────────────────"
  echo "│ $name"
  echo "└─────────────────────────────────────"

  output=$(eval "$cmd" 2>&1)
  echo "$output"

  local passed=$(echo "$output" | grep -oP '\d+(?= passed)' | tail -1 || echo "0")
  local failed=$(echo "$output" | grep -oP '\d+(?= failed)' | tail -1 || echo "0")

  # Para unittest, el formato es diferente: "Ran X tests" + "OK" o "FAILED"
  if echo "$output" | grep -q "^OK$"; then
    local ran=$(echo "$output" | grep -oP '(?<=Ran )\d+')
    passed=${ran:-0}
    failed=0
  elif echo "$output" | grep -qP "^FAILED \("; then
    local ran=$(echo "$output" | grep -oP '(?<=Ran )\d+')
    local errs=$(echo "$output" | grep -oP '(?<=failures=)\d+' || echo "0")
    failed=${errs:-1}
    passed=$(( ${ran:-0} - failed ))
  fi

  TOTAL_PASS=$(( TOTAL_PASS + passed ))
  TOTAL_FAIL=$(( TOTAL_FAIL + failed ))
}

# ── Python tests ──────────────────────────────────────────────────
run_suite "RAG query.py" \
  "python3 $SCRIPT_DIR/test_query.py 2>&1"

run_suite "Context Compactor compact.py" \
  "python3 $SCRIPT_DIR/test_compact.py 2>&1"

run_suite "Runtime Layer" \
  "python3 $SCRIPT_DIR/test_runtime.py 2>&1"

# ── Bash tests ────────────────────────────────────────────────────
run_suite "auto-heal.sh" \
  "bash $SCRIPT_DIR/test_auto_heal.sh 2>&1"

run_suite "ollama-check.sh" \
  "bash $SCRIPT_DIR/test_ollama_check.sh 2>&1"

# ── Resumen final ─────────────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════╗"
if [ "$TOTAL_FAIL" -eq 0 ]; then
  echo "║  ✅ ALL PASSED ($TOTAL_PASS tests)          ║"
else
  echo "║  ❌ $TOTAL_FAIL FAILED / $TOTAL_PASS PASSED            ║"
fi
echo "╚══════════════════════════════════════╝"
echo ""

[ "$TOTAL_FAIL" -eq 0 ] && exit 0 || exit 1
