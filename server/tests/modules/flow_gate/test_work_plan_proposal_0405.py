"""WP proposal-dialog server contracts.

Pin the contracts fixed by the plan's complete scope-and-authoring mention:

1. Content, placement, and empty values in '## 작업계획 맡길 범위'. T0011 rev1 removes
   the obsolete step-selection line because the UI has no such selector.
2. Non-WP mentions remain byte-identical even when scope is supplied.
3. POST workflow/advance preserves work_plan_scope.
4. work_plan_proposal uses new tokens in single mode only.
5. Zero counts for unselected types no longer cause 422 on creation.
6. The issued WP mention contains both scope and design_template/WP / submit guidance.

The final case invokes the real route, issues a real token, and verifies the mention's
section names, order, and both sources of authoring guidance.
"""
from __future__ import annotations

import json
import os

import pytest

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("ALLOWED_ORIGIN", "http://localhost")
os.environ.setdefault("CONTEXT", "/flowgate")
os.environ.setdefault("DB_TYPE", "sqlite")

from tests.modules.flow_gate.test_work_plan_0395 import (  # noqa: F401 — module fixtures are used by name
    GROUP,
    PROJECT,
    ROOT_DOC,
    _client,
    patch_store,
    seed,
    storage_root,
    tmp_db,
)

SCOPE = {
    "quantity_type_codes": ["DS", "D", "P", "T"],
    "provider_ids": ["aip_opus", "aip_sonnet"],
}
EMPTY_SCOPE = {"quantity_type_codes": [], "provider_ids": []}
# planner-note input added after the regression reported no way to pass a note.
# Keep the existing SCOPE/EMPTY_SCOPE fixtures without notes so their existing tests
# continue to exercise the previous contract.
SCOPE_WITH_NOTE = {**SCOPE, "note": "기존 코드 스타일을 따르고 테스트를 함께 작성하십시오."}
# flowgate.default.0416 TR0005: restore the missing [실행 프로바이더] single selection.
# This field is independent of the provider_ids candidate multiselect.
SCOPE_WITH_PROVIDER = {**SCOPE, "provider_id": "aip_opus"}

SCOPE_HEADER = "## 작업계획 맡길 범위"
INSTRUCTION_HEADER = "## Instruction to include next document header"
TEMPLATE_HEADER = "## Document template"
SUBMIT_HEADER = "## Artifact registration"


class _FakeRequest:
    def __init__(self, locale: str = "ko"):
        self.headers = {"x-locale": locale}
        self.base_url = "http://localhost/"


def _mention(head_type: str, **over):
    from modules.flow_gate.services import mention_service

    kwargs = dict(
        project=PROJECT, module="default", group="0405",
        parent_type="R", parent_doc_number="R0001", parent_title="다음 액션이 작업계획일 떄",
        parent_doc_id="R0001", head_type=head_type, head_status="pending",
        scratch_dir=r"C:\scratch\tok_1", raw_token="tok-raw-1",
        api_base_url="http://localhost/flowgate/api/v1", locale="ko",
    )
    kwargs.update(over)
    return mention_service.build_mention(**kwargs)


# ── 1. Section content ──

def test_scope_section_body_follows_p0004(seed):
    from modules.flow_gate.services import mention_service

    section = mention_service._work_plan_scope_section(SCOPE, PROJECT, "ko")
    lines = section.split("\n")
    assert lines[0] == SCOPE_HEADER
    assert lines[1] == "---"
    assert lines[2] == "아래 범위는 사람이 화면에서 고른 것입니다. 이 범위대로 작업계획을 작성하십시오."
    assert "장수를 셀 타입: DS " in section
    # the workflow_type_counts derivation this
    # section shows for the worker to use as evidence (empty here — no parent_doc_id).
    assert "workflow_type_counts 제시값: (없음)" in section
    assert "후보 공급자:" in section
    # 0405 T0011 rev1: remove '맡길 단계' from both the UI and the mention.
    assert "맡길 단계" not in section
    # phrasing replaces the old
    # "각각 1" command — this is no longer the story the mention tells the worker.
    assert "고른 타입의 수량은 각각 1입니다." not in section
    assert "선택된 타입은 수량을 결정해도 되는 범위이지 수량 1이라는 뜻이 아닙니다." in section
    assert "근거가 없는 수량을 1로 추측하지 말고 0으로 두며" in section
    assert "단계 배분은 당신에게 맡깁니다 — 위 타입과 수량대로 단계를 펼치십시오." in section


def test_scope_section_draws_none_for_empty_arrays(seed):
    """Keep the section with both arrays empty: empty scope differs from missing scope guidance."""
    from modules.flow_gate.services import mention_service

    section = mention_service._work_plan_scope_section(EMPTY_SCOPE, PROJECT, "ko")
    assert "장수를 셀 타입: (없음)" in section
    assert "후보 공급자: (없음)" in section
    assert "맡길 단계" not in section


