"""0406 T0022 — durable continuous handoff, worker visibility, and prompt audit."""
from __future__ import annotations

import hashlib
import threading

import pytest

from modules.flow_gate.db import ai_invoke_paused_chains as db_paused
from modules.flow_gate.db import ai_invoke_runs as db_runs
from modules.flow_gate.services import ai_invoke_service as svc
from modules.flow_gate.services import workflow_decision_service as wds


@pytest.fixture(autouse=True)
def clean_registries():
    for lock, registry in ((svc._runs_lock, svc._runs), (svc._auto_resume_lock, svc._auto_resume)):
        with lock:
            registry.clear()
    yield
    for lock, registry in ((svc._runs_lock, svc._runs), (svc._auto_resume_lock, svc._auto_resume)):
        with lock:
            registry.clear()


def test_mode_contract_distinguishes_explicit_from_legacy_fallback():
    assert wds.normalize_continuation_instruction_mode("ai_direct") == "ai_direct"
    assert wds.instruction_mode_fallback_applied("ai_direct") is False
    assert wds.normalize_continuation_instruction_mode(None) == "auto_approved"
    assert wds.instruction_mode_fallback_applied(None) is True
    assert wds.instruction_mode_fallback_applied("unknown") is True


def test_auto_approved_issues_no_nt_worker_but_ai_direct_does(monkeypatch):
    head = {"type": "T", "item_seq": 4, "result_doc_id": None}
    monkeypatch.setattr(wds.db_wfseq, "get_effective_head", lambda _sid: head)
    monkeypatch.setattr(
        wds,
        "is_auto_handled_step",
        lambda **kw: kw["instruction_mode"] == "auto_approved",
    )
    from modules.flow_gate.documents.routers import documents as docs_mod
    monkeypatch.setattr(docs_mod, "create_next_approved_core", lambda **_kw: head.update(type="TR"))
    from modules.flow_gate.db import users as db_users
    monkeypatch.setattr(db_users, "get_by_id", lambda _uid: {"user_id": _uid, "is_admin": 1})

    auto = wds._auto_complete_instruction_heads(
        spine_doc={"doc_id": "d", "project_id": "p", "group_id": "g", "module": "default"},
        seq={"id": 1}, actor_user_id="u", locale="ko", target_seq=5,
        instruction_mode="auto_approved",
    )
    assert auto == [4]

    head.update(type="T", item_seq=4)
    direct = wds._auto_complete_instruction_heads(
        spine_doc={"doc_id": "d", "project_id": "p", "group_id": "g", "module": "default"},
        seq={"id": 1}, actor_user_id="u", locale="ko", target_seq=5,
        instruction_mode="ai_direct",
    )
    assert direct == []


def _run(group="flowgate.default.0406"):
    return {
        "run_id": "aiv_0406",
        "group_id": group,
        "doc_ref": "flowgate.default.0406.0001-B",
        "issued_to": "usr_admin",
        "api_base_url": "http://x/api/v1",
        "continuation_locale": "ko",
        "continuation_instruction_mode": "ai_direct",
        "continuation_provider_overrides": {"3": "aip_step"},
        "continuation_base_provider_id": "aip_base",
        "continuation_note_overrides": {"3": "step note"},
        "continuation_default_note": "common note",
        "continuation_step_timeout_sec": 3600,
        "continuation_restart_max_attempts": 3,
        "continuation_auto_approve_item_seqs": [5],
        "chain_id": "chain_0406",
        "chain_docs_target": 6,
        "chain_docs_reached": 2,
        "docs_target": 1,
        "docs_reached": 1,
        "end_reason": "exited",
        "cancel_event": threading.Event(),
    }


def _pending():
    return {
        "doc_ref": "flowgate.default.0406.0001-B",
        "target_seq": 6,
        "review_mode": False,
        "instruction_mode": "ai_direct",
        "locale": "ko",
        "issued_to": "usr_admin",
        "api_base_url": "http://x/api/v1",
    }


