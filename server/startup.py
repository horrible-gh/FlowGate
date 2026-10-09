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


def sweep_ai_run_scratches():
    """0610: sweep run scratch after orphan leases have durable end records."""
    try:
        from modules.flow_gate.services import ai_invoke_service

        projects = ai_invoke_service.startup_sweep_run_scratches()
        logger.info(f"[startup] swept AI-run scratch for {projects} project(s)")
    except Exception as exc:
        logger.warning(f"[startup] AI-run scratch sweep failed: {exc}")


def sweep_token_scratches():
    """Sweep manifest-owned token directories after orphan lease recovery."""
    try:
        from modules.flow_gate.services import token_scratch

        projects = token_scratch.startup_sweep()
        purged = token_scratch.purge_expired_rows()
        logger.info(f"[startup] swept token scratch for {projects} project(s); purged {purged} expired token row(s)")
    except Exception as exc:
        logger.warning(f"[startup] token scratch sweep failed: {exc}")


def register_server_instance():
    """0669 1a (0666 L 2.26 steps 1~2): register this process before any lock recovery.

    Lock rows written from here on carry this instance id, so a later recovery can tell
    a dead owner's leftover from a live one. Failure keeps the old behaviour (rows are
    stamped NULL = owner unknown) and is only logged.
    """
    try:
        from modules.flow_gate.services.git import instance_registry

        instance_id = instance_registry.startup()
        logger.info(f"[startup] server instance registered: {instance_id}")
    except Exception as exc:
        logger.warning(f"[startup] server instance registration failed: {exc}")


def recover_git_sessions():
    """Recover Self-check ownership before Git session recovery.

    Self-check protection lives on the Group's G row (0669 unit 4). A run whose
    protection could not be written there stays recovery_incomplete, which keeps the
    Group's G closed on its own (0669 unit 9c removed the old project lock row).
    """
    try:
        from modules.flow_gate.services import git_service, tr_self_check_service

        tr_self_check_service.recover()
        git_service.startup_recovery()
    except Exception as exc:
        logger.warning(f"[startup] git session recovery failed: {exc}")


def recover_chat_commands():
    """0670 T0004: chat command rows a dead server left open are closed, never resumed."""
    try:
        from modules.flow_gate.services import chat_command_service

        closed = chat_command_service.recover()
        if closed:
            logger.info(f"[startup] closed {closed} interrupted chat command request(s)")
    except Exception as exc:
        logger.warning(f"[startup] chat command recovery failed: {exc}")
    try:
        # 0675 T0004 §2-5: decide the conversation anchor of rows written before it
        # existed, and repair rows a failed reply-time re-anchor left at run_start; the
        # counts per state before/after are the backfill's audit trail.
        from modules.flow_gate.services import chat_activity_anchor_service

        report = chat_activity_anchor_service.backfill()
        if report["resolved"] or report["after"]["commands"]["pending"] or report["after"]["changes"]["pending"]:
            logger.info(f"[startup] chat activity anchor backfill: {report}")
    except Exception as exc:
        logger.warning(f"[startup] chat activity anchor backfill failed: {exc}")


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


def start_snapshot_cleanup():
    """Recover interrupted snapshots, sweep expired ones, and start the TTL worker."""
    try:
        from modules.flow_gate.services import snapshot_materialization_service
        snapshot_materialization_service.startup()
    except Exception as exc:
        logger.warning(f"[startup] snapshot cleanup bootstrap failed: {exc}")


# 0684 T#4 (D#1 §7): the directory Source Bundle kept its Bundles and AI Scratch copies in,
# under every storage root. Its DB rows went with migration 140.
RETIRED_SOURCE_BUNDLE_DIR = "source-bundles"


def _storage_roots() -> set:
    """Every configured storage root, each collected on its own.

    Not the effective root: FLOWGATE_STORAGE_DIR shadows the system setting, the default
    and every project override in get_storage_root(), yet a store left under any of them
    from before the location changed still has to go.
    """
    import os
    from pathlib import Path
    from modules.flow_gate.storage import paths as storage_paths

    roots = {storage_paths.default_storage_root()}
    env = os.environ.get("FLOWGATE_STORAGE_DIR", "").strip()
    if env:
        roots.add(Path(env))
    system_root = storage_paths._system_storage_root_value()
    if system_root:
        roots.add(Path(system_root))
    try:
        from modules.flow_gate.db import projects as db_projects
        for row in db_projects.list_projects():
            override = storage_paths._project_override_value(row.get("project_id"))
            if override:
                roots.add(Path(override))
    except Exception as exc:
        logger.warning(f"[startup] project storage roots unavailable: {exc}")
    return roots


def remove_retired_source_bundle_storage(roots=None) -> list:
    """Delete the retired Source Bundle store once; later boots find nothing and do nothing.

    A link in place of the directory is left alone (never followed into another tree).
    Returns the directories removed.
    """
    import shutil
    from pathlib import Path

    removed = []
    for root in (roots if roots is not None else _storage_roots()):
        target = Path(root) / RETIRED_SOURCE_BUNDLE_DIR
        try:
            if target.is_symlink() or not target.is_dir():
                continue
            shutil.rmtree(target)
            removed.append(target)
            logger.info(f"[startup] removed retired Source Bundle storage: {target}")
        except Exception as exc:
            logger.warning(f"[startup] retired Source Bundle storage cleanup failed for {target}: {exc}")
    return removed


def run_all():
    """Run full bootstrap sequence (called on lifespan entry)."""
    configure_console_encoding()
    record_deployment()
    preload_singletons()
    recover_ai_invoke_leases()
    sweep_ai_run_scratches()
    sweep_token_scratches()
    register_server_instance()
    recover_git_sessions()
    encrypt_ai_provider_keys()
    start_snapshot_cleanup()
    remove_retired_source_bundle_storage()
    recover_chat_commands()