def test_a_stale_step_keys_field_is_ignored(seed):
    """legacy step_keys input must not restore the removed steps line."""
    from modules.flow_gate.services import mention_service

    section = mention_service._work_plan_scope_section(
        {**SCOPE, "step_keys": ["DS#1", "TR#1", "ZZ"]}, PROJECT, "ko",
    )
    assert "맡길 단계" not in section
    assert "DS#1" not in section and "TR#1" not in section and "- ZZ" not in section
    assert "장수를 셀 타입: DS " in section


def test_scope_section_localises_without_a_steps_line(seed):
    """Remove the steps line and its trailing guidance in en and ja too."""
    from modules.flow_gate.services import mention_service

    en = mention_service._work_plan_scope_section(SCOPE, PROJECT, "en")
    ja = mention_service._work_plan_scope_section(SCOPE, PROJECT, "ja")
    assert "Steps to delegate" not in en
    assert "Laying the steps out is delegated to you" in en
    assert "任せる段階" not in ja
    assert "段階の割り当てはあなたに任せます" in ja


def test_scope_section_includes_the_planner_note_when_present(seed):
    """T0004 finding 1: preserve the UI note on the '전달 멘트' line."""
    from modules.flow_gate.services import mention_service

    section = mention_service._work_plan_scope_section(SCOPE_WITH_NOTE, PROJECT, "ko")
    assert "전달 멘트: 기존 코드 스타일을 따르고 테스트를 함께 작성하십시오." in section


def test_scope_section_note_defaults_to_none_when_absent(seed):
    """Both legacy omitted notes and empty strings render '(없음)'."""
    from modules.flow_gate.services import mention_service

    without_key = mention_service._work_plan_scope_section(SCOPE, PROJECT, "ko")
    assert "전달 멘트: (없음)" in without_key

    blank = mention_service._work_plan_scope_section({**SCOPE, "note": "  "}, PROJECT, "ko")
    assert "전달 멘트: (없음)" in blank


def test_scope_section_note_label_localises(seed):
    from modules.flow_gate.services import mention_service

    en = mention_service._work_plan_scope_section(SCOPE_WITH_NOTE, PROJECT, "en")
    ja = mention_service._work_plan_scope_section(SCOPE_WITH_NOTE, PROJECT, "ja")
    assert "Delivery note: 기존 코드 스타일을 따르고 테스트를 함께 작성하십시오." in en
    assert "伝達メモ: 기존 코드 스타일을 따르고 테스트를 함께 작성하십시오." in ja
    assert "Delivery note: (none)" in mention_service._work_plan_scope_section(
        EMPTY_SCOPE, PROJECT, "en")
    assert "伝達メモ: (なし)" in mention_service._work_plan_scope_section(
        EMPTY_SCOPE, PROJECT, "ja")


# the newly restored execution-provider selection
# appears on its own line in the section and remains independent of
# the provider_ids candidate multiselect.

def test_scope_section_includes_the_default_provider_when_present(seed):
    from modules.flow_gate.services import mention_service

    section = mention_service._work_plan_scope_section(SCOPE_WITH_PROVIDER, PROJECT, "ko")
    assert "실행 프로바이더: aip_opus · aip_opus" in section


def test_scope_section_default_provider_defaults_to_none_when_absent(seed):
    from modules.flow_gate.services import mention_service

    without_key = mention_service._work_plan_scope_section(SCOPE, PROJECT, "ko")
    assert "실행 프로바이더: (없음)" in without_key

    blank = mention_service._work_plan_scope_section(
        {**SCOPE, "provider_id": "  "}, PROJECT, "ko")
    assert "실행 프로바이더: (없음)" in blank


def test_scope_section_default_provider_label_localises(seed):
    from modules.flow_gate.services import mention_service

    en = mention_service._work_plan_scope_section(SCOPE_WITH_PROVIDER, PROJECT, "en")
    ja = mention_service._work_plan_scope_section(SCOPE_WITH_PROVIDER, PROJECT, "ja")
    assert "Execution provider: aip_opus · aip_opus" in en
    assert "実行プロバイダー: aip_opus · aip_opus" in ja
    assert "Execution provider: (none)" in mention_service._work_plan_scope_section(
        EMPTY_SCOPE, PROJECT, "en")
    assert "実行プロバイダー: (なし)" in mention_service._work_plan_scope_section(
        EMPTY_SCOPE, PROJECT, "ja")


def test_default_provider_uses_registered_display_name(seed, monkeypatch):
    from modules.flow_gate.services import mention_service
    from modules.flow_gate.settings import ai_settings_service

    monkeypatch.setattr(
        ai_settings_service, "resolve_effective",
        lambda _project: {"providers": [{"id": "aip_opus", "name": "Claude Opus 5"}]},
    )
    section = mention_service._work_plan_scope_section(SCOPE_WITH_PROVIDER, PROJECT, "ko")
    assert "실행 프로바이더: aip_opus · Claude Opus 5" in section


def test_default_provider_renders_even_without_candidate_providers(seed):
    """Execution-provider selection is independent of provider_ids and renders with no candidates."""
    from modules.flow_gate.services import mention_service

    scope = {"quantity_type_codes": ["DS"], "provider_ids": [], "provider_id": "aip_opus"}
    section = mention_service._work_plan_scope_section(scope, PROJECT, "ko")
    assert "실행 프로바이더: aip_opus · aip_opus" in section
    assert "후보 공급자: (없음)" in section


