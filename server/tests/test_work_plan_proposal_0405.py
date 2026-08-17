"""작업계획(WP) 전용 제안 다이얼로그 — 서버 계약 (flowgate.default.0405 T0007).

P0004 [멘트 본문 — 범위와 작성법이 함께 실린 전문] 이 "이대로 고정한다"고 못 박은 것들을
그대로 시험한다.

  1. '## 작업계획 맡길 범위' 절의 본문·자리·빈 값 표기
     (0405 T0011 rev1 — 반려 "맡길 단계??? 이건 대체 왜나와": 절에서 '맡길 단계' 줄을 없앴다.
      화면에 단계를 고르는 칸이 없으므로 서버도 그 줄을 적지 않는다.)
  2. 비-WP 멘트는 범위를 보내도 한 바이트도 달라지지 않는다(회귀 금지)
  3. POST /workflow/advance 가 work_plan_scope 를 조용히 버리지 않는다
  4. action_scope='work_plan_proposal' 은 'new' 토큰이고 single 전용이다
  5. 고르지 않은 타입의 수량 0 이 생성 요청에서 더 이상 422 가 아니다
  6. 실제로 발급되는 WP 멘트 전문 — 범위 절과 작성법(design_template/WP · submit)이 함께 실린다

6번은 라우트를 그대로 호출해 진짜 토큰을 발급받고 그 응답의 mention 을 읽는다. 절 이름과
순서, 그리고 작성법을 얻는 두 창구가 실제 발급물에 들어 있는지를 문자열로 확인한다.
"""
from __future__ import annotations

import json

import pytest

