#!/bin/bash
set -u

LOG=/volume1/docker/openclaw/logs/health_sweep_cron.log
MAX=10485760
MIN_INTERVAL=3600
STAMP=/volume1/docker/openclaw/logs/health_sweep_cron.last_run
CMD="/usr/local/bin/docker exec openclaw python3 /home/tenazas/runtime/health_sweep.py"

if [ -f "$LOG" ] && [ "$(stat -c%s "$LOG")" -gt "$MAX" ]; then
  [ -f "${LOG}.1" ] && mv "${LOG}.1" "${LOG}.2"
  mv "$LOG" "${LOG}.1"
fi

echo "=== $(date -Iseconds) ===" >> "$LOG"

NOW=$(date +%s)
LAST_RUN=0
if [ -f "$STAMP" ]; then
  LAST_RUN=$(cat "$STAMP" 2>/dev/null || echo 0)
fi

if [ $((NOW - LAST_RUN)) -lt "$MIN_INTERVAL" ]; then
  echo "skipped=1 reason=min_interval remaining=$((MIN_INTERVAL - (NOW - LAST_RUN)))s" >> "$LOG"
  echo "" >> "$LOG"
  exit 0
fi

echo "$NOW" > "$STAMP"

if command -v timeout >/dev/null 2>&1; then
  timeout 240s /bin/bash -lc "$CMD" >> "$LOG" 2>&1
  STATUS=$?
  if [ $STATUS -eq 124 ]; then
    echo "health sweep timed out after 240s" >> "$LOG"
  fi
else
  /bin/bash -lc "$CMD" >> "$LOG" 2>&1
  STATUS=$?
fi

echo "exit_code=$STATUS" >> "$LOG"
echo "" >> "$LOG"

exit 0
