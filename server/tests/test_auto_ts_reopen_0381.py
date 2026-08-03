from __future__ import annotations

import json
import os
import sqlite3
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Optional
from unittest.mock import MagicMock

import pytest

os.environ["TESTING"] = "1"
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
_SERVER_DIR = Path(__file__).resolve().parents[1]
_SCHEMA_DIR = _SERVER_DIR / "sql" / "migrations" / "sqlite"
sys.path.insert(0, str(_SERVER_DIR))


class _MockDB:
    def __init__(self, db_path: str):
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")

    def execute(self, sql: str, params=None):
        self._conn.execute(sql, params or [])
        self._conn.commit()

    def fetch_one(self, sql: str, params=None):
        row = self._conn.execute(sql, params or []).fetchone()
        return dict(row) if row else None

    def fetch_all(self, sql: str, params=None):
        return [dict(row) for row in self._conn.execute(sql, params or []).fetchall()]

    @contextmanager
    def begin_transaction(self):
        self._conn.execute("BEGIN")
        txn = _MockTxn(self._conn)
        try:
            yield txn
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    def close(self):
        self._conn.close()


class _MockTxn:
    def __init__(self, conn):
        self._conn = conn
        self._last_cursor = None

    def execute(self, sql: str, params=None):
        self._last_cursor = self._conn.execute(sql, params or [])

    def fetch_one(self):
        row = self._last_cursor.fetchone() if self._last_cursor is not None else None
        return dict(row) if row else None

    def fetch_all(self):
        if self._last_cursor is None:
            return []
        return [dict(row) for row in self._last_cursor.fetchall()]


@pytest.fixture
def auto_store(tmp_path, monkeypatch):
    mock_db = _MockDB(str(tmp_path / "auto-reopen.db"))
    for sql_file in sorted(_SCHEMA_DIR.glob("*.sql")):
        mock_db._conn.executescript(sql_file.read_text(encoding="utf-8"))
    mock_db._conn.commit()
    storage_root = tmp_path / "storage"
    storage_root.mkdir()
    monkeypatch.setenv("FLOWGATE_STORAGE_DIR", str(storage_root))

    from modules.flow_gate.db import connection as conn_mod

    original_store = conn_mod.STORE

    class _PatchedStore(conn_mod.FlowGateStore):
        def __init__(self):
            self._db = mock_db
            self._sq = None

    conn_mod.STORE = _PatchedStore()
    yield storage_root
    conn_mod.STORE = original_store
    mock_db.close()


def _seed_group(
    storage_root: Path,
    suffix: str,
    *,
    root_done: bool = False,
    with_ac: bool = False,
    with_tsr: bool = False,
    with_phantom: bool = False,
) -> dict:
    from modules.flow_gate.db import documents as db_docs
    from modules.flow_gate.db import groups as db_groups
    from modules.flow_gate.db import projects, users
    from modules.flow_gate.db import workflow_sequences as db_wfseq
    from modules.flow_gate.storage import paths as storage_paths

    project_id = f"auto0381{suffix}"
    group_id = f"auto0381.default.{suffix}"
    user_id = f"usr_auto_{suffix}"
    projects.create({"project_id": project_id, "project_name": f"Auto {suffix}"})
    users.create({
        "user_id": user_id,
        "username": f"auto{suffix}",
        "email": f"auto{suffix}@test.com",
        "password": "hashed",
    })
    db_groups.create({
        "group_id": group_id,
        "project_id": project_id,
        "module": "default",
        "title": f"Auto reopen {suffix}",
    })

    specs = [
        (1, "R", "Root", "wf_done" if root_done else "wf_in_progress"),
        (2, "T", "Task", "approved"),
        (3, "TS", "Scenario", "approved"),
        (5, "TR", "Later task report", "approved"),
    ]
    if with_tsr:
        specs.append((4, "TSR", "Prior report", "approved"))
    if with_ac:
        specs.append((6, "AC", "Final approval", "approved"))
    if with_phantom:
        specs.append((7, "P", "Phantom", "approved"))

    ids = {"project_id": project_id, "group_id": group_id, "user_id": user_id}
    for seq, type_code, title, review_status in specs:
        doc_code = f"{seq:04d}-{type_code}"
        doc_id = f"{group_id}.{doc_code}"
        file_path = None
        if type_code != "AC":
            path = storage_paths.document_path(
                project_id=project_id,
                group_code=group_id,
                doc_code=doc_code,
                filename="document.md",
                module="default",
            )
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"---\ntitle: {title}\n---\n# {title}\n", encoding="utf-8")
            file_path = storage_paths.to_storage_relative(path, project_id)
        db_docs.create({
            "doc_id": doc_id,
            "project_id": project_id,
            "module": "default",
            "group_id": group_id,
            "type_code": type_code,
            "seq": seq,
            "title": title,
            "owner_id": user_id,
            "file_path": file_path,
        })
        db_docs.update(doc_id, {"doc_review_status": review_status})
        ids[type_code] = doc_id

    db_wfseq.insert_sequence(ids["R"])
    sequence = db_wfseq.get_sequence_by_doc_id(ids["R"])
    for order, type_code in enumerate(("T", "TS", "TSR", "TR"), start=1):
        db_wfseq.insert_sequence_item(
            sequence["id"], order, type_code, type_code, "doc", order
        )
    for item in db_wfseq.get_sequence_items(sequence["id"]):
        result_doc_id = ids.get(item["type"])
        if result_doc_id:
            db_wfseq.set_item_result_doc_id(item["id"], result_doc_id)
    return ids


def _create_run(ts_doc_id: str, runner_id: str, *, result: str = "fail", finish: bool = True):
    from modules.flow_gate.db import documents as db_docs
    from modules.flow_gate.db import test_runs as db_runs

    doc = db_docs.get_by_id(ts_doc_id)
    run = db_runs.insert_run(
        doc_id=ts_doc_id,
        revision_no=doc.get("revision_no") or 0,
        triggered_via="ui",
        runner_id=runner_id,
        cases=[{"case_no": "TC-1", "title": "case", "cmd": "false", "expect": "pass"}],
    )
    case = db_runs.list_cases(run["run_id"])[0]
    db_runs.mark_case_finished(
        case_id=case["id"], result=result, exit_code=1 if result == "fail" else 0,
        duration_ms=1, output_tail="red" if result == "fail" else "green",
    )
    if finish:
        db_runs.finish_run(
            run_id=run["run_id"], status="failed" if result == "fail" else "passed",
            case_passed=0 if result == "fail" else 1,
            case_failed=1 if result == "fail" else 0,
        )
    return db_runs.get_run(run["run_id"])


def _auto(ids: dict, run: dict):
    from modules.flow_gate.services import workflow_rework_service as rework
    from modules.flow_gate.services.mutation_policy import system_principal

    return rework.auto_reopen_failed_ts(
        ts_doc_id=ids["TS"],
        target_seq=3,
        actor_user_id=ids["user_id"],
        reason="test_run_code_failure",
        run_id=run["run_id"],
        mutation_context=system_principal(
            user_id=ids["user_id"], group_id=ids["group_id"], run_id=run["run_id"]
        ),
    )


