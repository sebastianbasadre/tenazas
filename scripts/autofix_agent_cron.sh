#!/bin/bash
set -u

LOG=/volume1/docker/openclaw/logs/autofix_agent_cron.log
MAX=10485760
CMD="/usr/local/bin/docker exec openclaw python3 /home/tenazas/runtime/autofix_agent.py"

if [ -f "$LOG" ] && [ "$(stat -c%s "$LOG")" -gt "$MAX" ]; then
  [ -f "${LOG}.1" ] && mv "${LOG}.1" "${LOG}.2"
  mv "$LOG" "${LOG}.1"
fi

echo "=== $(date -Iseconds) ===" >> "$LOG"

if command -v timeout >/dev/null 2>&1; then
  timeout 1200s /bin/bash -lc "$CMD" >> "$LOG" 2>&1
  STATUS=$?
  if [ $STATUS -eq 124 ]; then
    echo "autofix agent timed out after 1200s" >> "$LOG"
  fi
else
  /bin/bash -lc "$CMD" >> "$LOG" 2>&1
  STATUS=$?
fi

echo "exit_code=$STATUS" >> "$LOG"
echo "" >> "$LOG"

exit 0
