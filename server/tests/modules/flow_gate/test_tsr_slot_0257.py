"""Group the regression: one active TSR per workflow slot, and a terminal report failure.

Every test here fails against the unmodified code (see TR0005 "검증" for the recorded
red run) — they pin the rerun behaviour, not the behaviour main already had.
"""
from __future__ import annotations

import pytest


TS_DOC = {
    "doc_id": "flowgate.default.0257.0099-TS",
    "project_id": "flowgate",
    "branch": "main",
    "module": "default",
    "group_id": "flowgate.default.0257",
    "type_code": "TS",
    "title": "재테스트 시나리오",
    "owner_id": "owner-1",
    "doc_review_status": "pending_review",
    "revision_no": 3,
}

ACTIVE_TSR_ID = "flowgate.default.0257.0100-TSR"

CASES = [
    {
        "kind": "case",
        "case_no": "TC-1",
        "case_title": "smoke",
        "result": "pass",
        "exit_code": 0,
        "duration_ms": 1200,
        "output_tail": "ok",
    }
]


class _Recorder:
    """Collects the side effects assemble_tsr is allowed to have."""

    def __init__(self) -> None:
        self.reserved: list[tuple] = []
        self.created: list[dict] = []
        self.updated: list[tuple] = []
        self.registered: list[dict] = []
        self.transitions: list[dict] = []


@pytest.fixture
def env(monkeypatch, tmp_path):
    """Wire assemble_tsr to a temp filesystem and in-memory doc/workflow stores."""
    from modules.flow_gate.db import workflow_sequences as db_wfseq
    from modules.flow_gate.services import test_run_service
    from modules.flow_gate.workflow import pipeline_service

    rec = _Recorder()
    docs: dict[str, dict] = {}

    def document_path(*, project_id, group_code, doc_code, filename, module, branch):
        path = tmp_path / project_id / branch / module / group_code / f"{doc_code}_{filename}"
        return path

    monkeypatch.setattr(test_run_service.storage_paths, "document_path", document_path)
    monkeypatch.setattr(
        test_run_service.storage_paths,
        "to_storage_relative",
        lambda path, project_id=None: str(path),
    )

    def reserve_document(group_id, type_code, module=None):
        rec.reserved.append((group_id, type_code, module))
        return "0101-TSR"

    monkeypatch.setattr(
        test_run_service.numbering_service, "reserve_document", reserve_document
    )
    monkeypatch.setattr(
        test_run_service.id_formatter, "parse_doc_code", lambda code: ("TSR", 101)
    )

    def create(data):
        rec.created.append(data)
        docs[data["doc_id"]] = dict(data)
        return docs[data["doc_id"]]

    def update(doc_id, updates):
        rec.updated.append((doc_id, updates))
        docs.setdefault(doc_id, {"doc_id": doc_id}).update(updates)
        return docs[doc_id]

    monkeypatch.setattr(test_run_service.db_docs, "create", create)
    monkeypatch.setattr(test_run_service.db_docs, "update", update)
    monkeypatch.setattr(test_run_service.db_docs, "get_by_id", lambda doc_id: docs.get(doc_id))
    monkeypatch.setattr(
        test_run_service, "_maybe_chain_auto_approve_tsr", lambda *_a, **_kw: None
    )

    def register_workflow_result(**kwargs):
        rec.registered.append(kwargs)
        return None

    def transition_document_review(**kwargs):
        rec.transitions.append(kwargs)
        return None

    monkeypatch.setattr(pipeline_service, "register_workflow_result", register_workflow_result)
    monkeypatch.setattr(
        pipeline_service, "transition_document_review", transition_document_review
    )

    # Default workflow wiring: no sequence, no prior report. Tests override per case.
    monkeypatch.setattr(db_wfseq, "get_sequence_for_member_doc", lambda _doc_id: None)
    monkeypatch.setattr(db_wfseq, "get_sequence_items", lambda _seq_id: [])
    monkeypatch.setattr(db_wfseq, "get_pending_head_by_group", lambda *_a: None)
    monkeypatch.setattr(db_wfseq, "get_item_by_result_doc_id", lambda _doc_id: None)
    monkeypatch.setattr(
        test_run_service.db_docs, "get_documents_by_target_id", lambda *_a, **_kw: []
    )

    return {
        "rec": rec,
        "docs": docs,
        "svc": test_run_service,
        "wfseq": db_wfseq,
        "monkeypatch": monkeypatch,
    }