def test_1_unmanned_code_red_reopens_preserves_run_and_notifies_once(auto_store, monkeypatch):
    from modules.flow_gate.db import documents as db_docs
    from modules.flow_gate.db import test_runs as db_runs
    from modules.flow_gate.db import workflow_return_points as db_rp
    from modules.flow_gate.db.connection import get_store
    from modules.flow_gate.services import test_run_service
    from modules.flow_gate.services import workflow_rework_service as rework

    ids = _seed_group(auto_store, "u1")
    run = _create_run(ids["TS"], ids["user_id"])
    monkeypatch.setattr(rework.git_service, "reopen_group_git", MagicMock())
    time_machine_reopen = MagicMock(wraps=rework.reopen_to_target)
    monkeypatch.setattr(rework, "reopen_to_target", time_machine_reopen)
    outcome = _auto(ids, run)

    time_machine_reopen.assert_called_once()
    assert time_machine_reopen.call_args.kwargs["doc_id"] == ids["TS"]
    assert time_machine_reopen.call_args.kwargs["target_seq"] == 3
    assert outcome["auto_reopened"] is True
    assert db_docs.get_by_id(ids["TS"])["doc_review_status"] == "pending_review"
    assert db_docs.get_by_id(ids["TR"])["doc_review_status"] == "pending_review"
    assert db_rp.get_by_group(ids["group_id"]) is not None
    assert db_runs.get_run(run["run_id"])["tsr_doc_id"] is None
    assert len(db_runs.list_cases(run["run_id"])) == 1

    monkeypatch.setattr(
        test_run_service,
        "_continuation_token_for_doc",
        lambda *_: {"issued_to": ids["user_id"], "continuation_target_seq": 9},
    )
    doc = db_docs.get_by_id(ids["TS"])
    test_run_service._maybe_notify_chain_failure(doc, run, auto_reopen=outcome)
    test_run_service._maybe_notify_chain_failure(doc, run, auto_reopen=outcome)
    rows = get_store()._fetch_all(
        "SELECT metadata FROM workflow_events WHERE event_type = 'continuous_work_failed'"
    )
    assert len(rows) == 1
    metadata = json.loads(rows[0]["metadata"])
    assert metadata["auto_reopened"] is True
    assert metadata["target_doc_id"] == ids["TS"]
    assert metadata["target_seq"] == 3
    assert metadata["run_id"] == run["run_id"]


def _drive_code_red_broadcasts(monkeypatch, *, continuation_token):
    """Run the CODE-RED hook with the SSE bus captured, for one token flavour."""
    from modules.flow_gate.services import engine_recipe_service
    from modules.flow_gate.services import test_run_service
    from modules.flow_gate.services import workflow_rework_service as rework

    doc = {"doc_id": "g.0003-TS", "group_id": "g", "project_id": "p", "seq": 3, "owner_id": "u"}
    run = {"run_id": "run-ui", "runner_id": "u", "status": "failed"}
    emitted: list[tuple[str, dict]] = []
    monkeypatch.setattr(test_run_service, "_broadcast", lambda kind, _doc, payload: emitted.append((kind, payload)))
    monkeypatch.setattr(engine_recipe_service, "classify_failure", lambda *_: engine_recipe_service.CODE)
    monkeypatch.setattr(engine_recipe_service, "handle_run_failure", lambda *_: "code")
    monkeypatch.setattr(test_run_service, "_continuation_token_for_doc", lambda *_a, **_k: continuation_token)
    monkeypatch.setattr(test_run_service, "_maybe_notify_chain_failure", lambda *_a, **_k: None)
    monkeypatch.setattr(
        rework,
        "auto_reopen_failed_ts",
        lambda **_kw: {"auto_reopened": True, "target_doc_id": doc["doc_id"], "target_seq": 3, "run_id": run["run_id"]},
    )

    test_run_service._emit_finished(doc, run, None)
    test_run_service._handle_terminal_case_failure(doc, run, [{"result": "fail", "kind": "case"}])
    return emitted


def test_2_manual_code_red_emits_post_rework_refresh(monkeypatch):
    emitted = _drive_code_red_broadcasts(monkeypatch, continuation_token=None)
    assert [kind for kind, _ in emitted] == [
        "test_run_finished", "group_view_refresh", "group_view_refresh"
    ]
    payload = emitted[-1][1]
    assert payload["reason"] == "test_run_code_failure_auto_reopen"
    assert payload["doc_id"] == "g.0003-TS"
    assert payload["target_seq"] == 3
    assert payload["run_id"] == "run-ui"
    # The user-facing sentence is the client's to render: a server-side Korean string here
    # would be invisible to the en/ja bundles and untranslatable.
    assert not any(
        any("가" <= ch <= "힣" for ch in str(value)) for value in payload.values()
    )


def test_11_unmanned_code_red_also_refreshes_after_the_rewind(monkeypatch):
    """The unmanned chain is watched on screen too.

    _emit_finished broadcasts this run's group_view_refresh BEFORE the rewind commits, so
    a browser that only sees that one keeps rendering the TS as approved. Gating the
    post-rewind refresh on "manual runs only" made the unmanned chain — the main automation
    path — silently keep the stale, pre-rewind screen until a hand reload.
    """
    emitted = _drive_code_red_broadcasts(
        monkeypatch,
        continuation_token={"issued_to": "u", "continuation_target_seq": 9},
    )
    refresh_reasons = [payload.get("reason") for kind, payload in emitted if kind == "group_view_refresh"]
    assert refresh_reasons == ["test_run_finished", "test_run_code_failure_auto_reopen"]


def test_3_unmanned_infra_repair_never_reopens(monkeypatch):
    from modules.flow_gate.services import engine_recipe_service
    from modules.flow_gate.services import test_run_service
    from modules.flow_gate.services import workflow_rework_service as rework

    auto = MagicMock()
    notify = MagicMock()
    monkeypatch.setattr(rework, "auto_reopen_failed_ts", auto)
    monkeypatch.setattr(engine_recipe_service, "classify_failure", lambda *_: engine_recipe_service.INFRA)
    monkeypatch.setattr(engine_recipe_service, "handle_run_failure", lambda *_: "repair")
    monkeypatch.setattr(test_run_service, "_maybe_notify_chain_failure", notify)
    result = test_run_service._handle_terminal_case_failure(
        {"doc_id": "g.0003-TS"}, {"run_id": "infra-1"}, []
    )
    assert result is None
    auto.assert_not_called()
    notify.assert_not_called()


def test_4_ui_infra_skip_never_reopens(monkeypatch):
    from modules.flow_gate.services import engine_recipe_service
    from modules.flow_gate.services import test_run_service
    from modules.flow_gate.services import workflow_rework_service as rework

    auto = MagicMock()
    notify = MagicMock()
    monkeypatch.setattr(rework, "auto_reopen_failed_ts", auto)
    monkeypatch.setattr(engine_recipe_service, "classify_failure", lambda *_: engine_recipe_service.INFRA)
    monkeypatch.setattr(engine_recipe_service, "handle_run_failure", lambda *_: "skip")
    monkeypatch.setattr(test_run_service, "_maybe_notify_chain_failure", notify)
    test_run_service._handle_terminal_case_failure(
        {"doc_id": "g.0003-TS"}, {"run_id": "infra-ui"}, []
    )
    auto.assert_not_called()
    assert notify.call_args.kwargs["auto_reopen"] is None


