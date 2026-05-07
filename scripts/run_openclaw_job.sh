#!/bin/bash
set -u

if [ $# -lt 3 ]; then
  echo "usage: $0 <logfile> <timeout_seconds> <command...>" >&2
  exit 2
fi

LOG="$1"
TIMEOUT_SEC="$2"
shift 2

MAX=10485760

if [ -f "$LOG" ] && [ "$(stat -c%s "$LOG")" -gt "$MAX" ]; then
  [ -f "${LOG}.1" ] && mv "${LOG}.1" "${LOG}.2"
  mv "$LOG" "${LOG}.1"
fi

echo "=== $(date -Iseconds) ===" >> "$LOG"

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

echo "exit_code=$STATUS" >> "$LOG"
echo "" >> "$LOG"

exit 0
