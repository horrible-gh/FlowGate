"""Merge-review conversation turn copy (flowgate.default.0578 T0006 §2.2, D0005 §3.1).

``_materialize_pending_conversation_run`` no longer writes a finished Korean sentence
into the turn it stores: it stores a ``message_code`` (what happened) plus the
``message_params`` that event needs, and the SURFACE picks the words. The screen does
that through the client's ``main.git_review.turn_message.*`` keys; this module is the
other surface -- the AI history ``token_routes`` replays into a worker's prompt, which
has no Vue i18n dictionary and has to render the same nine events itself, in the
locale that worker's run started in (D0005 §3.6).

Two rules this module exists to keep:

* ``git_service`` never calls it. A translated sentence is never stored alongside the
  code it was rendered from (D0005 §3.2) -- that would be exactly the persisted natural
  language NR0003 F1 measured, only harder to find.
* It never raises. An unknown code, a missing interpolation value, a params dict of
  the wrong shape: every one of them falls back to the generic status line, because the
  alternative is an exception thrown from inside prompt assembly, which loses the whole
  history rather than one line of it (T0006 §3 work item 1-3).
"""
from __future__ import annotations

from typing import Any, Mapping, Optional

from modules.flow_gate import template_provision

# The stored `message_code` vocabulary (T0006 §2.2). `status` stays what it always was
# and is NOT derivable from these: `accepted` covers three of them.
CODE_STALE_RUN = "review_stale_run"
CODE_RUN_LOST = "review_run_lost"
CODE_APPLY_RE_REVIEW = "review_apply_re_review"
CODE_APPLY_HELD_ONLY = "review_apply_held_only"
CODE_APPLY_ROLLBACK_VERIFICATION_FAILED = "review_apply_rollback_verification_failed"
CODE_APPLY_FAILED = "review_apply_failed"
CODE_CANCELLED = "review_cancelled"
CODE_NO_ANSWER = "review_no_answer"
CODE_RUN_FAILED = "review_run_failed"

TURN_MESSAGE_CODES = (
    CODE_STALE_RUN,
    CODE_RUN_LOST,
    CODE_APPLY_RE_REVIEW,
    CODE_APPLY_HELD_ONLY,
    CODE_APPLY_ROLLBACK_VERIFICATION_FAILED,
    CODE_APPLY_FAILED,
    CODE_CANCELLED,
    CODE_NO_ANSWER,
    CODE_RUN_FAILED,
)

# Which of the three L0007 §2.9 identity checks fired. The CONDITIONS are unchanged and
# still live in `_materialize_pending_conversation_run`; only their outcome is carried
# here, as codes instead of sentences.
STALE_APPROVAL_SETTLED = "approval_settled"
STALE_CANDIDATE_REFROZEN = "candidate_refrozen"
STALE_INSTRUCTION_GENERATION_BUMPED = "instruction_generation_bumped"

STALE_REASON_CODES = (
    STALE_APPROVAL_SETTLED,
    STALE_CANDIDATE_REFROZEN,
    STALE_INSTRUCTION_GENERATION_BUMPED,
)

# `approval_settled` carries `review_state` -- EXCEPT when the approval wait ended by
# [반려], which puts the session back to `open` and leaves `review_state` None. That is a
# real, reachable case (test_review_gate_conversation_stale_run_discarded_when_rejected_
# while_active), not a malformed param, so it gets its own sentence instead of the
# generic line or a literal "None" on screen.
_STALE_SETTLED_NO_STATE = {
    "ko": "승인 대기가 끝났습니다",
    "en": "the approval wait ended",
    "ja": "承認待ちが終わりました",
}

# `status` (turn-level) and `review_state` (settled-approval reason) are stable internal
# identifiers, not copy -- the generic fallback and the `approval_settled` reason used to
# interpolate them raw, which put English/Korean product state names into ko/en/ja
# sentences (review finding: "settled-approval output contains completed"). Both maps are
# display-only: nothing that stores or compares `status`/`review_state` reads these.
_UNKNOWN_LABEL_KEY = "_unknown"

