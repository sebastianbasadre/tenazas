#!/bin/bash
set -u

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
exec "$SCRIPT_DIR/run_openclaw_job.sh" \
  /volume1/docker/openclaw/logs/notification_email_cron.log \
  180 \
  notification_email \
  /usr/local/bin/docker exec openclaw \
  python3 /home/tenazas/runtime/notification_poller.py --email
