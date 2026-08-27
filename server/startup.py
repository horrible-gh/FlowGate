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


def recover_ai_invoke_leases():
    """0401 NR0003 / T0004 작업 1: clear AI-run group leases orphaned by a restart.

    Every lease still in the table when this runs belongs to a process that no
    longer exists (this process's live-run registry starts empty), so it is safe
    to reclaim unconditionally at this point in the bootstrap sequence.
    """
    try:
        from modules.flow_gate.services import ai_invoke_service

        reclaimed = ai_invoke_service.startup_recover_leases()
        if reclaimed:
            logger.info(f"[startup] reclaimed {reclaimed} orphaned AI-run lease(s)")
    except Exception as exc:
        logger.warning(f"[startup] AI-run lease recovery failed: {exc}")


def recover_git_sessions():
    """0115 L0006 E8: restore/clean merge-conflict sessions and stale git locks."""
    try:
        from modules.flow_gate.services import git_service

        git_service.startup_recovery()
    except Exception as exc:
        logger.warning(f"[startup] git session recovery failed: {exc}")


def encrypt_ai_provider_keys():
    """0371 NR0007 §3: move legacy plaintext ai_providers.api_key rows to AES-256-GCM.

    The column existed as plaintext for a long time, so shipping encryption alone would
    leave every already-registered provider key readable in the DB. Idempotent: a run
    with nothing left to do costs one SELECT.
    """
    try:
        from modules.flow_gate.db import ai_providers as ai_providers_db

        migrated = ai_providers_db.encrypt_plaintext_api_keys()
        if migrated:
            logger.info(f"[startup] encrypted {migrated} plaintext AI provider api_key row(s)")
    except Exception as exc:
        logger.warning(f"[startup] AI provider api_key encryption failed: {exc}")


def record_deployment():
    """0468 T0013: durable SHA/version/JST process-start marker."""
    try:
        from modules.flow_gate.settings.system_settings_service import record_deployment_started
        record_deployment_started()
    except Exception as exc:
        logger.warning(f"[startup] deployment marker failed: {exc}")


def run_all():
    """Run full bootstrap sequence (called on lifespan entry)."""
    configure_console_encoding()
    record_deployment()
    preload_singletons()
    recover_ai_invoke_leases()
    recover_git_sessions()
    encrypt_ai_provider_keys()
