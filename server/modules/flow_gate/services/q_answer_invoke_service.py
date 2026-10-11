"""Query-answer hand-off — give ONE query item to an AI worker (0248 B0001).

D0005 §3.2 / L0007 §3.4 specify that a document-bound query is handed to an AI worker and
the answer lands back on the SAME item as author_kind='ai'. The route only ever minted an
edit token and returned it to the browser: nothing launched a worker and no UI surfaced the
token, so the click was a silent no-op (NR0003). This module is the missing half.

It serves the two hand-off routes the legacy Q-document flow has always offered (see
AnswerEditor.vue / qa_routes.py `dispatch_mode`), which the document-bound Q&A panel was
missing entirely — leaving the asker to answer their own question:

  • [copy mention] → issue_answer_token: the user pastes the mention into their own worker.
                   Works with no provider configured, which is why it is not a nicety.
  • [ask the AI to answer] → dispatch_answer_run: an in-app run through the shared
                   ai_invoke_service engine (group lock, provider chain + fallback, scratch
                   lifecycle, timeout, cancel, status/SSE all come free).

Both mint the same token and render the same mention, so the two paths cannot drift.

Two things separate this from every other invoke path:

1. The product is an answer ROW on an existing document, not a new document — the engine's
   document-reach oracle would score a perfect run as outcome='none'. We pass a
   completion_oracle so the run is judged by "did an AI answer appear on THIS item".
2. The mention must pin the worker to one item. The generic user-Q&A block
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
from modules.flow_gate.db import question_items as db_question_items
from modules.flow_gate.services import ai_invoke_service, q_service, token_service

logger = logging.getLogger(__name__)

# flowgate.default.0661 T0004: admission code for "this item already has a live responder".
RESPONDER_IN_PROGRESS_CODE = "question_responder_in_progress"
# Review rej_01M4HRWSVP3M0BSH finding 3: the run could not claim the item durably (it was
# answered in the meantime, another run owns it, or the claim write failed) — no token.
RESPONDER_CLAIM_REFUSED_CODE = "question_responder_claim_refused"


def _claim_item_for_run(
    item_id: int,
    run_id: str,
    provider_id: Optional[str],
    provider_source: Optional[str],
) -> bool:
    """Claim the item for ``run_id``; True only when this run durably owns an open item.

    The CAS (`claim_responder_dispatch`) changes nothing for an answered item and for a
    second claim by the same run, so a refused write is told apart by re-reading the row:
    a token re-issue inside the run that already holds the claim is fine, anything else
    (a person answered just before the start, another run owns it) is not. A failed write
    or read is a refusal too — the claim is what keeps one responder per item.
    """
    try:
        if db_question_items.claim_responder_dispatch(
            item_id, run_id, provider_id, provider_source,
        ):
            return True
        row = db_question_items.get_by_pk(item_id)
    except Exception:
        logger.warning("q-answer responder claim failed for item %s run %s",
                       item_id, run_id, exc_info=True)
        return False
    return bool(
        row
        and int(row.get("answer_count") or 0) == 0
        and row.get("responder_run_id") == run_id
        and row.get("responder_state") == db_question_items.RESPONDER_DISPATCHED
    )


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


def _item_row(item_id: int) -> Optional[dict]:
    """The durable item row, or None when it cannot be read (the guards below degrade)."""
    try:
        return db_question_items.get_by_pk(item_id)
    except Exception:
        logger.warning("q-answer item row read failed for item %s", item_id, exc_info=True)
        return None


def _make_oracle(item_id: int, baseline: int, run_holder: Optional[dict] = None):
    """Completion oracle: did a NEW AI answer land on this item since dispatch?

    Counts only author_kind='ai' rows past the dispatch-time baseline, so a human who
    answers the item while the worker is running does not mark the run complete, and an
    item that already carried an AI answer (re-request) still needs a fresh one.

    0661 T0004 F5: the responder's explicit hand-off to a user is the OTHER way this run
    can finish its job. `run_holder["run_id"]` is filled by the issue builder once the
    engine names the run; an item escalated by THIS run (responder_state='user_decision'
    with that run id) satisfies the oracle, so the run ends as complete rather than being
    mistaken for a worker that silently produced nothing.
    """
    def _satisfied() -> bool:
        if _ai_answer_count(item_id) > baseline:
            return True
        run_id = (run_holder or {}).get("run_id")
        if not run_id:
            return False
        row = _item_row(item_id)
        return bool(
            row
            and row.get("responder_state") == db_question_items.RESPONDER_USER_DECISION
            and row.get("responder_run_id") == run_id
        )

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
    # 0661 T0004 F5: the hand-off to a human is a SEPARATE signal, never an answer body.
    # The normal path is to answer; this is the exception path, and the conditions are
    # spelled out so uncertainty, a failed tool call or a thin context never become a
    # hand-off (T0004 "자동답변 우선 계약").
    escalate_url = f"{api_base_url}/q/{doc_id}/items/{item_id}/escalate"
    lines.append("")
    lines.append("[사용자 판단 이관 — 예외 경로]")
    lines.append(
        "정상 경로는 위 답변 등록입니다. 참조 문서·소스·기존 정책을 확인하고도 "
        "**사람의 업무적 의사결정**(요구사항 선택, 승인/우선순위 판단 등)이 반드시 필요한 질의에 한해, "
        "답변을 등록하지 말고 아래 이관 신호를 보내십시오."
    )
    lines.append(
        "단순한 확신 부족, 조사 시간 부족, 도구 호출 실패, 빈 출력은 이관 사유가 아닙니다 — "
        "그런 경우에는 근거를 밝힌 최선의 답변을 등록하십시오."
    )
    lines.append(f"POST {escalate_url}")
    lines.append(f"Authorization: Bearer {raw_token}")
    lines.append("Content-Type: application/json")
    lines.append("")
    lines.append('{"reason": "<사람이 결정해야 하는 이유와 선택지 요약>"}')
    lines.append("")
    lines.append(
        "이관 신호를 보낸 뒤에는 답변을 등록하지 마십시오. "
        "이 토큰으로 새 질의(Q)를 등록할 수는 없습니다(질의에 대한 질의 금지)."
    )
    lines.append("")
    lines.append("[완료 기준]")
    lines.append(
        "답변 등록 POST 가 200 으로 성공하면 작업 완료입니다. "
        "예외 경로인 이관 POST 가 200 이면 그것으로 작업을 끝냅니다. 그 외 문서 등록은 하지 마십시오."
    )
    return "\n".join(lines)


def issue_answer_token(
    *,
    doc: dict,
    item: dict,
    issued_to: str,
    api_base_url: str,
    ai_run_id: Optional[str] = None,
) -> dict:
    """Mint the item-bound edit token and render the worker mention for it.

    Both entrances of the answer hand-off share this: [ask the AI to answer] feeds the result
    into the run as its issue_builder, and [copy mention] hands the same text to the user's
    own worker. One builder is what keeps the two prompts byte-identical — the property
    ai_invoke_service.start_run's contract asks for, and the reason a copied mention and an
    in-app run cannot drift apart.

    `ai_run_id` is the run this token works for, and it is not bookkeeping: group 0378 made
    every group mutation pass mutation_policy.assert_group_mutation_allowed, which admits a
    worker only when its token matches the active lease on ALL of group / token_id / run_id /
    action_scope. start_run leases under the run id, so a token minted without one can never
    match — and the answer POST is this run's only product (0389 R0001). None on the
    [copy mention] path, which starts no run and so faces no lease of its own.
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
        ai_run_id=ai_run_id,
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


