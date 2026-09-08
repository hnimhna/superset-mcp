#!/usr/bin/env bash
# Emit freshly generated secrets for a local .env. Never commit the output.
set -euo pipefail
echo "SUPERSET_SECRET_KEY=$(openssl rand -base64 42 | tr -d '\n')"
echo "METADATA_DB_PASSWORD=$(openssl rand -hex 24)"
echo "WAREHOUSE_DB_PASSWORD=$(openssl rand -hex 24)"
echo "SUPERSET_ADMIN_PASSWORD=$(openssl rand -hex 24)"
