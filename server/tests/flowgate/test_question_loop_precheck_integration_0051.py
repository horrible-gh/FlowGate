"""D: B1 registration results feed A1's real worker hop boundary.

The established fixtures use separate SQLite stores for Q&A and the loop. Only
provider execution / the open-question port bridge them; registration, search,
checkpoint, worker and durable loop-wait insert are production code.
"""
import pytest
from fastapi import HTTPException
from tests.flowgate.test_question_policy_0021 import store  # noqa: F401
from tests.flowgate.test_fg_question_loop_wait_0051 import world, RUN  # noqa: F401
from tests.modules.flow_gate.test_qa_container import DOC
from modules.flow_gate.db import connection
from modules.flow_gate.services import q_service as qs, ai_invoke_service as service


def seed_answer(monkeypatch):
    # The loop fixture patches the shared document reader to its own SQLite DB.
    with monkeypatch.context() as ctx:
        ctx.setattr(qs.db_documents, "get_store", connection.get_store)
        item = qs.add_questions(DOC, [{"body": "테스트 서버 커밋 확인"}], created_by="u1")["added_item_ids"][0]
        qs.register_answer(DOC, item, "테스트 서버에서 확인합니다", author_kind="human", author_id="u1")
        return qs.get_qa_detail(DOC)["items"][-1]["answers"][-1]["id"]


def exercise(world, monkeypatch, aid, override):
    original = service._execute_provider_chain
    results = []

    def provider(run, chain, prompt):
        ok = original(run, chain, prompt)
        if len(world["state"]["hops"]) != 1:
            return ok
        with monkeypatch.context() as ctx:
            ctx.setattr(qs.db_documents, "get_store", connection.get_store)
            payload = {"body": "커밋 확인 테스트 서버"}
            if override:
                payload["rejected_answer_id"] = aid
                with pytest.raises(HTTPException) as caught:
                    qs.add_questions(DOC, [payload], created_by="u1")
                assert caught.value.status_code == 409
                assert caught.value.detail["added_item_ids"] == []
                assert len(qs.get_qa_detail(DOC)["items"]) == 1
                payload["reuse_rejection_reason"] = "현재 요구사항은 다른 배포 대상을 확인하도록 함"
            result = qs.add_questions(DOC, [payload], created_by="u1")
            results.append(result)
        # This port represents the questions created by the current provider hop.
        world["state"]["asked"][RUN] = [
            {"id": item, "asker_kind": "ai", "asker_ai_run_id": RUN,
             "anchor_doc_id": DOC, "asker_actual_provider_id": "reworker"}
            for item in result["added_item_ids"]]
        return ok

    monkeypatch.setattr(service, "_execute_provider_chain", provider)
    world["state"]["act"] = {2: {"verdict": "pass"}}
    run = world["start"]("rework")
    return run, results[0]


def test_reused_answer_creates_no_question_and_worker_starts_next_review(store, world, monkeypatch):
    aid = seed_answer(monkeypatch)
    run, result = exercise(world, monkeypatch, aid, False)
    assert result["status"] == "reused_answer"
    assert result["added_item_ids"] == []
    assert result["reused_answer"]["answer_id"] == aid
    assert world["waits"]() == []
    assert world["state"]["hops"] == ["rework", "review"]
    assert not run.get("question_loop_wait_id")


def test_409_then_reason_registration_parks_before_next_review(store, world, monkeypatch):
    aid = seed_answer(monkeypatch)
    run, result = exercise(world, monkeypatch, aid, True)
    assert len(result["added_item_ids"]) == 1
    assert world["state"]["hops"] == ["rework"]
    waits = world["waits"]()
    assert len(waits) == 1
    assert waits[0]["status"] == "waiting"
    assert waits[0]["question_item_ids"] == result["added_item_ids"]
    assert waits[0]["resume_stage"] == "review"
    assert run["question_loop_wait_id"] == waits[0]["wait_id"]