# review findings 3 and 4.
# rev1 merely listed the two values without telling the worker where to use them.
# That failed to ensure the note reached defaults.note and left an execution provider
# outside the candidates contradicting the tail instruction to use only candidates.
# Pin the two clarifying instructions in the section.

def test_scope_section_tells_the_worker_to_author_defaults_note_from_the_delivery_note(seed):
    """replace verbatim-copy instructions with new-authoring guidance."""
    from modules.flow_gate.services import mention_service

    section = mention_service._work_plan_scope_section(SCOPE_WITH_NOTE, PROJECT, "ko")
    assert "그대로 옮겨 적으십시오" not in section
    assert "defaults.note" in section
    assert "steps[].note" in section
    assert "새로 작성하십시오" in section
    # An empty value still carries the instruction, stating there is no delivery note.
    empty = mention_service._work_plan_scope_section(EMPTY_SCOPE, PROJECT, "ko")
    assert "그대로 옮겨 적으십시오" not in empty
    assert "참고할 전달 멘트가 없다는 뜻" in empty


def test_note_authoring_instruction_localises(seed):
    """en and ja must also omit verbatim-copy instructions."""
    from modules.flow_gate.services import mention_service

    en = mention_service._work_plan_scope_section(SCOPE_WITH_NOTE, PROJECT, "en")
    ja = mention_service._work_plan_scope_section(SCOPE_WITH_NOTE, PROJECT, "ja")
    assert "verbatim" not in en
    assert "author new text for defaults.note" in en
    assert "そのまま書き写して" not in ja
    assert "新しく書いてください" in ja


def test_execution_provider_rule_resolves_the_candidate_contradiction(seed):
    """An execution provider can be outside candidates; state which rule takes precedence."""
    from modules.flow_gate.services import mention_service

    outside = {**SCOPE, "provider_id": "aip_outside"}
    section = mention_service._work_plan_scope_section(outside, PROJECT, "ko")
    # Include all three statements: external execution provider, candidate restriction, and their relationship.
    assert "실행 프로바이더: aip_outside" in section
    assert "후보에 없는 공급자를 steps[].provider_id 에 적지 마십시오." in section
    assert "'실행 프로바이더'는 이 멘트를 받아 지금 실행 중인 공급자이며 단계 배정 후보가 아닙니다" in section
    assert "steps[].provider_id 와 defaults.provider_id 에는 위 '후보 공급자'에 있는 값만 적으십시오." in section


def test_execution_provider_rule_is_absent_without_an_execution_provider(seed):
    """Omit execution-provider guidance when none was selected, avoiding unnecessary mention text."""
    from modules.flow_gate.services import mention_service

    section = mention_service._work_plan_scope_section(SCOPE, PROJECT, "ko")
    assert "단계 배정 후보가 아닙니다" not in section
    # Always include the instruction to author a new note, regardless of execution-provider selection.
    assert "defaults.note" in section
    assert "새로 작성하십시오" in section


def test_execution_provider_rule_localises(seed):
    from modules.flow_gate.services import mention_service

    en = mention_service._work_plan_scope_section(SCOPE_WITH_PROVIDER, PROJECT, "en")
    ja = mention_service._work_plan_scope_section(SCOPE_WITH_PROVIDER, PROJECT, "ja")
    assert "not a step-assignment candidate" in en
    assert "段階割り当ての候補ではありません" in ja


def test_template_rules_say_where_the_two_values_go(seed):
    """acceptance criterion 2 also reaches workers through canonical WP template rules.

    rev1 had no defaults.provider_id rule and only defined defaults.note as a shared
    one-line note; it did not instruct workers how to use the delivery note.
    """
    from modules.flow_gate.services import work_plan_service as wp

    ko = "\n".join(wp.TEMPLATE_RULES["ko"])
    assert "그대로 옮겨 적습니다" not in ko
    assert "'전달 멘트'가 있으면 그것을 입력 삼아 이 한 줄을 새로 작성합니다" in ko
    assert "defaults.provider_id 는 provider_candidates 안의 값이거나 null 입니다" in ko
    for locale in ("ko", "en", "ja"):
        rules = "\n".join(wp.TEMPLATE_RULES[locale])
        assert "defaults.provider_id" in rules
        assert "defaults.note" in rules
        # the quantity-basis rule (evidence
        # first, workflow_type_counts as backup, never guess 1) must also be there.
        assert "workflow_type_counts" in rules


def test_the_template_pointer_delivers_those_rules_to_the_worker(seed):
    """Deliver the rules through the design_template/WP response linked by the mention."""
    from modules.flow_gate.services import help_catalog

    payload = help_catalog.build_child("design_template", "WP", _wp_help_ctx())
    rules = "\n".join(payload["content"]["rules"])
    assert "defaults.provider_id" in rules
    assert "'전달 멘트'가 있으면 그것을 입력 삼아 이 한 줄을 새로 작성합니다" in rules


