"""HTTP adapter of the gate decision history (flowgate.v02.0085 T#2, D0007 §7.2). Mounted under the ``/flowgate``
context next to the Graph run routes, whose path rule it follows (``/runs/{run_id}/nodes/{node_key}/gate-decision``);
same envelope (P0008 §1.2 / §1.3), user JWT only, RBAC read - checked on every call, so a user whose permission was
withdrawn sees nothing of the history any more.

    GET /api/v1/runs/{run_id}/nodes/{node_key}/gate-decisions?cursor=<opaque>&limit=<1..200>

``{"flow_id", "run_id", "node_key", "items": [...], "next_cursor": str | null, "latest": entry | null}``: the gate's
decisions in decision order (``history.history``: every round of the gate in the Flow, the old ``gate_confirmed`` /
``gate_rejected`` events as legacy entries with unknown Git / archive facts), one stable cursor page; ``latest`` is the
current result whatever the page. 404 ``run_not_found`` / ``gate_node_not_found`` (no gate of that key in the run's
revision), 422 for a bad cursor or limit.

flowgate.v02.0085 T#3 (D0007 §7.2): the recovery actions of an accepted decision, RBAC write - checked on every call,
also for a replayed request key, before anything is read of the decision:

    POST /api/v1/runs/{run_id}/nodes/{node_key}/gate-decisions/{decision_id}/retry
    POST /api/v1/runs/{run_id}/nodes/{node_key}/gate-decisions/{decision_id}/cancel
    POST /api/v1/runs/{run_id}/nodes/{node_key}/gate-decisions/{decision_id}/resolve

Body ``{"request_id", "expected_decision_version"}``, resolve also ``"resolution"`` (effects_confirmed | abandon),
``"reason"`` (required) and the evidence the person reviewed (required): ``"evidence_version"`` and
``"evidence_refs"`` (the decision's ``recovery.evidence_refs``, ``<decision>/<phase>@<version>`` each; an empty list
when it has no evidence row). 422 for a missing or bad field, 403 without the group's
write permission, 404 ``run_not_found`` / ``gate_decision_not_found`` (no such decision of that run and node - a
decision id of another run or node is not found here). The answers and refusals are ``recovery``'s.
"""
from __future__ import annotations

import re
from typing import Any, Optional

from fastapi import APIRouter, Body, Request

from flowgate.gate_decisions import history
from flowgate.gate_decisions import legacy_adapter as legacy
from flowgate.gate_decisions import recovery, repository

router = APIRouter(prefix="/api/v1", tags=["GateDecisions"])
# ASCII digits only, at most as long as the largest limit: ``str.isdigit`` takes ``²`` and other digits ``int`` refuses,
# and an unbounded run of digits is no limit either - both are 422, never a 500.
LIMIT_TEXT = re.compile(r"[0-9]{1,%d}" % len(str(history.MAX_LIMIT)))


def parse_history_page(cursor: Optional[str], limit: Optional[str]) -> tuple[tuple[Optional[str], int], list[dict]]:
    errors: list[dict] = []
    size = history.DEFAULT_LIMIT
    if limit is not None:
        text = str(limit).strip()
        value = int(text) if LIMIT_TEXT.fullmatch(text) else None
        if value is None or not (1 <= value <= history.MAX_LIMIT):
            errors.append({"field": "limit", "reason": f"not_in_range:1,{history.MAX_LIMIT}"})
        else:
            size = value
    if cursor is not None:
        try:
            history.decode_cursor(cursor)
        except ValueError:
            errors.append({"field": "cursor", "reason": "invalid_cursor"})
    return (cursor or None, size), errors


@router.get("/runs/{run_id}/nodes/{node_key}/gate-decisions")
def get_gate_decisions(run_id: str, node_key: str, request: Request, cursor: Optional[str] = None,
                       limit: Optional[str] = None):
    def handle() -> dict:
        user_id = legacy.authenticate(request)
        legacy.client_id_of(request)
        (page_cursor, size), errors = parse_history_page(cursor, limit)
        if errors:
            raise legacy.invalid(request, errors)
        flow_id = legacy.run_flow_id(run_id)
        group = legacy.load_flow(flow_id)
        legacy.authorize(user_id, group, "read")
        run = legacy.run_store.run_by_id(run_id)
        if run is None:
            raise legacy.GraphApiError(404, "run_not_found", {"run_id": run_id})
        if legacy.node_types(flow_id, int(run["revision_no"])).get(node_key) != "gate":
            raise legacy.GraphApiError(404, "gate_node_not_found", {"run_id": run_id, "node_key": node_key})
        page = history.history(flow_id, node_key, cursor=page_cursor, limit=size)
        return {"flow_id": flow_id, "run_id": run_id, "node_key": node_key, **page}

    return legacy.run(request, handle)


# ── flowgate.v02.0085 T#3: retry / cancel / resolve ──────────────────────────

REQUEST_ID_MAX_LEN = 128
REASON_MAX_LEN = 2000


