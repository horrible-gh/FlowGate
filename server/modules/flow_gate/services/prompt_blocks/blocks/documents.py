"""Document blocks: the unread-body rules and the bounded document reads.

Moved verbatim from ``mention_service`` (flowgate.v02test.0061 card 4). Every consumer -- the
mention lookup lines, the Q answer mention, the AI start precheck section and the help leaf
``documents/unreadable`` -- reads ``BODY_UNREADABLE_RULES`` from this module at call time, so
this is the one place the wording lives (0064 T-B1e: change the wording here only).
"""
from __future__ import annotations

from typing import Optional

from ..registry import Block, register

# The bounded-read details the help item ``document_access`` (``partial``) shows in structured form.
SECTION_READ_RULE = "Section reads accept exactly one of section, section_id, lines, or chars."
CONTENT_SEARCH_OPTIONS = "&include_matches=true&context_lines=2&hits_per_doc=5"

BODY_UNREADABLE_RULES: tuple[str, ...] = (
    "content가 null이면 읽지 못한 문서로 판단하십시오.",
    "content_state가 readable이 아니면 읽지 못한 문서로 판단하십시오.",
    "content_state가 readable이고 front matter뿐이면 읽은 빈 문서로 판단하십시오.",
    "문서의 열람 여부를 판단한 결과는 내부에서만 쓰고 답변에 나열하지 마십시오.",
    "읽지 못한 문서의 내용을 읽은 것처럼 인용하거나 요약하지 마십시오.",
    "읽지 못한 문서가 현재 요청 처리에 필요한 경우에만 문서 번호·사유(content_state 값)·답변에 미치는 영향·대체 근거(있으면)를 짧게 밝히십시오.",
    "대체 근거가 없으면 지어내지 마십시오.",
    "읽지 못한 문서가 없으면 '없음'·'해당 없음' 같은 열람 상태 보고를 쓰지 마십시오.",
    "읽지 못한 문서가 현재 답변과 무관하면 열람 상태 보고를 쓰지 마십시오.",
    "사용자가 근거나 열람 상황을 물으면 그 범위에서 설명하십시오.",
    "질문이 읽지 못한 문서의 본문을 전제로 하면 다른 자료에서 끌어낸 내용에 '원문 미확인 상태의 추정'이라고 표시하십시오. 이 표시는 필요한 문서의 미열람 보고와 별개로 하십시오.",
    "이관 안내가 제공된 경우에만 그 안내의 조건으로 이관 여부를 판단하십시오. 문서를 읽지 못한 사실을 새 이관 조건으로 삼지 마십시오.",
    "읽지 못한 이유가 권한 오류(403)이면 이 이유만으로 이관하지 마십시오. 이관 안내가 있으면 사람이 승인하거나 부여해야 하는 접근·실행 권한인지 그 안내에 따라 판단하십시오.",
)

BODY_UNREADABLE_ANSWER_REFERENCE = (
    "[문서 조회 도구]의 본문 미열람 규칙에 따라, 필요한 미열람 문서의 문서 번호·사유(content_state 값)·"
    "답변에 미치는 영향·대체 근거(있으면)를 짧게 밝히고 대체 근거를 지어내지 마십시오. "
    "미열람 문서가 없거나 답변과 무관하면 열람 상태 보고를 쓰지 마십시오. "
    "사용자가 근거나 열람 상황을 물으면 그 범위에서 설명하십시오. "
    "질문이 미열람 문서 본문을 전제로 하면 다른 자료에서 끌어낸 내용에 '원문 미확인 상태의 추정'이라고 별도로 표시하십시오."
)


def document_lookup_lines(
    base: str,
    raw_token: str,
    *,
    project: str = "",
    doc_id: str = "",
    rules: Optional[tuple[str, ...]] = None,
) -> list[str]:
    """Advertise the bounded document reads added by group 0370 (one shared block for creation/edit,
    workflow, review, chat and Q-answer work). ``rules`` defaults to ``BODY_UNREADABLE_RULES``
    read at call time."""
    target = doc_id or "<doc_id>"
    project_filter = f"&project={project}" if project else ""
    return [
        "Read only the document data you need; use the full-document GET only when necessary:",
        f"- Metadata without body: GET {base}/document/{target}/meta",
        f"- Outline without body: GET {base}/document/{target}/outline",
        f"- One section from that outline: GET {base}/document/{target}/section?section_id=<section_id>",
        f"- Relationships without body: GET {base}/document/{target}/relations",
        (
            f"- Search bodies with match locations/context: GET {base}/search/documents/content"
            f"?q=<keyword>{project_filter}{CONTENT_SEARCH_OPTIONS}"
        ),
        SECTION_READ_RULE,
        *(BODY_UNREADABLE_RULES if rules is None else rules),  # 0064 T-B1e: always, right before the credential line (P0006 §2.9 ②)
        f"Authorization: Bearer {raw_token}",
    ]


def _unreadable_rules() -> tuple[str, ...]:
    return BODY_UNREADABLE_RULES


def _answer_reference() -> str:
    return BODY_UNREADABLE_ANSWER_REFERENCE


register(Block("documents.unreadable", brief=_unreadable_rules, detail=_unreadable_rules,
               help_leaf="documents/unreadable"))
register(Block("documents.unreadable_answer", brief=_answer_reference, detail=_answer_reference,
               help_leaf="documents/unreadable"))
def document_lookup_brief(base, raw_token, *, project="", doc_id="", **_kwargs):
    target = doc_id or "<doc_id>"
    return [
        BODY_UNREADABLE_RULES[4],
        f"GET {base}/document/{target}/meta",
        f"GET {base}/document/{target}/outline",
        f"GET {base}/document/{target}/section?section_id=<section_id>",
        f"GET {base}/search/documents/content?q=<keyword>&project={project}",
        f"GET {base}/help/items/documents/partial",
        f"GET {base}/help/items/documents/unreadable",
        f"Authorization: Bearer {raw_token}",
    ]


register(Block("documents.lookup", brief=document_lookup_brief, detail=document_lookup_lines,
               help_leaf="documents/partial"))