def test_provider_lines_use_registered_display_names(seed, monkeypatch):
    from modules.flow_gate.services import mention_service
    from modules.flow_gate.settings import ai_settings_service

    monkeypatch.setattr(
        ai_settings_service, "resolve_effective",
        lambda _project: {"providers": [
            {"id": "aip_opus", "name": "Claude Opus 5"},
            {"id": "aip_sonnet", "name": "Claude Sonnet 5"},
        ]},
    )
    section = mention_service._work_plan_scope_section(SCOPE, PROJECT, "ko")
    assert "- aip_opus · Claude Opus 5" in section
    assert "- aip_sonnet · Claude Sonnet 5" in section


def test_unknown_provider_degrades_to_its_id(seed, monkeypatch):
    """Provider lookup failure still produces a mention, using the ID instead of a name."""
    from modules.flow_gate.services import mention_service
    from modules.flow_gate.settings import ai_settings_service

    def _boom(_project):
        raise RuntimeError("settings unavailable")
    monkeypatch.setattr(ai_settings_service, "resolve_effective", _boom)
    section = mention_service._work_plan_scope_section(SCOPE, PROJECT, "ko")
    assert "- aip_opus · aip_opus" in section


def test_scope_section_localises(seed):
    from modules.flow_gate.services import mention_service

    assert "## Work plan scope to delegate" in mention_service._work_plan_scope_section(
        EMPTY_SCOPE, PROJECT, "en")
    assert "(none)" in mention_service._work_plan_scope_section(EMPTY_SCOPE, PROJECT, "en")
    assert "## 作業計画を任せる範囲" in mention_service._work_plan_scope_section(
        EMPTY_SCOPE, PROJECT, "ja")


# ── 2. Placement and regression protection ──

def test_scope_section_sits_between_instruction_and_template(seed):
    out = _mention("WP", work_plan_scope=SCOPE)
    assert INSTRUCTION_HEADER in out and SCOPE_HEADER in out and TEMPLATE_HEADER in out
    assert out.index(INSTRUCTION_HEADER) < out.index(SCOPE_HEADER) < out.index(TEMPLATE_HEADER)


def test_wp_mention_without_scope_has_no_section(seed):
    assert SCOPE_HEADER not in _mention("WP")


@pytest.mark.parametrize("head_type", ["D", "T", "TR", "N", "TS"])
def test_non_wp_mention_is_byte_identical_with_a_scope(seed, head_type):
    """preserve every byte of non-WP mentions."""
    assert _mention(head_type, work_plan_scope=SCOPE) == _mention(head_type)


def test_edit_mention_never_grows_the_section(seed):
    """Do not confuse work_plan_fill edits with proposal creation; only new-document issuance adds the section."""
    out = _mention("WP", action_scope="edit", work_plan_scope=SCOPE)
    assert SCOPE_HEADER not in out


# ── 3. Propagate scope through /workflow/advance ──

def test_advance_request_models_declare_work_plan_scope():
    from modules.flow_gate.api.v1 import workflow_decision_routes as wdr

    body = wdr.AdvanceBodyRequest(doc_id=ROOT_DOC, work_plan_scope=SCOPE)
    assert body.work_plan_scope == SCOPE
    assert wdr.AdvanceBodyRequest(doc_id=ROOT_DOC).work_plan_scope is None
    assert wdr.AdvanceRequest(work_plan_scope=SCOPE).work_plan_scope == SCOPE


def test_advance_route_forwards_work_plan_scope(monkeypatch):
    from modules.flow_gate.api.v1 import workflow_decision_routes as wdr

    captured: dict = {}
    monkeypatch.setattr(wdr, "verify_bearer", lambda _r: {"issued_to": "pm-1"})
    monkeypatch.setattr(wdr._db_documents, "get_by_id",
                        lambda _id: {"group_id": "g", "project_id": PROJECT})
    monkeypatch.setattr(wdr._process_service, "is_group_disposed", lambda _g: False)
    monkeypatch.setattr(wdr, "advance_workflow",
                        lambda **k: captured.update(k) or {"ok": True})

    wdr.post_workflow_advance_rpc(
        wdr.AdvanceBodyRequest(doc_id=ROOT_DOC, work_plan_scope=SCOPE), _FakeRequest(),
    )
    assert captured["work_plan_scope"] == SCOPE

    captured.clear()
    wdr.post_workflow_advance_rpc(wdr.AdvanceBodyRequest(doc_id=ROOT_DOC), _FakeRequest())
    assert captured["work_plan_scope"] is None


# ── 4. AI invocation: work_plan_proposal ──

def test_proposal_scope_is_allowed_and_maps_to_a_new_token():
    from modules.flow_gate.api.v1 import ai_invoke_routes as air

    assert "work_plan_proposal" in air._ALLOWED_SCOPES
    assert air._TOKEN_SCOPE["work_plan_proposal"] == "new"
    # Preserve edit scope: the two scopes share input but produce different mentions.
    assert air._TOKEN_SCOPE["work_plan_fill"] == "edit"


def test_proposal_start_request_carries_the_scope():
    from modules.flow_gate.api.v1 import ai_invoke_routes as air

    body = air.AiInvokeStartRequest(
        project=PROJECT, module="default", group="0405", doc_ref=ROOT_DOC,
        action_scope="work_plan_proposal", mode="single", provider_id="aip_opus",
        selected_docs=["a", "b"], work_plan_scope=SCOPE,
    )
    assert body.work_plan_scope == SCOPE
    assert body.selected_docs == ["a", "b"]