from tests.test_work_plan_0395 import (  # noqa: F401 — module fixtures are used by name
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
# 0416 T0004 (B0001 "플래너한테 아무런 멘트도 전달할수가 없는거지?"): 화면에 새로 생긴
# 플래너 멘트 입력. 기존 SCOPE/EMPTY_SCOPE 는 note 없이 그대로 두어 그 두 상수를 쓰는
# 기존 시험이 전부 회귀 없이 통과하게 한다.
SCOPE_WITH_NOTE = {**SCOPE, "note": "기존 코드 스타일을 따르고 테스트를 함께 작성하십시오."}
# flowgate.default.0416 TR0005 (반려: "[실행 프로바이더] 이거 어디갔냐고"): 화면에 새로
# 생긴 실행 프로바이더 단일 선택. provider_ids(후보 다중선택)와는 별개 필드다.
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


# ── 1. 절의 본문 ──────────────────────────────────────────────────────────────

def test_scope_section_body_follows_p0004(seed):
    from modules.flow_gate.services import mention_service

    section = mention_service._work_plan_scope_section(SCOPE, PROJECT, "ko")
    lines = section.split("\n")
    assert lines[0] == SCOPE_HEADER
    assert lines[1] == "---"
    assert lines[2] == "아래 범위는 사람이 화면에서 고른 것입니다. 이 범위대로 작업계획을 작성하십시오."
    assert "장수를 셀 타입: DS " in section
    # flowgate.default.0423 T0005 item 8/15: the workflow_type_counts derivation this
    # section shows for the worker to use as evidence (empty here — no parent_doc_id).
    assert "workflow_type_counts 제시값: (없음)" in section
    assert "후보 공급자:" in section
    # 0405 T0011 rev1: '맡길 단계'는 화면에서도 멘트에서도 사라졌다.
    assert "맡길 단계" not in section
    # flowgate.default.0423 T0005 item 1/15: NR0003 §8 phrasing replaces the old
    # "각각 1" command — this is no longer the story the mention tells the worker.
    assert "고른 타입의 수량은 각각 1입니다." not in section
    assert "선택된 타입은 수량을 결정해도 되는 범위이지 수량 1이라는 뜻이 아닙니다." in section
    assert "근거가 없는 수량을 1로 추측하지 말고 0으로 두며" in section
    assert "단계 배분은 당신에게 맡깁니다 — 위 타입과 수량대로 단계를 펼치십시오." in section


def test_scope_section_draws_none_for_empty_arrays(seed):
    """두 배열이 모두 비어도 절은 사라지지 않는다 — '범위가 없다'와 '절을 못 받았다'는 다르다."""
    from modules.flow_gate.services import mention_service

    section = mention_service._work_plan_scope_section(EMPTY_SCOPE, PROJECT, "ko")
    assert "장수를 셀 타입: (없음)" in section
    assert "후보 공급자: (없음)" in section
    assert "맡길 단계" not in section


def test_a_stale_step_keys_field_is_ignored(seed):
    """0405 T0011 rev1: 옛 화면이 step_keys 를 보내와도 절에 단계 줄이 되살아나지 않는다."""
    from modules.flow_gate.services import mention_service

    section = mention_service._work_plan_scope_section(
        {**SCOPE, "step_keys": ["DS#1", "TR#1", "ZZ"]}, PROJECT, "ko",
    )
    assert "맡길 단계" not in section
    assert "DS#1" not in section and "TR#1" not in section and "- ZZ" not in section
    assert "장수를 셀 타입: DS " in section


def test_scope_section_localises_without_a_steps_line(seed):
    """en·ja 문안에서도 단계 줄과 그 꼬리 문장이 함께 사라졌다."""
    from modules.flow_gate.services import mention_service

    en = mention_service._work_plan_scope_section(SCOPE, PROJECT, "en")
    ja = mention_service._work_plan_scope_section(SCOPE, PROJECT, "ja")
    assert "Steps to delegate" not in en
    assert "Laying the steps out is delegated to you" in en
    assert "任せる段階" not in ja
    assert "段階の割り当てはあなたに任せます" in ja


def test_scope_section_includes_the_planner_note_when_present(seed):
    """T0004 발견 1: 화면에서 입력한 멘트가 '전달 멘트' 라벨 줄로 그대로 실린다."""
    from modules.flow_gate.services import mention_service

    section = mention_service._work_plan_scope_section(SCOPE_WITH_NOTE, PROJECT, "ko")
    assert "전달 멘트: 기존 코드 스타일을 따르고 테스트를 함께 작성하십시오." in section


def test_scope_section_note_defaults_to_none_when_absent(seed):
    """note 를 아예 보내지 않은 옛 화면·빈 문자열 둘 다 '(없음)'으로 적힌다."""
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


# flowgate.default.0416 TR0005 반려 대응 ("[실행 프로바이더] 이거 어디갔냐고"): 화면에 새로
# 생긴 실행 프로바이더 단일 선택이 절에 별도 줄로 실린다. provider_ids(후보 다중선택)와는
# 독립적인 값이다.

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
    """실행 프로바이더는 후보 다중선택(provider_ids)과 독립적이다 — 후보가 비어도 실린다."""
    from modules.flow_gate.services import mention_service

    scope = {"quantity_type_codes": ["DS"], "provider_ids": [], "provider_id": "aip_opus"}
    section = mention_service._work_plan_scope_section(scope, PROJECT, "ko")
    assert "실행 프로바이더: aip_opus · aip_opus" in section
    assert "후보 공급자: (없음)" in section


# flowgate.default.0416 TR0005 rev2 (검토 반려 "지적한거 셀프 수정해라" — 발견 3·4):
# rev1 의 절은 두 값을 "적어 주기만" 했다. 워커에게 그 값을 어디에 쓰라고 말하는 문장이
# 없으면 (1) 전달 멘트가 AI 가 만든 작업계획의 defaults.note 에 남는다는 보장이 없고
# (2) 후보 밖 실행 프로바이더가 실린 절은 tail 의 "후보에 없는 공급자를 적지 마십시오"와
# 정면으로 부딪힌다. 아래 시험들이 그 두 문장을 절에 못 박는다.

def test_scope_section_tells_the_worker_to_copy_the_note_into_defaults_note(seed):
    from modules.flow_gate.services import mention_service

    section = mention_service._work_plan_scope_section(SCOPE_WITH_NOTE, PROJECT, "ko")
    assert "위 '전달 멘트'는 최종 작업계획의 defaults.note 에 그대로 옮겨 적으십시오." in section
    # 빈 값이어도 지시는 사라지지 않는다 — 그때 defaults.note 가 무엇인지까지 말한다.
    empty = mention_service._work_plan_scope_section(EMPTY_SCOPE, PROJECT, "ko")
    assert "(없음)이면 defaults.note 는 빈 문자열입니다." in empty


def test_note_carry_instruction_localises(seed):
    from modules.flow_gate.services import mention_service

    en = mention_service._work_plan_scope_section(SCOPE_WITH_NOTE, PROJECT, "en")
    ja = mention_service._work_plan_scope_section(SCOPE_WITH_NOTE, PROJECT, "ja")
    assert "Copy the Delivery note above into the work plan's defaults.note verbatim." in en
    assert "「伝達メモ」を最終的な作業計画の defaults.note にそのまま書き写して" in ja


def test_execution_provider_rule_resolves_the_candidate_contradiction(seed):
    """실행 프로바이더는 후보 밖 값일 수 있다 — 그 절에서 어느 쪽을 따라야 하는지 말한다."""
    from modules.flow_gate.services import mention_service

    outside = {**SCOPE, "provider_id": "aip_outside"}
    section = mention_service._work_plan_scope_section(outside, PROJECT, "ko")
    # 세 문장이 한 절에 함께 실린다: 후보 밖 실행 프로바이더 / 후보 제한 / 그 둘의 관계.
    assert "실행 프로바이더: aip_outside" in section
    assert "후보에 없는 공급자를 steps[].provider_id 에 적지 마십시오." in section
    assert "'실행 프로바이더'는 이 멘트를 받아 지금 실행 중인 공급자이며 단계 배정 후보가 아닙니다" in section
    assert "steps[].provider_id 와 defaults.provider_id 에는 위 '후보 공급자'에 있는 값만 적으십시오." in section


def test_execution_provider_rule_is_absent_without_an_execution_provider(seed):
    """고른 실행 프로바이더가 없으면 그 줄에 대한 설명도 싣지 않는다(멘트를 늘리지 않는다)."""
    from modules.flow_gate.services import mention_service

    section = mention_service._work_plan_scope_section(SCOPE, PROJECT, "ko")
    assert "단계 배정 후보가 아닙니다" not in section
    # 전달 멘트 지시는 실행 프로바이더 유무와 무관하게 언제나 실린다.
    assert "defaults.note 에 그대로 옮겨 적으십시오." in section


def test_execution_provider_rule_localises(seed):
    from modules.flow_gate.services import mention_service

    en = mention_service._work_plan_scope_section(SCOPE_WITH_PROVIDER, PROJECT, "en")
    ja = mention_service._work_plan_scope_section(SCOPE_WITH_PROVIDER, PROJECT, "ja")
    assert "not a step-assignment candidate" in en
    assert "段階割り当ての候補ではありません" in ja


def test_template_rules_say_where_the_two_values_go(seed):
    """T0004 완료 기준 2번이 워커에게 닿는 두 번째 통로 — WP 정본 서식의 규칙 목록.

    rev1 에는 defaults.provider_id 규칙 자체가 없었고, defaults.note 규칙은 "모든 단계에
    공통으로 붙일 한 줄"이라는 뜻풀이라 '전달 멘트를 옮겨 적으라'는 지시가 아니었다.
    """
    from modules.flow_gate.services import work_plan_service as wp

    ko = "\n".join(wp.TEMPLATE_RULES["ko"])
    assert "'전달 멘트'가 있으면 그 값을 그대로 옮겨 적습니다" in ko
    assert "defaults.provider_id 는 provider_candidates 안의 값이거나 null 입니다" in ko
    for locale in ("ko", "en", "ja"):
        rules = "\n".join(wp.TEMPLATE_RULES[locale])
        assert "defaults.provider_id" in rules
        assert "defaults.note" in rules
        # flowgate.default.0423 T0005 item 3/15: the quantity-basis rule (evidence
        # first, workflow_type_counts as backup, never guess 1) must also be there.
        assert "workflow_type_counts" in rules


def test_the_template_pointer_delivers_those_rules_to_the_worker(seed):
    """규칙은 멘트가 가리키는 design_template/WP 응답으로 실제 배달된다."""
    from modules.flow_gate.services import help_catalog

    payload = help_catalog.build_child("design_template", "WP", _wp_help_ctx())
    rules = "\n".join(payload["content"]["rules"])
    assert "defaults.provider_id" in rules
    assert "'전달 멘트'가 있으면 그 값을 그대로 옮겨 적습니다" in rules


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
    """공급자 조회가 실패해도 멘트는 나온다 — 이름 대신 id 를 적을 뿐이다."""
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


# ── 2. 자리와 회귀 금지 ───────────────────────────────────────────────────────

def test_scope_section_sits_between_instruction_and_template(seed):
    out = _mention("WP", work_plan_scope=SCOPE)
    assert INSTRUCTION_HEADER in out and SCOPE_HEADER in out and TEMPLATE_HEADER in out
    assert out.index(INSTRUCTION_HEADER) < out.index(SCOPE_HEADER) < out.index(TEMPLATE_HEADER)


def test_wp_mention_without_scope_has_no_section(seed):
    assert SCOPE_HEADER not in _mention("WP")


@pytest.mark.parametrize("head_type", ["D", "T", "TR", "N", "TS"])
def test_non_wp_mention_is_byte_identical_with_a_scope(seed, head_type):
    """P0004: WP가 아닌 타입의 멘트가 한 글자도 달라지면 안 된다."""
    assert _mention(head_type, work_plan_scope=SCOPE) == _mention(head_type)


def test_edit_mention_never_grows_the_section(seed):
    """work_plan_fill(편집)과 혼동 금지 — 제안 절은 새 문서 발급에만 붙는다."""
    out = _mention("WP", action_scope="edit", work_plan_scope=SCOPE)
    assert SCOPE_HEADER not in out


# ── 3. /workflow/advance 가 범위를 실어 나른다 ────────────────────────────────

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


# ── 4. AI 호출 — work_plan_proposal ──────────────────────────────────────────

def test_proposal_scope_is_allowed_and_maps_to_a_new_token():
    from modules.flow_gate.api.v1 import ai_invoke_routes as air

    assert "work_plan_proposal" in air._ALLOWED_SCOPES
    assert air._TOKEN_SCOPE["work_plan_proposal"] == "new"
    # 편집 스코프는 그대로 — 두 스코프는 같은 범위를 쓰지만 다른 멘트를 만든다.
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


# ── 5. 고르지 않은 타입의 수량 0 ─────────────────────────────────────────────

def test_create_accepts_zero_for_unselected_types(seed, storage_root):
    """화면이 늘 보내는 {DS:1, D:0, T:1} 모양이 더 이상 422 가 아니다."""
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


def test_create_passes_the_planner_note_through_to_defaults(seed, storage_root):
    """T0004 완료 기준: 화면에서 입력한 멘트가 defaults.note 로 그대로 저장된다."""
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
    """빈 멘트도 계속 허용된다 — T0004 는 입력을 요구하지 않는다."""
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


# ── 6. 실제로 발급되는 WP 멘트 전문 ──────────────────────────────────────────

@pytest.fixture(scope="module")
def issued_wp_mention(seed, storage_root):
    """진짜 라우트 → 진짜 토큰 → 진짜 멘트. 이 결과가 TR 에 인용되는 그 본문이다."""
    from modules.flow_gate.api.v1 import workflow_decision_routes as wdr
    from modules.flow_gate.db.connection import FlowGateStore, get_store
    from modules.flow_gate.services import workflow_decision_service as wds

    # 0395 의 집중 픽스처는 등록 SQL 을 막아 둔다. 워크플로 CRUD 를 진짜로 돌리려면
    # 운영 폴백 로더를 되살려야 한다(0395 통합 시험과 같은 처리).
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
    """작성법을 얻는 두 창구가 실제 발급물에 들어 있다 — P0004 [워커가 멘트대로 등록한다]."""
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
    """멘트가 가리키는 design_template/WP 가 실제로 정본 JSON 서식을 돌려준다."""
    from modules.flow_gate.services import help_catalog

    ctx = _wp_help_ctx()
    assert help_catalog.decide_visibility("design_template", ctx).visible is True
    children = [row["name"] for row in help_catalog.enumerate_children("design_template", ctx)]
    assert "WP" in children
    payload = help_catalog.build_child("design_template", "WP", ctx)
    assert payload["content"]["body"]


def test_the_submit_pointer_states_the_work_plan_body_format(seed):
    """멘트가 가리키는 submit 항목이 WP 토큰에서 정본 JSON 이라고 말한다."""
    from modules.flow_gate.services import help_catalog

    payload = help_catalog.build_item("submit", _wp_help_ctx())["content"]
    assert payload["content_format"]["format"] == "canonical_json"
    assert payload["content_format"]["template_url"].endswith("/help/items/design_template/WP")


def test_issued_wp_mention_carries_the_planner_note(seed, storage_root):
    """T0004 — 화면에서 입력한 멘트가 실제로 발급되는 멘트 전문에 실린다.

    공유 issued_wp_mention 픽스처는 note 없는 SCOPE 로 모듈 범위에 고정돼 있으므로(다른
    시험들이 그 값에 기대고 있다), 여기서는 SCOPE_WITH_NOTE 로 별도 advance 를 호출한다.
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
        # 모듈 안의 다른 시험이 issued_wp_mention 픽스처를 먼저 태워 같은 ROOT_DOC 의
        # 시퀀스를 이미 결정해 두었을 수 있다 — 이 시험은 그 결정을 그대로 재사용한다.
        if "already_decided" not in str(exc):
            raise

    original_verify = wdr.verify_bearer
    # issued_wp_mention 픽스처와 같은 usr_wp_001 을 쓴다 — seed 가 실제로 만들어 둔
    # 사용자라 토큰 발급의 외래키 제약을 통과한다.
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
    # flowgate.default.0416 TR0005 rev2 (발견 3·4): 값과 함께 그 값을 어디에 쓰라는 지시도
    # 실제 발급물에 실린다 — 단위 시험만으로는 절이 멘트에 끼워지는 자리를 확인하지 못한다.
    assert "실행 프로바이더: aip_opus" in mention
    assert "위 '전달 멘트'는 최종 작업계획의 defaults.note 에 그대로 옮겨 적으십시오." in mention
    assert "'실행 프로바이더'는 이 멘트를 받아 지금 실행 중인 공급자이며 단계 배정 후보가 아닙니다" in mention


def test_create_leaves_every_step_unassigned_when_defaults_provider_is_null(seed):
    """flowgate.default.0416 TR0005 rev2 (검토 발견 2): defaults.provider_id 는 만들어지는
    모든 단계의 provider_id 로 그대로 번진다. 화면이 실행 프로바이더를 여기에 실으면 사람이
    고르지 않은 배정이 조용히 일어나고, "배정하지 않음"을 고를 수단도 없어진다 — T0004
    작업 3 은 단계/기본 공급자 배정을 생성 후 WorkPlanEditor.vue 의 책임으로 남겼다.
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

    # 대조군: 같은 호출에 실행 프로바이더를 실으면 세 단계가 전부 그 값으로 배정된다.
    assigned = wp.initial_body(
        ["DS", "T"], candidates, PROJECT,
        quantities={"DS": 1, "T": 1},
        defaults={"provider_id": "aip_opus", "note": "한 줄 지시"},
        type_providers={},
    )
    assert {step["provider_id"] for step in assigned["steps"]} == {"aip_opus"}


def test_ai_authored_wp_body_preserves_defaults_note(seed):
    """T0004 — AI 가 작성해 인박스로 등록하는 WP 본문도 같은 validate() 를 타므로
    defaults.note 가 검증 과정에서 사라지지 않는다(inbox_routes._handle_new 와 동일한 호출).
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


# ── 7. 등록된 AI 공급자가 없는 프로젝트 (0405 T0011 rev2) ────────────────────
#
# 사용자 반려: "AI공급자 선택할게 없으면 [2 후보공급자]는 안나오게 하고 1만 선택하고
# 생성할수 있게 해야하지 않겠니?" — 화면에서 ② 칸을 없애는 것만으로는 끝나지 않는다.
# 그 화면이 보내는 요청(빈 provider_candidates, 빈 provider_ids)을 서버가 받아 주어야
# 실제로 만들어진다.

def test_scope_tail_tells_the_worker_to_leave_the_provider_empty(seed):
    """후보가 비면 '후보에 없는 공급자를 적지 마십시오'가 아니라 '비워 두십시오'라고 적는다."""
    from modules.flow_gate.services import mention_service

    section = mention_service._work_plan_scope_section(
        {"quantity_type_codes": ["DS", "T"], "provider_ids": []}, PROJECT, "ko",
    )
    assert "후보 공급자: (없음)" in section
    assert "이 프로젝트에는 등록된 AI 공급자가 없으므로 steps[].provider_id 는 비워 두십시오." in section
    assert "후보에 없는 공급자를" not in section
    # 단계 배분을 워커에게 맡긴다는 마지막 줄은 두 경우 모두 그대로다.
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
    """공급자가 하나도 등록되지 않은 프로젝트에서는 ① 칸만으로 작업계획이 만들어진다."""
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


def test_create_still_rejects_empty_candidates_when_providers_exist(seed, storage_root):
    """고를 수 있는데 비워 보낸 요청은 지금까지대로 반려한다 — 완화는 '없을 때'뿐이다."""
    from unittest.mock import patch as mock_patch

    client = _client()
    with mock_patch(
        "modules.flow_gate.documents.routers.work_plan._providers",
        return_value=[{"id": "aip_opus", "name": "Claude Opus", "kind": "claude",
                       "exec_type": "cli", "enabled": True}],
    ):
        resp = client.post("/api/v1/documents/work-plan", json={
            "parent_doc_id": ROOT_DOC,
            "title": "0405 후보를 비워 보낸 요청",
            "counted_types": ["DS"],
            "provider_candidates": [],
            "quantities": {"DS": 1},
            "defaults": {"provider_id": None, "note": ""},
            "type_providers": {},
        })
    assert resp.status_code == 422, resp.text
    assert any(
        "provider_candidates" in str(err.get("loc") or err.get("field") or err)
        for err in resp.json().get("errors", [])
    )
