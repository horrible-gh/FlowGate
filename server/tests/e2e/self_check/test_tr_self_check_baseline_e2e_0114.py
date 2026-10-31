"""flowgate.v02.0114 T#3: baseline reuse end to end on real Self-check runs (D 0006 9.2-5 (a)(f),
9.2-6 (a)(b)(c)(d)).

One work unit, through the real pieces: ``token_service.issue`` records the pre-change anchor
of a throwaway git repository standing in for the managed group worktree; real Self-check
runs (policy, Group lock, git probe, worker, child process) execute a small pytest tree that
carries the real result block plugin; ``_finish`` stores the summary; the read path judges a
named baseline. Then the change is committed, the TR registers (``token_service.consume``) and
a rework token is issued -- the first baseline keeps serving every final run.

Replaced: the worktree resolver, the token scratch directory, the pepper and the SSE emit.
"""
from __future__ import annotations

import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from modules.flow_gate.db import connection
from modules.flow_gate.db import tr_self_check_anchors as db_anchors
from modules.flow_gate.services import token_service
from modules.flow_gate.services import tr_self_check_result as result_block
from modules.flow_gate.services import tr_self_check_service as service

P, G = "p_0114e", "g_0114e_1"
SEQ_DOC, T_DOC, TR_DOC = "d_0114e_b", "d_0114e_t", "d_0114e_tr"
PEPPER_ID, PEPPER = "E0114", "pepper-0114-e2e"
_TESTS_DIR = Path(__file__).resolve().parents[2]
TERMINAL = {"completed", "failed", "cancelled"}
REQUEST = {"program": "pytest", "args": ["-q", "-p", "no:cacheprovider", "tests"], "cwd": ".", "timeout_seconds": 300}
OLD = "tests/test_app.py::test_known_failure"
NEW = "tests/test_app.py::test_value"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _git(repo: Path, *args: str) -> str:
    done = subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t", *args],
                          check=True, capture_output=True, text=True)
    return done.stdout.strip()


