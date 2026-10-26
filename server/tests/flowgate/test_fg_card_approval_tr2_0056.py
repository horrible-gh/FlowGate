"""flowgate.v02.0056 T#1 — the card approval of a T2 card (T2 + TR2) through the TR2 approval itself.

On the TR2 connected harness of ``tests.e2e.tr2.test_tr2_connected_e2e_0565`` (real SQLite DB with every migration, real Git
worktree, real inbox submission with an issued token, real TR2 approval: precheck, source lock, apply,
validation, ``git commit``, ``tr_commit_ledger``) the card approval's writes (``approval.approve_documents``,
what ``POST /runs/{run_id}/nodes/{node_key}/approval`` runs after its checks) approve the card's T2 and TR2
in order, the instruction first:

- success: the TR2 is applied, validated and committed under the card approval's attempt key;
- validation refused: the change is rolled back, retried once with the server's retry key, the T2 approved
  before it stays approved (``approved_doc_ids``) and the TR2 waits in review;
- the next card approval after the cause is fixed retries it with the server's retry key — not a replay of
  the failure — and approves only what is left (the TR2);
- an approver with ``document.approve`` only (no ``document.update`` / ``own.draft``) approves a rejected TR2
  (put back in review by the card approval itself, then applied and committed).

The harness's token issue puts the token scratch outside the storage root (``token_service._scratch_dir``
patched), which ``token_scratch.create`` refuses (``unsafe_scratch_root_or_path``); here the token is issued
as the server issues it, its scratch under ``<storage root>/work/<project>/`` of the test's own storage root.
The harness's ``store`` is taken over only to apply the migrations as ``test_fg_card_settings`` applies them.
"""
from __future__ import annotations

import shutil
import sqlite3

import pytest

import tests.e2e.tr2.test_tr2_connected_e2e_0565 as harness
from tests.e2e.tr2.test_tr2_connected_e2e_0565 import (  # noqa: F401 — fixtures used by name
    PROJECT, USER, _Db, _QUERIES, _SCHEMA_DIR, auto_approved_t2, api, env, git, git_head, make_group, repo, submit_tr2,
)

pytestmark = [pytest.mark.skipif(shutil.which("git") is None, reason="git binary unavailable"),
              pytest.mark.usefixtures("token_scratch_in_storage")]

APPROVER = "usr_card_approver"        # document.approve without the author's edit permission
APPROVER_ONLY = {"document.read", "document.approve"}
BASE = b"greeting = 'hello'\ncount = 1\n"
APPLIED = b"greeting = 'hi'\ncount = 1\n"


@pytest.fixture
def store(tmp_path, monkeypatch):
    from modules.flow_gate.db import connection as conn_mod
    from modules.flow_gate.db import meta_cache

    monkeypatch.setenv("FLOWGATE_STORAGE_DIR", str(tmp_path / "storage"))
    path = str(tmp_path / "card_approval_tr2.db")
    conn = sqlite3.connect(path)
    for sql_file in sorted(_SCHEMA_DIR.glob("*.sql")):
        try:
            conn.executescript(sql_file.read_text(encoding="utf-8"))
        except sqlite3.OperationalError:
            pass
    conn.commit()
    conn.close()

    class _Store(conn_mod.FlowGateStore):
        def __init__(self):
            self._db = _Db(path)
            self._sq = None

        def _sql(self, key):
            return _QUERIES[key]

    previous = conn_mod.STORE
    conn_mod.STORE = _Store()
    meta_cache.clear_all()
    try:
        yield conn_mod.STORE
    finally:
        conn_mod.STORE = previous
        meta_cache.clear_all()


def _issue_token(group: dict, scope: str, doc_ref: str) -> str:
    """The harness's token issue without its scratch patch: the token scratch lands where the server puts it."""
    from modules.flow_gate.services import token_service

    return token_service.issue(project=PROJECT, group_id=group["group_id"], action_scope=scope, doc_ref=doc_ref,
                               issued_to=USER)["raw_token"]


@pytest.fixture
def token_scratch_in_storage(monkeypatch):
    monkeypatch.setattr(harness, "issue_token", _issue_token)


def _t2_and_tr2(code: str) -> tuple[dict, str, str]:
    group = make_group(code)
    # This consumer suite covers TR2 card approval after its T2 is already approved.
    t2 = auto_approved_t2(group)
    return group, t2, submit_tr2(group, t2)


def _status(doc_id: str):
    from modules.flow_gate.db import documents as db_docs

    return db_docs.get_by_id(doc_id)["doc_review_status"]


def _attempts(doc_id: str) -> list[dict]:
    """Oldest first."""
    from modules.flow_gate.db import tr2_approval_attempts as db_attempts

    return list(reversed(db_attempts.list_by_doc(doc_id)))


def _approve(group: dict, t2: str, tr2: str, user: str = USER) -> list[str]:
    from flowgate.card_approval import approval
    from flowgate.card_approval import legacy_adapter as legacy

    return approval.approve_documents(group["group_id"], [t2, tr2], tr2, user, legacy.permissions_of(user), "ko")


