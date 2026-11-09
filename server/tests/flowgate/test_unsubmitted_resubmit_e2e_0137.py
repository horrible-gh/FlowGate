"""flowgate.v02.0137 T0004 §5-A4 / TR0005 rev1 — A end to end through the REAL inbox.

Real SQLite with every migration, real token issue / verify / dry-run counter / consume, the
real registered edit service (document revision, revision history, workflow events), the
real token and run scratch directories under an isolated storage root, the real ledger
hook in POST /inbox and the real finalize / carry-over code. The doubles are only the ones
the 0127 card-edit fixtures already use (auth display card, notifications, write policy).
No AI provider is launched: the "worker" is this test, posting the way a CLI worker does.

Paths walked (T0004 §5-A1/A3/A4, review rej_01M6Q5JFT9ZRCVFB findings 1-5):
  A. run R1, token T1: failed submit (422) -> dry-run -> same-token retry lands (rev 1) ->
     the worker revises -> resubmits with the spent token (401) -> finalize keeps the
     scratch (success first, failure last) and its newest draft.
  C. follow-up run R3 on the same document: R1's retained drafts are carried into R3's
     token scratch (credential files refused), resubmitted with R3's token (rev 2).
  B. run R2: token T2 revoked mid-run -> _prepare_retry_token reissues T3, carries T2's draft,
     the old path is refused for T3, the carried path lands (rev 3), finalize settles it
     (0157: settled runs keep their scratch until the system TTL sweep, no carry-over).
"""
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from test_card_document_edit_0127 import env, F, D, HEADER  # noqa: F401  (fixture re-export)
from modules.flow_gate.api import inbox_routes as inbox
from modules.flow_gate.db import ai_invoke_runs as db_runs
from modules.flow_gate.db import document_revisions, documents
from modules.flow_gate.db.connection import now_iso
from modules.flow_gate.services import ai_invoke_service as svc
from modules.flow_gate.services import graph_source_hooks, token_service
from modules.flow_gate.services.ai_invoke import carryover, finalize, submission_ledger, worker
from flowgate.card_document_edit import integration, legacy_edit

try:  # flowgate.v02.0138 (merged after this group's base): runs own their id in the DB
    from modules.flow_gate.db import ai_run_identity as identity
except ImportError:  # pragma: no cover - depends on the merge state
    identity = None

PROJECT = "flowgate"
USER = "user"
R1, R3, R2 = "aiv_29990102_000001", "aiv_29990102_000002", "aiv_29990102_000003"


@pytest.fixture
def e2e(env, monkeypatch):  # noqa: F811
    monkeypatch.setenv("FLOWGATE_TOKEN_PEPPER_ACTIVE_ID", "test")
    monkeypatch.setenv("FLOWGATE_TOKEN_PEPPER_test", "e2e-pepper-0137")
    integration.register()
    monkeypatch.setattr(legacy_edit, "has_permission", lambda *a: True)
    monkeypatch.setattr(graph_source_hooks, "_fence_handler", None)
    from modules.flow_gate.services import node_title_hooks
    monkeypatch.setattr(node_title_hooks, "after_registration", lambda *a: None)
    monkeypatch.setattr(worker.db_group_ai_leases, "update_token", lambda *a, **k: None)
    # Order guard: in a large selection an earlier suite can leave this module's imported
    # `get_store` bound to its own fake store (seen as "'Store' object has no attribute
    # 'table_exists'"); this test runs on the env fixture's real store only.
    from modules.flow_gate.db import connection
    from flowgate.card_documents import decision_repository
    monkeypatch.setattr(decision_repository, "get_store", connection.get_store)
    if identity is not None:
        identity.initialize(scratch_roots=[], all_issuers_stopped=True)
    app = FastAPI()
    app.include_router(inbox.router, prefix="/flowgate")
    added: list[str] = []
    yield SimpleNamespace(env=env, client=TestClient(app), added=added)
    with svc._runs_lock:
        for run_id in added:
            svc._runs.pop(run_id, None)


