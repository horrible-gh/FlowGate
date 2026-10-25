"""0064 T-B1e — the AI start body precheck and the unread-body rules (L0005 2.14 / 2.15, P0006 §2.9 / §2.10).

On the real SQLite DB, routes and fixtures of ``test_fg_card_settings`` (card documents, card settings, a Graph
run whose AI start is recorded as a job): a Flow node AI start and a Q&A answer check every document they hand over,
put the "[읽을 수 없는 문서]" section between the card memos and the mention when one cannot be read, record
``ai_start_unreadable_docs`` once per run and tell the Canvas (``documents``); all readable, the mention is byte for
byte what it was before the check existed. The same rule wording reaches the Q&A / chat lookup lines, the help item
``document_access`` and the section from one constant (``mention_service.BODY_UNREADABLE_RULES``).

Verification ids: L0005 V-B1e-1 ~ 8, P0006 V-P-9.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

# The AI start reads the server settings when it builds its base URL (config).
os.environ.setdefault("ALLOWED_ORIGIN", "http://localhost")
os.environ.setdefault("CONTEXT", "/flowgate")
os.environ.setdefault("DB_TYPE", "sqlite")

import config  # noqa: E402,F401 — its import-time env export (agent pepper id) happens at collection, not in a test

from test_fg_card_settings import (  # noqa: E402,F401 — fixtures used by name
    AI, FA, FB, _doc, _h, _sql, _start, db_path, decided, edge, env, node, put_draft,
)
from test_fg_card_settings_review import INSTRUCTION_RUN, _admin_and_reviewer, _write_written  # noqa: E402

_SERVER_DIR = Path(__file__).resolve().parents[1]
ROOT = f"{FA}.0001-R"
SEP = "\n\n"
NODE_MENTION, ANSWER_MENTION = "NODE MENTION", "ANSWER MENTION"
QNA_RUN = "air_qna_answer"
BASE, TOKEN = "http://host/flowgate/api/v1", "worker-token"
# P0006 §2.9 — the rule sentences, pinned here as the protocol fixes them.
RULES = (
    "본문 미열람 규칙 1: content가 null이거나 content_state가 readable이 아닌 문서만 '읽지 못한 문서'입니다. "
    "front matter뿐인 빈 문서(content_state=readable)는 읽은 문서이며, 이 판정은 내부에서만 하고 답변에 나열하지 마십시오.",
    "본문 미열람 규칙 2: 읽지 못한 문서의 내용을 읽은 것처럼 인용하거나 요약하지 마십시오.",
    "본문 미열람 규칙 3: 읽지 못한 문서가 있고 현재 요청 처리에 필요한 경우에만 문서 번호·사유(content_state 값)·"
    "답변에 미치는 영향·대체 근거(있으면)를 짧게 밝히십시오. 읽지 못한 문서가 없거나 현재 답변과 무관하면 '없음'·'해당 없음' "
    "같은 열람 상태 보고를 쓰지 말고, 사용자가 근거나 열람 상황을 물으면 그 범위에서 설명하십시오.",
    "본문 미열람 규칙 4: 질문이 그 문서의 내용을 전제로 하면, 다른 자료에서 끌어낸 내용에 "
    "'원문 미확인 상태의 추정'이라고 표시하십시오.",
    "본문 미열람 규칙 5: 이관 조건은 그대로입니다. 사유가 권한 오류(403)이면 기존 '권한' 조건을 적용하십시오.",
)
# P0006 §1.3 — the server mention wording of each reason.
REASONS = {
    "file_path_missing": "본문 파일 경로가 기록되지 않음",
    "file_not_found": "본문 파일이 없음",
    "path_unresolved": "본문 파일 위치를 해석할 수 없음",
    "read_error": "본문을 읽는 중 오류",
}
REFERENCE_SENTENCE = "본문을 읽지 못한 문서가 있으면 [문서 조회 도구]의 본문 미열람 규칙 3·4항을 지키십시오."


@pytest.fixture(autouse=True)
def card_memos(env, monkeypatch):
    """The card memo port and its routes, as ``startup`` / ``main`` wire them."""
    from flowgate.card_memos import integration
    from flowgate.card_memos import legacy_adapter as memos
    from flowgate.card_memos import routes

    monkeypatch.setattr(memos, "has_permission", lambda u, p, perm: True)
    integration.register()
    env["client"].app.include_router(routes.router)
    yield
    integration.unregister()


# ── helpers ──────────────────────────────────────────────────────────────────

def _row(doc_id):
    from modules.flow_gate.db import documents as db_documents

    return db_documents.get_by_id(doc_id)


def _memo(env, flow, key):
    res = env["client"].post(f"/api/v1/flows/{flow}/nodes/{key}/memos",
                             json={"title": "Use the v2 error table", "body": "Every code from v2.", "ai_ref": True},
                             headers=_h())
    assert res.status_code == 201, res.text


def _memo_section(flow, key=None, doc_id=None):
    from modules.flow_gate.services import card_memo_hooks

    section = card_memo_hooks.memo_section(flow, node_key=key, doc_id=doc_id)
    assert section.startswith("## User memos")
    return section


def _ai_card_job(env, *, memo=True):
    """A confirmed R -> AI T card (FA) started: its instruction stage job (an edit start into the card's instruction
    line document, the Flow root attached as a reference). -> (node key, job)"""
    _admin_and_reviewer(env)
    first = put_draft(env, FA, 0, [node("tmp_r", "R"), node("tmp_c", "T", assignee=AI)],
                      [edge("tmp_e", "tmp_r", "tmp_c")])
    key = first["key_map"]["tmp_c"]
    assert env["client"].post(f"/api/v1/flows/{FA}/graph/revisions", json={"draft_version": 1},
                              headers=_h()).status_code == 201
    if memo:
        _memo(env, FA, key)
    _start(env)
    [job] = env["jobs"]
    env["jobs"].clear()
    assert job["action_scope"] == "edit" and job["doc_ref"] == ROOT and job["target_doc_id"]
    return key, job


def _issue_node(monkeypatch, job, times=1, run_id=INSTRUCTION_RUN):
    """Start the node AI through ``ai_node_starter`` with the engine's admission replaced: its issue builder is
    called ``times`` times for the run (a retry / reissue of the same run). -> the mentions."""
    from modules.flow_gate.graph_workflow.services import ai_node_starter
    from modules.flow_gate.services import ai_invoke_service, mention_service

    issued: list[str] = []
    monkeypatch.setattr(mention_service, "build_mention_from_token_rec", lambda **kw: NODE_MENTION)

    def start_run(**kw):
        for _ in range(times):
            issued.append(kw["issue_builder"](ai_run_id=run_id)["mention"])
        return {"run_id": run_id, "status": "running"}

    monkeypatch.setattr(ai_invoke_service, "start_run", start_run)
    assert ai_node_starter.start_ai_invoke(job) == {"run_id": run_id, "status": "running"}   # the AI starts
    return issued


def _before(key):
    """What ``_issue`` built before the precheck existed: the card memos in front of the mention."""
    from modules.flow_gate.services import card_memo_hooks

    return card_memo_hooks.prepend_memo_section(NODE_MENTION, FA, node_key=key)


def _path(doc_id):
    from modules.flow_gate.storage.paths import resolve_storage_path

    doc = _row(doc_id)
    return resolve_storage_path(doc["file_path"], doc["project_id"], branch=doc.get("branch") or "main")


def _break(doc_id):
    """The document's body file is gone (``file_not_found``)."""
    _path(doc_id).unlink()