def _live_responder_run_id(item_id: int) -> Optional[str]:
    """The run id of a responder still working on this item, else None (0661 T0004 F2).

    A durable 'dispatched' row whose run this process no longer tracks (a crash, a
    restart) does NOT block: that row is stale and the startup sweep / next dispatch
    supersedes it. Lookup failures block nothing — the group lease remains the hard guard.
    """
    row = _item_row(item_id)
    if not row or row.get("responder_state") != db_question_items.RESPONDER_DISPATCHED:
        return None
    run_id = row.get("responder_run_id")
    if not run_id:
        return None
    try:
        from modules.flow_gate.services.ai_invoke import runtime as ai_runtime

        return run_id if ai_runtime.is_run_live(run_id) else None
    except Exception:
        logger.warning("q-answer responder liveness probe failed for %s", item_id, exc_info=True)
        return None


def _record_dispatch_failure(item_id: int, exc: HTTPException, actor_user_id: Optional[str]) -> None:
    """Admission refused the responder — leave WHY on the item (0661 T0004 F3), best-effort."""
    detail = exc.detail if isinstance(exc.detail, dict) else {}
    code = str(detail.get("code") or "dispatch_error")
    message = detail.get("message") or (
        exc.detail if isinstance(exc.detail, str) else f"HTTP {exc.status_code}"
    )
    try:
        q_service.mark_responder_failed(
            item_id, code, str(message), actor_user_id=actor_user_id,
            notify_audience=actor_user_id,
        )
    except Exception:
        logger.warning("q-answer dispatch failure could not be recorded for item %s",
                       item_id, exc_info=True)


