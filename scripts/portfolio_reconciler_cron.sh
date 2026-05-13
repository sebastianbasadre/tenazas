#!/bin/bash
set -u

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
exec "$SCRIPT_DIR/run_openclaw_job.sh" \
  /volume1/docker/openclaw/logs/portfolio_reconciler_cron.log \
  180 \
  portfolio_reconciler \
  /usr/local/bin/docker exec openclaw \
  python3 /home/tenazas/runtime/portfolio_reconciler.py