def _readable_docs(flow, first_seq, count):
    out = []
    for seq in range(first_seq, first_seq + count):
        doc_id = _doc(flow, seq, "M")
        _write_written(doc_id, f"## body {seq}\n")
        out.append(doc_id)
    return out


def _missing_file_doc(flow, seq):
    from modules.flow_gate.db import documents as db_documents

    doc_id = _doc(flow, seq, "M")
    db_documents.update(doc_id, {"file_path": f"docs/{doc_id}.missing.md"})
    return doc_id


def _section(*entries):
    """The section of P0006 §2.9 ①: ``entries`` = (doc_id, reason) in the check order."""
    lines = [f"- {doc_id} ({_row(doc_id)['title']}): {REASONS[reason]}" for doc_id, reason in entries]
    return "\n".join(["[읽을 수 없는 문서]", *lines, *RULES])


def _code(doc_id):
    return doc_id.rsplit(".", 1)[-1]


def _recorded(env):
    return [(r["doc_id"], json.loads(r["note"])) for r in _sql(
        env["db"], "SELECT doc_id, note FROM events WHERE event_type = 'ai_start_unreadable_docs' ORDER BY event_id")]


def _documents_notices(env, flow):
    return [e.payload for e in env["events"]
            if e.event_type == "graph_canvas_changed" and e.payload["reason"] == "documents"
            and e.payload["flow_id"] == flow]


