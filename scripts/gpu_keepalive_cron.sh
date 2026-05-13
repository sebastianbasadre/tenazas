#!/bin/bash
set -u

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
exec "$SCRIPT_DIR/run_openclaw_job.sh" \
  /volume1/docker/openclaw/logs/gpu_keepalive_cron.log \
  90 \
  gpu_keepalive \
  /usr/local/bin/docker exec openclaw \
  python3 /home/tenazas/runtime/gpu_scheduler.py --keepalive
