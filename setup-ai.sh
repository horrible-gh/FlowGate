#!/usr/bin/env bash
#
# FlowGate AI provider setup (Linux) — the standalone counterpart to setup.sh.
#
# setup.sh installs FlowGate and asks one y/n question about registering the first
# AI provider. This script is that same step on its own, for when you:
#   - answered "n" during the install and want a provider now
#   - want to add a second/third provider to the fallback chain
#   - installed some other way (container, manual venv) and never saw the prompt
#
#   ./setup-ai.sh                       # interactive: pick a CLI on PATH, or an API key
#   ./setup-ai.sh --list                # show what is already registered
#   ./setup-ai.sh --kind claude         # take the documented command for this host as-is
#   ./setup-ai.sh --exec-type api --kind openai --api-model gpt-5.6-sol
#   ./setup-ai.sh --no-probe            # register without the connection test
#
# Every option and every FLOWGATE_AI_* variable is listed by:
#   ./setup-ai.sh --help
#
# All arguments are passed straight through to server/seed_ai_provider.py, which
# holds the whole implementation — this file only locates the interpreter, exactly
# as setup.sh does before calling create_dev_user.py. Nothing about which providers
# exist or what their commands look like is duplicated here, so a new provider kind
# never touches this file.
#
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
VENV_PYTHON="$ROOT/.venv/bin/python"
SEED="$ROOT/server/seed_ai_provider.py"

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
