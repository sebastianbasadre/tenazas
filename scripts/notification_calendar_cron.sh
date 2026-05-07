#!/bin/bash
set -u

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
exec "$SCRIPT_DIR/run_openclaw_job.sh" \
  /volume1/docker/openclaw/logs/notification_calendar_cron.log \
  90 \
  /usr/local/bin/docker exec openclaw \
  python3 /home/tenazas/runtime/notification_poller.py --calendar