_STATUS_LABELS = {
    "ko": {
        "accepted": "완료", "failed": "실패", "stale_run": "지난 후보에 대한 답변",
        "run_lost": "실행 기록 없음", "cancelled": "취소됨",
        _UNKNOWN_LABEL_KEY: "알 수 없는 상태",
    },
    "en": {
        "accepted": "completed", "failed": "failed", "stale_run": "answer to a previous candidate",
        "run_lost": "no run record", "cancelled": "cancelled",
        _UNKNOWN_LABEL_KEY: "an unknown state",
    },
    "ja": {
        "accepted": "完了", "failed": "失敗", "stale_run": "前の候補への回答",
        "run_lost": "実行記録なし", "cancelled": "キャンセル済み",
        _UNKNOWN_LABEL_KEY: "不明な状態",
    },
}

_REVIEW_STATE_LABELS = {
    "ko": {
        "applying": "적용 중", "reconciling": "정리 중", "completed": "완료",
        _UNKNOWN_LABEL_KEY: "알 수 없는 상태",
    },
    "en": {
        "applying": "applying", "reconciling": "reconciling", "completed": "completed",
        _UNKNOWN_LABEL_KEY: "an unknown state",
    },
    "ja": {
        "applying": "適用中", "reconciling": "整合中", "completed": "完了",
        _UNKNOWN_LABEL_KEY: "不明な状態",
    },
}


def _status_label(locale: str, status: Optional[str]) -> str:
    labels = _STATUS_LABELS[locale]
    if not isinstance(status, str) or not status:
        return labels[_UNKNOWN_LABEL_KEY]
    return labels.get(status, labels[_UNKNOWN_LABEL_KEY])


def _review_state_label(locale: str, state: str) -> str:
    labels = _REVIEW_STATE_LABELS[locale]
    return labels.get(state, labels[_UNKNOWN_LABEL_KEY])


_STALE_REASONS = {
    "ko": {
        STALE_APPROVAL_SETTLED: "승인 대기가 끝났습니다(현재 {review_state})",
        STALE_CANDIDATE_REFROZEN: "승인 대상이 새 후보로 바뀌었습니다",
        STALE_INSTRUCTION_GENERATION_BUMPED: "재지시로 지시 회차가 올라갔습니다",
    },
    "en": {
        STALE_APPROVAL_SETTLED: "the approval wait ended (now {review_state})",
        STALE_CANDIDATE_REFROZEN: "the approval target moved to a new candidate",
        STALE_INSTRUCTION_GENERATION_BUMPED: "a re-instruction raised the instruction generation",
    },
    "ja": {
        STALE_APPROVAL_SETTLED: "承認待ちが終わりました(現在 {review_state})",
        STALE_CANDIDATE_REFROZEN: "承認対象が新しい候補に変わりました",
        STALE_INSTRUCTION_GENERATION_BUMPED: "再指示により指示回次が上がりました",
    },
}

# Sentence fragments the templates below assemble. Kept apart from the templates so a
# locale's joiner/plan note stays next to the sentence it belongs to.
_PARTS = {
    "ko": {
        "reason_join": ", ",
        "unknown_reason": "승인 대상이 달라졌습니다",
        "stale_plan_discarded": " 함께 제출된 수정안은 적용하지 않았습니다.",
        "held_note": " 보류된 테스트 편집 {held_count}건은 적용하지 않았습니다([테스트 편집 포함 재지시]로 재요청할 수 있습니다).",
        "no_paths": "(없음)",
        "path_join": ", ",
    },
    "en": {
        "reason_join": ", ",
        "unknown_reason": "the approval target changed",
        "stale_plan_discarded": " The write plan submitted with it was not applied.",
        "held_note": " {held_count} held test edit(s) were not applied (ask again with [Re-instruct including test edits]).",
        "no_paths": "(none)",
        "path_join": ", ",
    },
    "ja": {
        "reason_join": "、",
        "unknown_reason": "承認対象が変わりました",
        "stale_plan_discarded": " 一緒に提出された修正案は適用していません。",
        "held_note": " 保留されたテスト編集 {held_count} 件は適用していません([テスト編集を含めて再指示]で再依頼できます)。",
        "no_paths": "(なし)",
        "path_join": "、",
    },
}

