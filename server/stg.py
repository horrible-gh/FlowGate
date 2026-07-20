import os
import sys
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# FLOWGATE_STORAGE_DIR is provided by server/.env (no hardcoded host path).
load_dotenv(os.path.join(BASE_DIR, ".env"))

# Resolve static/ relative paths relative to server/
os.chdir(BASE_DIR)
sys.path.insert(0, BASE_DIR)

def _listen_port() -> int:
    """Port from FLOWGATE_PORT, falling back to 8089.

    0273 NR0003 P1-2: this was hardcoded, and deploy/flowgate.service runs this
    file — so a Linux box with 8089 already taken could not be installed without
    editing the source, while setup.ps1 has had a -Port parameter all along.
    A blank or non-numeric value falls back rather than crashing the service.
    """
    raw = (os.environ.get("FLOWGATE_PORT") or "").strip()
    if not raw:
        return 8089
    try:
        return int(raw)
    except ValueError:
        print(f"[stg] ignoring invalid FLOWGATE_PORT={raw!r}; using 8089", file=sys.stderr)
        return 8089


if __name__ == "__main__":
    import uvicorn
    # reload=True per staging request; import-string form required for reload.
    # timeout_graceful_shutdown: backstop so any stuck in-flight request (e.g. a
    # long-lived SSE stream) cannot block shutdown indefinitely (group 0102 R0001).
    uvicorn.run(
        "routers.main:app",
        host=(os.environ.get("FLOWGATE_BIND_HOST") or "0.0.0.0").strip(),
        port=_listen_port(),
        reload=True, timeout_graceful_shutdown=3,
    )