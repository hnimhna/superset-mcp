#!/usr/bin/env bash
# Idempotent Superset bootstrap: migrate metadata DB, create admin, init roles.
# Safe to re-run - every step is a no-op when already applied.
set -euo pipefail

echo "[init] waiting for metadata database..."
attempt=0
until superset db upgrade > /tmp/db_upgrade.log 2>&1; do
  attempt=$((attempt + 1))
  if [ "$attempt" -ge 20 ]; then
    echo "[init] FATAL: 'superset db upgrade' still failing after $attempt attempts:" >&2
    tail -40 /tmp/db_upgrade.log >&2
    exit 1
  fi
  echo "[init]   metadata DB not ready (attempt $attempt), retrying in 3s"
  sleep 3
done
echo "[init] metadata DB migrated"

echo "[init] ensuring admin user '${SUPERSET_ADMIN_USERNAME}'"
superset fab create-admin \
  --username "${SUPERSET_ADMIN_USERNAME}" \
  --firstname "${SUPERSET_ADMIN_FIRSTNAME:-Superset}" \
  --lastname "${SUPERSET_ADMIN_LASTNAME:-Admin}" \
  --email "${SUPERSET_ADMIN_EMAIL}" \
  --password "${SUPERSET_ADMIN_PASSWORD}" 2>&1 | tail -2 || true

echo "[init] syncing roles and permissions"
superset init

echo "[init] complete"
