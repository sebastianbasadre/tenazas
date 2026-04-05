#!/bin/bash
# ─────────────────────────────────────────────────────────────────
#  Tenazas Test Runner — All 24 suites
#  Corre: bash tests/run_tests.sh
# ─────────────────────────────────────────────────────────────────

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
TOTAL_PASS=0
TOTAL_FAIL=0
TOTAL_SUITES=0
FAILED_SUITES=""

echo ""
echo "══════════════════════════════════════"
echo "  Tenazas Test Suite — $(date +"%H:%M:%S")"
echo "══════════════════════════════════════"

run_suite() {
  local name="$1"
  local cmd="$2"
  TOTAL_SUITES=$(( TOTAL_SUITES + 1 ))
  local output
  output=$(eval "$cmd" 2>&1)
  local exit_code=$?
  local ran=$(echo "$output" | grep -oP '(?<=Ran )\d+' | tail -1)
  ran=${ran:-0}
  if [ $exit_code -eq 0 ]; then
    echo "  OK  $name ($ran)"
    TOTAL_PASS=$(( TOTAL_PASS + ran ))
  else
    local errs=$(echo "$output" | grep -oP '(?<=failures=)\d+' | tail -1)
    errs=${errs:-1}
    TOTAL_PASS=$(( TOTAL_PASS + ran - errs ))
    TOTAL_FAIL=$(( TOTAL_FAIL + errs ))
    echo "  FAIL $name ($errs failed)"
    FAILED_SUITES="$FAILED_SUITES $name"
  fi
}

run_bash() {
  local name="$1"
  local cmd="$2"
  TOTAL_SUITES=$(( TOTAL_SUITES + 1 ))
  local output
  output=$(eval "$cmd" 2>&1)
  local p=$(echo "$output" | grep -c "PASS" || true)
  local f=$(echo "$output" | grep -c "FAIL" || true)
  TOTAL_PASS=$(( TOTAL_PASS + p ))
  TOTAL_FAIL=$(( TOTAL_FAIL + f ))
  if [ "$f" -eq 0 ]; then
    echo "  OK  $name ($p)"
  else
    echo "  FAIL $name ($f failed)"
    FAILED_SUITES="$FAILED_SUITES $name"
  fi
}

# Python suites
run_suite "runtime"           "python3 $SCRIPT_DIR/test_runtime.py"
run_suite "safety_guard"      "python3 $SCRIPT_DIR/test_safety_guard.py"
run_suite "orchestrator"      "python3 $SCRIPT_DIR/test_orchestrator.py"
run_suite "confidence"        "python3 $SCRIPT_DIR/test_confidence.py"
run_suite "self_debugger"     "python3 $SCRIPT_DIR/test_self_debugger.py"
run_suite "advanced"          "python3 $SCRIPT_DIR/test_advanced.py"
run_suite "world_model"       "python3 $SCRIPT_DIR/test_world_model.py"
run_suite "shared_memory"     "python3 $SCRIPT_DIR/test_shared_memory.py"
run_suite "planner"           "python3 $SCRIPT_DIR/test_planner.py"
run_suite "query"             "python3 $SCRIPT_DIR/test_query.py"
run_suite "skill_generator"   "python3 $SCRIPT_DIR/test_skill_generator.py"
run_suite "intelligence"      "python3 $SCRIPT_DIR/test_intelligence.py"
run_suite "model_router"      "python3 $SCRIPT_DIR/test_model_router.py"
run_suite "goal_engine"       "python3 $SCRIPT_DIR/test_goal_engine.py"
run_suite "self_dev_engine"   "python3 $SCRIPT_DIR/test_self_dev_engine.py"
run_suite "idle_learning"     "python3 $SCRIPT_DIR/test_idle_learning.py"
run_suite "idle_learning_v2"  "python3 $SCRIPT_DIR/test_idle_learning_v2.py"
run_suite "background_agents" "python3 $SCRIPT_DIR/test_background_agents.py"
run_suite "observability"     "python3 $SCRIPT_DIR/test_observability.py"
run_suite "cognitive"         "python3 $SCRIPT_DIR/test_cognitive.py"
run_suite "supervisor"        "python3 $SCRIPT_DIR/test_supervisor.py"
run_suite "stability"         "python3 $SCRIPT_DIR/test_stability.py"
run_suite "compact"           "python3 $SCRIPT_DIR/test_compact.py"
run_suite "finance"           "python3 $SCRIPT_DIR/test_finance.py"
run_suite "expenses"          "python3 $SCRIPT_DIR/test_expenses.py"
run_suite "jobs"              "python3 $SCRIPT_DIR/test_jobs.py"
run_suite "daily_improvement" "python3 $SCRIPT_DIR/test_daily_improvement.py"
run_suite "daily_brief"       "python3 $SCRIPT_DIR/test_daily_brief.py"
run_suite "op_memory"         "python3 $SCRIPT_DIR/test_operational_memory.py"
run_suite "personal_super"    "python3 $SCRIPT_DIR/test_personal_supervisor.py"
run_suite "vision_pipeline"   "python3 $SCRIPT_DIR/test_vision_pipeline.py"
run_suite "iol_broker"        "python3 $SCRIPT_DIR/test_iol_broker.py"

# Bash suites
run_bash "auto_heal"          "bash $SCRIPT_DIR/test_auto_heal.sh"
run_bash "ollama_check"       "bash $SCRIPT_DIR/test_ollama_check.sh"

# Summary
echo ""
echo "══════════════════════════════════════"
if [ "$TOTAL_FAIL" -eq 0 ]; then
  echo "  ALL PASSED: $TOTAL_PASS tests in $TOTAL_SUITES suites"
else
  echo "  $TOTAL_FAIL FAILED / $TOTAL_PASS PASSED ($TOTAL_SUITES suites)"
  echo "  Failed:$FAILED_SUITES"
fi
echo "══════════════════════════════════════"

[ "$TOTAL_FAIL" -eq 0 ] && exit 0 || exit 1
