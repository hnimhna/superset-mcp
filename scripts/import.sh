#!/usr/bin/env bash
# Thin wrapper so the documented interface is `scripts/import.sh`.
set -euo pipefail
cd "$(dirname "$0")/.."
if [ -f .env ]; then set -a; . ./.env; set +a; fi
exec "${PYTHON:-.venv/bin/python}" scripts/import.py "$@"