def _run(run_id="trun_2", tsr_doc_id=None):
    return {
        "run_id": run_id,
        "doc_id": TS_DOC["doc_id"],
        "revision_no": 3,
        "status": "running",
        "started_at": "2026-07-17T05:30:00+09:00",
        "port": 8123,
        "tsr_doc_id": tsr_doc_id,
    }


def _seed_active_tsr(env, *, slot_bound=True, review_status="approved"):
    """An existing report for this TS, optionally holding the workflow slot."""
    env["docs"][ACTIVE_TSR_ID] = {
        "doc_id": ACTIVE_TSR_ID,
        "target_id": TS_DOC["doc_id"],
        "type_code": "TSR",
        "revision_no": 0,
        "superseded_by": None,
        "doc_review_status": review_status,
        "file_path": "documents/flowgate/main/default/0257/0100-TSR_document.md",
    }
    env["monkeypatch"].setattr(
        env["svc"].db_docs,
        "get_documents_by_target_id",
        lambda *_a, **_kw: [env["docs"][ACTIVE_TSR_ID]],
    )
    if slot_bound:
        env["monkeypatch"].setattr(
            env["wfseq"],
            "get_item_by_result_doc_id",
            lambda doc_id: {"id": 42, "type": "TSR"} if doc_id == ACTIVE_TSR_ID else None,
        )


# ── the review required regressions (a)/(b): two successful runs, one active TSR slot ──

def test_rerun_revises_active_tsr_instead_of_reserving_a_second_number(env):
    """Main the regression symptom: rerunning tests created a second TSR document."""
    _seed_active_tsr(env)

    tsr_doc_id = env["svc"].assemble_tsr(TS_DOC, _run(), CASES)

    assert tsr_doc_id == ACTIVE_TSR_ID
    assert env["rec"].reserved == [], "재실행이 새 TSR 번호를 예약하면 안 된다"
    assert env["rec"].created == [], "재실행이 두 번째 TSR 문서를 만들면 안 된다"
    assert [doc_id for doc_id, _ in env["rec"].updated] == [ACTIVE_TSR_ID]


def test_rerun_rewrites_the_active_report_file_with_the_new_run(env, tmp_path):
    """Refresh the active TSR body and file_path with this run, preventing orphan previews."""
    _seed_active_tsr(env)

    env["svc"].assemble_tsr(TS_DOC, _run(run_id="trun_second"), CASES)

    _doc_id, updates = env["rec"].updated[0]
    written = tmp_path / "flowgate/main/default/flowgate.default.0257/0100-TSR_document.md"
    assert written.exists(), "재실행 보고서가 실제 파일로 기록되어야 한다"
    assert "trun_second" in written.read_text(encoding="utf-8")
    assert updates["file_path"] == str(written)
    assert updates["revision_no"] == 1


def test_first_run_still_creates_the_report(env):
    """Control: create a new document when no active TSR exists."""
    tsr_doc_id = env["svc"].assemble_tsr(TS_DOC, _run(run_id="trun_1"), CASES)

    assert tsr_doc_id == "flowgate.default.0257.0101-TSR"
    assert len(env["rec"].reserved) == 1
    assert len(env["rec"].created) == 1


# ── the earlier implementation: blocking manual TSR creation must preserve official assembly ──

def test_manual_tsr_ban_does_not_reach_the_official_assembly(env):
    """TSR is now refused at every MANUAL creation gate
    (``/documents/next-empty``, inbox ``action:new``, the ordinary ``'new'`` token). The
    ban is about who is writing the document, not about the type existing: this path is the
    server writing it from a finished run, and it must keep working exactly as before.

    Asserted together on purpose. ``is_server_assembled_type('TSR')`` being true is the
    precondition that makes the assertion meaningful — without it this would just be
    ``test_first_run_still_creates_the_report`` again, passing for the wrong reason.
    """
    from modules.flow_gate.documents.constants import is_server_assembled_type

    assert is_server_assembled_type("TSR") is True

    tsr_doc_id = env["svc"].assemble_tsr(TS_DOC, _run(run_id="trun_official"), CASES)

    assert tsr_doc_id == "flowgate.default.0257.0101-TSR"
    assert len(env["rec"].created) == 1
    assert env["rec"].created[0]["type_code"] == "TSR"
    # Exactly one number and document: the uniqueness manual creation used to break.
    assert len(env["rec"].reserved) == 1


