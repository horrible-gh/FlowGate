"""TR2 lifecycle, proposal recovery, DB CAS and readiness (0565 T0030), connected.

Same real boundaries as ``tests.e2e.tr2.test_tr2_connected_e2e_0565``: SQLite with every migration and a
connection per statement, a real Git worktree in scratch, the real HTTP routes. The
proposal-recovery scenarios damage the stored files exactly the way an operator or a
disk would, then go through the routes a screen uses.
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys

import pytest

from tests.e2e.tr2.test_tr2_connected_e2e_0565 import (  # noqa: F401 — fixtures are used by name
    CREATED, GATE_OK, MODULE, PROJECT, USER, api, approve, auto_approved_t2, edit_spec, env,
    git, make_group, repo, store, submit_tr2, tr2_body, view,
)


def full_api():
    """``api()`` plus the generic documents router (the "create next document" path)."""
    from modules.flow_gate.documents.routers import documents as documents_routes
    client = api()
    client.app.include_router(documents_routes.router, prefix="/api/v1")
    return client


def doc_row(doc_id: str) -> dict:
    from modules.flow_gate.db import documents as db_docs
    return db_docs.get_by_id(doc_id)


def canonical(doc_id: str):
    from modules.flow_gate.documents import tr2_service
    return tr2_service.canonical_path_for_doc(doc_row(doc_id))


def snapshot(doc_id: str, revision: int):
    from modules.flow_gate.documents import tr2_service
    return tr2_service.snapshot_path(doc_row(doc_id), revision)


def recovery(client, doc_id: str) -> dict:
    resp = client.get(f"/api/v1/documents/{doc_id}/tr2/recovery")
    assert resp.status_code == 200, resp.text
    return resp.json()


def assert_no_host_path(resp, *roots) -> None:
    text = resp.text
    assert ":\\\\" not in text and ":/" not in text.replace("://", ""), text
    for root in roots:
        assert str(root) not in text and str(root).replace("\\", "/") not in text, text


def next_empty(client, group: dict, prev_doc_id: str, title: str = "반영안"):
    return client.post("/api/v1/documents/next-empty", json={
        "project_id": PROJECT, "group_id": group["group_id"], "prev_doc_id": prev_doc_id,
        "type_code": "TR2", "title": title, "module": MODULE})


# ── §4 one creation contract ─────────────────────────────────────────────────────

def test_human_next_empty_creates_the_canonical_proposal(env):
    """The route that produced ``0005-TR2_document.md`` now writes revision 1 canonically."""
    from modules.flow_gate.documents import tr2_service
    group = make_group("0101")
    client = full_api()
    t2 = auto_approved_t2(group)

    resp = next_empty(client, group, t2)
    assert resp.status_code == 201, resp.text
    doc_id = resp.json()["doc_id"]
    row = doc_row(doc_id)
    assert row["file_path"] == tr2_service.canonical_relative_path(row)
    assert row["file_path"].endswith("_document.json") and row["revision_no"] == 1
    assert row["doc_review_status"] == "pending_review"
    stored = canonical(doc_id).read_bytes()
    assert snapshot(doc_id, 1).read_bytes() == stored  # the recovery source of revision 1

    first = view(client, doc_id)
    assert first["body"]["source_t2_doc_id"] == t2
    assert first["body"]["edit_spec"]["termination"] == "needs_more_work"
    # an empty proposal is never "ready": the server says why
    assert first["readiness"] == {**first["readiness"], "ready": False, "reason": "needs_more_work"}

    # human CRUD on the created proposal reaches an authoritatively ready revision
    base = f"/api/v1/documents/{doc_id}/tr2"
    resp = client.put(base, json={"expected_revision": 1, "body": tr2_body(t2)})
    assert resp.status_code == 200 and resp.json()["new_revision"] == 2, resp.text
    assert view(client, doc_id)["readiness"]["ready"] is True

    # and the AI inbox path follows the same contract (row path = canonical file)
    group2 = make_group("0102")
    t2b = auto_approved_t2(group2)
    ai = submit_tr2(group2, t2b)
    ai_row = doc_row(ai)
    assert ai_row["file_path"] == tr2_service.canonical_relative_path(ai_row)
    assert snapshot(ai, 1).read_bytes() == canonical(ai).read_bytes()
    revs = recovery(client, ai)["revisions"]
    assert [(r["revision_no"], r["origin"], r["usable"]) for r in revs] == [(1, "ai", True)]


def test_legacy_markdown_tr2_is_reported_then_converted_explicitly(env):
    """A TR2 registered as a Markdown skeleton (pre-T0030 next-empty) is not guessed at."""
    from modules.flow_gate.db import documents as db_docs
    from modules.flow_gate.db.connection import now_iso
    from modules.flow_gate.storage import paths as storage_paths
    from modules.flow_gate.workflow.pipeline_service import (
        register_workflow_result, transition_document_review)

    group = make_group("0103")
    client = full_api()
    t2 = auto_approved_t2(group)
    doc_id = f"{group['group_id']}.0003-TR2"
    legacy = storage_paths.document_path(PROJECT, group["group_id"], "0003-TR2", "document.md",
                                         module=MODULE, branch="main")
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_bytes("---\ntype: TR2\ntitle: 변경제안\n---\n".encode("utf-8"))
    rel = storage_paths.to_storage_relative(legacy, PROJECT)
    db_docs.create({"doc_id": doc_id, "project_id": PROJECT, "module": MODULE,
                    "group_id": group["group_id"], "type_code": "TR2", "seq": 3,
                    "title": "변경제안", "status": "draft", "target_id": t2,
                    "triggered_by": t2, "owner_id": USER, "file_path": rel})
    head = [i for i in group["items"] if i["type"] == "TR2"][0]
    register_workflow_result(item_id=head["id"], registered_path=rel, registered_doc_id=doc_id,
                             registered_at=now_iso(), actor_user_id=USER)
    transition_document_review(doc_id=doc_id, action="submit", actor_user_id=USER,
                               user_permissions={"document.update"})

    resp = client.get(f"/api/v1/documents/{doc_id}/tr2")
    assert resp.status_code == 409 and resp.json()["code"] == "tr2_storage_mismatch", resp.text
    assert resp.json()["recovery"] is True
    assert_no_host_path(resp, env["repo"])
    state = recovery(client, doc_id)
    assert state["state"] == "tr2_storage_mismatch" and state["recoverable"] is False
    assert state["approval_blocked"] is True and state["raw"]["available"] is True
    raw = client.get(f"/api/v1/documents/{doc_id}/tr2/raw").json()
    assert raw["content"].startswith("---\ntype: TR2") and raw["filename"] == legacy.name
    blocked = approve(client, doc_id, 0, request_key="legacy:0103")
    assert blocked.status_code == 409 and blocked.json()["code"] == "tr2_storage_mismatch"

    # explicit conversion: a new, empty (never approvable) canonical proposal
    resp = client.post(f"/api/v1/documents/{doc_id}/tr2/recovery/new", json={"expected_revision": 0})
    assert resp.status_code == 200 and resp.json()["new_revision"] == 1, resp.text
    converted = view(client, doc_id)
    assert converted["body"]["source_t2_doc_id"] == t2
    assert doc_row(doc_id)["file_path"].endswith("0003-TR2_document.json")
    assert legacy.is_file()  # the old skeleton is kept, never deleted


# ── §6 proposal revision recovery ────────────────────────────────────────────────

def _proposal_with_history(client, group: dict):
    t2 = auto_approved_t2(group)
    doc_id = submit_tr2(group, t2)                                       # r1 (AI)
    base = f"/api/v1/documents/{doc_id}/tr2"
    assert client.post(f"{base}/items", json={"expected_revision": 1, "collection": "edits",
                                              "item": CREATED}).status_code == 200   # r2
    assert client.delete(f"{base}/items/d1", params={"expected_revision": 2}).status_code == 200  # r3
    return t2, doc_id, base


def test_missing_current_file_recovers_from_an_exact_snapshot(env):
    from modules.flow_gate.db import tr2_approval_attempts as db_attempts
    group = make_group("0104")
    client = full_api()
    _t2, doc_id, base = _proposal_with_history(client, group)
    r2 = json.loads(snapshot(doc_id, 2).read_bytes())
    assert [r["revision_no"] for r in recovery(client, doc_id)["revisions"]] == [3, 2, 1]

    canonical(doc_id).unlink()
    resp = client.get(base)
    assert resp.status_code == 409 and resp.json()["code"] == "tr2_body_missing", resp.text
    assert_no_host_path(resp, env["repo"], canonical(doc_id).parent)
    state = recovery(client, doc_id)
    assert state["state"] == "tr2_body_missing" and state["recoverable"] is True
    assert state["recommended_revision_no"] == 3 and state["raw"]["available"] is False
    assert all(r["usable"] for r in state["revisions"])
    # nothing moves while the proposal is unreadable: no approval, no partial edit
    blocked = approve(client, doc_id, 3, request_key="missing:0104")
    assert blocked.status_code == 409 and blocked.json()["code"] == "tr2_body_missing"
    assert db_attempts.list_by_doc(doc_id) == []
    edit = client.post(f"{base}/items", json={"expected_revision": 3, "collection": "edits",
                                              "item": dict(CREATED, id="c9")})
    assert edit.status_code == 409 and edit.json()["code"] == "tr2_body_missing"

    # restore r2: a NEW revision (4); history is not rewritten
    stale = client.post(f"{base}/recovery/restore", json={"expected_revision": 2, "revision_no": 2})
    assert stale.status_code == 409 and stale.json()["code"] == "tr2_spec_changed"
    resp = client.post(f"{base}/recovery/restore", json={"expected_revision": 3, "revision_no": 2})
    assert resp.status_code == 200, resp.text
    assert resp.json()["new_revision"] == 4 and resp.json()["restored_from_revision"] == 2
    restored = view(client, doc_id)
    assert restored["document"]["revision_no"] == 4
    assert restored["body"]["edit_spec"] == r2["edit_spec"]
    assert restored["body"]["baseline_fingerprint"] == r2["baseline_fingerprint"]  # recomputed, same source
    assert restored["readiness"]["ready"] is True
    assert snapshot(doc_id, 4).read_bytes() == canonical(doc_id).read_bytes()
    assert [r["revision_no"] for r in recovery(client, doc_id)["revisions"]] == [4, 3, 2, 1]
    missing = client.post(f"{base}/recovery/restore", json={"expected_revision": 4, "revision_no": 99})
    assert missing.status_code == 404 and missing.json()["code"] == "tr2_revision_not_found"






# ── §8 DB-level CAS across connections and processes ────────────────────────────

def _db_path(env) -> str:
    return env["store"]._db._path






# ── §7 authoritative readiness ───────────────────────────────────────────────────












@pytest.fixture(autouse=True)
def dedicated_document_write_ports_0127(monkeypatch):
    # Direct service/route fixtures reproduce the composition root's required ports.
    from modules.flow_gate.graph_workflow.services import card_document_ports
    from flowgate.card_document_edit import integration as document_edit
    monkeypatch.setattr(card_document_ports, "_handlers", dict(card_document_ports._handlers))
    document_edit.register()