def test_proposal_rejects_continuous_mode(monkeypatch):
    from modules.flow_gate.api.v1 import ai_invoke_routes as air

    monkeypatch.setattr(air, "_require_user", lambda _r: {"issued_to": "usr_wp_001",
                                                          "_is_user_jwt": True})
    resp = air.start_ai_invoke(
        air.AiInvokeStartRequest(
            project=PROJECT, module="default", group="0405", doc_ref=ROOT_DOC,
            action_scope="work_plan_proposal", mode="continuous",
            continuation_target_seq=3,
        ),
        _FakeRequest(),
    )
    assert resp.status_code == 422
    payload = json.loads(resp.body)
    assert any(err["loc"] == "mode" for err in payload["errors"])


# ── 5. Zero counts for unselected types ──

def test_create_accepts_zero_for_unselected_types(seed, storage_root):
    """The normal UI shape {DS:1, D:0, T:1} no longer returns 422."""
    from unittest.mock import patch as mock_patch

    client = _client()
    with mock_patch(
        "modules.flow_gate.documents.routers.work_plan._providers",
        return_value=[{"id": "aip_opus", "name": "Claude Opus", "kind": "claude",
                       "exec_type": "cli", "enabled": True}],
    ), mock_patch(
        "modules.flow_gate.documents.routers.work_plan.numbering_service.reserve_document",
        return_value="0405-WP",
    ):
        resp = client.post("/api/v1/documents/work-plan", json={
            "parent_doc_id": ROOT_DOC,
            "title": "0405 부분 선택 작업계획",
            "counted_types": ["DS", "D", "P", "L", "DB", "N", "T", "TS"],
            "provider_candidates": ["aip_opus"],
            "quantities": {"DS": 1, "D": 0, "P": 0, "L": 0, "DB": 0, "N": 0, "T": 1, "TS": 0},
            "defaults": {"provider_id": None, "note": ""},
            "type_providers": {},
        })
    assert resp.status_code == 201, resp.text
    body = resp.json()["body"]
    assert body["quantities"]["D"]["count"] == 0
    assert [step["key"] for step in body["steps"]] == ["DS#1", "T#1", "TR#1"]


def test_non_ai_create_preserves_the_planner_note_verbatim_in_defaults(seed, storage_root):
    """0421 T0005: non-AI draft creation through [문서생성] preserves the entered common note.
    This differs from AI authoring through mention_service.note_rule and TEMPLATE_RULES.
    """
    from unittest.mock import patch as mock_patch

    client = _client()
    with mock_patch(
        "modules.flow_gate.documents.routers.work_plan._providers",
        return_value=[{"id": "aip_opus", "name": "Claude Opus", "kind": "claude",
                       "exec_type": "cli", "enabled": True}],
    ), mock_patch(
        "modules.flow_gate.documents.routers.work_plan.numbering_service.reserve_document",
        return_value="0407-WP",
    ):
        resp = client.post("/api/v1/documents/work-plan", json={
            "parent_doc_id": ROOT_DOC,
            "title": "0416 플래너 멘트가 실린 작업계획",
            "counted_types": ["DS", "T"],
            "provider_candidates": ["aip_opus"],
            "quantities": {"DS": 1, "T": 1},
            "defaults": {"provider_id": None, "note": "기존 코드 스타일을 따를 것"},
            "type_providers": {},
        })
    assert resp.status_code == 201, resp.text
    assert resp.json()["body"]["defaults"]["note"] == "기존 코드 스타일을 따를 것"


def test_create_still_accepts_an_empty_planner_note(seed, storage_root):
    """Empty notes remain valid; the earlier implementation does not require input."""
    from unittest.mock import patch as mock_patch

    client = _client()
    with mock_patch(
        "modules.flow_gate.documents.routers.work_plan._providers",
        return_value=[{"id": "aip_opus", "name": "Claude Opus", "kind": "claude",
                       "exec_type": "cli", "enabled": True}],
    ), mock_patch(
        "modules.flow_gate.documents.routers.work_plan.numbering_service.reserve_document",
        return_value="0408-WP",
    ):
        resp = client.post("/api/v1/documents/work-plan", json={
            "parent_doc_id": ROOT_DOC,
            "title": "0416 빈 멘트",
            "counted_types": ["DS"],
            "provider_candidates": ["aip_opus"],
            "quantities": {"DS": 1},
            "defaults": {"provider_id": None, "note": ""},
            "type_providers": {},
        })
    assert resp.status_code == 201, resp.text
    assert resp.json()["body"]["defaults"]["note"] == ""


def test_create_still_rejects_a_negative_quantity(seed, storage_root):
    from unittest.mock import patch as mock_patch

    client = _client()
    with mock_patch(
        "modules.flow_gate.documents.routers.work_plan._providers",
        return_value=[{"id": "aip_opus", "name": "Claude Opus", "kind": "claude",
                       "exec_type": "cli", "enabled": True}],
    ):
        resp = client.post("/api/v1/documents/work-plan", json={
            "parent_doc_id": ROOT_DOC,
            "title": "0405 음수 수량",
            "counted_types": ["DS"],
            "provider_candidates": ["aip_opus"],
            "quantities": {"DS": -1},
            "defaults": {"provider_id": None, "note": ""},
            "type_providers": {},
        })
    assert resp.status_code == 422, resp.text


