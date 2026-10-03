#!/bin/bash
set -u

if [ $# -lt 4 ]; then
  echo "usage: $0 <logfile> <timeout_seconds> <job_name> <command...>" >&2
  exit 2
fi

LOG="$1"
TIMEOUT_SEC="$2"
JOB_NAME="$3"
shift 3

MAX=10485760
MODE="${OPENCLAW_JOB_MODE:-direct}"
RUNNER_URL="${OPENCLAW_JOB_RUNNER_URL:-http://127.0.0.1:18891}"
RUNNER_TOKEN="${OPENCLAW_JOB_RUNNER_TOKEN:-}"
exit_job() {
  if [ "${OPENCLAW_JOB_LEGACY_EXIT_ZERO:-false}" = "true" ]; then
    exit 0
  fi
  exit "$1"
}

if [ -f "$LOG" ] && [ "$(stat -c%s "$LOG")" -gt "$MAX" ]; then
  [ -f "${LOG}.1" ] && mv "${LOG}.1" "${LOG}.2"
  mv "$LOG" "${LOG}.1"
fi

echo "=== $(date -Iseconds) ===" >> "$LOG"

if [ "$MODE" = "runner" ]; then
  if [ -z "$RUNNER_TOKEN" ]; then
    echo "OPENCLAW_JOB_RUNNER_TOKEN is required in runner mode" >> "$LOG"
    echo "exit_code=78" >> "$LOG"
    echo "" >> "$LOG"
    exit_job 78
  fi
  CURL_BIN="$(command -v curl || true)"
  if [ -z "$CURL_BIN" ]; then
    echo "curl not found; cannot use runner mode" >> "$LOG"
    echo "exit_code=127" >> "$LOG"
    echo "" >> "$LOG"
    exit_job 127
  fi
  echo "mode=runner job=${JOB_NAME} url=${RUNNER_URL}" >> "$LOG"
  if command -v timeout >/dev/null 2>&1; then
    timeout "${TIMEOUT_SEC}s" \
      "$CURL_BIN" -fsS \
      -X POST \
      -H "X-Tenazas-Job-Token: ${RUNNER_TOKEN}" \
      "${RUNNER_URL}/run/${JOB_NAME}" >> "$LOG" 2>&1
    STATUS=$?
    if [ $STATUS -eq 124 ]; then
      echo "job timed out after ${TIMEOUT_SEC}s" >> "$LOG"
    fi
  else
    "$CURL_BIN" -fsS \
      -X POST \
      -H "X-Tenazas-Job-Token: ${RUNNER_TOKEN}" \
      "${RUNNER_URL}/run/${JOB_NAME}" >> "$LOG" 2>&1
    STATUS=$?
  fi
else
  echo "mode=direct job=${JOB_NAME}" >> "$LOG"
  if command -v timeout >/dev/null 2>&1; then
    timeout "${TIMEOUT_SEC}s" "$@" >> "$LOG" 2>&1
    STATUS=$?
    if [ $STATUS -eq 124 ]; then
      echo "job timed out after ${TIMEOUT_SEC}s" >> "$LOG"
    fi
  else
    "$@" >> "$LOG" 2>&1
    STATUS=$?
  fi
fi

echo "exit_code=$STATUS" >> "$LOG"
echo "" >> "$LOG"

exit_job "$STATUS"