_TEMPLATES = {
    "ko": {
        CODE_STALE_RUN: "이 답을 만드는 동안 {reasons}. 아래 내용은 그 이전 후보를 보고 쓴 것입니다(stale_run).",
        CODE_RUN_LOST: "이 지시를 맡은 실행의 기록이 남아 있지 않아 답을 받지 못했습니다(run_lost). 같은 내용을 다시 보내 주십시오.",
        CODE_APPLY_RE_REVIEW: "요청한 수정을 적용해 새 승인 대상을 만들었습니다. 변경된 파일: {paths}",
        CODE_APPLY_HELD_ONLY: "제출된 연산이 모두 테스트 경로라 보류했습니다({held_count}건, 미적용). [테스트 편집 포함 재지시]로 다시 요청하십시오.",
        CODE_APPLY_ROLLBACK_VERIFICATION_FAILED: "수정 적용 실패 후 상태 복구 확인에도 실패했습니다 — 사람 확인이 필요합니다.",
        CODE_APPLY_FAILED: "수정 적용에 실패했습니다(원인 {error_count}건).",
        CODE_CANCELLED: "사용자가 이 실행을 중지했습니다(취소됨).",
        CODE_NO_ANSWER: "실행은 끝났지만 답변이 비어 있습니다.",
        CODE_RUN_FAILED: "실행이 실패해 답을 받지 못했습니다.",
        "generic_status": "이 차례는 '{status}' 상태로 끝났습니다.",
    },
    "en": {
        CODE_STALE_RUN: "While this answer was being written, {reasons}. The text below was written against the previous candidate (stale_run).",
        CODE_RUN_LOST: "No record is left of the run that took this instruction, so no answer arrived (run_lost). Please send the same message again.",
        CODE_APPLY_RE_REVIEW: "The requested change was applied and a new approval candidate was created. Changed files: {paths}",
        CODE_APPLY_HELD_ONLY: "Every submitted operation targets a test path, so all of them were held ({held_count}, not applied). Ask again with [Re-instruct including test edits].",
        CODE_APPLY_ROLLBACK_VERIFICATION_FAILED: "The change failed to apply and the rollback could not be verified either — a human has to check this.",
        CODE_APPLY_FAILED: "The change could not be applied ({error_count} cause(s)).",
        CODE_CANCELLED: "The user stopped this run (cancelled).",
        CODE_NO_ANSWER: "The run finished but its answer was empty.",
        CODE_RUN_FAILED: "The run failed, so no answer arrived.",
        "generic_status": "This turn ended in the '{status}' state.",
    },
    "ja": {
        CODE_STALE_RUN: "この回答を作成している間に{reasons}。以下の内容は、その前の候補を見て書かれたものです(stale_run)。",
        CODE_RUN_LOST: "この指示を担当した実行の記録が残っておらず、回答を受け取れませんでした(run_lost)。同じ内容をもう一度送ってください。",
        CODE_APPLY_RE_REVIEW: "依頼された修正を適用し、新しい承認対象を作成しました。変更されたファイル: {paths}",
        CODE_APPLY_HELD_ONLY: "提出された操作がすべてテストパスだったため保留しました({held_count} 件、未適用)。[テスト編集を含めて再指示]でもう一度依頼してください。",
        CODE_APPLY_ROLLBACK_VERIFICATION_FAILED: "修正の適用に失敗し、状態復旧の確認にも失敗しました — 人による確認が必要です。",
        CODE_APPLY_FAILED: "修正の適用に失敗しました(原因 {error_count} 件)。",
        CODE_CANCELLED: "ユーザーがこの実行を停止しました(キャンセル)。",
        CODE_NO_ANSWER: "実行は終了しましたが、回答が空でした。",
        CODE_RUN_FAILED: "実行が失敗し、回答を受け取れませんでした。",
        "generic_status": "このターンは '{status}' の状態で終了しました。",
    },
}

def _as_int(value: Any) -> Optional[int]:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _as_nonneg_int(value: Any) -> Optional[int]:
    """Strict count validation: no bools, no floats/fractions, no negatives.

    A malformed count (missing, wrong type, or negative) means the whole code's
    params failed its schema -- the caller falls back to the generic notice rather
    than rendering a sentence built from a count it cannot trust.
    """
    n = _as_int(value)
    if n is None or n < 0:
        return None
    return n


def _generic(locale: str, status: Optional[str]) -> str:
    return _TEMPLATES[locale]["generic_status"].format(status=_status_label(locale, status))