def _text(raw: dict, name: str, limit: int, errors: list[dict], *, required: bool) -> Optional[str]:
    value = raw.get(name)
    if value is None:
        if required:
            errors.append({"field": name, "reason": "required"})
        return None
    if not isinstance(value, str):
        errors.append({"field": name, "reason": "not_a_string"})
    elif not value.strip() or any(ord(c) < 32 and c not in "\n\t" for c in value):
        errors.append({"field": name, "reason": "invalid_format"})
    elif len(value) > limit:
        errors.append({"field": name, "reason": "too_long"})
    else:
        return value
    return None


def _version(raw: dict, name: str, errors: list[dict], *, required: bool) -> Optional[int]:
    value = raw.get(name)
    if value is None:
        if required:
            errors.append({"field": name, "reason": "required"})
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        errors.append({"field": name, "reason": "not_a_version"})
        return None
    return value


def parse_recovery(action: str, body: Any) -> tuple[dict, list[dict]]:
    if not isinstance(body, dict):
        return {}, [{"field": "body", "reason": "not_an_object"}]
    errors: list[dict] = []
    out = {"request_id": _text(body, "request_id", REQUEST_ID_MAX_LEN, errors, required=True),
           "expected_decision_version": _version(body, "expected_decision_version", errors, required=True)}
    if out["request_id"] is not None and out["request_id"] != out["request_id"].strip():
        errors.append({"field": "request_id", "reason": "invalid_format"})
    if action == repository.RESOLVE:
        resolution = body.get("resolution")
        if resolution not in recovery.RESOLUTIONS:
            errors.append({"field": "resolution", "reason": "not_in:" + ",".join(recovery.RESOLUTIONS)})
        out["resolution"] = resolution
        out["reason"] = _text(body, "reason", REASON_MAX_LEN, errors, required=True)
        # T#3 rework (D0007 §7.2): the evidence the person reviewed - its version and its references - is required.
        out["evidence_version"] = _version(body, "evidence_version", errors, required=True)
        out["evidence_refs"] = _refs(body, "evidence_refs", errors)
    return out, errors


EVIDENCE_REF = re.compile(r"^[A-Za-z0-9_.:-]{1,64}/[a-z_]{1,32}@[0-9]{1,9}$")
EVIDENCE_REFS_MAX = 64


def _refs(raw: dict, name: str, errors: list[dict]) -> Optional[list]:
    """``evidence_refs``: a list (empty when the decision has no evidence row) of ``<decision>/<phase>@<version>``."""
    value = raw.get(name)
    if value is None:
        errors.append({"field": name, "reason": "required"})
        return None
    if not isinstance(value, list) or len(value) > EVIDENCE_REFS_MAX:
        errors.append({"field": name, "reason": "not_a_list"})
        return None
    if any(not isinstance(v, str) or not EVIDENCE_REF.match(v) for v in value) or len(set(value)) != len(value):
        errors.append({"field": name, "reason": "invalid_format"})
        return None
    return list(value)


def _recovery_action(action: str, run_id: str, node_key: str, decision_id: str, request: Request, body: Any):
    def handle() -> dict:
        user_id = legacy.authenticate(request)
        legacy.client_id_of(request)
        parsed, errors = parse_recovery(action, body)
        if errors:
            raise legacy.invalid(request, errors)
        flow_id = legacy.run_flow_id(run_id)
        group = legacy.load_flow(flow_id)
        legacy.authorize(user_id, group, "write")            # every call, a replay too, before the decision is read
        decision = repository.get(decision_id)
        if decision is None or decision["run_id"] != run_id or decision["node_key"] != node_key \
                or decision["group_id"] != flow_id:
            raise legacy.GraphApiError(404, "gate_decision_not_found", {"run_id": run_id, "node_key": node_key,
                                                                        "decision_id": decision_id})
        return getattr(recovery, action)(run_id, node_key, decision_id, user_id, parsed)

    return legacy.run(request, handle)


@router.post("/runs/{run_id}/nodes/{node_key}/gate-decisions/{decision_id}/retry")
def post_gate_decision_retry(run_id: str, node_key: str, decision_id: str, request: Request,
                             body: Any = Body(default=None)):
    return _recovery_action(repository.RETRY, run_id, node_key, decision_id, request, body)


@router.post("/runs/{run_id}/nodes/{node_key}/gate-decisions/{decision_id}/cancel")
def post_gate_decision_cancel(run_id: str, node_key: str, decision_id: str, request: Request,
                              body: Any = Body(default=None)):
    return _recovery_action(repository.CANCEL, run_id, node_key, decision_id, request, body)


@router.post("/runs/{run_id}/nodes/{node_key}/gate-decisions/{decision_id}/resolve")
def post_gate_decision_resolve(run_id: str, node_key: str, decision_id: str, request: Request,
                               body: Any = Body(default=None)):
    return _recovery_action(repository.RESOLVE, run_id, node_key, decision_id, request, body)