def test_5_report_assembly_failure_does_not_enter_rework(auto_store, monkeypatch, tmp_path):
    from modules.flow_gate.db import test_runs as db_runs
    from modules.flow_gate.services import test_run_service

    ids = _seed_group(auto_store, "report")
    run = _create_run(ids["TS"], ids["user_id"], result="pass", finish=False)
    monkeypatch.setattr(test_run_service.storage_paths, "resolve_project_src_root", lambda *_a, **_k: tmp_path)
    monkeypatch.setattr(test_run_service, "_record_source_root", lambda *_: None)
    monkeypatch.setattr(test_run_service, "_allocate_port", lambda: 19001)
    monkeypatch.setattr(test_run_service, "_scratch_dir", lambda *_: tmp_path / "scratch")
    monkeypatch.setattr(test_run_service, "_execute_setup", lambda *_: (False, None))
    monkeypatch.setattr(
        test_run_service,
        "_execute_case",
        lambda _d, _r, case, *_a: db_runs.mark_case_finished(
            case_id=case["id"], result="pass", exit_code=0, duration_ms=1, output_tail="green"
        ),
    )
    monkeypatch.setattr(test_run_service, "_execute_teardown", lambda *_: None)
    monkeypatch.setattr(test_run_service, "_finalize_services", lambda *_: None)
    monkeypatch.setattr(test_run_service, "_remove_scratch", lambda *_: None)
    monkeypatch.setattr(test_run_service, "assemble_tsr", MagicMock(side_effect=RuntimeError("report")))
    monkeypatch.setattr(test_run_service.test_command_service, "reflect_from_passed_run", lambda *_: None)
    monkeypatch.setattr(test_run_service.engine_recipe_service, "reflect_from_passed_run", lambda *_: None)
    monkeypatch.setattr(test_run_service, "_emit_finished", lambda *_: None)
    monkeypatch.setattr(test_run_service, "_maybe_notify_chain_failure", lambda *_: None)
    rework_hook = MagicMock()
    monkeypatch.setattr(test_run_service, "_handle_terminal_case_failure", rework_hook)

    test_run_service._execute_run_inner(run)
    finished = db_runs.get_run(run["run_id"])
    assert finished["status"] == "failed"
    assert finished["error"] == "report_assembly_failed"
    rework_hook.assert_not_called()


def test_6_duplicate_and_stale_completion_are_noops(auto_store, monkeypatch):
    from modules.flow_gate.db import documents as db_docs
    from modules.flow_gate.services import workflow_rework_service as rework

    monkeypatch.setattr(rework.git_service, "reopen_group_git", MagicMock())
    ids = _seed_group(auto_store, "race1")
    run = _create_run(ids["TS"], ids["user_id"])
    assert _auto(ids, run)["auto_reopened"] is True
    duplicate = _auto(ids, run)
    assert duplicate["auto_reopened"] is False
    assert duplicate["auto_reopen_skipped"] == "duplicate_completion"

    stale_ids = _seed_group(auto_store, "race2")
    stale_run = _create_run(stale_ids["TS"], stale_ids["user_id"])
    db_docs.update(stale_ids["TS"], {"revision_no": 1})
    stale = _auto(stale_ids, stale_run)
    assert stale["auto_reopened"] is False
    assert stale["auto_reopen_skipped"] == "stale_revision"
    assert db_docs.get_by_id(stale_ids["TS"])["doc_review_status"] == "approved"


def test_7_prior_tsr_is_preserved_and_revised_in_place(auto_store, monkeypatch):
    from modules.flow_gate.db import documents as db_docs
    from modules.flow_gate.services import test_run_service
    from modules.flow_gate.services import workflow_rework_service as rework

    ids = _seed_group(auto_store, "tsr", with_tsr=True)
    run = _create_run(ids["TS"], ids["user_id"])
    monkeypatch.setattr(rework.git_service, "reopen_group_git", MagicMock())
    assert _auto(ids, run)["auto_reopened"] is True
    prior = db_docs.get_by_id(ids["TSR"])
    assert prior is not None
    assert prior["doc_review_status"] == "pending_review"

    monkeypatch.setattr(test_run_service, "_register_tsr_workflow_result", lambda *_: None)
    revised_id = test_run_service._revise_active_tsr(
        db_docs.get_by_id(ids["TS"]), prior, "# fresh green report\n", "Fresh report"
    )
    assert revised_id == ids["TSR"]
    assert db_docs.get_by_id(ids["TSR"])["revision_no"] == 1


def test_8_done_root_preserves_ac_and_rearms_git(auto_store, monkeypatch):
    from modules.flow_gate.db import documents as db_docs
    from modules.flow_gate.services import workflow_rework_service as rework

    ids = _seed_group(auto_store, "done", root_done=True, with_ac=True, with_tsr=True)
    run = _create_run(ids["TS"], ids["user_id"])
    reopen_git = MagicMock()
    monkeypatch.setattr(rework.git_service, "reopen_group_git", reopen_git)
    assert _auto(ids, run)["auto_reopened"] is True
    assert db_docs.get_by_id(ids["R"])["doc_review_status"] == "wf_in_progress"
    ac = db_docs.get_by_id(ids["AC"])
    assert ac is not None
    assert ac["status"] == "archived"
    assert json.loads(ac["meta"])["workflow_invalidated_run_id"] == run["run_id"]
    assert rework.git_service._group_ac_doc_id(ids["group_id"]) is None

    # Once the real sequence documents are approved again, the archived historical AC
    # must not make the workflow look final-approved; a fresh AC gate is pending.
    from modules.flow_gate.documents.routers import documents as doc_routes
    for type_code in ("TS", "TSR", "TR"):
        db_docs.update(ids[type_code], {"doc_review_status": "approved"})
    parsed = doc_routes._parse_doc_workflow(db_docs.get_by_id(ids["R"]))
    assert parsed["workflow_head_type"] == "AC"
    assert parsed["workflow_head_status"] == "pending"
    reopen_git.assert_called_once_with(ids["project_id"], ids["group_id"])


def test_9_phantom_document_is_not_reopened(auto_store, monkeypatch):
    from modules.flow_gate.db import documents as db_docs
    from modules.flow_gate.services import workflow_rework_service as rework

    ids = _seed_group(auto_store, "phantom", with_phantom=True)
    run = _create_run(ids["TS"], ids["user_id"])
    monkeypatch.setattr(rework.git_service, "reopen_group_git", MagicMock())
    outcome = _auto(ids, run)
    assert outcome["auto_reopened"] is True
    assert db_docs.get_by_id(ids["TR"])["doc_review_status"] == "pending_review"
    assert db_docs.get_by_id(ids["P"])["doc_review_status"] == "approved"


