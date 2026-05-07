#!/bin/bash
set -u

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
exec "$SCRIPT_DIR/run_openclaw_job.sh" \
  /volume1/docker/openclaw/logs/finance_daily_cron.log \
  180 \
  /usr/local/bin/docker exec openclaw \
  python3 /home/tenazas/runtime/finance_daily.py