@pytest.fixture
def flow(migrated_sqlite_db, monkeypatch, tmp_path):
    from sqloader.sqlite3 import SQLiteWrapper
    store = object.__new__(connection.FlowGateStore)
    store._db, store._sq = SQLiteWrapper(migrated_sqlite_db("test_tr_self_check_baseline_e2e_0114.db")), None
    monkeypatch.setattr(connection, "STORE", store)
    monkeypatch.setenv("FLOWGATE_TOKEN_PEPPER_ACTIVE_ID", PEPPER_ID)
    monkeypatch.setenv(f"FLOWGATE_TOKEN_PEPPER_{PEPPER_ID}", PEPPER)
    now = _now()
    ex = store._execute
    ex("INSERT INTO users (user_id, username, email, password, is_active, created_at, updated_at) "
       "VALUES ('u_0114e', 'u0114e', 'u0114e@test', 'x', 1, ?, ?)", [now, now])
    ex("INSERT INTO projects (project_id, project_name, is_active, created_at, updated_at) "
       "VALUES (?, 'P 0114 e2e', 1, ?, ?)", [P, now, now])
    ex("INSERT INTO project_settings (project_id, updated_at, tr_self_check_enabled) VALUES (?, ?, 1)", [P, now])
    ex("INSERT INTO groups (group_id, project_id, module, title, status, created_at, updated_at) "
       "VALUES (?, ?, 'default', 'G', 'OPEN', ?, ?)", [G, P, now, now])
    for seq, (doc_id, type_code, status) in enumerate(((SEQ_DOC, "B", "draft"), (T_DOC, "T", "approved"),
                                                       (TR_DOC, "TR", "draft")), 1):
        ex("INSERT INTO documents (doc_id, project_id, group_id, module, type_code, seq, title, status, branch,"
           " revision_no, rejection_history, doc_review_status, created_at, updated_at) VALUES (?, ?, ?, 'default',"
           " ?, ?, 't', ?, 'main', 1, '[]', ?, ?, ?)",
           [doc_id, P, G, type_code, seq, status, "approved" if status == "approved" else None, now, now])
    ex("INSERT INTO workflow_sequences (doc_id) VALUES (?)", [SEQ_DOC])
    seq_id = store._fetch_one("SELECT id FROM workflow_sequences WHERE doc_id = ?", [SEQ_DOC])["id"]
    for item_seq, (type_code, result) in enumerate((("T", T_DOC), ("TR", None)), 1):
        ex("INSERT INTO workflow_sequence_items (sequence_id, item_seq, type, label, doc_class, sort_order,"
           " result_doc_id) VALUES (?, ?, ?, ?, 'B', ?, ?)", [seq_id, item_seq, type_code, type_code, item_seq, result])

    # The managed group worktree: a pytest tree that loads the real result block plugin.
    repo = tmp_path / "worktree"
    (repo / "tests").mkdir(parents=True)
    (repo / ".gitignore").write_text("__pycache__/\n.pytest_cache/\n", encoding="utf-8")
    (repo / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    (repo / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    (repo / "tests" / "conftest.py").write_text(
        "import sys\nfrom pathlib import Path\n"
        f"sys.path.insert(0, {str(_TESTS_DIR)!r})\n"
        "sys.path.insert(0, str(Path(__file__).resolve().parents[1]))\n"
        "import selfcheck_evidence\n\n\n"
        "def pytest_configure(config):\n"
        "    selfcheck_evidence.register(config, capabilities=lambda: {'git': True})\n", encoding="utf-8")
    (repo / "tests" / "test_app.py").write_text(
        "import app\n\n\n"
        "def test_known_failure():\n    assert 'known' == 'broken'\n\n\n"
        "def test_value():\n    assert app.VALUE == 1\n\n\n"
        "def test_ok():\n    assert True\n", encoding="utf-8")
    _git(repo, "init", "-q")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    monkeypatch.setattr(service.git_service, "effective_src_root_ex",
                        lambda project_id, group_id, state=None: (repo, "worktree"))
    monkeypatch.setattr(service.git_service, "_emit", lambda *a, **k: None)
    scratch = tmp_path / "scratch"
    monkeypatch.setattr(token_service, "_scratch_dir", lambda project, token_id: scratch / token_id)
    monkeypatch.setattr(token_service, "to_storage_relative", lambda path, project: str(path))
    monkeypatch.setattr(token_service.token_scratch, "create", lambda project, token_id, path: None)
    monkeypatch.setattr(token_service.token_scratch, "sweep_on_issue", lambda project: None)
    try:
        yield {"store": store, "repo": repo}
    finally:
        _drain(store)
        try:
            store._db.close()
        except Exception:
            pass


def _drain(store):
    rows = store._fetch_all("SELECT self_check_run_id FROM tr_self_check_runs WHERE project_id = ?"
                            " AND status IN ('pending', 'running')", [P]) or []
    for row in rows:
        try:
            service._cancel(row["self_check_run_id"], lambda rid=row["self_check_run_id"]: service.db_runs.get_run(rid))
        except Exception:
            pass
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline and store._fetch_one(
            "SELECT 1 AS x FROM tr_self_check_runs WHERE project_id = ? AND status IN ('pending', 'running')", [P]):
        time.sleep(0.2)


def _run(token: dict) -> dict:
    started = service.start_draft(token, dict(REQUEST, args=list(REQUEST["args"])))
    deadline = time.monotonic() + 240
    while True:
        row = service.read_draft(token, started["self_check_run_id"])
        if row["status"] in TERMINAL:
            return row
        assert time.monotonic() < deadline, row["status"]
        time.sleep(0.3)


def test_one_work_unit_keeps_its_first_baseline_before_and_after_commit_and_through_rework(flow):
    repo, store = flow["repo"], flow["store"]
    base_head = _git(repo, "rev-parse", "HEAD")
    issued = token_service.issue(P, G, "new", SEQ_DOC, "u_0114e")       # (d) no caller code: the issue point
    token = token_service.verify(issued["raw_token"])
    anchor = db_anchors.get_for_token(issued["token_id"])
    assert (anchor["state"], anchor["base_commit"]) == ("recorded", base_head)

    # 9.2-5 (a)(f): the baseline, made before any product change
    base = _run(token)
    assert (base["status"], base["exit_code"], base["timed_out"]) == ("completed", 1, False)
    summary = base["result_summary"]
    assert summary["status"] == result_block.COMPLETE, summary
    assert [f["nodeid"] for f in summary["failures"]] == [OLD]
    assert set(base["environment_evidence"]) == set(result_block.ENV_CATEGORIES)
    assert summary["definitions"]["run_status"] == "ok" and OLD in summary["definitions"]["items"]
    raw = store._fetch_one("SELECT host_node_key FROM tr_self_check_runs WHERE self_check_run_id = ?",
                           [base["self_check_run_id"]])
    assert raw["host_node_key"]

    # (a) product change, final regression before the commit
    (repo / "app.py").write_text("VALUE = 2\n", encoding="utf-8")
    before_commit = _run(token)
    judged = service.read_draft(token, before_commit["self_check_run_id"], base["self_check_run_id"])["baseline_comparison"]
    assert judged["reusable"] is True, judged["reasons"]
    assert judged["comparison"]["new_failures"] == [NEW]
    assert judged["comparison"]["existing_failures"] == [OLD]
    assert (before_commit["status"], before_commit["exit_code"]) == ("completed", 1)   # verdict unchanged

    # (a) commit, final regression after the commit: the same first baseline
    _git(repo, "commit", "-qam", "change")
    after_commit = _run(token)
    judged = service.read_draft(token, after_commit["self_check_run_id"], base["self_check_run_id"])["baseline_comparison"]
    assert judged["reusable"] is True, judged["reasons"]
    assert judged["comparison"]["new_failures"] == [NEW]
    assert judged["anchor"]["base_commit"] == base_head

    # (b) an earlier after-change run named as baseline is refused; its failure is not "existing"
    refused = service.read_draft(token, after_commit["self_check_run_id"],
                                 before_commit["self_check_run_id"])["baseline_comparison"]
    assert refused["reusable"] is False and refused["comparison"] is None
    assert "dirty_worktree" in {r["code"] for r in refused["reasons"]}

    # (c)(d) registration attaches runs and anchor to the TR; a rework token inherits the anchor
    assert token_service.consume(issued["token_id"], P, TR_DOC) is True
    assert db_anchors.get_for_tr(TR_DOC)["anchor_id"] == anchor["anchor_id"]
    rework = token_service.issue(P, G, "edit", TR_DOC, "u_0114e")
    assert db_anchors.get_for_token(rework["token_id"])["anchor_id"] == anchor["anchor_id"]
    assert store._fetch_one("SELECT COUNT(*) AS n FROM tr_self_check_anchors WHERE project_id = ?", [P])["n"] == 1
    judged = service.read(TR_DOC, after_commit["self_check_run_id"], base["self_check_run_id"])["baseline_comparison"]
    assert judged["reusable"] is True and judged["comparison"]["existing_failures"] == [OLD]
