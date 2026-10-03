#!/bin/bash
set -eu
umask 077
cd /home/tenazas

# ── Git sandbox for proposal_executor (idempotent) ───────────────

if [ ! -d /home/tenazas/.git ]; then
  cd /home/tenazas
  git init --initial-branch main 2>/dev/null
  git config user.email "tenazas@local"
  git config user.name "tenazas"

  cat > .gitignore << 'GITEOF'
# Ignore everything by default
*

# Allow runtime source
!runtime/
runtime/*
!runtime/*.py
!runtime/proposal_executors/
runtime/proposal_executors/*
!runtime/proposal_executors/*.py
!runtime/devices/
runtime/devices/*
!runtime/devices/*.py
!runtime/tool_tuning/
runtime/tool_tuning/*
!runtime/tool_tuning/*.py

# Allow tests
!tests/
tests/*
!tests/*.py
!tests/simulation/
tests/simulation/*
!tests/simulation/**/*.py

# Allow root scripts
!.gitignore
!startup.sh
!ollama-check.sh

# Always ignore state/logs/data
runtime/logs/
runtime/state/
runtime/smoking/
runtime/learning/
runtime/__pycache__/
runtime/proposal_executors/__pycache__/
runtime/devices/__pycache__/
runtime/tool_tuning/__pycache__/
tests/__pycache__/
state/
logs/
learning/
*.json
*.jsonl
*.sqlite
GITEOF

  git add -A 2>/dev/null
  git commit -m "init: baseline for proposal_executor sandbox" 2>/dev/null
  echo "[startup] Git repo initialized for proposal_executor"
fi


# Every long-running service now has independent supervision and probes.
exec python3 /home/tenazas/runtime/service_supervisor.py