def _canvas_line(env, flow, key, slot):
    res = env["client"].get(f"/api/v1/flows/{flow}/canvas", headers=_h())
    assert res.status_code == 200, res.text
    shown = next(n for n in res.json()["nodes"] if n["node_key"] == key)
    return next(line for line in shown["card_documents"] if line["slot"] == slot)


def _targets(job):
    from modules.flow_gate.services import ai_start_precheck

    return ai_start_precheck.node_targets(job)


# ── V-B1e-1 / V-P-9: the section, its place, one event per run, the card warning ─────

def test_an_unreadable_line_puts_its_section_between_the_memos_and_the_mention_once_per_run(env, decided,
                                                                                           monkeypatch):
    key, job = _ai_card_job(env)
    instruction = job["target_doc_id"]
    _break(instruction)                                    # the card's instruction line: file not found
    before = len(env["events"])
    issued = _issue_node(monkeypatch, job, times=2)        # the run's token issued twice (a retry)

    section = _section((ROOT, "file_path_missing"), (instruction, "file_not_found"))   # the root has no file
    assert section.count("\n") == 2 + len(RULES)           # V-P-9: the header, two documents, five rules
    expected = _memo_section(FA, key) + SEP + section + SEP + NODE_MENTION
    assert issued == [expected, expected]                  # [memos] [unreadable documents] [mention], every issue
    [(doc_id, note)] = _recorded(env)                      # one event for the run
    assert doc_id == ROOT
    assert note == {"ai_run_id": INSTRUCTION_RUN, "kind": "node", "node_key": key, "checked": len(_targets(job)),
                    "unreadable": [f"{ROOT}:{_code(ROOT)}", f"{instruction}:{_code(instruction)}"]}
    notices = [e.payload for e in env["events"][before:]
               if e.event_type == "graph_canvas_changed" and e.payload["reason"] == "documents"]
    assert notices and all(p["flow_id"] == FA and p["draft_version"] is None and p["revision_no"] is None
                           and p["node_keys"] == [] and p["origin_client_id"] is None for p in notices)
    line = _canvas_line(env, FA, key, "instruction")       # the card warns through the line read
    assert (line["doc_id"], line["body_state"], line["has_body"]) == (instruction, "file_not_found", False)


def test_a_second_run_of_the_card_records_its_own_event(env, decided, monkeypatch):
    key, job = _ai_card_job(env, memo=False)
    _issue_node(monkeypatch, job, run_id="air_first")
    _issue_node(monkeypatch, job, run_id="air_second")
    assert [note["ai_run_id"] for _doc_id, note in _recorded(env)] == ["air_first", "air_second"]


# ── V-B1e-2: all readable -> byte for byte as before, no event, no notice ─────

@pytest.mark.parametrize("count", [3, 250])
def test_an_all_readable_start_mention_is_byte_identical_to_before(env, decided, monkeypatch, count):
    from modules.flow_gate.services import ai_start_precheck

    key, job = _ai_card_job(env)
    _write_written(ROOT, "## 요구사항\n")
    if count > 3:                                          # beyond PRECHECK_BATCH: references fill it up to 250
        extra = _readable_docs(FA, 100, count - len(_targets(job)))
        job = {**job, "ref_doc_ids": [*(job.get("ref_doc_ids") or []), *extra]}
    assert len(_targets(job)) == count
    batches: list[int] = []
    real_rows = ai_start_precheck._rows
    monkeypatch.setattr(ai_start_precheck, "_rows", lambda ids: batches.append(len(ids)) or real_rows(ids))
    before = len(env["events"])

    assert _issue_node(monkeypatch, job) == [_before(key)]
    assert batches == ([count] if count <= ai_start_precheck.PRECHECK_BATCH else [200, count - 200])
    assert _recorded(env) == []
    assert not [e for e in env["events"][before:] if e.event_type == "graph_canvas_changed"]


