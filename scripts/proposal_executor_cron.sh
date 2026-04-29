#!/bin/bash
LOG=/volume1/docker/openclaw/logs/proposal_executor_cron.log
MAX=10485760
if [ -f "$LOG" ] && [ $(stat -c%s "$LOG") -gt $MAX ]; then
  [ -f "${LOG}.1" ] && mv "${LOG}.1" "${LOG}.2"
  mv "$LOG" "${LOG}.1"
fi
echo "=== $(date -Iseconds) ===" >> "$LOG"
/usr/local/bin/docker exec openclaw python3 \
  /home/tenazas/runtime/proposal_executor.py --once --json \
  >> "$LOG" 2>&1
echo "" >> "$LOG"
