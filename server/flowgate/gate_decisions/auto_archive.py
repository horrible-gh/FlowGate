"""flowgate.v02gui.0002 C (v02test.0024.0006-B C-1): a confirm gate's archive ON archives the Flow once its run completes.

The confirm card's ``archive`` switch was the Git checkpoint only (``publish``: ``refs/flowgate/gate-archive/<id>``);
the Flow stayed in the active tree until a person pressed the toolbar [아카이브]. The contract asked for is: archive ON on
a confirm whose approval and its post-processing are done -> that Flow is archived with no extra click.

When: the run's completion (``settle``: ``idle(completed)``). A confirm held by its accepted publish job keeps its run
from completing (``settle._gate_reserved``), so a completed run means every Git apply / archive job of its gates has
ended; with archive ON a confirm always goes through that job (``preflight``: ``needs_work = git_apply or archive``).

How: ``request`` runs inside the completion's Flow guard and only queues the work on ``ctx.jobs``; the work runs after
the commit, outside every guard and transaction, through the same ``flow_archive.change`` as the manual toolbar button
(same target id, the same ``archive_eligibility`` judged again under its own Flow guard, the same archive event to the
tree / tabs / Canvas). It acts as the person who confirmed, with the same write permission check as the route; a person
who lost that permission meanwhile gets no automatic archive. A refusal (not eligible, no permission) leaves the Flow as
it is and is logged; the manual button stays the way out. An archived Flow is answered as it is (a repeat is harmless).
Archive OFF on every confirm of the run: nothing is done. Nothing here touches Git, a decision or a document.
"""
from __future__ import annotations

import logging
from typing import Optional

from flowgate.gate_decisions import repository as repo

_log = logging.getLogger(__name__)


def archiving_actor(flow_id: str, run_id: str) -> Optional[str]:
    """The person of the run's latest succeeded confirm whose frozen settings ask for archive, or None."""
    found = None
    for row in repo.decisions_of_run(flow_id, run_id):
        if row.get("decision") != repo.CONFIRM or row.get("status") != repo.SUCCEEDED:
            continue
        effective = (row.get("settings") or {}).get("effective") or {}
        if effective.get("archive") and row.get("actor_user_id"):
            found = row["actor_user_id"]
    return found


def request(ctx) -> None:
    """``settle``: the run just became ``idle(completed)``, inside its Flow guard. Queues the Flow archive for after
    the commit when a confirm of the run asked for it; never raises into the completion."""
    flow_id, run_id = ctx.flow_id, ctx.run_id
    try:
        if not repo.available():
            return
        actor = archiving_actor(flow_id, run_id)
    except Exception:  # noqa: BLE001 — the completion goes on; the manual button stays
        _log.exception("auto archive of %s: the run's confirms could not be read", flow_id)
        return
    if actor is not None:
        ctx.jobs.append(lambda: archive(flow_id, run_id, actor))


def archive(flow_id: str, run_id: str, actor: str) -> Optional[dict]:
    """After the commit, outside every guard: the manual archive's own path. The archive view, or None (refused)."""
    from modules.flow_gate.graph_workflow.api.envelope import authorize
    from modules.flow_gate.graph_workflow.services import flow_archive
    from modules.flow_gate.graph_workflow.services.errors import GraphApiError
    try:
        view = flow_archive.change(flow_id, actor, True, authorize)
    except GraphApiError as exc:
        _log.info("auto archive of %s (run %s) refused: %s", flow_id, run_id, exc.code)
        return None
    except Exception:  # noqa: BLE001 — a job after the commit never fails the completion it follows
        _log.exception("auto archive of %s (run %s) failed", flow_id, run_id)
        return None
    _log.info("auto archive of %s (run %s) by %s: archived=%s", flow_id, run_id, actor, view.get("archived"))
    return view