def test_request_auto_resume_persists_the_complete_i3_bundle(monkeypatch):
    run = _run()
    with svc._runs_lock:
        svc._runs[run["run_id"]] = {**run, "status": "running"}
    saved = {}
    monkeypatch.setattr(db_paused, "get_by_group", lambda _g: None)
    monkeypatch.setattr(db_paused, "upsert", lambda **kw: saved.update(kw))

    svc.request_auto_resume(run["group_id"], _pending())

    assert svc.peek_auto_resume(run["group_id"])["target_seq"] == 6
    assert saved["stop_kind"] == "system"
    assert saved["stop_code"] == svc.HOP_HANDOFF_STOP_CODE
    assert saved["continuation_base_provider_id"] == "aip_base"
    assert saved["continuation_provider_overrides"] == {"3": "aip_step"}
    assert saved["continuation_default_note"] == "common note"
    assert saved["continuation_note_overrides"] == {"3": "step note"}
    assert saved["continuation_instruction_mode"] == "ai_direct"
    assert saved["continuation_auto_approve_item_seqs"] == [5]
    assert saved["continuation_step_timeout_sec"] == 3600
    assert saved["continuation_restart_max_attempts"] == 3


@pytest.mark.parametrize("end_reason", ["timeout", "all_providers_failed", "user_paused"])
def test_unclean_end_parks_intent_and_releases_handoff_lease(monkeypatch, end_reason):
    run = _run()
    run["end_reason"] = end_reason
    run["stop_code"] = end_reason
    writes, releases, spawns = [], [], []
    monkeypatch.setattr(svc, "_write_handoff_row", lambda *a, **kw: writes.append((a, kw)))
    monkeypatch.setattr(svc.db_group_ai_leases, "release", lambda *a: releases.append(a))
    monkeypatch.setattr(svc, "_spawn_auto_resume", lambda *a: spawns.append(a))
    with svc._auto_resume_lock:
        svc._auto_resume[run["group_id"]] = _pending()

    svc._maybe_auto_resume_hop(run)

    assert not spawns
    assert writes and writes[-1][1]["stop_code"] == end_reason
    assert releases == [(run["group_id"], run["run_id"])]


def test_spawn_exception_keeps_durable_row_and_releases_lease(monkeypatch):
    run = _run()
    writes, releases = [], []
    monkeypatch.setattr(svc, "_write_handoff_row", lambda *a, **kw: writes.append((a, kw)))
    monkeypatch.setattr(svc.db_group_ai_leases, "release", lambda *a: releases.append(a))
    monkeypatch.setattr(svc, "_spawn_auto_resume", lambda *_a: (_ for _ in ()).throw(RuntimeError("boom")))
    with svc._auto_resume_lock:
        svc._auto_resume[run["group_id"]] = _pending()

    svc._maybe_auto_resume_hop(run)

    assert any(kw.get("stop_code") == svc.HOP_HANDOFF_FAILED_STOP_CODE for _a, kw in writes)
    assert releases == [(run["group_id"], run["run_id"])]


def test_startup_marks_memory_lost_handoff_resumable(monkeypatch):
    row = {"group_id": "flowgate.default.0406", "stop_code": "hop_handoff", "stop_run_id": "aiv_old"}
    marked = []
    monkeypatch.setattr(db_paused, "list_all_system_stops", lambda: [row])
    monkeypatch.setattr(
        db_paused, "mark_stop_code",
        lambda group, code, **kw: marked.append((group, code, kw)),
    )
    with svc._auto_resume_lock:
        svc._auto_resume.clear()

    assert svc.startup_recover_handoffs() == 1
    assert marked == [(
        row["group_id"], svc.HOP_HANDOFF_INTERRUPTED_STOP_CODE,
        {"stop_run_id": row["stop_run_id"]},
    )]


