"""flowgate.v02.0114 T#3: the pre-change anchor, the run row's new evidence columns, and the
token issue point they hang on (D 0006 3.3, 7.2, 9.2-5 (c)(f), 9.2-6 (c)-(h)).

Runs on a fully migrated SQLite through the real ``token_service.issue`` / ``consume`` and
the real workflow head lookup (sequence B -> T(approved) -> TR(pending)). The group worktree
is a throwaway git repository; only the worktree resolver, the token scratch directory and
the pepper are replaced.
"""
from __future__ import annotations

import ast
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from modules.flow_gate.db import connection
from modules.flow_gate.db import tr_self_check_anchors as db_anchors
from modules.flow_gate.db import tr_self_check_runs as db_runs
from modules.flow_gate.services import git_service
from modules.flow_gate.services import token_service
from modules.flow_gate.services import tr_self_check_anchor as anchors
from modules.flow_gate.services import tr_self_check_baseline as baseline
from modules.flow_gate.services import tr_self_check_result as result_block
from modules.flow_gate.services import tr_self_check_service as service

_SERVER = Path(__file__).resolve().parents[3]
_MIGRATIONS = _SERVER / "sql" / "migrations"
_MIGRATION = "163_tr_self_check_baseline.sql"
P, G, G_OFF = "p_0114a", "g_0114a_1", "g_0114a_off"
SEQ_DOC, T_DOC, TR_DOC = "d_0114a_b", "d_0114a_t", "d_0114a_tr"
PEPPER_ID, PEPPER = "A0114", "pepper-0114-anchor"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _git(repo: Path, *args: str) -> str:
    done = subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t", *args],
                          check=True, capture_output=True, text=True)
    return done.stdout.strip()