# ── V-B1e-6: nothing is dropped for a limit ──────────────────────────────────

def test_the_last_of_250_targets_unreadable_is_still_listed(env, decided, monkeypatch):
    from modules.flow_gate.services import ai_start_precheck

    key, job = _ai_card_job(env)
    _write_written(ROOT, "## 요구사항\n")
    lines = ai_start_precheck.card_document_ports.card_line_doc_ids(FA, node_key=key)
    head = [job["doc_ref"], job["target_doc_id"], *(job.get("ref_doc_ids") or []), *lines]
    readable = _readable_docs(FA, 100, 250 - len(dict.fromkeys(head)) - 1)
    broken = _missing_file_doc(FA, 400)
    job = {**job, "ref_doc_ids": [*(job.get("ref_doc_ids") or []), *lines, *readable, broken]}
    targets = _targets(job)
    assert len(targets) == 250 and targets[-1] == broken

    [mention] = _issue_node(monkeypatch, job)
    assert mention == _memo_section(FA, key) + SEP + _section((broken, "file_not_found")) + SEP + NODE_MENTION
    assert "생략" not in mention and "omitted" not in mention
    [(_doc_id, note)] = _recorded(env)
    assert note["checked"] == 250 and note["unreadable"] == [f"{broken}:{_code(broken)}"]


# ── V-B1e-3: the check never stops the start ─────────────────────────────────

def _boom(*_a, **_k):
    raise RuntimeError("precheck down")


@pytest.mark.parametrize("where", ["precheck", "targets", "rows"])
def test_a_failing_check_starts_the_ai_without_the_section(env, decided, monkeypatch, where):
    from modules.flow_gate.services import ai_start_precheck

    key, job = _ai_card_job(env)                           # the root is unreadable: a working check would list it
    target = {"precheck": (ai_start_precheck, "precheck"),
              "targets": (ai_start_precheck.card_document_ports, "card_line_doc_ids"),
              "rows": (ai_start_precheck, "_rows")}[where]
    monkeypatch.setattr(*target, _boom)
    before = len(env["events"])

    assert _issue_node(monkeypatch, job) == [_before(key)]
    assert _recorded(env) == []
    assert not [e for e in env["events"][before:] if e.event_type == "graph_canvas_changed"]


def test_a_lost_event_record_keeps_the_section_and_the_start(env, decided, monkeypatch):
    from modules.flow_gate.services import ai_start_precheck

    key, job = _ai_card_job(env, memo=False)
    monkeypatch.setattr(ai_start_precheck, "insert_event", _boom)
    assert _issue_node(monkeypatch, job) == [_section((ROOT, "file_path_missing")) + SEP + NODE_MENTION]
    assert _recorded(env) == []


# ── V-B1e-8: one read error is that document's, the rest are checked ─────────

def test_a_read_error_is_listed_for_that_document_and_the_check_goes_on(env, decided, monkeypatch):
    from modules.flow_gate.services import ai_start_precheck

    key, job = _ai_card_job(env, memo=False)
    _write_written(ROOT, "## 요구사항\n")
    instruction = job["target_doc_id"]
    _path(instruction).write_bytes(b"---\ntitle: x\n---\n\xff\xfe broken")         # not UTF-8
    report = _canvas_line(env, FA, job["node_key"], "report")["doc_id"]
    later = _missing_file_doc(FA, 300)
    job = {**job, "ref_doc_ids": [*(job.get("ref_doc_ids") or []), report, later]}
    real_read = ai_start_precheck.body_read_state

    def read(doc):
        if doc["doc_id"] == report:
            raise PermissionError("denied")                # a read failure that is no decoding error
        return real_read(doc)

    monkeypatch.setattr(ai_start_precheck, "body_read_state", read)
    [mention] = _issue_node(monkeypatch, job)              # the AI starts
    assert mention == _section((instruction, "read_error"), (report, "read_error"),
                               (later, "file_not_found")) + SEP + NODE_MENTION
    [(_doc_id, note)] = _recorded(env)
    assert note["checked"] == len(_targets(job)) and len(note["unreadable"]) == 3
    assert _canvas_line(env, FA, key, "instruction")["body_state"] == "read_error"