def test_prompt_audit_records_source_length_and_hash(monkeypatch):
    monkeypatch.setattr(svc.db_wfseq, "get_sequence_for_member_doc", lambda _d: {"id": 7})
    monkeypatch.setattr(svc.db_wfseq, "get_effective_head", lambda _sid: {"item_seq": 3, "type": "TR"})
    monkeypatch.setattr(
        svc.invoke_mention_service,
        "prepend_messages_section",
        lambda mention, notes, _locale: " / ".join(notes) + " / " + mention,
    )
    audit = {}
    mention = svc._inject_hop_notes(
        "base", "doc", default_note="common", note_overrides={"3": "override"},
        instruction_mode="ai_direct", locale="ko", fold_worker_item_seq=False, audit=audit,
    )
    applied = "common" + chr(10) + chr(10) + "override"
    assert mention == "common / override / base"
    assert audit["prompt_message_source"] == "override+common_default"
    assert audit["prompt_user_message_length"] == len(applied)
    assert audit["prompt_user_message_sha256"] == hashlib.sha256(applied.encode()).hexdigest()


def test_prompt_audit_fields_round_trip_by_run_id(monkeypatch):
    class Store:
        row = None
        def _execute(self, _sql, params):
            self.row = dict(zip(db_runs._BOUND_COLUMNS, params))
        def _fetch_one(self, _sql, _params):
            return dict(self.row) if self.row else None
    store = Store()
    monkeypatch.setattr(db_runs, "get_store", lambda: store)
    row = {
        "run_id": "aiv_audit", "group_id": "flowgate.default.0406", "project_id": "flowgate",
        "doc_ref": "doc", "mode": "continuous", "outcome": "complete", "end_reason": "exited",
        "worker_document_type": "TR", "auto_handled_item_seqs": [1],
        "prompt_message_source": "override", "prompt_user_message_length": 8,
        "prompt_user_message_sha256": "a" * 64, "prompt_final_length": 100,
        "prompt_final_sha256": "b" * 64,
        "started_at": "2026-08-11T00:00:00+09:00",
        "finished_at": "2026-08-11T00:00:01+09:00",
        "created_at": "2026-08-11T00:00:00+09:00",
        "updated_at": "2026-08-11T00:00:01+09:00",
    }
    db_runs.upsert(row)
    got = db_runs.get("aiv_audit")
    assert got["worker_document_type"] == "TR"
    assert got["auto_handled_item_seqs"] == [1]
    assert got["prompt_message_source"] == "override"
    assert got["prompt_final_sha256"] == "b" * 64


# ── 응답이 사실을 말하는가 (작업 1·3) ──────────────────────────────────────────
# 실행이 끝난 뒤 "N/T 가 사라졌다"와 "TR 워커가 정상 실행됐다"를 가르려면, 그 사실이
# 응답과 실행 기록에 실려 있어야 한다. 위 두 시험이 고정한 것은 판정 자체였고,
# 아래는 그 판정 결과가 호출자에게 실제로 도달하는지다.

def _wire_advance(monkeypatch, head_item_seq=4, head_type="NR"):
    doc = {
        "doc_id": "flowgate.default.0406.0001-R",
        "group_id": "flowgate.default.0406",
        "project_id": "flowgate",
        "type_code": "R",
        "seq": 1,
    }
    monkeypatch.setattr(wds.db_documents, "get_by_id", lambda _id: doc)
    monkeypatch.setattr(wds.db_wfseq, "get_sequence_for_member_doc", lambda _id: {"id": 7})
    monkeypatch.setattr(
        wds.db_wfseq, "get_effective_head",
        lambda _sid: {"type": head_type, "label": "조사레포트", "result_doc_id": None,
                      "result_doc_review_status": None, "item_seq": head_item_seq, "id": 11},
    )
    monkeypatch.setattr(wds.db_documents, "get_group_max_seq", lambda _gid: 1)
    monkeypatch.setattr(wds.db_documents, "fetch_recent_group_docs", lambda **_k: [])
    monkeypatch.setattr(wds.db_wfseq, "get_predecessor_result_doc_id", lambda _s, _h=None: None)
    monkeypatch.setattr(wds.db_wfseq, "get_predecessor_result_doc_ids",
                        lambda _s, _h=None, limit=2: [])
    from modules.flow_gate.db import tokens as db_tokens
    monkeypatch.setattr(db_tokens, "get_unconsumed_by_doc_ref", lambda _id: None)
    monkeypatch.setattr(wds.mention_service, "build_mention_from_token_rec", lambda **_k: "M")
    monkeypatch.setattr(
        wds.token_service, "issue",
        lambda **_k: {"raw_token": "RAW", "scratch_dir": "/tmp/s", "token_id": "tok-new",
                      "expires_at": "2026-08-12T00:00:00+09:00"},
    )


