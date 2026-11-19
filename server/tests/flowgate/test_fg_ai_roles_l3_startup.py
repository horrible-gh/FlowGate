"""L-3 N-1/N-4: new-card automatic answers own one claim and one candidate."""
import pytest
from tests.modules.flow_gate.test_q_responder_startup_0111 import env, question, dispatch, finish, script  # noqa: F401
from flowgate.ai_roles import record_store


@pytest.mark.parametrize("action,expected", [("answer", "answered"), ("fast_fail", "failed"),
                                             ("zero_output", "failed")])
def test_n_new_single_candidate_has_no_startup_tail(env, question, monkeypatch, action, expected):
    fake, row = question
    monkeypatch.setattr(record_store, "available", lambda: False)
    launches = script(monkeypatch, row, action)
    o = {"origin_kind": "card_qa", "purpose": "qa_response", "role_source": "card_review_role",
         "requested_ai_id": "aip_1", "target": {"flow_id": "f", "node_key": "n"},
         "qa": {"item_id": 201, "card_class": "qa_new"}}
    r = finish(dispatch(default_tail=False, invocation_origin=o), row)
    assert launches == ["aip_1"] and row["responder_state"] == expected
    assert len(fake.claims) == 1 and row["responder_attempts"] == 1
    assert r["invocation_origin"] == o
    assert len(r["responder_startup_policy"]["provider_ids"]) == 1


# flowgate.v02test.0051 T0005 (A1): a new card's pick is its review AI with the independent default tail (default_tail
# True, as a legacy card's). The dispatch still judges the candidates again after the adapter: a review AI disabled in
# between is skipped for the next independent enabled default, and with none left (only the asker's provider) the
# question is refused and marked failed before any claim or launch. (Before: the designated role only —
# ``designated_not_enabled`` even while an independent default was enabled.)
@pytest.mark.parametrize("race,code", [("disabled", "no_independent_responder")])
def test_n_dispatch_rechecks_designated_responder_after_adapter(env, question, monkeypatch, race, code):
    from types import SimpleNamespace as NS
    from fastapi import HTTPException
    from flowgate.ai_roles import qa_adapter as qa, resolve, role_store, invocations
    from modules.flow_gate.services import q_answer_invoke_service as answer
    from tests.modules.flow_gate import test_ai_invoke_no_output_retry_0359 as base

    fake, row = question
    monkeypatch.setattr(role_store, "available", lambda: True)
    slot = NS(source="card_explicit", value=role_store.RoleValue(
        kind="assigned", provider_id="aip_1", provider_name=None))
    monkeypatch.setattr(resolve, "read_card_role_inputs", lambda *a: resolve.CardRoleInputs(
        slots={"review": slot}, init=None, stored={}))
    monkeypatch.setattr(qa.legacy, "project_of", lambda *a: "flowgate")
    chosen = qa.choose(base.GROUP, {"node_key": "n", "node_type": "document", "doc_type": "T"}, "aip_3")
    assert chosen == ("aip_1", "card_review_role", None, True, "qa_new")
    if race == "disabled":
        # the review AI disabled after the pick, and the only other independent default gone too: aip_3 is the asker
        env["chain"]["providers"] = [p for p in env["chain"]["providers"] if p["id"] not in (chosen[0], "aip_2")]
    else:
        row["asker_actual_provider_id"] = chosen[0]
    launches = script(monkeypatch, row, "answer")
    recorded = []
    monkeypatch.setattr(invocations, "refused", lambda origin, exc, graph_exec_id:
                        recorded.append((origin, exc.detail["code"])))
    o = {"origin_kind": "card_qa", "purpose": "qa_response", "qa": {"card_class": "qa_new"}}
    with pytest.raises(HTTPException) as caught:
        answer.dispatch_answer_run(
            doc={"doc_id": base.DOC_REF, "project_id": "flowgate", "group_id": base.GROUP},
            item={"id": 201, "asker_provider": {"ai_provider_id": "aip_3"}},
            issued_to="owner", api_base_url="http://localhost/flowgate/api/v1",
            provider_id=chosen[0], provider_source=chosen[1], responder_candidates=[],
            default_tail=chosen[3], invocation_origin=o)
    assert caught.value.status_code == 409 and caught.value.detail["code"] == code
    assert row["responder_state"] == "failed" and row["responder_error_code"] == code
    assert recorded == [(o, code)]
    assert not launches and not fake.claims and not base.svc._runs
