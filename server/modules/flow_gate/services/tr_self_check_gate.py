"""The gate in front of the stale reclaim of a Self-check-owned G (flowgate.v02.0080 T#2,
NR0007 §5-0 ⑤, §5 item 9).

Both stale-reclaim callers -- the sweeper (``job_sweeper.step_stale_locks``) and any other
holder's G acquire (``lock_manager._try_acquire_once``) -- delete through
``lock_manager.reclaim_stale_row``, and that one function asks :func:`reclaim_gate` first
for a ``holder_kind='selfcheck'`` row. ``is_stale``'s verdict (owner DEAD, heartbeat
expired) only made the row a candidate; it is not evidence. The gate judges with the same
proofs as ``recover()`` (``tr_self_check_proof``), so the rule exists once:

====================  ===================  ===================  ========
run row               owning server        process tree         result
====================  ===================  ===================  ========
active                MATCH / UNKNOWN      --                   KEEP
active                proved dead          proved empty         RECLAIM (run failed first)
active                proved dead          member / UNKNOWN     PROTECT (run recovery_incomplete)
active, recovering    --                   --                   KEEP (a pass is proving it)
active, recovering    proved dead          re-proved            as the rows above, once left behind
none                  proved dead          (nothing spawned)    RECLAIM
none                  other                --                   KEEP
terminal              --                   proved empty         RECLAIM
terminal              --                   member / UNKNOWN     PROTECT
====================  ===================  ===================  ========

KEEP leaves the row exactly as it is (the acquire path then answers BUSY), PROTECT marks
it protected (RECOVERY_REQUIRED for everyone). Any failure inside the gate is KEEP. The gate
takes no lock: it reads, and writes only by CAS (run ``claim_recovering``, G epoch). It never
signals a process -- a tree that still has members is left to the reaper / re-verification.
Rows of every other holder kind never reach it.

A KEEP after a failure must not leave the fence behind (0080 T#2 rework): a pass whose CAS
landed but that failed before writing an outcome undoes its own CAS (``abandon_recovering``:
recovering -> none, only if nothing wrote the row since), so the next pass proves the row
from the start. When even the undo fails, or the CAS landed but its caller never learned
it, the row stays ``recovering``; a later pass treats such a row as left behind once no
one has written it for ``FLOWGATE_SELFCHECK_RECOVERING_STALE_SEC`` and re-runs the proof
under the fence already there. Neither path ends or frees anything without the proof.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime

from modules.flow_gate.db import git_concurrency as gdb
from modules.flow_gate.db import tr_self_check_runs as db_runs
from modules.flow_gate.db.connection import now_iso
from modules.flow_gate.services import tr_self_check_proof as proof

_log = logging.getLogger(__name__)

RECLAIM, KEEP, PROTECT = "reclaim", "keep", "protect"
PROTECT_REASON = "selfcheck_reap_unconfirmed"
RECOVERING_STALE_ENV = "FLOWGATE_SELFCHECK_RECOVERING_STALE_SEC"
_ACTIVE = ("pending", "running")


def run_id_of(lock_row: dict) -> str | None:
    """``holder_ctx_id='scr:<run_id>'`` (``lock_manager.selfcheck_ctx_id``)."""
    ctx = str(lock_row.get("holder_ctx_id") or "")
    return ctx[4:] if ctx.startswith("scr:") and len(ctx) > 4 else None


def reclaim_gate(lock_row: dict) -> str:
    """RECLAIM / KEEP / PROTECT for one Self-check-owned G row (table above)."""
    try:
        return _judge(lock_row)
    except Exception:
        _log.warning("selfcheck G %s: reclaim gate failed; kept", lock_row.get("lock_key"), exc_info=True)
        return KEEP


def _judge(lock: dict) -> str:
    rid = run_id_of(lock)
    if not rid:
        return KEEP
    run = db_runs.get_run(rid)
    if run is None:
        # G won, create_pending not written: spawn only follows create_pending, so a server
        # proved dead left no child of this run.
        return RECLAIM if proof.owner_server_state(lock.get("instance_id")).gone else KEEP
    if run["status"] not in _ACTIVE:
        # release() of an ended run did not land.
        return RECLAIM if proof.tree_state(run).state == proof.ABSENT else _protect(lock, rid)
    state = run.get("recovery_state")
    if state == "incomplete":
        return _protect(lock, rid)
    if state not in ("none", "recovering"):
        return KEEP
    if state == "recovering" and not _left_behind(run):
        return KEEP          # recover(), a cancel or another gate pass is proving it now
    owners = {proof.owner_instance_of(run), lock.get("instance_id")}
    if not all(proof.owner_server_state(owner).gone for owner in owners):
        return KEEP
    if state == "recovering":
        # The pass that fenced the row never wrote its outcome nor undid the fence. The fence
        # stands (a dead server's worker still cannot mark_running); prove again under it.
        _log.warning("selfcheck %s: recovering since %s with no outcome; the gate re-proves it",
                     rid, run.get("updated_at"))
        return _settle(lock, rid)
    # Owner proved dead: fence the row (a dead server's worker cannot mark_running after
    # this), then the single release rule re-proves the server and the tree.
    claimed_at = db_runs.claim_recovering(rid)
    if claimed_at is None:
        return KEEP
    try:
        return _settle(lock, rid)
    except Exception:
        _undo(rid, claimed_at)
        raise


def _settle(lock: dict, rid: str) -> str:
    """Release or protect a run fenced as ``recovering`` (by this pass or one left behind)."""
    current = db_runs.get_run(rid)
    if current is None or current["status"] not in _ACTIVE or current.get("recovery_state") != "recovering":
        return KEEP
    verdict = proof.release_verdict(current)
    if verdict.decision == proof.RELEASE:
        finished = db_runs.finish_recovered_interrupted(rid)
        if finished is None:
            return KEEP
        _emit(finished)
        _log.warning("selfcheck %s: owner dead and tree empty; run ended, G reclaimed", rid)
        return RECLAIM
    incomplete = db_runs.mark_recovery_incomplete(rid, verdict.reason or proof.TREE_UNPROVEN)
    _emit(incomplete)
    return _protect(lock, rid)


def _undo(rid: str, claimed_at: str) -> None:
    """Give back this pass's fence after a failure, so the row is proved anew next time.

    Only a row still ``recovering`` with the claim's own ``updated_at`` moves; an outcome
    already written (incomplete, ended) or any later write keeps the row as it is."""
    try:
        n = db_runs.abandon_recovering(rid, claimed_at)
    except Exception:
        _log.warning("selfcheck %s: undoing the gate's fence failed; a pass after %ss re-proves it",
                     rid, _recovering_stale_sec(), exc_info=True)
        return
    _log.warning("selfcheck %s: the gate failed after its fence; fence undone (rows=%s)", rid, n)


def _recovering_stale_sec() -> float:
    """How long a ``recovering`` row goes unwritten before a pass takes it as left behind.

    Every pass that fences a row ends in seconds (a kill waits 3 s), so the default leaves
    them a wide margin; bounds 30..3600, default 120."""
    try:
        value = float(os.getenv(RECOVERING_STALE_ENV, "120"))
    except ValueError:
        value = 120.0
    return min(max(value, 30.0), 3600.0)


def _left_behind(run: dict) -> bool:
    try:
        written = datetime.fromisoformat(str(run.get("updated_at")))
        age = (datetime.fromisoformat(now_iso()) - written).total_seconds()
    except (TypeError, ValueError):
        return False
    return age >= _recovering_stale_sec()


def _protect(lock: dict, rid: str) -> str:
    # The owner is dead (or its run ended), so no heartbeat of a live holder meets the mark.
    n = gdb.protect_lock(lock["lock_key"], lock["lock_epoch"], PROTECT_REASON, now_iso())
    _log.warning("selfcheck %s: G %s protected by the reclaim gate (rows=%s)", rid, lock["lock_key"], n)
    return PROTECT if n == 1 else KEEP


def _emit(row: dict | None) -> None:
    try:
        from modules.flow_gate.services import tr_self_check_service as service
        service._emit(row)
    except Exception:
        _log.warning("selfcheck run event failed", exc_info=True)