# ── 6. The complete issued WP mention ──

@pytest.fixture(scope="module")
def issued_wp_mention(seed, storage_root):
    """Real route, token, and mention: the actual body cited in the TR."""
    from modules.flow_gate.api.v1 import workflow_decision_routes as wdr
    from modules.flow_gate.db.connection import FlowGateStore, get_store
    from modules.flow_gate.services import workflow_decision_service as wds

    # The focused 0395 fixture disables registered SQL. Restore the production fallback
    # loader to exercise real workflow CRUD, as the 0395 integration tests do.
    store = get_store()
    store._sql = FlowGateStore._sql.__get__(store, type(store))

    wds.decide_workflow(
        doc_id=ROOT_DOC, doc_class="R",
        sequence=[{"id": 1, "type": "WP", "label": "작업계획"}],
    )

    original_verify = wdr.verify_bearer
    wdr.verify_bearer = lambda _r: {"issued_to": "usr_wp_001"}
    try:
        resp = wdr.post_workflow_advance_rpc(
            wdr.AdvanceBodyRequest(
                doc_id=ROOT_DOC,
                ref_doc_ids=[ROOT_DOC],
                work_plan_scope=SCOPE,
            ),
            _FakeRequest(),
        )
    finally:
        wdr.verify_bearer = original_verify
    assert resp.status_code == 201, resp.body
    payload = json.loads(resp.body)
    print("\n===== ISSUED WP MENTION (token_id=%s) =====" % payload.get("token_id"))
    print(payload["mention"])
    print("===== END ISSUED WP MENTION =====")
    return payload


def test_issued_wp_mention_carries_the_scope_section(issued_wp_mention):
    mention = issued_wp_mention["mention"]
    assert SCOPE_HEADER in mention
    assert "장수를 셀 타입: DS " in mention
    assert "맡길 단계" not in mention
    assert "aip_opus" in mention and "aip_sonnet" in mention


def test_issued_wp_mention_carries_how_to_write_it(issued_wp_mention):
    """Include both authoring-guidance entry points in the actual issued mention."""
    mention = issued_wp_mention["mention"]
    assert TEMPLATE_HEADER in mention
    assert "/help/items/design_template/WP" in mention
    assert SUBMIT_HEADER in mention
    assert "/help/items/submit" in mention


def test_issued_wp_mention_section_order(issued_wp_mention):
    mention = issued_wp_mention["mention"]
    assert (
        mention.index(INSTRUCTION_HEADER)
        < mention.index(SCOPE_HEADER)
        < mention.index(TEMPLATE_HEADER)
        < mention.index("## Reference documents")
        < mention.index(SUBMIT_HEADER)
    )


def _wp_help_ctx():
    return {
        "locale": "ko", "base_url": "http://localhost/flowgate/api/v1",
        "doc_type": "WP", "action_scope": "new", "project": PROJECT,
        "group_id": GROUP, "doc_id": ROOT_DOC, "scratch_dir": r"C:\scratch\tok_1",
        "source_mode": "remote", "tool_kind": "read", "registry": {"tools": []},
        "principal_kind": "worker_token",
    }


def test_the_template_pointer_is_not_a_dead_link(seed):
    """The linked design_template/WP endpoint returns the real canonical JSON template."""
    from modules.flow_gate.services import help_catalog

    ctx = _wp_help_ctx()
    assert help_catalog.decide_visibility("design_template", ctx).visible is True
    children = [row["name"] for row in help_catalog.enumerate_children("design_template", ctx)]
    assert "WP" in children
    payload = help_catalog.build_child("design_template", "WP", ctx)
    assert payload["content"]["body"]


def test_the_submit_pointer_states_the_work_plan_body_format(seed):
    """The linked submit help specifies canonical JSON for WP tokens."""
    from modules.flow_gate.services import help_catalog

    payload = help_catalog.build_item("submit", _wp_help_ctx())["content"]
    assert payload["content_format"]["format"] == "canonical_json"
    assert payload["content_format"]["template_url"].endswith("/help/items/design_template/WP")