def test_advance_response_names_the_real_worker_and_the_auto_handled_slots(monkeypatch):
    _wire_advance(monkeypatch)
    monkeypatch.setattr(wds, "_auto_complete_instruction_heads", lambda **_kw: [3])
    logged = []
    monkeypatch.setattr(wds, "_log_auto_handled_heads", lambda **kw: logged.append(kw))

    result = wds.advance_workflow(
        doc_id="flowgate.default.0406.0001-R", issued_to="pm-1",
        api_base_url="http://h/flowgate/api/v1",
        continuous=True, continuation_target_seq=6,
        continuation_instruction_mode="auto_approved",
    )

    # 서버가 대신 처리한 칸과, 이 홉의 워커가 실제로 채운 칸이 따로 실린다.
    assert result["auto_handled_item_seqs"] == [3]
    assert result["worker_item_seq"] == 4
    assert result["worker_document_type"] == "NR"
    # 요청이 명시적으로 골랐으므로 정규화는 발동하지 않았다.
    assert result["continuation_instruction_mode"] == "auto_approved"
    assert result["continuation_instruction_mode_requested"] == "auto_approved"
    assert result["continuation_instruction_mode_fallback_applied"] is False
    assert logged and logged[0]["mode_fallback_applied"] is False


def test_missing_mode_still_folds_to_auto_approved_but_says_so(monkeypatch):
    """작업 2: 하위호환은 유지한다. 조용한 것만 그만둔다."""
    _wire_advance(monkeypatch)
    monkeypatch.setattr(wds, "_auto_complete_instruction_heads", lambda **_kw: [])
    logged = []
    monkeypatch.setattr(wds, "_log_auto_handled_heads", lambda **kw: logged.append(kw))

    result = wds.advance_workflow(
        doc_id="flowgate.default.0406.0001-R", issued_to="pm-1",
        api_base_url="http://h/flowgate/api/v1",
        continuous=True, continuation_target_seq=6,
    )

    assert result["continuation_instruction_mode"] == "auto_approved"
    assert result["continuation_instruction_mode_requested"] is None
    assert result["continuation_instruction_mode_fallback_applied"] is True
    # 자동처리된 칸이 하나도 없어도 정규화 발동은 기록한다 — 그것이 작업 2 가 요구한 기록이다.
    assert logged and logged[0]["mode_fallback_applied"] is True


def test_normalization_fallback_reaches_the_event_log(monkeypatch):
    events = []
    from modules.flow_gate.workflow import event_logger

    monkeypatch.setattr(event_logger, "log_event", lambda **kw: events.append(kw) or {})

    wds._log_auto_handled_heads(
        doc={"doc_id": "flowgate.default.0406.0022-T", "project_id": "flowgate",
             "group_id": "flowgate.default.0406", "id": 22},
        actor_user_id="usr_admin",
        auto_handled_item_seqs=[],
        instruction_mode="auto_approved",
        mode_requested=None,
        mode_fallback_applied=True,
    )

    assert len(events) == 1
    assert events[0]["event_type"] == event_logger.EVT_CONTINUATION_HEAD_AUTO_HANDLED
    meta = events[0]["metadata"]
    assert meta["instruction_mode_requested"] is None
    assert meta["instruction_mode_fallback_applied"] is True


