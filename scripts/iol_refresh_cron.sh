#!/bin/bash
set -u

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
exec "$SCRIPT_DIR/run_openclaw_job.sh" \
  /volume1/docker/openclaw/logs/iol_refresh_cron.log \
  120 \
  iol_refresh \
  /usr/local/bin/docker exec openclaw \
  python3 /home/tenazas/tools/iol_refresh.py