def test_10_full_worker_pipeline_real_code_red_reopens_ts_end_to_end(auto_store, monkeypatch, tmp_path):
    """Drives the exact production entry point a live worker uses (`_execute_run_inner`)
    through a genuine CODE RED with NONE of classify_failure / handle_run_failure /
    auto_reopen_failed_ts / reopen_to_target mocked out. Only the OS-level process
    execution is stubbed (as test_5 does for the passed-run case) so this proves the
    real wiring — not a spy on it — is what flips the TS back to "승인이전" (pending_review).
    """
    from modules.flow_gate.db import documents as db_docs
    from modules.flow_gate.db import test_runs as db_runs
    from modules.flow_gate.db import workflow_return_points as db_rp
    from modules.flow_gate.db.connection import get_store
    from modules.flow_gate.services import test_run_service
    from modules.flow_gate.services import workflow_rework_service as rework

    ids = _seed_group(auto_store, "e2e")
    monkeypatch.setattr(rework.git_service, "reopen_group_git", MagicMock())

    run = db_runs.insert_run(
        doc_id=ids["TS"],
        revision_no=0,
        triggered_via="ui",
        runner_id=ids["user_id"],
        cases=[{"case_no": "TC-1", "title": "assert result", "cmd": "run-case", "expect": "pass"}],
    )

    monkeypatch.setattr(test_run_service.storage_paths, "resolve_project_src_root", lambda *_a, **_k: tmp_path)
    monkeypatch.setattr(test_run_service, "_record_source_root", lambda *_: None)
    monkeypatch.setattr(test_run_service, "_allocate_port", lambda: 19002)
    monkeypatch.setattr(test_run_service, "_scratch_dir", lambda *_: tmp_path / "scratch")
    monkeypatch.setattr(test_run_service, "_execute_setup", lambda *_: (False, None))

    def _fake_case(_doc, _run, case, *_a):
        db_runs.mark_case_finished(
            case_id=case["id"],
            result="fail",
            exit_code=1,
            duration_ms=5,
            output_tail="AssertionError: expected 2 got 3",
        )

    monkeypatch.setattr(test_run_service, "_execute_case", _fake_case)
    monkeypatch.setattr(test_run_service, "_execute_teardown", lambda *_: None)
    monkeypatch.setattr(test_run_service, "_finalize_services", lambda *_: None)
    monkeypatch.setattr(test_run_service, "_remove_scratch", lambda *_: None)
    emitted: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        test_run_service, "_broadcast", lambda kind, _doc, payload: emitted.append((kind, payload))
    )

    test_run_service._execute_run_inner(run)

    finished = db_runs.get_run(run["run_id"])
    assert finished["status"] == "failed"
    assert finished["error"] is None  # a real case RED, not setup/report failure

    assert db_docs.get_by_id(ids["TS"])["doc_review_status"] == "pending_review"
    assert db_docs.get_by_id(ids["TR"])["doc_review_status"] == "pending_review"
    assert db_rp.get_by_group(ids["group_id"]) is not None

    event_rows = get_store()._fetch_all(
        "SELECT metadata FROM workflow_events WHERE event_type = 'workflow_reopen'"
    )
    assert len(event_rows) == 1
    metadata = json.loads(event_rows[0]["metadata"])
    assert metadata.get("run_id") == run["run_id"]
    assert metadata.get("reason") == "test_run_code_failure"

    # The screen half: the real pipeline must end on a refresh emitted AFTER the rewind
    # committed, otherwise every open browser keeps rendering the pre-rewind TS.
    assert emitted[-1][0] == "group_view_refresh"
    assert emitted[-1][1]["reason"] == "test_run_code_failure_auto_reopen"
    assert emitted[-1][1]["doc_id"] == ids["TS"]


# ── production-shape replay (R0001 0381 — the rewind must fire on REAL runs) ──
#
# Tests 1-11 build a synthetic group. The rejections said the opposite of what those
# tests assert: "실제로 테스트를 돌려보면 여전히 승인 완료로 남는다". The three tests
# below are seeded from rows read out of the live database instead, so a shape that
# only exists in production cannot pass here and fail there.

_PROD_DOCS = [
    # (seq, type_code, review_status, status, has_file) — timeweaver.agent.0003, read live.
    (1, "B", "wf_done", "open", True),          # the root is a B(버그), not an R
    (2, "N", "approved", "open", True),
    (3, "NR", "approved", "open", True),
    (4, "T", "approved", "open", True),
    (5, "TR", "approved", "open", True),
    (6, "TS", "approved", "open", True),        # <- the failing 테스트시나리오지시
    (7, "TSR", "approved", "open", True),
    (8, "AC", "approved", "draft", False),      # file-less, status 'draft'
]
_PROD_SEQUENCE = [
    (1, "N", "조사지시"), (2, "NR", "조사레포트"), (3, "T", "작업지시"),
    (4, "TR", "작업레포트"), (5, "TS", "테스트시나리오지시"), (6, "TSR", "테스트레포트"),
]
# Verbatim from test_run_cases of trun_20260801_000006 / trun_20260728_000005 /
# trun_20260727_000001 — three genuine REDs whose own case output names a missing module.
_PROD_MODULE_ERROR_TAIL = (
    r"ImportError while loading conftest 'C:\src\server\tests\conftest.py'." "\r\n"
    r"E   ModuleNotFoundError: No module named 'time_weaver'" "\r\n"
)
_PROD_NPM_SETUP_TAIL = (
    "up to date, audited 938 packages in 6s\n53 vulnerabilities (4 low, 24 moderate)\n"
    "npm warn EBADENGINE Unsupported engine {\n"
)


def _seed_production_group(storage_root: Path, suffix: str) -> dict:
    """Rebuild the exact document/sequence shape of a live group that went RED."""
    from modules.flow_gate.db import documents as db_docs
    from modules.flow_gate.db import groups as db_groups
    from modules.flow_gate.db import projects, users
    from modules.flow_gate.db import workflow_sequences as db_wfseq
    from modules.flow_gate.storage import paths as storage_paths

    project_id = f"prod0381{suffix}"
    group_id = f"prod0381.agent.{suffix}"
    user_id = "4d96c7c2-c0be-4f4e-8594-bd65d2a8fa39"   # the live owner_id
    projects.create({"project_id": project_id, "project_name": f"Prod {suffix}"})
    users.create({
        "user_id": user_id, "username": f"prod{suffix}",
        "email": f"prod{suffix}@test.com", "password": "hashed",
    })
    db_groups.create({
        "group_id": group_id, "project_id": project_id,
        "module": "agent", "title": "production replay",
    })

    ids = {"project_id": project_id, "group_id": group_id, "user_id": user_id}
    for seq, type_code, review_status, status, has_file in _PROD_DOCS:
        doc_code = f"{seq:04d}-{type_code}"
        doc_id = f"{group_id}.{doc_code}"
        file_path = None
        if has_file:
            path = storage_paths.document_path(
                project_id=project_id, group_code=group_id, doc_code=doc_code,
                filename="document.md", module="agent",
            )
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"---\ntitle: {type_code}\n---\n# {type_code}\n", encoding="utf-8")
            file_path = storage_paths.to_storage_relative(path, project_id)
        db_docs.create({
            "doc_id": doc_id, "project_id": project_id, "module": "agent",
            "group_id": group_id, "type_code": type_code, "seq": seq,
            "title": type_code, "owner_id": user_id, "file_path": file_path,
        })
        db_docs.update(doc_id, {"doc_review_status": review_status, "status": status})
        ids[type_code] = doc_id

    db_wfseq.insert_sequence(ids["B"])
    sequence = db_wfseq.get_sequence_by_doc_id(ids["B"])
    for item_seq, type_code, label in _PROD_SEQUENCE:
        db_wfseq.insert_sequence_item(
            sequence["id"], item_seq, type_code, label, "B", item_seq - 1
        )
    for item in db_wfseq.get_sequence_items(sequence["id"]):
        if ids.get(item["type"]):
            db_wfseq.set_item_result_doc_id(item["id"], ids[item["type"]])
    return ids


