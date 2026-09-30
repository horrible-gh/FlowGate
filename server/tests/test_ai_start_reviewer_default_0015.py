"""Run-level default reviewer for a workflow_decide Start (no item_seq exists yet)."""
import pytest

from modules.flow_gate.services import workflow_decision_service as wds
from modules.flow_gate.services.ai_invoke import review


def test_default_key_accepted_only_when_allowed():
    assert wds.normalize_continuation_reviewer_overrides({"*": " aip_rev "}, allow_default=True) == {"*": "aip_rev"}
    with pytest.raises(ValueError):
        wds.normalize_continuation_reviewer_overrides({"*": "aip_rev"})


def test_resolve_reviewer_uses_default_after_explicit_item(monkeypatch):
    monkeypatch.setattr(review, "_provider_enabled", lambda project, provider: True)
    monkeypatch.setattr(review, "_stored_review_policy_for_item_seq", lambda doc, seq: (1, "aip_stored"))
    overrides = {"*": "aip_default", "2": "aip_item"}
    assert review.resolve_reviewer(overrides, 2, "p", "doc") == "aip_item"
    assert review.resolve_reviewer(overrides, 3, "p", "doc") == "aip_default"
    assert review.resolve_reviewer(None, 3, "p", "doc") == "aip_stored"