# ── the review regression (c): repeating completion for one run consumes no new number ──

def test_same_run_completion_retry_reuses_the_recorded_report(env):
    run = _run(run_id="trun_1", tsr_doc_id=ACTIVE_TSR_ID)

    assert env["svc"].assemble_tsr(TS_DOC, run, CASES) == ACTIVE_TSR_ID
    assert env["rec"].reserved == []
    assert env["rec"].created == []


# ── the review: link rerun reports to their slot, preventing orphans ──

def test_rerun_binds_the_report_to_its_own_slot_when_the_head_moved_on(env):
    """Link rerun reports to the slot even after the first report consumed it."""
    _seed_active_tsr(env, slot_bound=False)
    env["monkeypatch"].setattr(
        env["wfseq"], "get_sequence_for_member_doc", lambda _doc_id: {"id": 7}
    )
    env["monkeypatch"].setattr(
        env["wfseq"],
        "get_sequence_items",
        lambda _seq_id: [
            {"id": 40, "type": "T", "sort_order": 0, "result_doc_id": "x-TR"},
            {"id": 41, "type": "TS", "sort_order": 1, "result_doc_id": TS_DOC["doc_id"]},
            {"id": 42, "type": "TSR", "sort_order": 2, "result_doc_id": None},
        ],
    )
    # The head has advanced past TSR — the old lookup found no slot and orphaned the report.
    env["monkeypatch"].setattr(
        env["wfseq"], "get_pending_head_by_group", lambda *_a: {"id": 99, "type": "M"}
    )

    env["svc"].assemble_tsr(TS_DOC, _run(), CASES)

    assert len(env["rec"].registered) == 1, "재실행 보고서가 슬롯에 연결되지 않았다"
    assert env["rec"].registered[0]["item_id"] == 42
    assert env["rec"].registered[0]["registered_doc_id"] == ACTIVE_TSR_ID


def test_reused_report_is_not_registered_twice_on_its_own_slot(env):
    """Reassembling a report already linked to its slot must not duplicate result registration."""
    _seed_active_tsr(env, slot_bound=False)
    env["monkeypatch"].setattr(
        env["wfseq"], "get_sequence_for_member_doc", lambda _doc_id: {"id": 7}
    )
    env["monkeypatch"].setattr(
        env["wfseq"],
        "get_sequence_items",
        lambda _seq_id: [
            {"id": 41, "type": "TS", "sort_order": 1, "result_doc_id": TS_DOC["doc_id"]},
            {"id": 42, "type": "TSR", "sort_order": 2, "result_doc_id": ACTIVE_TSR_ID},
        ],
    )

    assert env["svc"].assemble_tsr(TS_DOC, _run(), CASES) == ACTIVE_TSR_ID
    assert env["rec"].registered == []


def test_report_awaiting_review_is_not_resubmitted(env):
    """Reusing a pending_review report must not attempt an invalid submit transition."""
    _seed_active_tsr(env, review_status="pending_review")

    env["svc"].assemble_tsr(TS_DOC, _run(), CASES)

    assert env["rec"].transitions == []


def test_approved_report_is_resubmitted_for_re_review(env):
    """New results return an approved report to pending review."""
    _seed_active_tsr(env, review_status="approved")

    env["svc"].assemble_tsr(TS_DOC, _run(), CASES)

    assert [t["action"] for t in env["rec"].transitions] == ["submit"]
    assert env["rec"].transitions[0]["doc_id"] == ACTIVE_TSR_ID


# ── the review / regression (d): report assembly failure is terminal ──

