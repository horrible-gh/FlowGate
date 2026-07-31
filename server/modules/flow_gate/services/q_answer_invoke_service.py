"""Query-answer hand-off — give ONE query item to an AI worker (0248 B0001).

D0005 §3.2 / L0007 §3.4 specify that a document-bound query is handed to an AI worker and
the answer lands back on the SAME item as author_kind='ai'. The route only ever minted an
edit token and returned it to the browser: nothing launched a worker and no UI surfaced the
token, so the click was a silent no-op (NR0003). This module is the missing half.

It serves the two hand-off routes the legacy Q-document flow has always offered (see
AnswerEditor.vue / qa_routes.py `dispatch_mode`), which the document-bound Q&A panel was
missing entirely — leaving the asker to answer their own question:

  • [멘트 복사]  → issue_answer_token: the user pastes the mention into their own worker.
                   Works with no provider configured, which is why it is not a nicety.
  • [AI에게 답변 요청] → dispatch_answer_run: an in-app run through the shared
                   ai_invoke_service engine (group lock, provider chain + fallback, scratch
                   lifecycle, timeout, cancel, status/SSE all come free).

Both mint the same token and render the same mention, so the two paths cannot drift.

Two things separate this from every other invoke path:

1. The product is an answer ROW on an existing document, not a new document — the engine's
   document-reach oracle would score a perfect run as outcome='none'. We pass a
   completion_oracle so the run is judged by "did an AI answer appear on THIS item".
2. The mention must pin the worker to one item. The generic '## 사용자 질의응답' block
   (prompt_copy_service._append_qa_block) lists a document's whole Q&A, which does not say
   which item to answer, so this builds a dedicated prompt naming the item and its POST
   contract.

The token is edit-scoped and doc_ref-bound because that is exactly what the receiving route
(q_tapi_routes._resolve_writer) accepts. On the run path it never leaves the server (it is
injected as the run's FLOWGATE_TOKEN env, so the /ai-request response carries only the run
handle). On the copy path the token IS the deliverable — it has to reach the browser to be
pasted, exactly as /token/issue hands one to every other copy-mention site.
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import HTTPException

from modules.flow_gate.db import answers as db_answers
from modules.flow_gate.db import groups as db_groups
from modules.flow_gate.services import ai_invoke_service, q_service, token_service

logger = logging.getLogger(__name__)


def _ai_answer_count(item_id: int) -> int:
    """AI answers currently registered on the item. Failure → 0 (see _make_oracle)."""
    try:
        return sum(
            1
            for a in db_answers.list_by_question_item(item_id)
            if (a.get("author_kind") or "") == "ai"
        )
    except Exception:
        logger.warning("q-answer oracle count failed for item %s", item_id, exc_info=True)
        return 0


def _make_oracle(item_id: int, baseline: int):
    """Completion oracle: did a NEW AI answer land on this item since dispatch?

    Counts only author_kind='ai' rows past the dispatch-time baseline, so a human who
    answers the item while the worker is running does not mark the run complete, and an
    item that already carried an AI answer (re-request) still needs a fresh one.
    """
    def _satisfied() -> bool:
        return _ai_answer_count(item_id) > baseline

    return _satisfied


def _source_tool_block(api_base_url: str, raw_token: str, doc: dict) -> list[str]:
    """The remote source tool pointer for the answer worker (0349 D0004 D-3).

    The answer token is minted with action_scope='edit' bound to this document, so the
    server already grants it source tools — full CRUD when the document's step is a work
    step, read/search otherwise. This mention was the largest advertise/allow gap of the
    eight builders: it named no tool at all, so a worker asked "does the code actually do
    X?" had to answer from memory. The kind is NOT pinned here — pinning it to read/search
    would just invert the same mismatch on work steps — it is asked of the registry, the
    same judge the permission check uses.

    Never raises: an answer mention without the tool block is degraded, one that fails to
    build is a dead hand-off.
    """
    from modules.flow_gate.services import mention_service, tool_registry

    project = doc.get("project_id") or ""
    doc_id = doc.get("doc_id") or ""
    try:
        if not mention_service._include_remote_source_crud(project):
            return []
        kind, _reason = tool_registry.kind_for_token(
            {"action_scope": "edit", "doc_ref": doc_id}
        )
        # Same five lines every other mention gets, under this file's bracket headers.
        # The Authorization line repeats the one in the POST block below on purpose: this
        # block is read first, and a tool pointer without credentials is not actionable.
        lines = mention_service._remote_source_crud_lines(
            api_base_url.rstrip("/"), raw_token, None, kind=kind
        )
        return ["", "[소스 도구]", *lines] if lines else []
    except Exception:
        logger.warning("answer mention source tool block failed for %s", doc_id, exc_info=True)
        return []


def _document_lookup_block(api_base_url: str, raw_token: str, doc: dict) -> list[str]:
    """Bounded document-query pointers for a Q-answer worker (0370 T0012)."""
    from modules.flow_gate.services import mention_service

    return [
        "",
        "[문서 조회 도구]",
        *mention_service._document_lookup_lines(
            api_base_url.rstrip("/"),
            raw_token,
            project=doc.get("project_id") or "",
            doc_id=doc.get("doc_id") or "",
        ),
    ]


def build_answer_mention(
    *,
    doc: dict,
    item: dict,
    raw_token: str,
    scratch_dir: str,
    api_base_url: str,
) -> str:
    """The worker prompt for answering exactly one query item.

    Carries the parent-document context, the item verbatim (title/body/options with their
    server-assigned ids), and the literal POST contract, because the worker has no session
    and cannot browse the UI to work out what it is being asked.
    """
    doc_id = doc.get("doc_id") or ""
    group_id = doc.get("group_id") or ""
    group = db_groups.get_by_id(group_id) if group_id else None
    item_id = item.get("id")
    answers_url = f"{api_base_url}/q/{doc_id}/items/{item_id}/answers"

    lines: list[str] = []
    lines.append("[작업]")
    lines.append("아래 질의 1건에 답변하십시오. 문서를 새로 만들지 말고, 이 질의에만 답하십시오.")
    lines.append("")
    lines.append("[문맥]")
    lines.append(f"- 프로젝트: {doc.get('project_id', '')}")
    if group is not None:
        lines.append(f"- 그룹: {group_id} — {group.get('title', '')}")
    else:
        lines.append(f"- 그룹: {group_id}")
    lines.append(f"- 대상 문서: {doc_id} ({doc.get('title', '')})")
    if doc.get("file_path"):
        lines.append(f"- 문서 파일: {doc.get('file_path')}")
    lines.extend(_source_tool_block(api_base_url, raw_token, doc))
    lines.extend(_document_lookup_block(api_base_url, raw_token, doc))

    lines.append("")
    lines.append("[질의]")
    lines.append(f"- 번호: Q{item.get('seq')}")
    if item.get("title"):
        lines.append(f"- 제목: {item.get('title')}")
    lines.append("- 내용:")
    for body_line in str(item.get("body") or "").splitlines() or [""]:
        lines.append(f"    {body_line}")

    options = item.get("options") or []
    if options:
        lines.append("- 보기(선택지): 아래 id 중 하나를 selected_option_ids 에 넣으십시오.")
        for opt in options:
            lines.append(f"    [{opt.get('id')}] {opt.get('label')}")
    lines.append("")
    lines.append("[답변 등록 방법]")
    lines.append(f"POST {answers_url}")
    lines.append(f"Authorization: Bearer {raw_token}")
    lines.append("Content-Type: application/json")
    lines.append("")
    if options:
        lines.append('{"body": "<답변 본문>", "selected_option_ids": ["<보기 id>"]}')
        lines.append("")
        lines.append(
            "보기를 고르면 body 는 비워도 됩니다(서버가 보기 label 로 채웁니다). "
            "보기가 마땅치 않으면 selected_option_ids 를 비우고 body 만 쓰십시오."
        )
    else:
        lines.append('{"body": "<답변 본문>"}')
    lines.append("")
    lines.append(
        "author_kind 는 서버가 'ai' 로 고정하므로 보내지 않아도 됩니다. "
        "이 토큰은 이 문서에만 쓸 수 있습니다."
    )
    lines.append(f"작업 디렉터리(scratch): {scratch_dir}")
    lines.append("")
    lines.append("[완료 기준]")
    lines.append("위 POST 가 200 으로 성공하면 작업 완료입니다. 그 외 문서 등록은 하지 마십시오.")
    return "\n".join(lines)


def issue_answer_token(
    *,
    doc: dict,
    item: dict,
    issued_to: str,
    api_base_url: str,
) -> dict:
    """Mint the item-bound edit token and render the worker mention for it.

    Both entrances of the answer hand-off share this: [AI에게 답변 요청] feeds the result
    into the run as its issue_builder, and [멘트 복사] hands the same text to the user's
    own worker. One builder is what keeps the two prompts byte-identical — the property
    ai_invoke_service.start_run's contract asks for, and the reason a copied mention and an
    in-app run cannot drift apart.
    """
    issued = token_service.issue(
        project=doc.get("project_id") or "",
        group_id=doc.get("group_id") or "",
        # The receiving route (_resolve_writer) admits an edit token whose doc_ref
        # matches the path doc_id — reuse that grant rather than minting a new
        # action_scope, which would need a CHECK migration in all three dialects.
        action_scope="edit",
        doc_ref=doc.get("doc_id") or "",
        issued_to=issued_to,
    )
    return {
        "raw_token": issued["raw_token"],
        "token_id": issued["token_id"],
        "scratch_dir": issued["scratch_dir"],
        "expires_at": issued.get("expires_at"),
        "mention": build_answer_mention(
            doc=doc,
            item=item,
            raw_token=issued["raw_token"],
            scratch_dir=issued["scratch_dir"],
            api_base_url=api_base_url,
        ),
    }


def dispatch_answer_run(
    *,
    doc: dict,
    item: dict,
    issued_to: str,
    api_base_url: str,
    provider_id: Optional[str] = None,
) -> dict:
    """Issue the item-bound edit token and launch the answer run.

    Returns the ai_invoke_service.start_run payload (run_id / status / provider …).
    Raises HTTPException for admission failures (no provider, run already in progress),
    which the caller surfaces with the ai-invoke error envelope.
    """
    doc_id = doc.get("doc_id") or ""
    group_id = doc.get("group_id") or ""
    project_id = doc.get("project_id") or ""
    item_id = int(item["id"])

    # Baseline BEFORE the worker starts, so the oracle only credits this run's answer.
    baseline = _ai_answer_count(item_id)

    def _issue() -> dict:
        return issue_answer_token(
            doc=doc, item=item, issued_to=issued_to, api_base_url=api_base_url,
        )

    return ai_invoke_service.start_run(
        project_id=project_id,
        module=None,
        group_id=group_id,
        doc_ref=doc_id,
        action_scope="edit",
        mode="single",
        continuation_target_seq=None,
        continuation_review_mode=False,
        continuation_instruction_mode=None,
        continuation_locale=None,
        issued_to=issued_to,
        api_base_url=api_base_url,
        # issue_builder supplies the mention; this is only the engine's fallback path.
        mention_builder=lambda _raw, _scratch: None,
        provider_id=provider_id,
        issue_builder=_issue,
        completion_oracle=_make_oracle(item_id, baseline),
    )


def resolve_item(doc_id: str, item_id: int) -> dict:
    """The query item as the UI sees it (options parsed), or 404 if not on this document."""
    container = q_service.get_qa_detail(doc_id)
    for it in container.get("items", []):
        if it.get("id") == item_id:
            return it
    raise HTTPException(
        status_code=404,
        detail=f"question_item {item_id} does not belong to document {doc_id}",
    )