def _run_production_red(monkeypatch, tmp_path, ids, cases, setup=None):
    """Drive the real worker entry point over production-shaped cases. No classifier mock."""
    from modules.flow_gate.db import test_runs as db_runs
    from modules.flow_gate.services import test_run_service
    from modules.flow_gate.services import workflow_rework_service as rework

    setup = setup or []
    monkeypatch.setattr(rework.git_service, "reopen_group_git", MagicMock())
    run = db_runs.insert_run(
        doc_id=ids["TS"], revision_no=0, triggered_via="ui", runner_id=ids["user_id"],
        setup=[{"kind": "setup", "case_no": s["case_no"], "title": "", "cmd": "build", "expect": ""}
               for s in setup],
        cases=[{"case_no": c["case_no"], "title": c["case_no"], "cmd": "run", "expect": "pass"}
               for c in cases],
    )

    monkeypatch.setattr(test_run_service.storage_paths, "resolve_project_src_root", lambda *_a, **_k: tmp_path)
    monkeypatch.setattr(test_run_service, "_record_source_root", lambda *_: None)
    monkeypatch.setattr(test_run_service, "_allocate_port", lambda: 19003)
    monkeypatch.setattr(test_run_service, "_scratch_dir", lambda *_: tmp_path / "scratch")

    setup_by_no = {s["case_no"]: s for s in setup}

    def _fake_setup(_doc, run_row, steps, *_a):
        for step in steps:
            spec = setup_by_no[step["case_no"]]
            db_runs.mark_case_finished(
                case_id=step["id"], result=spec["result"], exit_code=spec.get("exit_code"),
                duration_ms=11, output_tail=spec.get("output_tail") or "",
            )
        return (False, None)   # the environment build itself did not abort the run

    monkeypatch.setattr(test_run_service, "_execute_setup", _fake_setup)

    by_no = {c["case_no"]: c for c in cases}

    def _fake_case(_doc, _run, case, *_a):
        spec = by_no[case["case_no"]]
        db_runs.mark_case_finished(
            case_id=case["id"], result=spec["result"],
            exit_code=spec.get("exit_code"), duration_ms=7,
            output_tail=spec.get("output_tail") or "",
        )

    monkeypatch.setattr(test_run_service, "_execute_case", _fake_case)
    monkeypatch.setattr(test_run_service, "_execute_teardown", lambda *_: None)
    monkeypatch.setattr(test_run_service, "_finalize_services", lambda *_: None)
    monkeypatch.setattr(test_run_service, "_remove_scratch", lambda *_: None)
    monkeypatch.setattr(test_run_service, "_broadcast", lambda *_a, **_k: None)

    test_run_service._execute_run_inner(run)
    return db_runs.get_run(run["run_id"])


def test_12_production_group_shape_red_sends_the_ts_back(auto_store, monkeypatch, tmp_path):
    """A live group's real shape — B root at wf_done, an approved TSR behind the TS, and a
    file-less AC — must still put the 테스트시나리오지시 back to 승인이전.
    """
    from modules.flow_gate.db import documents as db_docs
    from modules.flow_gate.db import workflow_return_points as db_rp

    ids = _seed_production_group(auto_store, "shape")
    finished = _run_production_red(monkeypatch, tmp_path, ids, [
        {"case_no": "TC-1", "result": "pass", "exit_code": 0, "output_tail": "14 passed"},
        {"case_no": "TC-2", "result": "fail", "exit_code": 1,
         "output_tail": "NameError: name 'sort' is not defined"},
    ])

    assert finished["status"] == "failed" and finished["error"] is None
    assert db_docs.get_by_id(ids["TS"])["doc_review_status"] == "pending_review"
    assert db_docs.get_by_id(ids["TSR"])["doc_review_status"] == "pending_review"
    # the B root, not just an R root, must come back off wf_done
    assert db_docs.get_by_id(ids["B"])["doc_review_status"] == "wf_in_progress"
    # a file-less AC is archived, never deleted (FlowGate is a time machine)
    ac = db_docs.get_by_id(ids["AC"])
    assert ac is not None and ac["status"] == "archived"
    assert db_rp.get_by_group(ids["group_id"]) is not None


def test_13_real_red_whose_case_log_names_a_missing_module_still_rewinds(
    auto_store, monkeypatch, tmp_path
):
    """The defect the live data exposed.

    classify_failure scanned EVERY step's log for "No module named" and called the run
    INFRA. But a test case that ran and printed ModuleNotFoundError is the product's own
    missing import — a real RED. Replaying the 30 most recent failed runs in production
    through the old rule mislabelled 3 of the 27 genuine REDs, and for those the rewind
    silently never fired: the TS stayed 승인 완료 exactly as reported.
    """
    from modules.flow_gate.db import documents as db_docs
    from modules.flow_gate.services import engine_recipe_service as svc

    run = {"error": None, "case_passed": 0, "case_failed": 3}
    items = [{"kind": "case", "exit_code": 1, "output_tail": _PROD_MODULE_ERROR_TAIL}] * 3
    assert svc.classify_failure(run, items) == svc.CODE

    ids = _seed_production_group(auto_store, "moderr")
    finished = _run_production_red(
        monkeypatch, tmp_path, ids,
        [{"case_no": "TC-%d" % n, "result": "fail", "exit_code": 1,
          "output_tail": _PROD_MODULE_ERROR_TAIL} for n in (1, 2, 3)],
        setup=[{"case_no": "SETUP-1", "result": "pass", "exit_code": 0,
                "output_tail": _PROD_NPM_SETUP_TAIL}],
    )

    assert finished["status"] == "failed" and finished["error"] is None
    assert db_docs.get_by_id(ids["TS"])["doc_review_status"] == "pending_review"


def test_14_missing_module_from_a_setup_step_is_still_infra_and_never_rewinds(
    auto_store, monkeypatch, tmp_path
):
    """The other half of the same rule — do not over-correct.

    When the environment build is what could not find the module, the test never ran:
    that stays INFRA and the TS must keep its approval so the 0157 repair loop owns it.
    """
    from modules.flow_gate.db import documents as db_docs
    from modules.flow_gate.services import engine_recipe_service as svc

    setup_items = [
        {"kind": "setup", "exit_code": 1, "output_tail": _PROD_MODULE_ERROR_TAIL},
        {"kind": "case", "exit_code": 1, "output_tail": "AssertionError"},
    ]
    assert svc.classify_failure({"error": None, "case_passed": 1, "case_failed": 1}, setup_items) == svc.INFRA

    ids = _seed_production_group(auto_store, "setuperr")
    finished = _run_production_red(
        monkeypatch, tmp_path, ids,
        [{"case_no": "TC-1", "result": "pass", "exit_code": 0, "output_tail": "ok"},
         {"case_no": "TC-2", "result": "fail", "exit_code": 1,
          "output_tail": "AssertionError: expected 2 got 3"}],
        setup=[{"case_no": "SETUP-1", "result": "fail", "exit_code": 1,
                "output_tail": _PROD_MODULE_ERROR_TAIL}],
    )
    assert finished["status"] == "failed"
    assert db_docs.get_by_id(ids["TS"])["doc_review_status"] == "approved"


