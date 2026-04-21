#!/bin/bash
# Post-install hook for the guardian distro.
# Runs after install-distro.py has linked the distro into ~/.tabula/.
#
# Builds the Docker sandbox image used by skills/execute-code.
set -euo pipefail

TABULA_HOME="${TABULA_HOME:-$HOME/.tabula}"
DISTRO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SANDBOX_DIR="$DISTRO_DIR/skills/execute-code/sandbox"

if ! command -v docker >/dev/null 2>&1; then
  echo "    !! docker not found — guardian execute_code will fail at runtime."
  echo "       Install Docker (or OrbStack on macOS) and run:"
  echo "         docker build -t tabula-guardian-sandbox:latest $SANDBOX_DIR"
  exit 0
fi

echo "    Building guardian sandbox image"
docker build -q -t tabula-guardian-sandbox:latest "$SANDBOX_DIR" >/dev/null
echo "    Sandbox image ready: tabula-guardian-sandbox:latest"