# ── V-B1e-7: the Q&A answer AI, its run and its copied mention ───────────────

def _qna_card(env):
    """A T card on FB (no root): its report line holds the question's document, its instruction line is unreadable.
    -> (node key, report doc, instruction doc id)"""
    body = put_draft(env, FB, 0, [node("tmp_t", "T")])
    key = body["key_map"]["tmp_t"]
    lines = {line["slot"]: line for line in body["card_documents"][key]}
    _memo(env, FB, key)
    instruction, report = lines["instruction"]["doc_id"], lines["report"]["doc_id"]
    _break(instruction)
    return key, _row(report), instruction


def test_a_qa_answer_lists_the_unreadable_line_of_the_card_showing_its_document(env, monkeypatch):
    from modules.flow_gate.services import q_answer_invoke_service as qa

    monkeypatch.setattr(qa, "build_answer_mention", lambda **kw: ANSWER_MENTION)
    key, report, instruction = _qna_card(env)
    before = len(env["events"])
    expected = (_memo_section(FB, doc_id=report["doc_id"]) + SEP + _section((instruction, "file_not_found"))
                + SEP + ANSWER_MENTION)

    def issue(ai_run_id=None):
        return qa.issue_answer_token(doc=report, item={"id": 17}, issued_to="u_kim", api_base_url=BASE,
                                     ai_run_id=ai_run_id)["mention"]

    assert [issue(QNA_RUN), issue(QNA_RUN)] == [expected, expected]          # the run (and its reissue)
    [(doc_id, note)] = _recorded(env)
    assert doc_id == report["doc_id"]
    assert note == {"ai_run_id": QNA_RUN, "kind": "qna", "item_id": 17, "checked": 2,
                    "unreadable": [f"{instruction}:{_code(instruction)}"]}
    assert _documents_notices(env, FB) and env["events"][before:]
    assert _canvas_line(env, FB, key, "instruction")["body_state"] == "file_not_found"

    assert [issue(), issue()] == [expected, expected]                        # [copy mention]: the same section
    assert [note["ai_run_id"] for _d, note in _recorded(env)] == [QNA_RUN, None, None]   # no run: every time


def test_a_qa_document_without_a_card_checks_itself_and_its_target_in_the_same_group(env, monkeypatch):
    from modules.flow_gate.db import documents as db_documents
    from modules.flow_gate.services import q_answer_invoke_service as qa

    monkeypatch.setattr(qa, "build_answer_mention", lambda **kw: ANSWER_MENTION)
    free = _doc(FB, 60, "T")
    _write_written(free, "## 작업\n")
    same, other = _doc(FB, 61, "N"), _doc(FA, 62, "N")                      # no file: both unreadable

    def mention():
        return qa.issue_answer_token(doc=_row(free), item={"id": 3}, issued_to="u_kim",
                                     api_base_url=BASE)["mention"]

    assert mention() == ANSWER_MENTION                                       # readable, no target: as before
    db_documents.update(free, {"target_id": other})
    assert mention() == ANSWER_MENTION                                       # a target of another group: not checked
    db_documents.update(free, {"target_id": same})
    assert mention() == _section((same, "file_path_missing")) + SEP + ANSWER_MENTION
    assert _recorded(env)[-1][1]["checked"] == 2


# ── V-B1e-4: the Q&A answer mention keeps its escalation and gains the rules ──

def _answer_mention(monkeypatch, doc_id):
    from modules.flow_gate.services import mention_service
    from modules.flow_gate.services import q_answer_invoke_service as qa

    monkeypatch.setattr(mention_service, "_include_remote_source_crud", lambda _project: False)
    monkeypatch.setattr(qa.db_groups, "get_by_id", lambda _gid: None)
    return qa.build_answer_mention(
        doc={"doc_id": doc_id, "group_id": FB, "project_id": "flowgate", "title": "Lookup"},
        item={"id": 7, "seq": 1, "title": "Question", "body": "What changed?", "options": []},
        raw_token=TOKEN, scratch_dir="C:/scratch", api_base_url=BASE,
    ).split("\n")