def test_issued_wp_mention_carries_the_planner_note(seed, storage_root):
    """include the UI-entered note in the actual issued mention.

    The shared module fixture uses note-free SCOPE for other tests, so issue a separate
    advance request with SCOPE_WITH_NOTE here.
    """
    from modules.flow_gate.api.v1 import workflow_decision_routes as wdr
    from modules.flow_gate.db.connection import FlowGateStore, get_store
    from modules.flow_gate.services import workflow_decision_service as wds

    store = get_store()
    store._sql = FlowGateStore._sql.__get__(store, type(store))

    try:
        wds.decide_workflow(
            doc_id=ROOT_DOC, doc_class="R",
            sequence=[{"id": 1, "type": "WP", "label": "작업계획"}],
        )
    except ValueError as exc:
        # Another test may already have decided the same ROOT_DOC sequence through
        # issued_wp_mention. Reuse that decision for this test.
        if "already_decided" not in str(exc):
            raise

    original_verify = wdr.verify_bearer
    # Use usr_wp_001, as in issued_wp_mention: seed creates this real user,
    # satisfying token-issuance foreign-key constraints.
    wdr.verify_bearer = lambda _r: {"issued_to": "usr_wp_001"}
    try:
        resp = wdr.post_workflow_advance_rpc(
            wdr.AdvanceBodyRequest(
                doc_id=ROOT_DOC,
                ref_doc_ids=[ROOT_DOC],
                work_plan_scope={**SCOPE_WITH_NOTE, "provider_id": "aip_opus"},
            ),
            _FakeRequest(),
        )
    finally:
        wdr.verify_bearer = original_verify
    assert resp.status_code == 201, resp.body
    mention = json.loads(resp.body)["mention"]
    assert "전달 멘트: 기존 코드 스타일을 따르고 테스트를 함께 작성하십시오." in mention
    # findings 3/4: the issued mention carries both values and usage
    # instructions; unit tests alone cannot prove insertion into the final mention.
    assert "실행 프로바이더: aip_opus" in mention
    assert "그대로 옮겨 적으십시오" not in mention
    assert "defaults.note" in mention
    assert "steps[].note" in mention
    assert "새로 작성하십시오" in mention
    assert "'실행 프로바이더'는 이 멘트를 받아 지금 실행 중인 공급자이며 단계 배정 후보가 아닙니다" in mention


def test_create_leaves_every_step_unassigned_when_defaults_provider_is_null(seed):
    """defaults.provider_id propagates to every generated step.
    Using the execution provider there would silently assign steps and remove the
    unassigned option. the earlier implementation task 3 leaves step/default assignment to WorkPlanEditor.vue
    after creation.
    """
    from modules.flow_gate.services import work_plan_service as wp

    candidates = wp.snapshot_candidates(
        ["aip_opus"],
        [{"id": "aip_opus", "name": "Claude Opus", "kind": "claude", "exec_type": "cli"}],
    )
    body = wp.initial_body(
        ["DS", "T"], candidates, PROJECT,
        quantities={"DS": 1, "T": 1},
        defaults={"provider_id": None, "note": "한 줄 지시"},
        type_providers={},
    )
    assert body["defaults"] == {"provider_id": None, "note": "한 줄 지시"}
    assert [step["key"] for step in body["steps"]] == ["DS#1", "T#1", "TR#1"]
    assert {step["provider_id"] for step in body["steps"]} == {None}

    # Control: supplying an execution provider assigns all three steps to it.
    assigned = wp.initial_body(
        ["DS", "T"], candidates, PROJECT,
        quantities={"DS": 1, "T": 1},
        defaults={"provider_id": "aip_opus", "note": "한 줄 지시"},
        type_providers={},
    )
    assert {step["provider_id"] for step in assigned["steps"]} == {"aip_opus"}


def test_ai_authored_wp_body_preserves_defaults_note(seed):
    """AI-authored WP inbox submissions use the same validate() call as _handle_new;
    validation must preserve defaults.note.
    """
    from modules.flow_gate.services import work_plan_service as wp

    plan = wp.initial_body(
        ["DS", "T"],
        [{"provider_id": "aip_opus", "display_name": "Claude Opus"}],
        PROJECT,
        quantities={"DS": 1, "T": 1},
        defaults={"provider_id": None, "note": "AI 가 받은 멘트를 그대로 담아야 한다"},
        type_providers={},
    )
    validated = wp.validate(plan, project_id=PROJECT, action="create")
    assert validated["defaults"]["note"] == "AI 가 받은 멘트를 그대로 담아야 한다"


def test_issued_token_is_a_new_scope_token(issued_wp_mention):
    assert issued_wp_mention["action_scope"] == "new"
    assert issued_wp_mention["token"]
    assert issued_wp_mention["scratch_dir"]


# ── 7. Project without registered AI providers ──
#
# The user requested creation with only the first field when no provider exists.
# Hiding candidate field 2 in the UI is insufficient:
# the server must accept its empty provider_candidates and provider_ids
# for creation to succeed.

def test_scope_tail_tells_the_worker_to_leave_the_provider_empty(seed):
    """With no candidates, instruct the worker to leave the provider empty instead of selecting a candidate."""
    from modules.flow_gate.services import mention_service

    section = mention_service._work_plan_scope_section(
        {"quantity_type_codes": ["DS", "T"], "provider_ids": []}, PROJECT, "ko",
    )
    assert "후보 공급자: (없음)" in section
    assert "이 프로젝트에는 등록된 AI 공급자가 없으므로 steps[].provider_id 는 비워 두십시오." in section
    assert "후보에 없는 공급자를" not in section
    # Preserve the final instruction delegating step allocation to the worker in both cases.
    assert "단계 배분은 당신에게 맡깁니다" in section


