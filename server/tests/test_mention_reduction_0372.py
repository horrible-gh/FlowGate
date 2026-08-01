"""Group 0372 set 3 — 전 문서 타입 멘트 축소 (D-0003 §3-2, L-0005 §2-10).

The worker mention now carries only what D-0003 §3-1 says must stay inline —
token-specific values, prohibitions, and the submission address/credential —
while every "how to" body lives behind help items. This suite pins the four
things set 3 changed on top of the earlier per-type reductions:

  1. the central help-index block (## 도움말 / Help / ヘルプ) appears exactly once
     in every worker mention, right below the identity + guide blocks;
  2. the standalone "## doc_type guide" section is gone from every builder
     (absorbed into the always-visible `doc_type` help-index entry);
  3. the clarification / review-phase guides keep the Q POST address + token but
     no longer embed the placeholder JSON body (→ `question` help item, whose
     example now demonstrates the `options` array);
  4. the decide / sequence-edit / review submissions keep address + credential
     and point at the `submit` help item instead of inlining a body example,
     and the TS engine-recipe block rides in the `test_commands` help item.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ["TESTING"] = "1"
os.environ.setdefault("SECRET_KEY", "test-secret-key")

_SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SERVER_DIR))

from modules.flow_gate.services import help_catalog  # noqa: E402
from modules.flow_gate.services import mention_service  # noqa: E402

_BASE = "http://h/flowgate/api/v1"
_TOKEN_REC = {"project": "p", "group_id": "p.default.0372", "scratch_dir": "S"}
_R_DOC = {"doc_id": "p.default.0372.0001-R", "type_code": "R", "seq": 1, "title": "r"}
_DS_DOC = {"doc_id": "p.default.0372.0002-DS", "type_code": "DS", "seq": 2, "title": "d"}

_HELP_HEADERS = {"ko": "## 도움말", "en": "## Help", "ja": "## ヘルプ"}


@pytest.fixture(autouse=True)
def _remote_mode(monkeypatch):
    """Pin source mode on so builder output does not depend on a DB."""
    monkeypatch.setattr(mention_service, "_include_remote_source_crud", lambda project: True)


def _build_new(locale: str = "ko", **over) -> str:
    params = {
        "project": "p",
        "module": "default",
        "group": "0372",
        "parent_type": "T",
        "parent_doc_number": "T0001",
        "parent_title": "t",
        "parent_doc_id": "R0001",
        "head_type": "TR",
        "head_status": "pending",
        "scratch_dir": "S",
        "raw_token": "RAW",
        "api_base_url": _BASE,
        "locale": locale,
    }
    params.update(over)
    return mention_service.build_mention(**params)


def _build_decide(locale: str = "ko", **over) -> str:
    return mention_service.build_workflow_decision_mention(
        token_rec=_TOKEN_REC, target_doc=_R_DOC, api_base_url=_BASE,
        raw_token="RAW", locale=locale, **over,
    )


def _build_seq_edit(locale: str = "ko") -> str:
    return mention_service.build_sequence_edit_mention(
        token_rec=_TOKEN_REC, target_doc=_R_DOC, api_base_url=_BASE,
        raw_token="RAW", locale=locale,
    )


def _build_review(locale: str = "ko") -> str:
    return mention_service.build_review_mention(
        token_rec=_TOKEN_REC, target_doc=_DS_DOC, api_base_url=_BASE,
        raw_token="RAW", locale=locale,
    )


def _headers(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.startswith("## ")]


# ── 1. central help-index block (L-0005 §2-10) ───────────────────────────────

@pytest.mark.parametrize("locale", ["ko", "en", "ja"])
@pytest.mark.parametrize("build", [_build_new, _build_decide, _build_seq_edit, _build_review])
def test_every_worker_mention_carries_the_help_block_once(build, locale):
    out = build(locale)
    header = _HELP_HEADERS[locale]
    assert out.count(f"{header}\n") == 1
    # The three pinned lines: index, item pick, detail=true — plus the credential.
    assert f"GET {_BASE}/help" in out
    assert f"GET {_BASE}/help/items/{{name}}" in out
    assert f"GET {_BASE}/help?detail=true" in out
    assert "Authorization: Bearer RAW" in out


@pytest.mark.parametrize("build", [_build_new, _build_decide, _build_seq_edit, _build_review])
def test_help_block_sits_right_below_identity_and_guide(build):
    headers = _headers(build("ko"))
    assert headers[0] == "## Document information"
    assert headers[2] == "## 도움말"


def test_help_block_slot_holds_in_continuous_mode():
    headers = _headers(_build_new(continuous=True))
    assert headers[:3] == ["## Document information", "## Continuous work", "## 도움말"]


# ── 2. doc_type guide absorbed into the index (D-0003 §3-2 "뺌") ─────────────

@pytest.mark.parametrize("build", [_build_new, _build_decide, _build_seq_edit, _build_review])
def test_standalone_doc_type_guide_section_is_gone(build):
    out = build("ko")
    assert "## doc_type guide" not in out
    assert "/help/doc_type" not in out


def test_decide_and_sequence_edit_point_at_the_doc_type_item_inline():
    # The instruction bodies still name where valid type codes come from — the
    # `doc_type` help item, not the retired standalone section.
    assert f"GET {_BASE}/help/items/doc_type" in _build_decide()
    assert f"GET {_BASE}/help/items/doc_type" in _build_seq_edit()


# ── 3. clarification / review-phase guides: address stays, JSON body moves ───

@pytest.mark.parametrize("locale", ["ko", "en", "ja"])
def test_clarification_guide_keeps_address_drops_json(locale):
    out = _build_new(locale)
    assert f"POST {_BASE}/q/R0001/questions" in out
    assert '"asker_kind"' not in out
    assert f"GET {_BASE}/help/items/question" in out
    # The no-choices guard itself is untouched (D-0003 §3-2 "남김").
    assert "Do NOT present choices" in out
    assert "register a Q" in out


def test_review_phase_guide_keeps_address_drops_json():
    out = _build_new(continuous=True, continuous_review_mode=True)
    assert "## Review phase" in out
    assert "/questions" in out
    assert '"asker_kind"' not in out
    assert f"GET {_BASE}/help/items/question" in out


def test_question_help_item_example_now_demonstrates_options():
    for locale in ("ko", "en", "ja"):
        example = help_catalog.build_question_content(locale)["example"]["body"]
        assert example["asker_kind"] == "ai"
        options = [q.get("options") for q in example["questions"] if q.get("options")]
        assert options, f"{locale}: question example must demonstrate the options array"


# ── 4. submissions keep address + credential, bodies live behind `submit` ────

def test_decide_submission_is_address_plus_pointer():
    out = _build_decide()
    assert f"Submit the workflow decision: POST {_BASE}/workflow/decide" in out
    assert f"GET {_BASE}/help/items/submit" in out
    assert '"doc_class"' not in out and '"sequence"' not in out


def test_sequence_edit_submission_is_address_plus_pointer():
    out = _build_seq_edit()
    assert f"Submit the sequence edit: PATCH {_BASE}/workflow/sequence" in out
    assert f"GET {_BASE}/help/items/submit" in out
    assert '"items"' not in out


def test_review_submission_is_address_plus_pointer_verdict_guide_stays():
    out = _build_review()
    assert f"Submit your review: POST {_BASE}/inbox" in out
    assert f"GET {_BASE}/help/items/submit" in out
    assert '"action": "review"' not in out
    # Verdict semantics stay inline (L-0006 keeps the purpose-specific slot).
    assert "## Verdict guide" in out
    assert "verdict values:" in out
    for value in ("- pass", "- issues", "- hold"):
        assert value in out


def test_ts_mention_no_longer_inlines_engine_recipes():
    out = _build_new(head_type="TS")
    assert "## Engine recipes" not in out
    # The TS authoring pointer (set-3 per-type reduction) is still there.
    assert "help/items/authoring_guide/TS" in out


def test_engine_recipes_ride_in_the_test_commands_help_item():
    content = help_catalog._content_test_commands({"project": "", "base_url": _BASE})
    assert "test-commands/help" in content["engine_recipes"]
    assert "?engine=" in content["engine_recipes"]


# ── locale hygiene for the strings set 3 added ───────────────────────────────

def test_new_strings_do_not_leak_korean_into_en_ja():
    import re

    hangul = re.compile(r"[가-힣]")
    for build in (_build_new, _build_decide, _build_seq_edit, _build_review):
        for locale in ("en", "ja"):
            out = build(locale)
            for line in out.splitlines():
                if "변경 파일" in line:
                    # the parser-literal section title is never translated (L-0005)
                    continue
                assert not hangul.search(line), f"{build.__name__}/{locale}: {line!r}"