def _evidence(phase: str, **data) -> None:
    print("E2E_0137 " + json.dumps({"phase": phase, **data}, ensure_ascii=True, sort_keys=True))


def _post(e2e, raw: str, **body):
    payload = {"action": "edit", "project": PROJECT, "module": "v02", "group_name": F, "doc_id": D,
               "edit_reason": "worker_self", **body}
    return e2e.client.post("/flowgate/api/v1/inbox", headers={"Authorization": "Bearer " + raw},
                           json=payload)


def _issue(run_id: str) -> dict:
    issued = token_service.issue(PROJECT, F, "edit", D, USER, ai_run_id=run_id)
    issued["scratch_dir"] = token_service.verify(issued["raw_token"])["scratch_dir"]
    issued["mention"] = "## prompt"
    return issued


def _start(e2e, run_id: str) -> tuple[dict, dict]:
    started_at = now_iso()
    nonce = None
    if identity is not None:
        nonce = str(uuid4())
        identity.claim({"run_id": run_id, "execution_nonce": nonce, "project_id": PROJECT,
                        "group_id": F, "started_at": started_at})
    scratch = svc._create_scratch(PROJECT, run_id, nonce) if nonce else svc._create_scratch(PROJECT, run_id)
    issued = _issue(run_id)
    assert carryover.bind_run_scratch(PROJECT, run_id, scratch, group_id=F, doc_ref=D,
                                      issued_to=USER, token_id=issued["token_id"])
    run = {"run_id": run_id, "project_id": PROJECT, "group_id": F, "doc_ref": D, "issued_to": USER,
           "mode": "single", "action_scope": "edit", "hop_kind": "work", "attempt_no": 1,
           # current_token_id is NOT seeded: admission does not set it either; the inbox
           # binding seeds it from token_id on the first submission (worker._run_register_context).
           "token_id": issued["token_id"],
           "raw_token": issued["raw_token"], "token_scratch_dir": issued["scratch_dir"],
           "scratch_dir": str(scratch), "started_at": started_at, "execution_nonce": nonce,
           "register_errors": [],
           "reached_doc_ids": [], "outcome": None, "scratch_retained": None, "mention": "## prompt"}
    with svc._runs_lock:
        svc._runs[run_id] = run
    e2e.added.append(run_id)
    return run, issued


def _end(run: dict) -> None:
    """What finalize persists for the scratch decision, then the run leaves the live set."""
    run["outcome"] = "complete"          # the stale-judge case: complete whatever happened
    finalize._finalize_scratch(run)
    run["finished_at"] = now_iso()
    db_runs.upsert({**run, "docs_target": 1, "docs_reached": 1, "created_at": run["started_at"],
                    "updated_at": run["finished_at"]})
    with svc._runs_lock:
        svc._runs.pop(run["run_id"], None)


def _token_row(token_id: str) -> dict:
    from modules.flow_gate.db import tokens as db_tokens
    return db_tokens.get_by_id(token_id)


def _ledger(run: dict) -> list:
    return [(e["ok"], e["status"]) for e in run.get(submission_ledger.EVENTS_KEY) or []]