def _stale_sentence(locale: str, params: Mapping[str, Any]) -> Optional[str]:
    raw = params.get("changed")
    if not isinstance(raw, (list, tuple)) or not raw:
        return None
    if "plan_discarded" in params and not isinstance(params["plan_discarded"], bool):
        return None
    reason_map = _STALE_REASONS[locale]
    parts = _PARTS[locale]
    rendered = []
    for code in raw:
        template = reason_map.get(code) if isinstance(code, str) else None
        if template is None:
            rendered.append(parts["unknown_reason"])
            continue
        if "{review_state}" in template:
            state = params.get("review_state")
            if not isinstance(state, str) or not state:
                rendered.append(_STALE_SETTLED_NO_STATE[locale])
                continue
            template = template.format(review_state=_review_state_label(locale, state))
        rendered.append(template)
    text = _TEMPLATES[locale][CODE_STALE_RUN].format(
        reasons=parts["reason_join"].join(rendered)
    )
    if params.get("plan_discarded") is True:
        text += parts["stale_plan_discarded"]
    return text


def _re_review_sentence(locale: str, data: Mapping[str, Any]) -> Optional[str]:
    raw_paths = data.get("paths")
    if not isinstance(raw_paths, (list, tuple)) or not all(isinstance(p, str) for p in raw_paths):
        return None
    path_count = _as_nonneg_int(data.get("path_count"))
    if path_count is None or path_count != len(raw_paths):
        return None
    held = 0
    if "held_count" in data:
        held = _as_nonneg_int(data.get("held_count"))
        if held is None:
            return None
    parts = _PARTS[locale]
    rendered = parts["path_join"].join(raw_paths) if raw_paths else parts["no_paths"]
    text = _TEMPLATES[locale][CODE_APPLY_RE_REVIEW].format(paths=rendered)
    if held > 0:
        text += parts["held_note"].format(held_count=held)
    return text


def _apply_failed_sentence(locale: str, data: Mapping[str, Any]) -> Optional[str]:
    errors = _as_nonneg_int(data.get("error_count"))
    if errors is None:
        return None
    held = 0
    if "held_count" in data:
        held = _as_nonneg_int(data.get("held_count"))
        if held is None:
            return None
    text = _TEMPLATES[locale][CODE_APPLY_FAILED].format(error_count=errors)
    if held > 0:
        text += _PARTS[locale]["held_note"].format(held_count=held)
    return text


def render_turn_message(
    code: Optional[str],
    params: Optional[Mapping[str, Any]],
    locale: Optional[str],
    status: Optional[str] = None,
) -> str:
    """The product sentence for one stored ``message_code`` in ``locale``.

    ``status`` is only ever used for the generic fallback line; it is NOT consulted to
    pick a sentence, because `accepted` maps to three different codes (T0006 §2.2).
    Never raises -- see the module docstring.
    """
    normalized = template_provision.normalize_locale(locale)
    if normalized not in _TEMPLATES:  # normalize_locale already guarantees this
        normalized = "ko"
    data: Mapping[str, Any] = params if isinstance(params, Mapping) else {}

    # `code` must be one of the nine stored codes -- NOT any key that happens to exist
    # in the locale template dict. `generic_status` lives in that dict too (it is the
    # fallback template itself), so without this check an inbound `message_code:
    # "generic_status"` would match it and render the literal, unformatted `'{status}'
    # 상태로...` placeholder instead of falling back (review finding).
    if not isinstance(code, str) or code not in TURN_MESSAGE_CODES:
        return _generic(normalized, status)

    if code == CODE_STALE_RUN:
        return _stale_sentence(normalized, data) or _generic(normalized, status)
    if code == CODE_APPLY_RE_REVIEW:
        return _re_review_sentence(normalized, data) or _generic(normalized, status)
    if code == CODE_APPLY_FAILED:
        return _apply_failed_sentence(normalized, data) or _generic(normalized, status)
    if code == CODE_APPLY_HELD_ONLY:
        held = _as_nonneg_int(data.get("held_count"))
        if held is None:
            return _generic(normalized, status)
        return _TEMPLATES[normalized][code].format(held_count=held)

    return _TEMPLATES[normalized][code]
