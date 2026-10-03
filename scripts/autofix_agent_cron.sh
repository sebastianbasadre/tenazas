#!/bin/bash
set -u

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
exec "$SCRIPT_DIR/run_openclaw_job.sh" \
  /volume1/docker/openclaw/logs/autofix_agent_cron.log \
  1200 \
  autofix_agent \
  /usr/local/bin/docker exec openclaw \
  python3 /home/tenazas/runtime/autofix_agent.py