# ── the workflow SEQUENCE itself, not just the document row ───────────────────
#
# Everything above proves documents.doc_review_status flips. The rejection is about the
# other half: "워크플로 시퀀스가 [테스트레포트]에서 가만히 있는 게 아니라 [테스트시나리지시]가
# 승인되기 전으로 되돌리라고". The strip's current step is derived, never stored — it is
# workflow_sequences.get_effective_head (linked-but-unapproved slot wins over the first
# unlinked slot) and the workflow_head_index that DocHeader renders. A RED assembles no
# 테스트레포트, so before the rewind the head sits on the empty TSR slot; that is the state
# being reported. These two tests pin the head MOVING OFF it, through the real worker.


def _sequence_head(ids: dict) -> Optional[dict]:
    from modules.flow_gate.db import workflow_sequences as db_wfseq

    root_id = ids.get("R") or ids["B"]
    sequence = db_wfseq.get_sequence_by_doc_id(root_id)
    return db_wfseq.get_effective_head(sequence["id"])


def _slot_status(ids: dict, type_code: str) -> Optional[str]:
    from modules.flow_gate.db import workflow_sequences as db_wfseq

    root_id = ids.get("R") or ids["B"]
    sequence = db_wfseq.get_sequence_by_doc_id(root_id)
    for item in db_wfseq.get_sequence_items(sequence["id"]):
        if item["type"] == type_code:
            return item.get("status")
    return None


def _run_red_on_seeded_group(monkeypatch, tmp_path, ids):
    """Real `_execute_run_inner` over one failing case. Only OS execution is stubbed."""
    from modules.flow_gate.db import test_runs as db_runs
    from modules.flow_gate.services import test_run_service
    from modules.flow_gate.services import workflow_rework_service as rework

    monkeypatch.setattr(rework.git_service, "reopen_group_git", MagicMock())
    run = db_runs.insert_run(
        doc_id=ids["TS"], revision_no=0, triggered_via="ui", runner_id=ids["user_id"],
        cases=[{"case_no": "TC-1", "title": "assert", "cmd": "run", "expect": "pass"}],
    )
    monkeypatch.setattr(test_run_service.storage_paths, "resolve_project_src_root", lambda *_a, **_k: tmp_path)
    monkeypatch.setattr(test_run_service, "_record_source_root", lambda *_: None)
    monkeypatch.setattr(test_run_service, "_allocate_port", lambda: 19004)
    monkeypatch.setattr(test_run_service, "_scratch_dir", lambda *_: tmp_path / "scratch")
    monkeypatch.setattr(test_run_service, "_execute_setup", lambda *_: (False, None))
    monkeypatch.setattr(test_run_service, "_execute_case", lambda _d, _r, case, *_a: db_runs.mark_case_finished(
        case_id=case["id"], result="fail", exit_code=1, duration_ms=3,
        output_tail="AssertionError: expected 2 got 3",
    ))
    monkeypatch.setattr(test_run_service, "_execute_teardown", lambda *_: None)
    monkeypatch.setattr(test_run_service, "_finalize_services", lambda *_: None)
    monkeypatch.setattr(test_run_service, "_remove_scratch", lambda *_: None)
    monkeypatch.setattr(test_run_service, "_broadcast", lambda *_a, **_k: None)
    test_run_service._execute_run_inner(run)
    return db_runs.get_run(run["run_id"])


def test_15_sequence_head_leaves_the_tsr_slot_and_returns_to_the_ts(
    auto_store, monkeypatch, tmp_path
):
    """워크플로 시퀀스가 [테스트레포트]에 머무르지 않고 [테스트시나리지시]로 돌아온다.

    Asserted on both surfaces the UI reads: get_effective_head (the sequence strip's
    single source of truth) and workflow_head_index/-_type (what DocHeader renders as
    "현재 단계"). Driven by the real worker, with nothing in the rewind path mocked.
    """
    from modules.flow_gate.db import documents as db_docs
    from modules.flow_gate.documents.routers import documents as doc_routes

    ids = _seed_group(auto_store, "seqhead")

    # Before: the TS is 승인 완료, the RED produced no 테스트레포트, so the sequence is
    # parked on the empty TSR slot — the reported "테스트레포트에서 가만히 있는" state.
    before = _sequence_head(ids)
    assert before is not None and before["type"] == "TSR"
    assert before["result_doc_id"] is None
    assert _slot_status(ids, "TS") == "done"
    before_view = doc_routes._parse_doc_workflow(db_docs.get_by_id(ids["TS"]))
    assert before_view["workflow_steps"] == ["T", "TS", "TSR", "TR"]
    assert before_view["workflow_head_index"] == 2          # TSR
    assert before_view["workflow_head_type"] == "TSR"

    finished = _run_red_on_seeded_group(monkeypatch, tmp_path, ids)
    assert finished["status"] == "failed" and finished["error"] is None

    # After: the head is the TS slot itself, and that slot is no longer 'done'.
    after = _sequence_head(ids)
    assert after is not None and after["type"] == "TS"
    assert after["result_doc_id"] == ids["TS"]
    assert _slot_status(ids, "TS") == "in_progress"
    assert db_docs.get_by_id(ids["TS"])["doc_review_status"] == "pending_review"

    after_view = doc_routes._parse_doc_workflow(db_docs.get_by_id(ids["TS"]))
    assert after_view["workflow_head_index"] == 1           # TS, one step back
    assert after_view["workflow_head_type"] == "TS"
    assert after_view["workflow_head_doc_id"] == ids["TS"]
    # the TS is the head, i.e. the document the user is now expected to fix
    assert after_view["workflow_self_index"] == after_view["workflow_head_index"]


def test_16_production_shape_sequence_head_returns_to_the_ts(auto_store, monkeypatch, tmp_path):
    """Same assertion on the live group shape: B root at wf_done with an approved TSR.

    There every slot is 'done' and the head is None (the workflow reads as 최종승인 대기),
    so "가만히 있는" looks different but must resolve to the same place — the TS slot.
    """
    from modules.flow_gate.db import documents as db_docs

    ids = _seed_production_group(auto_store, "seqhead")
    assert _sequence_head(ids) is None                       # nothing left to do
    assert _slot_status(ids, "TS") == "done"
    assert _slot_status(ids, "TSR") == "done"

    finished = _run_production_red(monkeypatch, tmp_path, ids, [
        {"case_no": "TC-1", "result": "pass", "exit_code": 0, "output_tail": "14 passed"},
        {"case_no": "TC-2", "result": "fail", "exit_code": 1,
         "output_tail": "NameError: name 'sort' is not defined"},
    ])
    assert finished["status"] == "failed" and finished["error"] is None

    head = _sequence_head(ids)
    assert head is not None and head["type"] == "TS"
    assert head["result_doc_id"] == ids["TS"]
    assert _slot_status(ids, "TS") == "in_progress"
    assert db_docs.get_by_id(ids["B"])["doc_review_status"] == "wf_in_progress"