@pytest.fixture
def exec_env(monkeypatch, tmp_path):
    """Drive _execute_run_inner with every case green and the filesystem stubbed out."""
    from modules.flow_gate.services import test_run_service as svc

    calls = {"finish": [], "recovery": [], "notify": [], "reflect": []}

    src_root = tmp_path / "src"
    src_root.mkdir()
    monkeypatch.setattr(svc.db_docs, "get_by_id", lambda _id: TS_DOC)
    monkeypatch.setattr(
        svc.storage_paths, "resolve_project_src_root", lambda *_a, **_kw: src_root
    )
    monkeypatch.setattr(svc, "_allocate_port", lambda: 8123)
    monkeypatch.setattr(svc, "_scratch_dir", lambda _doc, _run_id: tmp_path / "scratch")
    monkeypatch.setattr(svc.db_test_runs, "set_run_port", lambda *_a, **_kw: None)
    monkeypatch.setattr(svc.db_test_runs, "get_run", lambda _run_id: _run(run_id="trun_x"))
    monkeypatch.setattr(svc.db_test_runs, "list_cases", lambda _run_id: CASES)
    monkeypatch.setattr(svc, "_execute_setup", lambda *_a, **_kw: (False, None))
    monkeypatch.setattr(svc, "_execute_case", lambda *_a, **_kw: None)
    monkeypatch.setattr(svc, "_execute_teardown", lambda *_a, **_kw: None)
    monkeypatch.setattr(svc, "_finalize_services", lambda _services, _active=None: None)
    monkeypatch.setattr(svc, "_remove_scratch", lambda _path: None)
    monkeypatch.setattr(svc, "_emit_finished", lambda *_a, **_kw: None)
    monkeypatch.setattr(svc.process_service, "is_group_disposed", lambda _g: False)
    monkeypatch.setattr(
        svc.test_command_service,
        "reflect_from_passed_run",
        lambda *_a, **_kw: calls["reflect"].append("test_command"),
    )
    monkeypatch.setattr(
        svc.engine_recipe_service,
        "reflect_from_passed_run",
        lambda *_a, **_kw: calls["reflect"].append("engine_recipe"),
    )

    def finish_run(**kwargs):
        calls["finish"].append(kwargs)

    def handle_run_failure(*_a, **_kw):
        calls["recovery"].append("handle_run_failure")
        return "repair"

    monkeypatch.setattr(svc.db_test_runs, "finish_run", finish_run)
    monkeypatch.setattr(svc.engine_recipe_service, "handle_run_failure", handle_run_failure)
    monkeypatch.setattr(
        svc, "_maybe_notify_chain_failure", lambda *_a, **_kw: calls["notify"].append("notify")
    )
    return {"svc": svc, "calls": calls, "monkeypatch": monkeypatch}


def test_report_assembly_failure_is_terminal_and_skips_the_recovery_loop(exec_env):
    """Do not misclassify an all-passing run as INFRA and enter a rerun loop."""
    def boom(*_a, **_kw):
        raise RuntimeError("disk full")

    exec_env["monkeypatch"].setattr(exec_env["svc"], "assemble_tsr", boom)

    exec_env["svc"]._execute_run_inner(_run(run_id="trun_x"))

    finish = exec_env["calls"]["finish"][0]
    assert finish["status"] == "failed"
    assert finish["error"] == "report_assembly_failed"
    assert finish["tsr_doc_id"] is None
    assert finish["case_failed"] == 0
    assert exec_env["calls"]["recovery"] == [], "조립 실패는 0157 자동복구 재실행 대상이 아니다"
    assert exec_env["calls"]["notify"] == ["notify"], "무인 체인에 조용히 멈추지 않고 알려야 한다"


def test_report_assembly_failure_still_reflects_verified_commands(exec_env):
    """Preserve command learning (0152/0157) deliberately because the cases really passed."""
    exec_env["monkeypatch"].setattr(
        exec_env["svc"], "assemble_tsr", lambda *_a, **_kw: (_ for _ in ()).throw(RuntimeError("x"))
    )

    exec_env["svc"]._execute_run_inner(_run(run_id="trun_x"))

    assert exec_env["calls"]["reflect"] == ["test_command", "engine_recipe"]


def test_real_test_failure_still_enters_the_recovery_loop(exec_env):
    """Control: genuine RED still uses the 0157 recovery path, as before the fix.

    Read error with .get because the old finish_run call omitted it entirely.
    Pin recovery routing for non-assembly RED failures, not the presence of that key.
    """
    failing = [{**CASES[0], "result": "fail", "exit_code": 1}]
    exec_env["monkeypatch"].setattr(exec_env["svc"].db_test_runs, "list_cases", lambda _r: failing)

    exec_env["svc"]._execute_run_inner(_run(run_id="trun_x"))

    finish = exec_env["calls"]["finish"][0]
    assert finish["status"] == "failed"
    assert finish.get("error") != "report_assembly_failed"
    assert exec_env["calls"]["recovery"] == ["handle_run_failure"]