@pytest.fixture
def world(migrated_sqlite_db, monkeypatch, tmp_path):
    from sqloader.sqlite3 import SQLiteWrapper
    db_path = migrated_sqlite_db("test_tr_self_check_anchor_0114.db")
    store = object.__new__(connection.FlowGateStore)
    store._db, store._sq = SQLiteWrapper(db_path), None
    monkeypatch.setattr(connection, "STORE", store)
    monkeypatch.setenv("FLOWGATE_TOKEN_PEPPER_ACTIVE_ID", PEPPER_ID)
    monkeypatch.setenv(f"FLOWGATE_TOKEN_PEPPER_{PEPPER_ID}", PEPPER)
    now = _now()
    ex = store._execute
    for table in ("tr_self_check_anchor_tokens", "tr_self_check_anchors"):
        ex(f"DELETE FROM {table}")
    ex("INSERT INTO users (user_id, username, email, password, is_active, created_at, updated_at) "
       "VALUES ('u_0114a', 'u0114a', 'u0114a@test', 'x', 1, ?, ?) ON CONFLICT(user_id) DO NOTHING", [now, now])
    ex("INSERT INTO projects (project_id, project_name, is_active, created_at, updated_at) "
       "VALUES (?, 'P 0114 anchor', 1, ?, ?) ON CONFLICT(project_id) DO NOTHING", [P, now, now])
    ex("INSERT INTO project_settings (project_id, updated_at, tr_self_check_enabled) VALUES (?, ?, 1)"
       " ON CONFLICT(project_id) DO UPDATE SET tr_self_check_enabled = 1", [P, now])
    for gid in (G, G_OFF):
        ex("INSERT INTO groups (group_id, project_id, module, title, status, created_at, updated_at) "
           "VALUES (?, ?, 'default', 'G', 'OPEN', ?, ?) ON CONFLICT(group_id) DO NOTHING", [gid, P, now, now])
    for seq, (doc_id, type_code, status) in enumerate(((SEQ_DOC, "B", "draft"), (T_DOC, "T", "approved"),
                                                       (TR_DOC, "TR", "draft")), 1):
        ex("INSERT INTO documents (doc_id, project_id, group_id, module, type_code, seq, title, status, branch,"
           " revision_no, rejection_history, doc_review_status, created_at, updated_at) VALUES (?, ?, ?, 'default',"
           " ?, ?, 't', ?, 'main', 1, '[]', ?, ?, ?) ON CONFLICT(doc_id) DO NOTHING",
           [doc_id, P, G, type_code, seq, status, "approved" if status == "approved" else None, now, now])
    if not store._fetch_one("SELECT id FROM workflow_sequences WHERE doc_id = ?", [SEQ_DOC]):
        ex("INSERT INTO workflow_sequences (doc_id) VALUES (?)", [SEQ_DOC])
        seq_id = store._fetch_one("SELECT id FROM workflow_sequences WHERE doc_id = ?", [SEQ_DOC])["id"]
        for item_seq, (type_code, result) in enumerate((("T", T_DOC), ("TR", None)), 1):
            ex("INSERT INTO workflow_sequence_items (sequence_id, item_seq, type, label, doc_class, sort_order,"
               " result_doc_id) VALUES (?, ?, ?, ?, 'B', ?, ?)", [seq_id, item_seq, type_code, type_code, item_seq, result])

    repo = tmp_path / "worktree"
    repo.mkdir()
    (repo / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    _git(repo, "init", "-q")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    roots = {G: (repo, "worktree"), G_OFF: (None, "integration_off")}
    monkeypatch.setattr(git_service, "effective_src_root_ex",
                        lambda project_id, group_id, state=None: roots.get(group_id, (None, "no_group")))
    scratch = tmp_path / "scratch"
    monkeypatch.setattr(token_service, "_scratch_dir", lambda project, token_id: scratch / token_id)
    monkeypatch.setattr(token_service, "to_storage_relative", lambda path, project: str(path))
    monkeypatch.setattr(token_service.token_scratch, "create", lambda project, token_id, path: None)
    monkeypatch.setattr(token_service.token_scratch, "sweep_on_issue", lambda project: None)
    monkeypatch.setattr(token_service.token_scratch, "delete_owned", lambda *a, **k: True)
    try:
        yield {"store": store, "repo": repo}
    finally:
        try:
            store._db.close()
        except Exception:
            pass


def _issue(scope="new", doc_ref=SEQ_DOC, group=G):
    return token_service.issue(P, group, scope, doc_ref, "u_0114a")


def _anchor_rows(store):
    return store._fetch_all("SELECT * FROM tr_self_check_anchors WHERE project_id = ?", [P]) or []


# ── schema ───────────────────────────────────────────────────────────────

def test_migration_163_exists_for_every_dialect_and_adds_the_evidence_columns(world):
    for dialect in ("sqlite", "postgres", "mysql"):
        text = (_MIGRATIONS / dialect / _MIGRATION).read_text(encoding="utf-8")
        for name in ("result_summary", "environment_evidence", "host_node_key",
                     "tr_self_check_anchors", "tr_self_check_anchor_tokens", "open_key", "tr_doc_id"):
            assert name in text, (dialect, name)
    cols = {r["name"] for r in world["store"]._fetch_all("PRAGMA table_info(tr_self_check_runs)")}
    assert {"result_summary", "environment_evidence", "host_node_key"} <= cols


def test_create_pending_copies_the_host_identifier_into_the_run_row(world):
    row = db_runs.create_pending(P, G, TR_DOC, "u_0114a", "v1", "pytest", ["-q"], "/bin/pytest", "pytest", "venv",
                                 ".", 60, ["PATH"], host_node_key="node-0114")
    try:
        assert row["host_node_key"] == "node-0114"
    finally:
        db_runs.finish_completed(row["self_check_run_id"], exit_code=0)


# ── 9.2-5 (c)(f): only a normally completed run is summarized; verdict fields untouched ──

@pytest.mark.parametrize("finish", ["timed_out", "failed", "cancelled", "recovered"])
def test_5c_a_timed_out_cancelled_failed_or_recovered_run_gets_no_summary(world, finish):
    row = db_runs.create_pending(P, G, TR_DOC, "u_0114a", "v1", "pytest", ["-q"], "/bin/pytest", "pytest", "venv",
                                 ".", 60, ["PATH"])
    rid = row["self_check_run_id"]
    if finish == "timed_out":
        db_runs.finish_completed(rid, exit_code=1, timed_out=True)
    elif finish == "failed":
        db_runs.finish_failed(rid, error_code="selfcheck_internal_error")
    elif finish == "cancelled":
        db_runs.finish_cancelled(rid, exit_code=1)
    else:
        db_runs.mark_recovering(rid)
        db_runs.finish_recovered_interrupted(rid)
    assert db_runs.record_result_summary(rid, '{"status":"complete"}', "{}") is None
    assert db_runs.get_run(rid)["result_summary"] is None


def test_5f_recording_a_summary_changes_no_verdict_field(world):
    row = db_runs.create_pending(P, G, TR_DOC, "u_0114a", "v1", "pytest", ["-q"], "/bin/pytest", "pytest", "venv",
                                 ".", 60, ["PATH"])
    rid = row["self_check_run_id"]
    done = db_runs.finish_completed(rid, exit_code=1, stdout_tail="tail")
    keep = ("status", "exit_code", "timed_out", "stdout_tail", "finished_at", "active_key", "error_code")
    after = db_runs.record_result_summary(rid, '{"status":"mismatch"}', None)
    assert {k: after[k] for k in keep} == {k: done[k] for k in keep}
    assert db_runs.record_result_summary(rid, '{"status":"complete"}', None) is None   # written once
    public = service.public(after)
    assert public["result_summary"] == {"status": "mismatch"} and public["environment_evidence"] is None


def test_5c_the_service_summarizes_only_on_the_completed_not_timed_out_branch():
    source = Path(service.__file__).read_text(encoding="utf-8")
    finish = source[source.index("def _finish("):source.index("def _record_result_summary(")]
    assert finish.count("_record_result_summary(") == 1
    branch = finish[finish.index("terminal = db_runs.finish_completed("):]
    assert "if terminal is not None and not result.timed_out:" in branch.split("except Exception as exc:")[0]


# ── 9.2-6 (d)(f): the anchor written at the common issue point ───────────

def test_6d_a_new_tr_draft_token_records_the_clean_worktree_head_and_the_registered_tr_keeps_it(world):
    head = _git(world["repo"], "rev-parse", "HEAD")
    issued = _issue()
    anchor = db_anchors.get_for_token(issued["token_id"])
    assert (anchor["state"], anchor["base_commit"], anchor["tr_doc_id"]) == ("recorded", head, None)
    assert token_service.consume(issued["token_id"], P, TR_DOC) is True       # registration
    linked = db_anchors.get_for_tr(TR_DOC)
    assert linked["anchor_id"] == anchor["anchor_id"] and linked["base_commit"] == head
    assert linked["open_key"] is None
    run = {"tr_doc_id": TR_DOC, "owner_token_id": issued["token_id"]}
    assert anchors.resolve_for_run(run)["anchor_id"] == anchor["anchor_id"]
    draft_run = {"tr_doc_id": None, "owner_token_id": issued["token_id"]}
    assert anchors.resolve_for_run(draft_run)["anchor_id"] == anchor["anchor_id"]


def test_6c_rework_and_reissued_tokens_inherit_the_first_anchor_and_never_create_another(world):
    first = _issue()
    base_head = _git(world["repo"], "rev-parse", "HEAD")
    again = _issue()                                                # re-issued draft token, same unit
    assert db_anchors.get_for_token(again["token_id"])["anchor_id"] == db_anchors.get_for_token(first["token_id"])["anchor_id"]
    token_service.consume(again["token_id"], P, TR_DOC)
    (world["repo"] / "app.py").write_text("VALUE = 2\n", encoding="utf-8")   # the change is committed
    _git(world["repo"], "commit", "-qam", "change")
    for _ in range(2):                                              # rejection -> rework tokens
        rework = _issue("edit", TR_DOC)
        anchor = db_anchors.get_for_token(rework["token_id"])
        assert (anchor["base_commit"], anchor["tr_doc_id"]) == (base_head, TR_DOC)
    assert len(_anchor_rows(world["store"])) == 1


def test_6c_every_issue_path_reaches_the_one_recording_point_without_its_own_code():
    callers = {
        "modules/flow_gate/api/token_routes.py": "token_service.issue(",                 # manual issue
        "modules/flow_gate/services/ai_invoke/admission.py": "token_service.issue(",      # AI run
        "modules/flow_gate/services/workflow_decision_service.py": "token_service.issue(",  # continuation
    }
    for rel, call in callers.items():
        text = (_SERVER / rel).read_text(encoding="utf-8")
        assert call in text, rel
        assert "tr_self_check_anchor" not in text and "record_on_issue" not in text, rel
    issue = Path(token_service.__file__).read_text(encoding="utf-8")
    body = issue[issue.index("def issue("):issue.index("def _record_self_check_anchor(")]
    tail = body[body.index("with get_store().transaction():"):]
    # recorded after the transaction committed, before the token is returned
    assert tail.index("_record_self_check_anchor(data)") < tail.index("return {")
    assert "_record_self_check_anchor" not in body[:body.index("with get_store().transaction():")]


def test_6e_6f_a_dirty_tree_records_none_and_a_later_clean_issue_of_the_unit_does_not_replace_it(world):
    (world["repo"] / "app.py").write_text("VALUE = 9\n", encoding="utf-8")       # uncommitted change
    first = _issue()
    anchor = db_anchors.get_for_token(first["token_id"])
    assert (anchor["state"], anchor["base_commit"], anchor["reason"]) == ("none", None, "dirty_worktree")
    _git(world["repo"], "commit", "-qam", "change")                             # now clean, HEAD moved
    later = _issue()
    assert db_anchors.get_for_token(later["token_id"])["anchor_id"] == anchor["anchor_id"]
    assert db_anchors.get_for_token(later["token_id"])["base_commit"] is None


@pytest.mark.parametrize("inject", ["probe", "store", "lookup"])
def test_6e_6f_a_recording_failure_never_blocks_or_reshapes_the_issue_and_leaves_no_basis(world, monkeypatch, inject):
    plain = _issue("new", SEQ_DOC, G_OFF)                       # a token whose unit records nothing
    if inject == "probe":
        monkeypatch.setattr(anchors, "_git", lambda *a: (_ for _ in ()).throw(RuntimeError("git down")))
    elif inject == "store":
        monkeypatch.setattr(db_anchors, "create_open", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db down")))
    else:
        monkeypatch.setattr("modules.flow_gate.services.remote_tool_service._worker_token_step_type_result",
                            lambda token: (None, True))
    issued = _issue()
    assert set(issued) == set(plain)
    assert token_service.verify(issued["raw_token"])["token_id"] == issued["token_id"]
    anchor = anchors.resolve_for_run({"tr_doc_id": None, "owner_token_id": issued["token_id"]})
    assert anchor is None or anchor["state"] == "none"
    expected = {"probe": "probe_failed", "lookup": "step_lookup_failed"}.get(inject)
    assert (anchor or {}).get("reason") == expected
    # (e) the first clean run after the change is no baseline without a basis
    result = baseline.judge({"self_check_run_id": "b", "project_id": P, "group_id": G},
                            {"self_check_run_id": "a", "project_id": P, "group_id": G},
                            anchor=anchor, is_ancestor=lambda c, h: True)
    assert "anchor_missing" in {r["code"] for r in result["reasons"]}


def _raise(message):
    def boom(*args, **kwargs):
        raise RuntimeError(message)
    return boom


@pytest.mark.parametrize("inject", ["store", "lookup", "map", "before_feature"])
def test_6e_6f_a_unit_whose_first_issue_left_no_anchor_never_records_a_later_head(world, monkeypatch, inject):
    """Rejection rej_01M67VSC9EK69SR8 P1: the first issue's anchor is lost (its write or its
    lookup raised, or the token came from code without anchors); the change is committed; the
    unit's token is issued again. The re-issue must not read the after-change HEAD."""
    targets = {"store": (db_anchors, "create_open"), "lookup": (db_anchors, "get_open"),
               "map": (db_anchors, "map_token"), "before_feature": (token_service, "_record_self_check_anchor")}
    owner, name = targets[inject]
    real = getattr(owner, name)
    monkeypatch.setattr(owner, name, (lambda data: None) if inject == "before_feature" else _raise("down"))
    first = _issue()
    assert token_service.verify(first["raw_token"])["token_id"] == first["token_id"]   # issue unaffected
    assert db_anchors.get_for_token(first["token_id"]) is None
    monkeypatch.setattr(owner, name, real)
    if inject == "map":     # the anchor row exists but its first token was never mapped: still inherited
        assert db_anchors.get_open(P, G, SEQ_DOC)["state"] == "recorded"
    else:
        assert _anchor_rows(world["store"]) == []
    (world["repo"] / "app.py").write_text("VALUE = 2\n", encoding="utf-8")      # the change is committed
    _git(world["repo"], "commit", "-qam", "change")
    after_head = _git(world["repo"], "rev-parse", "HEAD")
    for _ in range(2):                                                        # re-issues of the unit
        again = _issue()
        anchor = db_anchors.get_for_token(again["token_id"])
        assert anchor["base_commit"] != after_head
        if inject != "map":
            assert (anchor["state"], anchor["base_commit"], anchor["reason"]) == \
                ("none", None, "earlier_issue_without_anchor")
    assert len(_anchor_rows(world["store"])) == 1
    if inject != "map":
        resolved = anchors.resolve_for_run({"tr_doc_id": None, "owner_token_id": again["token_id"]})
        result = baseline.judge({"self_check_run_id": "b", "project_id": P, "group_id": G},
                                {"self_check_run_id": "a", "project_id": P, "group_id": G},
                                anchor=resolved, is_ancestor=lambda c, h: True)
        assert "anchor_missing" in {r["code"] for r in result["reasons"]} and result["comparison"] is None
        token_service.consume(again["token_id"], P, TR_DOC)                   # registered: still none
        assert db_anchors.get_for_tr(TR_DOC)["state"] == "none"


def test_6e_an_unreadable_issue_history_proves_no_first_issue(world, monkeypatch):
    monkeypatch.setattr(db_anchors, "unaccounted_draft_issues", _raise("tokens unreadable"))
    issued = _issue()
    anchor = db_anchors.get_for_token(issued["token_id"])
    assert (anchor["state"], anchor["base_commit"], anchor["reason"]) == ("none", None, "issue_history_unavailable")


def test_6d_the_next_unit_after_a_registration_is_a_first_issue_again(world, monkeypatch):
    """The proof is scoped to the draft unit: tokens of a unit that a registration already
    closed (here: one that predates anchors) do not deny the next unit its basis."""
    real = token_service._record_self_check_anchor
    monkeypatch.setattr(token_service, "_record_self_check_anchor", lambda data: None)
    stale = _issue()                                   # abandoned, from before anchors existed
    registered = _issue()
    monkeypatch.setattr(token_service, "_record_self_check_anchor", real)
    time.sleep(1.1)                                    # token timestamps are whole seconds
    token_service.consume(registered["token_id"], P, TR_DOC)
    (world["repo"] / "app.py").write_text("VALUE = 5\n", encoding="utf-8")
    _git(world["repo"], "commit", "-qam", "previous unit's change")
    head = _git(world["repo"], "rev-parse", "HEAD")
    nxt = _issue()
    anchor = db_anchors.get_for_token(nxt["token_id"])
    assert (anchor["state"], anchor["base_commit"]) == ("recorded", head)
    assert db_anchors.unaccounted_draft_issues(P, G, SEQ_DOC, nxt["token_id"]) == []
    assert stale["token_id"] not in db_anchors.unaccounted_draft_issues(P, G, SEQ_DOC, "x")


def test_6f_a_failing_issue_rolls_back_as_before_and_records_nothing(world, monkeypatch):
    monkeypatch.setattr(token_service.db_events, "create", lambda event: (_ for _ in ()).throw(RuntimeError("event")))
    with pytest.raises(RuntimeError, match="event"):
        _issue()
    assert _anchor_rows(world["store"]) == []


def test_6f_concurrent_issues_of_one_unit_leave_one_anchor(world, monkeypatch):
    real = db_anchors.get_open
    calls = {"n": 0}

    def racing(*args):
        calls["n"] += 1
        return None if calls["n"] <= 2 else real(*args)     # both issues see "no anchor yet"

    monkeypatch.setattr(db_anchors, "get_open", racing)
    first = db_anchors.create_open(P, G, SEQ_DOC, state="recorded", base_commit="a" * 40, reason=None, token_id="t1")
    second = db_anchors.create_open(P, G, SEQ_DOC, state="recorded", base_commit="b" * 40, reason=None, token_id="t2")
    assert second["anchor_id"] == first["anchor_id"] and second["base_commit"] == "a" * 40
    assert len(_anchor_rows(world["store"])) == 1


def test_6f_a_token_that_is_not_a_tr_work_unit_records_nothing(world):
    review = _issue("review", TR_DOC)
    edit_of_t = _issue("edit", T_DOC)
    assert db_anchors.get_for_token(review["token_id"]) is None
    assert db_anchors.get_for_token(edit_of_t["token_id"]) is None


# ── 9.2-6 (g): one place creates token rows ──────────────────────────────

def test_6g_token_rows_are_created_only_by_the_common_issue_point():
    inserts, creators = [], []
    for path in sorted((_SERVER / "modules").rglob("*.py")):
        rel = path.relative_to(_SERVER).as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8-sig", errors="replace"))
        # SQL literals only: a docstring that says "INSERT into tokens" creates no row.
        docstrings = {id(node.body[0].value) for node in ast.walk(tree)
                      if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
                      and node.body and isinstance(node.body[0], ast.Expr)
                      and isinstance(node.body[0].value, ast.Constant)}
        if any(isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings
               and re.search(r"INSERT\s+INTO\s+tokens\b", node.value, re.IGNORECASE) for node in ast.walk(tree)):
            inserts.append(rel)
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "create"
                    and isinstance(node.func.value, ast.Name) and node.func.value.id in ("db_tokens", "tokens")):
                creators.append(rel)
    assert inserts == ["modules/flow_gate/db/tokens.py"]
    assert set(creators) == {"modules/flow_gate/services/token_service.py"}


# ── 9.2-6 (h): history rewrite ───────────────────────────────────────────

def test_6h_an_anchor_outside_the_current_history_is_not_an_ancestor(world):
    repo = world["repo"]
    a = _git(repo, "rev-parse", "HEAD")
    (repo / "app.py").write_text("VALUE = 3\n", encoding="utf-8")
    _git(repo, "commit", "-qam", "b")
    b = _git(repo, "rev-parse", "HEAD")
    _git(repo, "checkout", "-q", "--orphan", "rewritten")
    _git(repo, "commit", "-qm", "rewritten")
    c = _git(repo, "rev-parse", "HEAD")
    check = baseline._is_ancestor(P, G)
    assert check(a, b) is True
    assert check(a, c) is False
    assert check("not-a-sha", c) is None
    result = baseline.judge({"self_check_run_id": "b", "project_id": P, "group_id": G},
                            {"self_check_run_id": "x", "project_id": P, "group_id": G, "source_head_after": c},
                            anchor={"state": "recorded", "base_commit": a}, is_ancestor=check)
    assert "anchor_not_ancestor" in {r["code"] for r in result["reasons"]}


def test_6_compare_reports_an_unknown_baseline_and_never_another_groups_run(world):
    other = db_runs.create_pending(P, G_OFF, TR_DOC, "u_0114a", "v1", "pytest", ["-q"], "/bin/pytest", "pytest",
                                   "venv", ".", 60, ["PATH"])
    db_runs.finish_completed(other["self_check_run_id"], exit_code=0)
    after = {"self_check_run_id": "scr_after", "project_id": P, "group_id": G, "tr_doc_id": TR_DOC}
    assert baseline.compare(after, "scr_missing")["reasons"] == [{"code": "baseline_not_found"}]
    foreign = baseline.compare(after, other["self_check_run_id"])
    assert foreign["reasons"] == [{"code": "different_group"}] and foreign["comparison"] is None
    assert result_block.loads(None) is None
