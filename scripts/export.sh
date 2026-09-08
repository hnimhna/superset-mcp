#!/usr/bin/env bash
# Thin wrapper so the documented interface is `scripts/export.sh`.
# Loads .env for local use; in CI the variables come from the secret store.
set -euo pipefail
cd "$(dirname "$0")/.."
if [ -f .env ]; then set -a; . ./.env; set +a; fi
exec "${PYTHON:-.venv/bin/python}" scripts/export.py "$@"
