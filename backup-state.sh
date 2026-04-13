#!/usr/bin/env bash
# backup-state.sh — Backup Tenazas state + config to NAS 2
#
# Cron: 0 4 * * * /volume1/docker/openclaw/backup-state.sh
# Retains last 7 daily backups.
#
# What it backs up:
#   - state/ (openclaw.json, credentials, IOL, workspace, memory)
#   - docker-compose.yml
#   - himalaya-config/
#   - gcal-token.json
#   - workspace/runtime/smoking/ (tracker data)
#   - workspace/runtime/learning/ (proposals, dreams)
#
# Destination: /volume2/backups/tenazas/ (NAS local)
#              Optional: rsync to NAS 2 (192.168.2.3)

set -euo pipefail

SRC="/volume1/docker/openclaw"
DEST="/volume2/backups/tenazas"
DATE=$(date +%Y-%m-%d_%H%M)
BACKUP_DIR="${DEST}/${DATE}"
RETAIN_DAYS=7

echo "[backup] Starting Tenazas state backup — ${DATE}"

# Create backup dir
mkdir -p "${BACKUP_DIR}"

# Core state (secrets + config)
cp -a "${SRC}/state/openclaw.json" "${BACKUP_DIR}/" 2>/dev/null || true
cp -a "${SRC}/state/iol_config.json" "${BACKUP_DIR}/" 2>/dev/null || true
cp -a "${SRC}/state/iol_credentials.json" "${BACKUP_DIR}/" 2>/dev/null || true
cp -a "${SRC}/gcal-token.json" "${BACKUP_DIR}/" 2>/dev/null || true
cp -a "${SRC}/docker-compose.yml" "${BACKUP_DIR}/" 2>/dev/null || true

# Credentials dir
cp -ra "${SRC}/state/credentials" "${BACKUP_DIR}/credentials" 2>/dev/null || true

# Himalaya email config
cp -ra "${SRC}/himalaya-config" "${BACKUP_DIR}/himalaya-config" 2>/dev/null || true

# Identity
cp -ra "${SRC}/state/identity" "${BACKUP_DIR}/identity" 2>/dev/null || true

# Workspace (skills, memory, datasets) — exclude large generated files
mkdir -p "${BACKUP_DIR}/workspace"
cp -ra "${SRC}/state/workspace/skills" "${BACKUP_DIR}/workspace/skills" 2>/dev/null || true
cp -ra "${SRC}/state/workspace/memory" "${BACKUP_DIR}/workspace/memory" 2>/dev/null || true
cp -ra "${SRC}/state/workspace/datasets" "${BACKUP_DIR}/workspace/datasets" 2>/dev/null || true

# Evals (datasets, seeds, results)
cp -ra "${SRC}/state/workspace/evals" "${BACKUP_DIR}/workspace/evals" 2>/dev/null || true

# Runtime data (smoking, learning)
mkdir -p "${BACKUP_DIR}/runtime"
cp -ra "${SRC}/workspace/runtime/smoking" "${BACKUP_DIR}/runtime/smoking" 2>/dev/null || true
cp -ra "${SRC}/workspace/runtime/learning" "${BACKUP_DIR}/runtime/learning" 2>/dev/null || true

# Finance + Jobs data
cp -ra "${SRC}/workspace/runtime/finance" "${BACKUP_DIR}/runtime/finance" 2>/dev/null || true
cp -ra "${SRC}/workspace/runtime/jobs" "${BACKUP_DIR}/runtime/jobs" 2>/dev/null || true

# Size check
SIZE=$(du -sh "${BACKUP_DIR}" | cut -f1)
echo "[backup] Backup size: ${SIZE}"

# Cleanup old backups
find "${DEST}" -maxdepth 1 -type d -mtime +${RETAIN_DAYS} -exec rm -rf {} \; 2>/dev/null || true
KEPT=$(ls -1d "${DEST}"/20* 2>/dev/null | wc -l)
echo "[backup] Retained backups: ${KEPT}"

# Optional: rsync to NAS 2
if ping -c 1 -W 2 192.168.2.3 >/dev/null 2>&1; then
    rsync -a --delete "${DEST}/" sbasadre@192.168.2.3:/volume1/backups/tenazas/ 2>/dev/null && \
        echo "[backup] Synced to NAS 2 (192.168.2.3)" || \
        echo "[backup] WARN: rsync to NAS 2 failed"
else
    echo "[backup] SKIP: NAS 2 not reachable"
fi

echo "[backup] Done — ${BACKUP_DIR}"