def dispatch_answer_run(
    *,
    doc: dict,
    item: dict,
    issued_to: str,
    api_base_url: str,
    provider_id: Optional[str] = None,
    provider_source: Optional[str] = None,
    actor_user_id: Optional[str] = None,
) -> dict:
    """Issue the item-bound edit token and launch the answer run.

    Returns the ai_invoke_service.start_run payload (run_id / status / provider …).
    Raises HTTPException for admission failures (no provider, run already in progress),
    which the caller surfaces with the ai-invoke error envelope.

    0661 T0004 (F2/F3/F6): one responder per item at a time — an item whose 'dispatched'
    run is still live is refused with `question_responder_in_progress` before admission
    (the group lease would refuse it anyway, but with a code that says nothing about the
    item). The run that IS admitted claims the item durably from inside the issue builder
    (`question_items.responder_run_id`, requested provider + why it was picked), which is
    what the finalization settle, the escalate route and the self-answer guard key on. An
    admission refusal is written to the item as a 'failed' state with the admission code
    instead of vanishing into a log line.
    """
    doc_id = doc.get("doc_id") or ""
    group_id = doc.get("group_id") or ""
    project_id = doc.get("project_id") or ""
    item_id = int(item["id"])

    live_run_id = _live_responder_run_id(item_id)
    if live_run_id:
        raise HTTPException(status_code=409, detail={
            "code": RESPONDER_IN_PROGRESS_CODE,
            "message": "An AI responder is already answering this question.",
            "run_id": live_run_id,
        })

    # Baseline BEFORE the worker starts, so the oracle only credits this run's answer.
    baseline = _ai_answer_count(item_id)
    run_holder: dict = {"run_id": None}

    # Declaring ai_run_id is what makes ai_invoke_service._call_issue_builder hand the run
    # identity over (it inspects the signature); a bare def would silently keep minting the
    # lease-orphan token that made every answer POST 403 GROUP_AI_RUN_OWNER_MISMATCH.
    def _issue(ai_run_id: Optional[str] = None) -> dict:
        if ai_run_id:
            # Fail closed (review rej_01M4HRWSVP3M0BSH finding 3): without the durable claim
            # a person may have answered between the dispatch and this point, and an AI
            # token minted anyway would POST a second answer onto an answered item. Raising
            # here mints nothing; admission gives the group lease back and the caller sees
            # the refusal.
            if not _claim_item_for_run(item_id, ai_run_id, provider_id, provider_source):
                raise HTTPException(status_code=409, detail={
                    "code": RESPONDER_CLAIM_REFUSED_CODE,
                    "message": (
                        "This question could not be claimed for an AI responder (it was "
                        "answered or claimed in the meantime)."
                    ),
                    "item_id": item_id,
                })
            run_holder["run_id"] = ai_run_id
        return issue_answer_token(
            doc=doc, item=item, issued_to=issued_to, api_base_url=api_base_url,
            ai_run_id=ai_run_id,
        )

    try:
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
            completion_oracle=_make_oracle(item_id, baseline, run_holder),
        )
    except HTTPException as exc:
        if run_holder["run_id"] is None:
            # An answered item is never flagged (mark_responder_failed skips it), so a claim
            # lost to a person's answer leaves no failure behind; a claim write error does.
            _record_dispatch_failure(item_id, exc, actor_user_id or issued_to)
        raise


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