# ── The link the previous revisions were missing ──────────────────────────────
# Everything above proves the DATABASE moved. None of it proves the SCREEN moved:
# the workflow strip's colour and the action bar's buttons are computed in the
# browser from the document-detail payload, so a server assertion can be green while
# the strip still paints [테스트시나리오지시] 녹색(완료) and the action bar still shows
# no 승인/반려. This test pins the exact payload the browser receives; the client spec
# client/tests/main/workflowHead.autoReopen.0381.spec.ts reads the SAME file and asserts
# what the real components render from it. Neither side can drift alone.

_UI_CONTRACT_FIXTURE = (
    Path(__file__).resolve().parents[2]
    / "client" / "tests" / "main" / "fixtures" / "autoReopenWorkflowHead.0381.json"
)
_UI_CONTRACT_KEYS = (
    "doc_id", "type_code", "project_id", "group_id", "doc_review_status",
    "workflow_steps", "workflow_root_type", "workflow_head_index", "workflow_head_type",
    "workflow_head_status", "workflow_head_doc_id", "workflow_head_doc_review_status",
    "workflow_self_index", "next_step_exists",
)


def _ui_contract_payload(doc_id: str) -> dict:
    """The document-detail subset DocHeader exposes to the workflow strip / action bar."""
    from modules.flow_gate.db import documents as db_docs
    from modules.flow_gate.documents.routers import documents as doc_routes

    doc = db_docs.get_by_id(doc_id)
    view = dict(doc_routes._parse_doc_workflow(doc))
    for key in ("doc_id", "type_code", "project_id", "group_id", "doc_review_status"):
        view.setdefault(key, doc.get(key))
        if view.get(key) is None:
            view[key] = doc.get(key)
    return {key: view.get(key) for key in _UI_CONTRACT_KEYS}


def test_17_ui_contract_payload_matches_the_checked_in_fixture(
    auto_store, monkeypatch, tmp_path
):
    """Pin the browser-facing payload before and after the automatic rewind.

    'uilink' is not an arbitrary suffix — the fixture's doc ids are built from it, so a
    renamed seed makes this fail loudly instead of silently comparing nothing.
    """
    fixture = json.loads(_UI_CONTRACT_FIXTURE.read_text(encoding="utf-8"))
    ids = _seed_production_group(auto_store, "uilink")
    assert ids["TS"] == fixture["tab_doc_id"]

    assert _ui_contract_payload(ids["TS"]) == fixture["before"]

    finished = _run_production_red(monkeypatch, tmp_path, ids, [
        {"case_no": "TC-1", "result": "pass", "exit_code": 0, "output_tail": "14 passed"},
        {"case_no": "TC-2", "result": "fail", "exit_code": 1,
         "output_tail": "NameError: name 'sort' is not defined"},
    ])
    assert finished["status"] == "failed" and finished["error"] is None

    assert _ui_contract_payload(ids["TS"]) == fixture["after"]


# ── the failure the reporter actually ran: the 준비(setup) 단계 aborts ──────────
#
# Read out of the preview server's own database (flowgate_test): 15 consecutive runs of
# test.test.0042.0006-TS between 06:54 and 09:38 on 2026-08-03, every one of them
#   status=failed, error='setup_failed', triggered_via='ui', case_passed=0, case_failed=0
# with SETUP-1 exiting 1 on "The system cannot find the path specified." Not one test case
# ever ran, so nothing above this line could fire: error='setup_failed' is in
# _INFRA_ERROR_CODES, and the setup branch never called the rewind at all. The 테스트시나리오
# 지시 stayed 승인 완료 through all 15 attempts — precisely what was reported.

_LIVE_SETUP_CMD = (
    r"cd server && python -m venv .venv && .venv\Scripts\python -m pip install -q -r "
    r"requirements.txt && mariadb-install-db --datadir=\"{SCRATCH}\mariadb\" "
    r"--password=flowgate --port={PORT} --silent && cd ..\client && npm install"
)
_LIVE_SETUP_TAIL = "The system cannot find the path specified.\r\n"


def _run_setup_abort(monkeypatch, tmp_path, ids, *, triggered_via="ui"):
    """Real `_execute_run_inner` whose 준비 단계 aborts, exactly like the live rows."""
    from modules.flow_gate.db import test_runs as db_runs
    from modules.flow_gate.services import test_run_service
    from modules.flow_gate.services import workflow_rework_service as rework

    monkeypatch.setattr(rework.git_service, "reopen_group_git", MagicMock())
    run = db_runs.insert_run(
        doc_id=ids["TS"], revision_no=0, triggered_via=triggered_via, runner_id=ids["user_id"],
        setup=[{"kind": "setup", "case_no": "SETUP-1", "title": "",
                "cmd": _LIVE_SETUP_CMD, "expect": ""}],
        cases=[{"case_no": "TC-1", "title": "contract", "cmd": "pytest -q", "expect": "pass"}],
    )
    monkeypatch.setattr(test_run_service.storage_paths, "resolve_project_src_root", lambda *_a, **_k: tmp_path)
    monkeypatch.setattr(test_run_service, "_record_source_root", lambda *_: None)
    monkeypatch.setattr(test_run_service, "_allocate_port", lambda: 19005)
    monkeypatch.setattr(test_run_service, "_scratch_dir", lambda *_: tmp_path / "scratch")

    def _aborting_setup(_doc, run_row, steps, *_a):
        for step in steps:
            db_runs.mark_case_finished(
                case_id=step["id"], result="fail", exit_code=1, duration_ms=2900,
                output_tail=_LIVE_SETUP_TAIL,
            )
        return (True, "setup_failed")          # the stage aborts — no case is ever started

    monkeypatch.setattr(test_run_service, "_execute_setup", _aborting_setup)
    monkeypatch.setattr(test_run_service, "_execute_case", MagicMock())
    monkeypatch.setattr(test_run_service, "_execute_teardown", lambda *_: None)
    monkeypatch.setattr(test_run_service, "_finalize_services", lambda *_: None)
    monkeypatch.setattr(test_run_service, "_remove_scratch", lambda *_: None)
    monkeypatch.setattr(test_run_service, "_broadcast", lambda *_a, **_k: None)

    test_run_service._execute_run_inner(run)
    return db_runs.get_run(run["run_id"])


def test_18_manual_setup_abort_sends_the_scenario_back_to_pre_approval(
    auto_store, monkeypatch, tmp_path
):
    """The reported case, replayed end to end: 준비 단계 실패도 [테스트시나리지시]로 되돌린다."""
    from modules.flow_gate.db import documents as db_docs
    from modules.flow_gate.db import test_runs as db_runs
    from modules.flow_gate.db import workflow_return_points as db_rp

    ids = _seed_production_group(auto_store, "setupabort")
    assert _sequence_head(ids) is None                      # 최종승인 대기, 시퀀스가 멈춰 있다
    assert db_docs.get_by_id(ids["TS"])["doc_review_status"] == "approved"

    finished = _run_setup_abort(monkeypatch, tmp_path, ids)

    # the live row shape, reproduced
    assert finished["status"] == "failed"
    assert finished["error"] == "setup_failed"
    assert (finished["case_passed"] or 0) == 0 and (finished["case_failed"] or 0) == 0

    # …and now the workflow actually moves
    assert db_docs.get_by_id(ids["TS"])["doc_review_status"] == "pending_review"
    head = _sequence_head(ids)
    assert head is not None and head["type"] == "TS"
    assert head["result_doc_id"] == ids["TS"]
    assert _slot_status(ids, "TS") == "in_progress"
    assert db_docs.get_by_id(ids["B"])["doc_review_status"] == "wf_in_progress"
    assert db_rp.get_by_group(ids["group_id"]) is not None

    # the failed setup evidence is kept, not thrown away
    cases = db_runs.list_cases(finished["run_id"])
    assert any(c.get("kind") == "setup" and c.get("result") == "fail" for c in cases)

    # and the browser gets the same payload the UI contract test pins
    payload = _ui_contract_payload(ids["TS"])
    assert payload["workflow_head_type"] == "TS"
    assert payload["workflow_head_status"] == "in_progress"
    assert payload["doc_review_status"] == "pending_review"


