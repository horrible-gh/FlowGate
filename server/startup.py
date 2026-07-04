"""Server initialization helper — bootstrap logic extracted from main.py."""
import sys
import io
import time
import LogAssist.log as logger


def configure_console_encoding():
    """Force Windows console encoding to UTF-8."""
    if sys.platform == "win32":
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")


def preload_singletons():
    """Pre-build heavy singletons — prevent delays during gameplay.
    """
    try:
        from modules.flow_gate.services import test_run_service

        test_run_service.startup()
    except Exception as exc:
        logger.warning(f"[startup] test-run worker bootstrap failed: {exc}")


def run_all():
    """Run full bootstrap sequence (called on lifespan entry)."""
    configure_console_encoding()
    preload_singletons()