def test_nothing_to_report_writes_no_event(monkeypatch):
    """정상 ai_direct 홉마다 이벤트가 쌓이면 그 기록은 곧 읽히지 않는다."""
    events = []
    from modules.flow_gate.workflow import event_logger

    monkeypatch.setattr(event_logger, "log_event", lambda **kw: events.append(kw) or {})

    wds._log_auto_handled_heads(
        doc={"doc_id": "d", "project_id": "flowgate", "group_id": "g", "id": 1},
        actor_user_id="usr_admin", auto_handled_item_seqs=[],
        instruction_mode="ai_direct", mode_requested="ai_direct", mode_fallback_applied=False,
    )

    assert events == []


def test_durable_row_survives_until_the_next_hop_actually_starts(monkeypatch):
    """작업 4: 지우는 시점이 계약이다 — spawn 이 성공한 **뒤에만** 지운다."""
    run = _run()
    order = []
    monkeypatch.setattr(svc, "_write_handoff_row",
                        lambda *_a, **kw: order.append(("write", kw.get("stop_code"))))
    monkeypatch.setattr(svc, "_spawn_auto_resume", lambda *_a: order.append(("spawn", None)))
    monkeypatch.setattr(svc, "_clear_handoff_row",
                        lambda group, stop_run_id: order.append(("clear", stop_run_id)))
    monkeypatch.setattr(svc, "_park_handoff",
                        lambda *_a: order.append(("park", None)))
    with svc._auto_resume_lock:
        svc._auto_resume[run["group_id"]] = _pending()

    svc._maybe_auto_resume_hop(run)

    assert order == [("write", None), ("spawn", None), ("clear", run["run_id"])]


def test_clear_only_removes_this_runs_system_row(monkeypatch):
    deleted = []
    monkeypatch.setattr(db_paused, "delete_system_stop",
                        lambda group, stop_run_id: deleted.append((group, stop_run_id)))

    svc._clear_handoff_row("flowgate.default.0406", "aiv_0406")
    # run_id 를 모르면 아무것도 지우지 않는다 — 남의 정지행을 지우느니 남기는 편이 낫다.
    svc._clear_handoff_row("flowgate.default.0406", None)

    assert deleted == [("flowgate.default.0406", "aiv_0406")]