def test_the_answer_mention_carries_the_rules_keeps_the_escalation_and_points_at_rules_3_and_4(env, monkeypatch):
    doc_id = f"{FB}.0001-T"
    lines = _answer_mention(monkeypatch, doc_id)
    lookup = [
        "", "[문서 조회 도구]",
        "Read only the document data you need; use the full-document GET only when necessary:",
        f"- Metadata without body: GET {BASE}/document/{doc_id}/meta",
        f"- Outline without body: GET {BASE}/document/{doc_id}/outline",
        f"- One section from that outline: GET {BASE}/document/{doc_id}/section?section_id=<section_id>",
        f"- Relationships without body: GET {BASE}/document/{doc_id}/relations",
        f"- Search bodies with match locations/context: GET {BASE}/search/documents/content"
        "?q=<keyword>&project=flowgate&include_matches=true&context_lines=2&hits_per_doc=5",
        "Section reads accept exactly one of section, section_id, lines, or chars.",
        *RULES,                                            # always, right before the credential line
        f"Authorization: Bearer {TOKEN}",
    ]
    start = lines.index("[문서 조회 도구]") - 1
    assert lines[start:start + len(lookup)] == lookup
    escalate = lines.index("[사용자 판단 이관 — 예외 경로]")
    assert lines[escalate + 1:escalate + 7] == [
        "정상 경로는 위 답변 등록입니다. 참조 문서·소스·기존 정책을 확인해 직접 답하십시오. "
        "아래 세 경우에 한해서만 답변을 등록하지 말고 아래 이관 신호를 보내십시오.",
        "- 인프라: 서버, 배포 환경, 외부 계정·서비스처럼 문서와 소스로 알 수 없는 운영 환경 정보",
        "- 권한: 사람이 승인하거나 부여해야 하는 접근·실행 권한",
        "- 지시 자체가 모호해, 문서와 소스를 확인해도 무엇을 원하는지 정할 수 없는 경우",
        "로직·구현 방식·코드 구조·설계 선택에 관한 질의는 절대 이관하지 마십시오. "
        "문서와 소스를 근거로 직접 정해 답하십시오. "
        "단순한 확신 부족, 조사 시간 부족, 도구 호출 실패, 빈 출력도 이관 사유가 아닙니다 — "
        "그런 경우에는 근거를 밝힌 최선의 답변을 등록하십시오.",
        REFERENCE_SENTENCE,                                # one sentence more, nothing else changed
    ]
    assert lines[escalate + 7].startswith("POST ") and lines[escalate + 7].endswith("/escalate")
    assert lines.count(REFERENCE_SENTENCE) == 1


def test_the_chat_lookup_lines_carry_the_rules_too(monkeypatch):
    from modules.flow_gate.services import invoke_mention_service, mention_service

    monkeypatch.setattr(mention_service, "_include_remote_source_crud", lambda _project: False)
    text = "\n".join(invoke_mention_service._chat_lookup_sections(
        base=BASE, raw_token=TOKEN, project="flowgate", group_name=FB))
    assert "\n".join([*RULES, f"Authorization: Bearer {TOKEN}"]) in text


# ── V-B1e-5: the help item and the one rule constant ─────────────────────────

def _help_document_access(monkeypatch):
    from modules.flow_gate.services import help_catalog

    monkeypatch.setattr(help_catalog.tool_registry, "resolve_registry",
                        lambda *_a, **_k: {"kind": "read_only", "source_mode": "remote", "reason": None})
    ctx = help_catalog.resolve_context({"project": "flowgate", "group_id": FB, "doc_ref": f"{FB}.0001-T",
                                        "action_scope": "new"}, "ko", BASE)
    return help_catalog.build_item("document_access", ctx)["content"]


def test_the_help_item_document_access_carries_the_content_states_and_the_rules(monkeypatch):
    from modules.flow_gate.services import mention_service

    content = _help_document_access(monkeypatch)
    assert tuple(mention_service.BODY_UNREADABLE_RULES) == RULES
    assert content["rules"] == list(RULES)
    assert content["content_state"] == {
        "readable": "본문을 읽었다. front matter뿐이면 빈 문서이며 읽기 실패가 아니다",
        "file_path_missing": "문서의 파일 경로가 기록되지 않았다",
        "file_not_found": "파일 경로는 있으나 파일이 없다",
        "path_unresolved": "파일 경로를 저장소 안에서 해석할 수 없다",
    }
    assert {"body", "path", "partial", "note"} <= set(content)                # the current items stay