def test_scope_tail_for_empty_candidates_localises(seed):
    from modules.flow_gate.services import mention_service

    empty = {"quantity_type_codes": ["DS"], "provider_ids": []}
    en = mention_service._work_plan_scope_section(empty, PROJECT, "en")
    ja = mention_service._work_plan_scope_section(empty, PROJECT, "ja")
    assert "leave steps[].provider_id empty" in en
    assert "Do not write a provider outside these candidates" not in en
    assert "steps[].provider_id は空欄にしてください" in ja
    assert "候補にないプロバイダーを" not in ja


def test_scope_tail_is_unchanged_when_candidates_exist(seed):
    from modules.flow_gate.services import mention_service

    section = mention_service._work_plan_scope_section(SCOPE, PROJECT, "ko")
    assert "후보에 없는 공급자를 steps[].provider_id 에 적지 마십시오." in section
    assert "비워 두십시오" not in section


def test_create_accepts_empty_candidates_when_the_project_has_no_provider(seed, storage_root):
    """A project without registered providers can create a plan using only the first field."""
    from unittest.mock import patch as mock_patch

    client = _client()
    with mock_patch(
        "modules.flow_gate.documents.routers.work_plan._providers", return_value=[],
    ), mock_patch(
        "modules.flow_gate.documents.routers.work_plan.numbering_service.reserve_document",
        return_value="0406-WP",
    ):
        resp = client.post("/api/v1/documents/work-plan", json={
            "parent_doc_id": ROOT_DOC,
            "title": "0405 공급자 없는 프로젝트의 작업계획",
            "counted_types": ["DS", "D", "P", "L", "DB", "N", "T", "TS"],
            "provider_candidates": [],
            "quantities": {"DS": 1, "D": 0, "P": 0, "L": 0, "DB": 0, "N": 0, "T": 1, "TS": 0},
            "defaults": {"provider_id": None, "note": ""},
            "type_providers": {},
        })
    assert resp.status_code == 201, resp.text
    body = resp.json()["body"]
    assert body["provider_candidates"] == []
    assert [step["key"] for step in body["steps"]] == ["DS#1", "T#1", "TR#1"]
    assert {step["provider_id"] for step in body["steps"]} == {None}


def test_create_now_accepts_empty_candidates_when_providers_exist(seed, storage_root):
    """0591 T0005: the former 0411 T0004 concern about empty candidates disabling [전체]
    no longer applies. WorkPlanAiScopeDialog and suggest_work_plan derive selectable_ids
    from live project providers, not the stored snapshot; the dedicated selection test
    pins this. R0001 requires [+문서생성] to create an all-zero canonical WP with both
    selections empty, even with registered providers. AI delegation through
    [멘트복사]/[AI호출] still requires a provider on the client.
    """
    from unittest.mock import patch as mock_patch

    client = _client()
    with mock_patch(
        "modules.flow_gate.documents.routers.work_plan._providers",
        return_value=[{"id": "aip_opus", "name": "Claude Opus", "kind": "claude",
                       "exec_type": "cli", "enabled": True}],
    ), mock_patch(
        "modules.flow_gate.documents.routers.work_plan.numbering_service.reserve_document",
        return_value="0591-WP",
    ):
        resp = client.post("/api/v1/documents/work-plan", json={
            "parent_doc_id": ROOT_DOC,
            "title": "0591 등록 공급자가 있어도 빈 후보로 생성",
            "counted_types": ["DS"],
            "provider_candidates": [],
            "quantities": {"DS": 1},
            "defaults": {"provider_id": None, "note": ""},
            "type_providers": {},
        })
    assert resp.status_code == 201, resp.text
    body = resp.json()["body"]
    assert body["provider_candidates"] == []


def test_create_builds_an_all_zero_canonical_wp_with_nothing_picked(seed, storage_root):
    """0591 T0005 §3: [+문서생성] with no types or candidate providers selected creates
    canonical all-zero WP content: steps=[], zero quantities, provider_candidates=[]."""
    from unittest.mock import patch as mock_patch

    client = _client()
    with mock_patch(
        "modules.flow_gate.documents.routers.work_plan._providers",
        return_value=[
            {"id": "aip_opus", "name": "Claude Opus", "kind": "claude", "exec_type": "cli", "enabled": True},
            {"id": "aip_sonnet", "name": "Claude Sonnet", "kind": "claude", "exec_type": "cli", "enabled": True},
        ],
    ), mock_patch(
        "modules.flow_gate.documents.routers.work_plan.numbering_service.reserve_document",
        return_value="0592-WP",
    ):
        resp = client.post("/api/v1/documents/work-plan", json={
            "parent_doc_id": ROOT_DOC,
            "title": "0591 all-zero canonical WP",
            "counted_types": ["DS", "D", "P", "L", "DB", "N", "T", "TS"],
            "provider_candidates": [],
            "quantities": {"DS": 0, "D": 0, "P": 0, "L": 0, "DB": 0, "N": 0, "T": 0, "TS": 0},
            "defaults": {"provider_id": None, "note": ""},
            "type_providers": {},
        })
    assert resp.status_code == 201, resp.text
    body = resp.json()["body"]
    assert body["steps"] == []
    assert body["provider_candidates"] == []
    assert {item["count"] for item in body["quantities"].values()} == {0}
