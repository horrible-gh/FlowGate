#!/usr/bin/env bash
# Terminal component installer: [--from-dir DIRECTORY]
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
VENV_PYTHON="$ROOT/.venv/bin/python"
SEED="$ROOT/server/setup_terminal.py"

if [[ ! -f "$SEED" ]]; then
    echo "[!] $SEED not found — run this from a FlowGate checkout."
    exit 1
fi

# The venv is what setup.sh builds and what the systemd unit runs, so it is the
# interpreter that actually has the server's dependencies. Fall back to the
# ambient python3 only so a container/manual install (deps already on the system
# interpreter) is not locked out.
if [[ -x "$VENV_PYTHON" ]]; then
    PYTHON="$VENV_PYTHON"
elif command -v python3 >/dev/null 2>&1; then
    PYTHON="$(command -v python3)"
    echo "[!] No venv at $ROOT/.venv — falling back to $PYTHON."
    echo "    If this fails on a missing import, run ./setup.sh first."
else
    echo "[!] No Python found. Install Python 3 (or run ./setup.sh) and try again."
    exit 1
fi

# No `|| true` here, unlike the call inside setup.sh: there the install must survive
# a provider that would not register, while here registering IS the job, so the exit
# status is the answer the caller wants.
exec "$PYTHON" "$SEED" "$@"
