#!/bin/bash
# Start Telegram gateway for Tabula.
# Token is loaded from .env by run.py itself.
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TABULA_HOME="${TABULA_HOME:-$HOME/.tabula}"
VENV_PYTHON="$TABULA_HOME/.venv/bin/python3"

exec "$VENV_PYTHON" "$SCRIPT_DIR/run.py"
