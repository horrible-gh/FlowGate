"""Group 0257 B0001: one active TSR per workflow slot, and a terminal report failure.

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


# ── NR0003 필수 회귀 (a)/(b): 연속 두 번 통과 → 활성 TSR 슬롯 1개 ────────────────────

def test_rerun_revises_active_tsr_instead_of_reserving_a_second_number(env):
    """B0001의 주 증상: 재테스트가 TSR 문서를 두 번째로 만들어내던 경로."""
    _seed_active_tsr(env)

    tsr_doc_id = env["svc"].assemble_tsr(TS_DOC, _run(), CASES)

    assert tsr_doc_id == ACTIVE_TSR_ID
    assert env["rec"].reserved == [], "재실행이 새 TSR 번호를 예약하면 안 된다"
    assert env["rec"].created == [], "재실행이 두 번째 TSR 문서를 만들면 안 된다"
    assert [doc_id for doc_id, _ in env["rec"].updated] == [ACTIVE_TSR_ID]


def test_rerun_rewrites_the_active_report_file_with_the_new_run(env, tmp_path):
    """활성 TSR의 본문과 file_path가 이번 회차 결과로 갱신된다(고아 미리보기 방지)."""
    _seed_active_tsr(env)

    env["svc"].assemble_tsr(TS_DOC, _run(run_id="trun_second"), CASES)

    _doc_id, updates = env["rec"].updated[0]
    written = tmp_path / "flowgate/main/default/flowgate.default.0257/0100-TSR_document.md"
    assert written.exists(), "재실행 보고서가 실제 파일로 기록되어야 한다"
    assert "trun_second" in written.read_text(encoding="utf-8")
    assert updates["file_path"] == str(written)
    assert updates["revision_no"] == 1


def test_first_run_still_creates_the_report(env):
    """대조군: 활성 TSR이 없으면 지금까지처럼 새 문서를 만든다."""
    tsr_doc_id = env["svc"].assemble_tsr(TS_DOC, _run(run_id="trun_1"), CASES)

    assert tsr_doc_id == "flowgate.default.0257.0101-TSR"
    assert len(env["rec"].reserved) == 1
    assert len(env["rec"].created) == 1


# ── NR0003 필수 회귀 (c): 동일 run 완료 재호출은 새 번호를 소비하지 않는다 ──────────

def test_same_run_completion_retry_reuses_the_recorded_report(env):
    run = _run(run_id="trun_1", tsr_doc_id=ACTIVE_TSR_ID)

    assert env["svc"].assemble_tsr(TS_DOC, run, CASES) == ACTIVE_TSR_ID
    assert env["rec"].reserved == []
    assert env["rec"].created == []


# ── NR0003 §3: 재실행 보고서가 슬롯에 연결된다(고아 방지) ────────────────────────

def test_rerun_binds_the_report_to_its_own_slot_when_the_head_moved_on(env):
    """첫 보고서가 슬롯을 소비한 뒤에도 재실행 보고서는 슬롯에 연결되어야 한다."""
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
    """이미 같은 슬롯에 연결된 보고서를 재조립해도 결과 등록은 중복되지 않는다."""
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
    """pending_review인 보고서 재사용은 submit 전이를 시도하지 않는다(무효 전이 방지)."""
    _seed_active_tsr(env, review_status="pending_review")

    env["svc"].assemble_tsr(TS_DOC, _run(), CASES)

    assert env["rec"].transitions == []


def test_approved_report_is_resubmitted_for_re_review(env):
    """승인 상태였던 보고서는 새 결과가 들어오면 다시 검토 대기로 돌아간다."""
    _seed_active_tsr(env, review_status="approved")

    env["svc"].assemble_tsr(TS_DOC, _run(), CASES)

    assert [t["action"] for t in env["rec"].transitions] == ["submit"]
    assert env["rec"].transitions[0]["doc_id"] == ACTIVE_TSR_ID


# ── NR0003 §2 / 필수 회귀 (d): 리포트 조립 실패는 terminal ────────────────────────

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
    monkeypatch.setattr(svc, "_finalize_services", lambda _services: None)
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
    """모든 케이스가 통과한 run을 INFRA 실패로 오인해 재실행 루프에 태우면 안 된다."""
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
    """케이스는 실제로 통과했으므로 명령 학습(0152/0157)은 의도적으로 유지한다."""
    exec_env["monkeypatch"].setattr(
        exec_env["svc"], "assemble_tsr", lambda *_a, **_kw: (_ for _ in ()).throw(RuntimeError("x"))
    )

    exec_env["svc"]._execute_run_inner(_run(run_id="trun_x"))

    assert exec_env["calls"]["reflect"] == ["test_command", "engine_recipe"]


def test_real_test_failure_still_enters_the_recovery_loop(exec_env):
    """대조군: 진짜 RED는 지금까지처럼 0157 자동복구 경로를 탄다(수정 전에도 통과).

    .get("error")로 읽는 이유는 수정 전 코드가 finish_run에 error를 아예 넘기지 않아서다.
    이 테스트가 지키려는 것은 error 키가 아니라 '조립 실패가 아닌 RED는 복구 경로로 간다'는 배선이다.
    """
    failing = [{**CASES[0], "result": "fail", "exit_code": 1}]
    exec_env["monkeypatch"].setattr(exec_env["svc"].db_test_runs, "list_cases", lambda _r: failing)

    exec_env["svc"]._execute_run_inner(_run(run_id="trun_x"))

    finish = exec_env["calls"]["finish"][0]
    assert finish["status"] == "failed"
    assert finish.get("error") != "report_assembly_failed"
    assert exec_env["calls"]["recovery"] == ["handle_run_failure"]