def test_19_unmanned_setup_abort_still_belongs_to_the_repair_loop(monkeypatch):
    """Do not steal the 0157 INFRA loop's failure (T0004 완료기준 3)."""
    from modules.flow_gate.services import engine_recipe_service
    from modules.flow_gate.services import test_run_service
    from modules.flow_gate.services import workflow_rework_service as rework

    auto = MagicMock()
    notify = MagicMock()
    monkeypatch.setattr(rework, "auto_reopen_failed_ts", auto)
    monkeypatch.setattr(test_run_service, "_maybe_notify_chain_failure", notify)

    for recovery in ("repair", "escalated"):
        monkeypatch.setattr(engine_recipe_service, "handle_run_failure", lambda *_a, r=recovery: r)
        assert test_run_service._handle_setup_stage_failure(
            {"doc_id": "g.0006-TS"}, {"run_id": "setup-%s" % recovery}, []
        ) is None
    auto.assert_not_called()
    notify.assert_not_called()


# ── the reporter's own group, rebuilt row by row ──────────────────────────────

_LIVE_0042_DOCS = [
    # (seq, type_code, review_status, status) — read from flowgate_test.documents,
    # group test.test.0042, the group every one of the 15 failed runs belongs to.
    (1, "R", "wf_in_progress", "closed"),
    (2, "N", "approved", "open"),
    (3, "NR", "approved", "open"),
    (4, "T", "approved", "open"),
    (5, "TR", "approved", "open"),
    (6, "TS", "approved", "draft"),          # 승인 완료인데 status 는 draft
]
_LIVE_0042_SEQUENCE = [
    (1, "N", "調査指示"), (2, "NR", "調査レポート"), (3, "T", "タスク指示"),
    (4, "TR", "タスクレポート"), (5, "TS", "テストシナリオ指示"), (6, "TSR", "テストレポート"),
]


def _seed_live_0042(storage_root: Path, suffix: str) -> dict:
    """test.test.0042 exactly as it stands in the preview database — no TSR doc, no AC."""
    from modules.flow_gate.db import documents as db_docs
    from modules.flow_gate.db import groups as db_groups
    from modules.flow_gate.db import projects, users
    from modules.flow_gate.db import workflow_sequences as db_wfseq
    from modules.flow_gate.storage import paths as storage_paths

    project_id = f"live0042{suffix}"
    group_id = f"live0042.test.{suffix}"
    user_id = "140e7511-cac3-4d73-8833-b5063e0a7d33"      # the reporter's own user_id
    projects.create({"project_id": project_id, "project_name": "live 0042"})
    users.create({"user_id": user_id, "username": f"live{suffix}",
                  "email": f"live{suffix}@test.com", "password": "hashed"})
    db_groups.create({"group_id": group_id, "project_id": project_id,
                      "module": "test", "title": "live 0042"})

    ids = {"project_id": project_id, "group_id": group_id, "user_id": user_id}
    for seq, type_code, review_status, status in _LIVE_0042_DOCS:
        doc_code = f"{seq:04d}-{type_code}"
        doc_id = f"{group_id}.{doc_code}"
        path = storage_paths.document_path(
            project_id=project_id, group_code=group_id, doc_code=doc_code,
            filename="document.md", module="test",
        )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"---\ntitle: {type_code}\n---\n# {type_code}\n", encoding="utf-8")
        db_docs.create({
            "doc_id": doc_id, "project_id": project_id, "module": "test",
            "group_id": group_id, "type_code": type_code, "seq": seq,
            "title": type_code, "owner_id": user_id,
            "file_path": storage_paths.to_storage_relative(path, project_id),
        })
        db_docs.update(doc_id, {"doc_review_status": review_status, "status": status})
        ids[type_code] = doc_id

    db_wfseq.insert_sequence(ids["R"])
    sequence = db_wfseq.get_sequence_by_doc_id(ids["R"])
    for item_seq, type_code, label in _LIVE_0042_SEQUENCE:
        db_wfseq.insert_sequence_item(
            sequence["id"], item_seq, type_code, label, "R", item_seq - 1
        )
    for item in db_wfseq.get_sequence_items(sequence["id"]):
        if ids.get(item["type"]):
            db_wfseq.set_item_result_doc_id(item["id"], ids[item["type"]])
    return ids


def test_20_the_reported_group_and_run_end_up_where_the_reporter_asked(
    auto_store, monkeypatch, tmp_path
):
    """The whole complaint, measured on both surfaces of the reporter's own group.

    Before: the strip parks on the empty [테스트레포트] slot, the [테스트시나리오지시] cell is
    'done' (green, app.css:403) and the action bar for that tab is the forward [다음 단계] —
    there is nothing to approve or reject. After: the head IS the TS slot, its cell is
    'current' (blue, app.css:405) and the bar is the 승인/반려 review bar.
    """
    from modules.flow_gate.db import documents as db_docs

    fixture = json.loads(_UI_CONTRACT_FIXTURE.read_text(encoding="utf-8"))
    ids = _seed_live_0042(auto_store, "repro")
    assert ids["TS"] == fixture["reported_tab_doc_id"]

    before = _ui_contract_payload(ids["TS"])
    assert before == fixture["reported_before"]
    assert before["doc_review_status"] == "approved"
    assert before["workflow_head_type"] == "TSR"        # parked on the empty 테스트레포트 slot
    assert before["workflow_head_status"] == "pending"
    assert before["workflow_head_index"] == 5
    assert before["workflow_self_index"] == 4          # the TS sits BEHIND the head → 'done'

    finished = _run_setup_abort(monkeypatch, tmp_path, ids)
    assert finished["status"] == "failed" and finished["error"] == "setup_failed"

    after = _ui_contract_payload(ids["TS"])
    assert after == fixture["reported_after"]
    assert after["doc_review_status"] == "pending_review"
    assert after["workflow_head_type"] == "TS"
    assert after["workflow_head_status"] == "in_progress"
    assert after["workflow_head_index"] == 4           # the head moved back one slot
    assert after["workflow_head_doc_id"] == ids["TS"]
    assert after["workflow_head_index"] == after["workflow_self_index"]

    # the R root was already mid-flight and must not be pushed anywhere
    assert db_docs.get_by_id(ids["R"])["doc_review_status"] == "wf_in_progress"
    # the earlier steps keep their approval — only the TS and what follows it come back
    for type_code in ("N", "NR", "T", "TR"):
        assert db_docs.get_by_id(ids[type_code])["doc_review_status"] == "approved"
