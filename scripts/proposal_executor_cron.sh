#!/bin/bash
set -u

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
exec "$SCRIPT_DIR/run_openclaw_job.sh" \
  /volume1/docker/openclaw/logs/proposal_executor_cron.log \
  300 \
  proposal_executor \
  /usr/local/bin/docker exec openclaw \
  python3 /home/tenazas/runtime/proposal_executor.py --once --json