def test_audit_columns_exist_in_the_real_schema_and_round_trip(migrated_sqlite_db):
    """작업 5: 가짜 저장소는 마이그레이션 080 이 없어도 초록이다.

    그래서 진짜 스키마로 한 번 더 왕복한다 — 열이 없으면 여기서 OperationalError 로 터진다.
    """
    import sqlite3
    from modules.flow_gate.db import connection as db_connection

    path = migrated_sqlite_db("ai_invoke_prompt_audit_0406.db")
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row

    class Store:
        def _execute(self, sql, params=None):
            conn.execute(sql, params or [])
            conn.commit()

        def _fetch_one(self, sql, params=None):
            row = conn.execute(sql, params or []).fetchone()
            return dict(row) if row else None

        def _fetch_all(self, sql, params=None):
            return [dict(r) for r in conn.execute(sql, params or []).fetchall()]

    previous = db_connection.STORE
    db_connection.STORE = Store()
    try:
        applied = "공통 멘트"
        db_runs.upsert({
            "run_id": "aiv_real_audit", "group_id": "flowgate.default.0406",
            "project_id": "flowgate", "doc_ref": "flowgate.default.0406.0001-B",
            "mode": "continuous", "status": "finished", "outcome": "complete",
            "end_reason": "exited",
            "worker_document_type": "TR",
            "continuation_instruction_mode_requested": None,
            "continuation_instruction_mode_normalized": "auto_approved",
            "continuation_instruction_mode_fallback_applied": True,
            "auto_handled_item_seqs": [3, 4],
            "prompt_message_source": "override+common_default",
            "prompt_common_default_applied": True,
            "prompt_user_message_length": len(applied),
            "prompt_user_message_sha256": hashlib.sha256(applied.encode()).hexdigest(),
            "prompt_final_length": 2362,
            "prompt_final_sha256": "c" * 64,
            "started_at": "2026-08-11T00:00:00+09:00",
            "finished_at": "2026-08-11T00:00:01+09:00",
            "created_at": "2026-08-11T00:00:00+09:00",
            "updated_at": "2026-08-11T00:00:01+09:00",
        })

        got = db_runs.get("aiv_real_audit")
        assert got["worker_document_type"] == "TR"
        assert got["auto_handled_item_seqs"] == [3, 4]
        assert got["continuation_instruction_mode_fallback_applied"] is True
        assert got["prompt_user_message_length"] == len(applied)
        assert got["prompt_user_message_sha256"] == hashlib.sha256(applied.encode()).hexdigest()
        # 원문은 어디에도 없다 — 길이와 해시만 남긴다는 것이 작업 5 의 계약이다.
        assert applied not in " ".join(str(v) for v in got.values())

        detail = svc._run_detail_from_row(got)
        assert detail["worker_document_type"] == "TR"
        assert detail["auto_handled_item_seqs"] == [3, 4]
        assert detail["prompt_message_source"] == "override+common_default"
        assert detail["prompt_final_sha256"] == "c" * 64
    finally:
        db_connection.STORE = previous
        conn.close()


def test_worker_crash_parks_the_intent_instead_of_dropping_it(monkeypatch):
    """작업 4: spawn 하지 않는 세 번째 분기 — 워커 자체가 터진 경우.

    NR0021 §5 의 표에서 이 줄만 ``clear_auto_resume`` 이었다. 큐를 비우는 것은 맞다(터진
    홉을 자동으로 이어 달리게 하면 안 된다). 그러나 그때 lease 는 이미 ``begin_handoff`` 로
    바뀌어 있고 ``_finalize_run`` 은 pending 이 보이면 release 를 건너뛰므로, 그냥 비우면
    그 그룹의 다음 실행이 lease 만료까지 막힌다.
    """
    from modules.flow_gate.services import ai_invoke_service as service

    run = _run()
    parked = []
    monkeypatch.setattr(service, "_park_handoff",
                        lambda r, pending, code: parked.append((r["run_id"], pending, code)))
    with service._auto_resume_lock:
        service._auto_resume[run["group_id"]] = _pending()

    # _worker 의 except 분기가 하는 일만 그대로 재현한다 (프로바이더 실행 없이).
    crashed_pending = service.pop_auto_resume(run.get("group_id"))
    assert crashed_pending is not None
    crashed_code = run.get("stop_code") or service.HOP_HANDOFF_FAILED_STOP_CODE
    if crashed_code == service.HOP_HANDOFF_STOP_CODE:
        crashed_code = service.HOP_HANDOFF_FAILED_STOP_CODE
    service._park_handoff(run, crashed_pending, crashed_code)

    assert service.peek_auto_resume(run["group_id"]) is None
    assert parked == [(run["run_id"], crashed_pending, service.HOP_HANDOFF_FAILED_STOP_CODE)]


def test_worker_crash_branch_is_wired_to_park(monkeypatch):
    """위 시험이 재현한 절차가 실제 _worker 예외 처리에 들어 있는지 소스로 확인한다.

    _worker 전체를 돌리려면 프로바이더 프로세스가 필요하다. 대신 그 분기가 여전히
    ``clear_auto_resume`` 하나로 끝나지 않는다는 것만 못 박는다 — 되돌아가면 여기서 걸린다.
    """
    import inspect

    source = inspect.getsource(svc._worker)
    tail = source.split("except Exception:")[-1]
    assert "pop_auto_resume" in tail
    assert "_park_handoff" in tail