def test_unsubmitted_drafts_survive_and_resubmit_with_the_current_token(e2e):
    # ── A: failure -> dry-run -> same-token retry -> revise -> spent-token failure ──────────
    r1, t1 = _start(e2e, R1)
    t1_dir = Path(t1["scratch_dir"])
    v1 = t1_dir / "draft_v1.md"
    v1.write_text(HEADER + "v1 body\n", encoding="utf-8")
    failed = _post(e2e, t1["raw_token"], doc_path=str(v1))            # expected_revision missing
    assert failed.status_code == 422, failed.text
    assert submission_ledger.hop_events(r1)[-1]["status"] == 422
    assert finalize._submission_settlement({**r1, "outcome": "complete"})[:2] == (
        False, "last_submission_failed")
    dry = _post(e2e, t1["raw_token"], doc_path=str(v1), expected_revision=0, dry_run=True)
    assert dry.status_code == 200 and dry.json()["dry_run"] is True
    assert token_service.verify(t1["raw_token"])["dry_run_count"] == 1          # not consumed
    assert documents.get_by_id(D)["revision_no"] == 0
    assert len(_ledger(r1)) == 1                                                 # dry-run unrecorded
    landed = _post(e2e, t1["raw_token"], doc_path=str(v1), expected_revision=0)
    assert landed.status_code == 200, landed.text
    assert landed.json()["revision_no"] == 1
    assert _token_row(t1["token_id"])["consumed_at"]
    assert finalize._submission_settlement({**r1, "outcome": "complete"})[:2] == (
        True, "submitted_after_error")
    _evidence("A.retry", statuses=[failed.status_code, dry.status_code, landed.status_code],
              revision=documents.get_by_id(D)["revision_no"], ledger=_ledger(r1),
              t1_consumed=bool(_token_row(t1["token_id"])["consumed_at"]))
    # the worker writes a better draft and tries again with the token it already spent
    v2_text = HEADER + "v2 body - revised after the first submission\n"
    v2 = t1_dir / "draft_v2.md"
    v2.write_text(v2_text, encoding="utf-8")
    (Path(r1["scratch_dir"]) / "notes_cwd.md").write_text("cwd notes", encoding="utf-8")
    (t1_dir / "request.json").write_text(json.dumps(
        {"headers": {"Authorization": "Bearer " + t1["raw_token"]}}), encoding="utf-8")
    (t1_dir / "token.txt").write_text(t1["raw_token"] + "\n", encoding="utf-8")
    spent = _post(e2e, t1["raw_token"], doc_path=str(v2), expected_revision=1)
    assert spent.status_code == 401
    assert _ledger(r1) == [(False, 422), (True, 200), (False, 401)]
    _end(r1)
    assert r1["scratch_cleanup"]["action"] == "retained"
    assert r1["scratch_cleanup"]["reason"] == "last_submission_failed"
    assert Path(r1["scratch_dir"]).is_dir() and v2.read_text(encoding="utf-8") == v2_text
    assert documents.get_by_id(D)["revision_no"] == 1                           # v2 never landed
    _evidence("A.retained", spent_status=spent.status_code, cleanup=r1["scratch_cleanup"])

    # ── C: a follow-up run gets R1's drafts and resubmits with ITS token ─────────────────
    r3, t3 = _start(e2e, R3)
    mention, record = carryover.carry_into_new_run(
        project_id=PROJECT, group_id=F, doc_ref=D, issued_to=USER, run_id=R3,
        action_scope="edit", issue=t3, mention="## prompt", run_live=svc.is_run_live)
    assert record["status"] == "copied", record
    assert carryover.NOTICE_HEADING in mention and t1["raw_token"] not in mention
    dest = Path(t3["scratch_dir"]) / "carryover" / f"run_{R1}"
    carried_v2 = dest / f"token_scratch_{t1['token_id']}" / "draft_v2.md"
    assert carried_v2.read_text(encoding="utf-8") == v2_text
    assert (dest / f"run_scratch_{R1}" / "notes_cwd.md").is_file()
    manifest = json.loads((dest / "CARRYOVER.json").read_text(encoding="utf-8"))
    refused = {Path(s["path"]).name: s["reason"] for s in manifest["skipped"]}
    assert refused == {"request.json": "contains_credential", "token.txt": "contains_token"}
    assert t1["raw_token"] not in json.dumps(manifest)
    assert inbox.validate_doc_path(str(carried_v2), t3["scratch_dir"]) is True
    assert inbox.validate_doc_path(str(v2), t3["scratch_dir"]) is False
    old_path = _post(e2e, t3["raw_token"], doc_path=str(v2), expected_revision=1)
    assert 400 <= old_path.status_code < 500 and documents.get_by_id(D)["revision_no"] == 1
    resubmitted = _post(e2e, t3["raw_token"], doc_path=str(carried_v2), expected_revision=1)
    assert resubmitted.status_code == 200, resubmitted.text
    assert resubmitted.json()["revision_no"] == 2
    assert _token_row(t3["token_id"])["consumed_at"]
    _end(r3)
    # 0157: a settled run keeps its scratch until the system TTL; it only stops being a
    # carry-over source (scratch_retained stays empty).
    assert r3["scratch_cleanup"] == {**r3["scratch_cleanup"], "action": "retained",
                                     "reason": "settled_retention_active",
                                     "submission": "submitted_after_error"}
    assert Path(r3["scratch_dir"]).is_dir() and not r3["scratch_retained"]
    assert Path(r1["scratch_dir"]).is_dir()                       # the source is never touched
    _evidence("C.follow_up", carry=record, old_path_status=old_path.status_code,
              resubmit_status=resubmitted.status_code,
              revision=documents.get_by_id(D)["revision_no"], ledger=_ledger(r3),
              cleanup=r3["scratch_cleanup"]["submission"])

    # ── B: same run, token replaced mid-run ─────────────────────────────────────────────
    r2, t2 = _start(e2e, R2)
    t2_draft = Path(t2["scratch_dir"]) / "draft_r2.md"
    r2_text = HEADER + "v3 body - written under the token that was replaced\n"
    t2_draft.write_text(r2_text, encoding="utf-8")
    token_service.revoke(t2["token_id"], reason="e2e_0137_reissue")
    holder: dict = {}

    def issue_builder(ai_run_id=None):
        holder.update(_issue(ai_run_id or R2))
        return dict(holder)

    r2["issue_builder"] = issue_builder
    prepared = worker._prepare_retry_token(r2)
    assert prepared["reissued"] is True and prepared["token_id"] == holder["token_id"]
    assert carryover.NOTICE_HEADING in prepared["mention"]
    owner = json.loads((Path(r2["scratch_dir"]) / carryover.RUN_OWNER_NAME).read_text(encoding="utf-8"))
    assert owner["token_ids"] == [t2["token_id"], holder["token_id"]]
    carried = (Path(holder["scratch_dir"]) / "carryover" / f"hop2_{t2['token_id']}"
               / f"token_scratch_{t2['token_id']}" / "draft_r2.md")
    assert carried.read_text(encoding="utf-8") == r2_text
    revoked = _post(e2e, t2["raw_token"], doc_path=str(t2_draft), expected_revision=2)
    assert revoked.status_code == 401
    landed_b = _post(e2e, holder["raw_token"], doc_path=str(carried), expected_revision=2)
    assert landed_b.status_code == 200, landed_b.text
    assert landed_b.json()["revision_no"] == 3
    assert _ledger(r2) == [(False, 401), (True, 200)]
    _end(r2)
    assert r2["scratch_cleanup"]["action"] == "retained"
    assert r2["scratch_cleanup"]["reason"] == "settled_retention_active"
    assert Path(r2["scratch_dir"]).is_dir()
    _evidence("B.reissue", revoked_status=revoked.status_code, landed_status=landed_b.status_code,
              revision=documents.get_by_id(D)["revision_no"], ledger=_ledger(r2),
              cleanup=r2["scratch_cleanup"]["submission"])

    # ── history: one revision and one consumed token per landed submission ───────────────
    revisions = document_revisions.list_by_doc(D)
    assert len(revisions) == 3
    events = e2e.env.db._fetch_all(
        "SELECT event_type, metadata FROM workflow_events WHERE event_type IN "
        "('doc_edited', 'token_consumed') ORDER BY rowid")
    edited = [json.loads(e["metadata"] or "{}").get("token_id") for e in events
              if e["event_type"] == "doc_edited"]
    assert edited == [t1["token_id"], t3["token_id"], holder["token_id"]]
    assert sum(1 for e in events if e["event_type"] == "token_consumed") == 3
    body = Path(e2e.env.root / "storage").rglob("*.md")
    _evidence("history", revisions=len(revisions), doc_edited_tokens=edited,
              token_consumed=sum(1 for e in events if e["event_type"] == "token_consumed"),
              final_revision=documents.get_by_id(D)["revision_no"], md_files=len(list(body)))