def test_the_rules_do_not_force_an_unconditional_read_status_report(monkeypatch):
    from modules.flow_gate.services import mention_service

    rules = mention_service.BODY_UNREADABLE_RULES
    assert len(rules) == 5 and [r.split(":")[0] for r in rules] == [f"본문 미열람 규칙 {n}" for n in range(1, 6)]
    for marker in ("(가)", "(나)", "(다)", "답변 첫머리", "구분해 적으십시오"):
        assert not any(marker in rule for rule in rules)
    assert "경우에만" in rules[2] and "열람 상태 보고를 쓰지 말고" in rules[2]       # conditional, brief report
    assert "읽은 것처럼 인용하거나 요약하지 마십시오" in rules[1]                           # rule 2 kept
    assert "원문 미확인 상태의 추정" in rules[3] and "권한 오류(403)" in rules[4]            # rules 4 and 5 kept
    assert not any(marker in line for line in _help_document_access(monkeypatch)["rules"]
                   for marker in ("(가)", "(나)", "(다)"))


def test_one_constant_feeds_the_lookup_lines_the_help_item_and_the_section(monkeypatch):
    from modules.flow_gate.services import ai_start_precheck, mention_service

    monkeypatch.setattr(mention_service, "BODY_UNREADABLE_RULES", ("RULE X",))
    assert "RULE X" in mention_service._document_lookup_lines(BASE, TOKEN, project="flowgate", doc_id="d")
    assert _help_document_access(monkeypatch)["rules"] == ["RULE X"]
    section = ai_start_precheck.unreadable_section(
        {"checked": 1, "unreadable": [{"doc_id": "d", "title": "t", "reason": "file_not_found"}]})
    assert section == "[읽을 수 없는 문서]\n- d (t): 본문 파일이 없음\nRULE X"
    assert ai_start_precheck.unreadable_section({"checked": 9, "unreadable": []}) == ""
    assert ai_start_precheck.REASON_TEXT == REASONS


# ── the card line port and the wiring ────────────────────────────────────────

def test_the_card_line_port_names_the_documents_a_card_shows(env, decided):
    from modules.flow_gate.db import documents as db_documents
    from modules.flow_gate.graph_workflow.services import card_document_ports as ports

    from flowgate.card_documents import integration

    body = put_draft(env, FB, 0, [node("tmp_t", "T"), node("tmp_n", "N")])
    key, other = body["key_map"]["tmp_t"], body["key_map"]["tmp_n"]
    lines = {line["slot"]: line["doc_id"] for line in body["card_documents"][key]}
    expected = [lines["instruction"], lines["report"]]
    assert ports.card_line_doc_ids(FB, node_key=key) == expected
    assert ports.card_line_doc_ids(FB, doc_id=lines["report"]) == expected     # the card showing the document
    revised = _doc(FB, 50, "TR")                                               # a revision: the line shows it
    db_documents.update(lines["report"], {"superseded_by": revised})
    assert ports.card_line_doc_ids(FB, node_key=key) == [lines["instruction"], revised]
    assert ports.card_line_doc_ids(FB, doc_id=revised) == [lines["instruction"], revised]
    assert other not in ports.card_line_doc_ids(FB, node_key=key)
    assert ports.card_line_doc_ids(FB, doc_id=ROOT) == [] and ports.card_line_doc_ids(None, node_key=key) == []
    integration.unregister()
    try:
        assert ports.card_line_doc_ids(FB, node_key=key) == []                 # no handler: nothing to add
    finally:
        integration.register()


def test_the_node_start_calls_the_check_once_before_the_memos():
    source = (_SERVER_DIR / "modules" / "flow_gate" / "graph_workflow" / "services" / "ai_node_starter.py").read_text(
        encoding="utf-8")
    issue = source[source.index("    def _issue("):source.index("    return ai_invoke_service.start_run(")]
    assert issue.count("ai_start_precheck.") == 1
    assert issue.index("ai_start_precheck.node_mention(") < issue.index("card_memo_hooks.prepend_memo_section(")
