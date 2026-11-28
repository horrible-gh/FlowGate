"""Scenario uses the existing authenticated decision API and journal."""
import pytest
from test_fg_card_settings import env, db_path, legacy_cards, decided
from test_fg_document_decisions_0126 import decisions_env, _card, _get, _request, _post, _body
pytestmark = pytest.mark.usefixtures("legacy_cards")


def test_scenario_approval_rejection_and_empty_gate(decisions_env, monkeypatch):
    from flowgate.card_completion import own_effects
    # Dispatch is outside this decision test; body gate, permissions, journal and badges are real.
    monkeypatch.setattr(own_effects, "run", lambda doc, **kw: kw["write"]())
    env = decisions_env
    key = _card(env, "TS")
    view = _get(env, key)
    assert set(view["documents"]) == {"scenario", "report"}
    from test_fg_card_approval_0056 import TS_AUTOMATED
    _body(view["documents"]["scenario"]["doc_id"], TS_AUTOMATED)
    result = _post(env, key, _request(env, key, "scenario"), "scenario")
    assert result["documents"]["scenario"]["doc_review_status"] == "approved"
    assert result["documents"]["scenario"]["human_decision"]["valid"]
    result = _post(env, key, _request(env, key, "scenario", "reject", "reject"), "scenario")
    assert result["documents"]["scenario"]["doc_review_status"] == "rejected"
    _body(result["documents"]["scenario"]["doc_id"], "")
    _post(env, key, _request(env, key, "scenario", "approve", "empty"), "scenario", status=409)