def test_the_card_approval_applies_validates_and_commits_the_tr2(env):
    from modules.flow_gate.db import tr_commit_ledger as db_ledger

    repo = env["repo"]
    group, t2, tr2 = _t2_and_tr2("0301")
    (repo / "gate.ok").write_bytes(b"ok")
    before = git_head(repo)
    assert _approve(group, t2, tr2) == [tr2]                                 # the T2 was approved already
    assert (_status(t2), _status(tr2)) == ("approved", "approved")
    assert git(repo, "rev-parse", "HEAD^").strip() == before                 # applied, validated, committed
    assert (repo / "src" / "app.txt").read_bytes() == APPLIED
    assert [(a["state"], a["request_key"].split(":")[0]) for a in _attempts(tr2)] == [("succeeded", "card-approval")]
    assert [r["commit_sha"] for r in db_ledger.list_by_group(group["group_id"])] == [git_head(repo)]


def test_a_refused_tr2_keeps_the_instruction_approved_and_the_next_card_approval_retries_it(env):
    from modules.flow_gate.graph_workflow.services.errors import GraphApiError
    from modules.flow_gate.workflow.pipeline_service import transition_document_review

    from flowgate.card_approval import legacy_adapter as legacy

    repo = env["repo"]
    group, t2, tr2 = _t2_and_tr2("0302")
    transition_document_review(doc_id=t2, action="mark_revised", actor_user_id=USER,
                               user_permissions=legacy.permissions_of(USER))   # the T2 waits again
    assert _status(t2) == "pending_review"
    before = git_head(repo)

    with pytest.raises(GraphApiError) as refused:                            # gate.ok absent: validation fails
        _approve(group, t2, tr2)
    err = refused.value
    assert (err.status, err.code) == (409, "document_approval_refused")
    assert (err.params["doc_id"], err.params["reason"], err.params["approved_doc_ids"]) == (
        tr2, "tr2_validation_failed", [t2])                                  # partial: the T2 stays approved
    assert (_status(t2), _status(tr2)) == ("approved", "pending_review")
    first = _attempts(tr2)
    assert [(a["state"], a["result_code"], a["error_code"]) for a in first] == [
        ("failed", "recovered_rollback", "tr2_validation_failed")] * 2
    assert first[0]["request_key"].startswith("card-approval:")
    assert first[1]["request_key"] == f"retry:{first[0]['attempt_id']}"     # retried once, the server's retry key
    assert git_head(repo) == before and git(repo, "status", "--porcelain") == ""
    assert (repo / "src" / "app.txt").read_bytes() == BASE

    (repo / "gate.ok").write_bytes(b"ok")                                    # the cause is fixed
    assert _approve(group, t2, tr2) == [tr2]                                 # the rest only
    assert _status(tr2) == "approved"
    again = _attempts(tr2)
    assert len(again) == 3 and again[2]["state"] == "succeeded"
    assert again[2]["request_key"] == f"retry:{first[1]['attempt_id']}"    # a new attempt, not a replay
    assert git(repo, "rev-parse", "HEAD^").strip() == before
    assert (repo / "src" / "app.txt").read_bytes() == APPLIED
    assert _approve(group, t2, tr2) == []                                    # nothing left: idempotent
    assert len(_attempts(tr2)) == 3


def test_an_approver_without_edit_permission_approves_a_rejected_tr2(env, monkeypatch):
    from modules.flow_gate.db import users
    from modules.flow_gate.workflow import pipeline_service
    from modules.flow_gate.workflow.routers import workflow

    from flowgate.card_approval import legacy_adapter as legacy

    users.create({"user_id": APPROVER, "username": "cardapprover", "email": "approver@test.com",
                  "password": "hashed", "is_admin": 0})
    real = workflow._get_user_permissions
    monkeypatch.setattr(workflow, "_get_user_permissions",
                        lambda u: set(APPROVER_ONLY) if (u or {}).get("user_id") == APPROVER else real(u))
    repo = env["repo"]
    group, t2, tr2 = _t2_and_tr2("0303")
    pipeline_service.transition_document_review(doc_id=tr2, action="reject", actor_user_id=USER,
                                                user_permissions=legacy.permissions_of(USER), comment="다시")
    assert _status(tr2) == "rejected"
    with pytest.raises(pipeline_service.PermissionError):                    # v0.1 asks the author's permission
        pipeline_service.transition_document_review(doc_id=tr2, action="mark_revised", actor_user_id=APPROVER,
                                                    user_permissions=set(APPROVER_ONLY), dry_run=True)
    (repo / "gate.ok").write_bytes(b"ok")
    before = git_head(repo)
    assert _approve(group, t2, tr2, APPROVER) == [tr2]
    assert _status(tr2) == "approved"
    assert git(repo, "rev-parse", "HEAD^").strip() == before
    assert (repo / "src" / "app.txt").read_bytes() == APPLIED
    assert [(a["state"], a["actor_user_id"]) for a in _attempts(tr2)] == [("succeeded", APPROVER)]
