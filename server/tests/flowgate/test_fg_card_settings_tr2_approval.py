"""flowgate.v02.0053 T#2 — after review follow approves a TR2 through the TR2 approval itself.

Both follow paths of the card's review stage (no review, and a passed review) approve with
``flowgate.card_settings.legacy_adapter.approve``. On the TR2 connected harness of
``tests.e2e.tr2.test_tr2_connected_e2e_0565`` (real DB, real Git worktree, real inbox submission, real TR2 approval:
precheck, source lock, apply, validation, commit) it approves as the unmanned chain does: the issuer's own
permissions, the issuer as the server-owned completion principal (``tr2_principal_required`` no longer
stops it), the document revision and a TR2 attempt key. Without the approve permission nothing is
attempted and the TR2 waits for a person; a refused approval (validation failure, rolled back, retried
once with a new key) leaves it waiting too.

The harness's ``store`` is taken over only to apply the migrations as ``test_fg_card_settings`` applies
them: a statement an earlier migration already made (``idx_chat_cmd_run`` of 136, again in 137) must not
stop the setup. Its token issue is taken over too (flowgate.v02.0056 T#1, ``token_scratch_in_storage``): the
harness puts the token scratch outside the storage root, which the token scratch policy refuses.
"""
from __future__ import annotations

import shutil
import sqlite3

import pytest

from test_fg_card_approval_tr2_0056 import token_scratch_in_storage  # noqa: F401 — fixture used by name
from tests.e2e.tr2.test_tr2_connected_e2e_0565 import (  # noqa: F401 — fixtures used by name
    USER, _Db, _QUERIES, _SCHEMA_DIR, auto_approved_t2, api, env, git, git_head, make_group, repo, submit_tr2,
)

pytestmark = [pytest.mark.skipif(shutil.which("git") is None, reason="git binary unavailable"),
              pytest.mark.usefixtures("token_scratch_in_storage")]


@pytest.fixture
def store(tmp_path, monkeypatch):
    from modules.flow_gate.db import connection as conn_mod
    from modules.flow_gate.db import meta_cache

    monkeypatch.setenv("FLOWGATE_STORAGE_DIR", str(tmp_path / "storage"))
    path = str(tmp_path / "card_tr2.db")
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

WORKER = "usr_card_worker"           # an issuer without document.approve
BASE = b"greeting = 'hello'\ncount = 1\n"


def _pending_tr2(code):
    group = make_group(code)
    return group, submit_tr2(group, auto_approved_t2(group))


def test_follow_approves_a_tr2_through_its_own_approval_with_the_issuer_authority(env):
    from modules.flow_gate.db import documents as db_docs
    from modules.flow_gate.db import tr2_approval_attempts as db_attempts

    from flowgate.card_settings import legacy_adapter

    repo = env["repo"]
    group, tr2 = _pending_tr2("0201")
    (repo / "gate.ok").write_bytes(b"ok")
    before = git_head(repo)
    assert legacy_adapter.approve(group["group_id"], tr2, USER) is True
    assert db_docs.get_by_id(tr2)["doc_review_status"] == "approved"
    assert git(repo, "rev-parse", "HEAD^").strip() == before                # the proposal was committed
    assert (repo / "src" / "app.txt").read_bytes() == b"greeting = 'hi'\ncount = 1\n"
    attempts = db_attempts.list_by_doc(tr2)
    assert [(a["state"], a["request_key"].split(":")[0]) for a in attempts] == [("succeeded", "card")]


def test_follow_leaves_a_tr2_to_a_person_when_the_issuer_cannot_approve(env):
    from modules.flow_gate.db import documents as db_docs
    from modules.flow_gate.db import tr2_approval_attempts as db_attempts
    from modules.flow_gate.db import users

    from flowgate.card_settings import legacy_adapter

    users.create({"user_id": WORKER, "username": "cardworker", "email": "worker@test.com",
                  "password": "hashed", "is_admin": 0})
    repo = env["repo"]
    group, tr2 = _pending_tr2("0202")
    (repo / "gate.ok").write_bytes(b"ok")
    before = git_head(repo)
    assert legacy_adapter.approve(group["group_id"], tr2, WORKER) is False
    assert db_docs.get_by_id(tr2)["doc_review_status"] == "pending_review"
    assert db_attempts.list_by_doc(tr2) == [] and git_head(repo) == before
    assert (repo / "src" / "app.txt").read_bytes() == BASE


def test_a_refused_tr2_approval_is_rolled_back_and_left_to_a_person(env):
    from modules.flow_gate.db import documents as db_docs
    from modules.flow_gate.db import tr2_approval_attempts as db_attempts

    from flowgate.card_settings import legacy_adapter

    repo = env["repo"]
    group, tr2 = _pending_tr2("0203")
    before = git_head(repo)                                                  # gate.ok absent: validation fails
    assert legacy_adapter.approve(group["group_id"], tr2, USER) is False
    attempts = db_attempts.list_by_doc(tr2)
    assert [(a["state"], a["error_code"]) for a in attempts] == [("failed", "tr2_validation_failed")] * 2
    assert all(a["request_key"].startswith("card:") for a in attempts)
    assert len({a["request_key"] for a in attempts}) == 2                     # retried once, with a new key
    assert db_docs.get_by_id(tr2)["doc_review_status"] == "pending_review"
    assert git_head(repo) == before and (repo / "src" / "app.txt").read_bytes() == BASE
