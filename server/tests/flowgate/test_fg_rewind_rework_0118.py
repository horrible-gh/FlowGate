"""flowgate.v02.0118 T#1 / T#2 (0003-NR §2.5 / §3.1): a Time Machine rewind and its rework stay inside the card.

T#1 — ``lines.adopt_result``: the report line a run result already holds (``owned=0``) is taken by the rework's new
result; the earlier result, registered by an attempt the rewind invalidated, is superseded by it (only the supersede
link is written — body, review status and history stay), so the Canvas draws no free document card. A person's own
non-owned document and a root are only rebound, as before. ``settle_displaced`` supersedes an earlier result that such
a rework already left on no line (the existing damaged data) by what the line shows now.

T#2 — ``graph_projector``: a card the rewind put back to pending (no node run result) keeps the title of the written
result its report line still shows; a title state, a Custom label and an unrun card's label are unchanged, and the AI
naming judgement (``card_title_source`` over ``card_document``) still reads the label.

The line table is an in-memory SQLite table (``test_fg_free_documents_0111``'s fixture); documents are dictionaries
and the v0.1 reads/writes ``adopt_result`` uses are replaced by dictionary operations — no application data is touched.
"""
from __future__ import annotations

import copy

import pytest

from flowgate.card_documents import lines, repository
from modules.flow_gate.graph_workflow.projection.graph_projector import (
    GraphView, card_document, card_title_source, project_graph, reported_document,
)
from test_fg_free_documents_0111 import FLOW, KEY, ROUTES, by_key, doc, node, scenario  # noqa: F401 — fixture



@pytest.fixture()
def bound(scenario, monkeypatch):  # noqa: F811 — the imported fixture
    """``scenario`` with the v0.1 document reads / supersede over its dictionaries and a stale set per node."""
    s = scenario
    s.stale = {}
    s.supersedes = []

    def find(doc_id):
        return next((d for d in s.documents if d["doc_id"] == doc_id), None)

    def supersede(old_id, new_id):
        s.supersedes.append((old_id, new_id))
        find(old_id)["superseded_by"] = new_id

    monkeypatch.setattr(lines.legacy, "document", find)
    monkeypatch.setattr(lines.legacy, "supersede", supersede)
    monkeypatch.setattr(lines.legacy, "flow_documents", lambda _flow: list(s.documents))
    monkeypatch.setattr(lines.legacy, "invalidated_documents", lambda _flow, key: set(s.stale.get(key, ())))
    s.find = find
    return s


def report(out, key=KEY):
    return next(line for line in by_key(out)[key]["card_documents"] if line["slot"] == "report")


def history(d):
    """What a supersede must not change: everything of the document but its ``superseded_by`` link."""
    return {k: v for k, v in d.items() if k != "superseded_by"}


def task_card(s, instruction_owned=True):
    """``작업`` card KEY: its instruction T (card draft, edited) and its report line holding TR₁ — the first run's result,
    rebound there with ``owned=0`` (the 0003-NR §2.2 step 1 state). TR₁ was approved, then reopened by the rewind."""
    t1, tr1 = doc(2, "T"), doc(3, "TR", doc_review_status="pending_review", title="상태 필터 작업 보고",
                               review_history=["approved", "reopened"], rejection_history=[{"reason": "r1"}])
    s.documents = [t1, tr1]
    s.add_line(t1, slot="instruction", owned=instruction_owned)
    s.add_line(tr1, slot="report", owned=False, ordinal=1)
    return t1, tr1


# ── T#1: the rework's result supersedes the result it displaces ──────────────

@pytest.mark.parametrize("state,mode", ROUTES)
def test_t1_rework_result_supersedes_the_invalidated_attempts_result(bound, state, mode):
    s = bound
    _t1, tr1 = task_card(s)
    kept = history(copy.deepcopy(tr1))
    tr2 = doc(5, "TR", title="상태 필터 재작업 보고")
    s.documents.append(tr2)
    s.stale[KEY] = {tr1["doc_id"]}                         # TR₁ was registered by the rewound (invalidated) attempt
    lines.adopt_result(FLOW, KEY, tr2["doc_id"])
    assert s.supersedes == [(tr1["doc_id"], tr2["doc_id"])]
    assert history(tr1) == kept                            # body / review / rejection history untouched
    assert [r["doc_id"] for r in repository.rows(FLOW, [KEY]) if r["slot"] == "report"] == [tr2["doc_id"]]
    out, _view, _cards = s.render(state, mode)
    assert out.free_docs == []                             # no 「검토 중」 free card next to the card
    assert f"doc:{tr1['doc_id']}" not in by_key(out)
    assert report(out)["doc_id"] == tr2["doc_id"]


def test_t1_repeated_rewinds_keep_one_supersede_chain(bound):
    s = bound
    _t1, tr1 = task_card(s)
    tr2, tr3 = doc(5, "TR"), doc(7, "TR")
    s.documents += [tr2]
    s.stale[KEY] = {tr1["doc_id"]}
    lines.adopt_result(FLOW, KEY, tr2["doc_id"])
    s.documents.append(tr3)
    s.stale[KEY] = {tr1["doc_id"], tr2["doc_id"]}           # the second rewind invalidated TR₂'s attempt too
    lines.adopt_result(FLOW, KEY, tr3["doc_id"])
    assert s.supersedes == [(tr1["doc_id"], tr2["doc_id"]), (tr2["doc_id"], tr3["doc_id"])]
    for state, mode in ROUTES:
        out, _view, _cards = s.render(state, mode)
        assert out.free_docs == []
        assert report(out)["doc_id"] == tr3["doc_id"]


def test_t1_owned_draft_is_superseded_as_before(bound):
    s = bound
    draft, tr1 = doc(3, "TR", status="draft", doc_review_status=None), doc(5, "TR")
    s.documents = [draft, tr1]
    s.add_line(draft, slot="report", owned=True, ordinal=1)
    lines.adopt_result(FLOW, KEY, tr1["doc_id"])
    assert s.supersedes == [(draft["doc_id"], tr1["doc_id"])]


@pytest.mark.parametrize("state,mode", ROUTES)
def test_t1_a_live_or_persons_document_is_only_rebound(bound, state, mode):
    """A non-owned line document no invalidated attempt registered (a person's linked document, a live attempt's
    result) stays a live document as before 0118: it is rebound, never superseded."""
    s = bound
    person, tr2 = doc(3, "TR", title="사람이 연결한 보고"), doc(5, "TR")
    s.documents = [person, tr2]
    s.add_line(person, slot="report", owned=False, ordinal=1)
    s.stale[KEY] = {"someone.else.0009-TR"}
    lines.adopt_result(FLOW, KEY, tr2["doc_id"])
    assert s.supersedes == []
    assert "superseded_by" not in person
    out, _view, _cards = s.render(state, mode)
    assert out.free_docs == [f"doc:{person['doc_id']}"]   # unchanged 0111 behaviour: the person's document is kept


def test_t1_a_root_line_is_never_superseded(bound):
    s = bound
    s.draft = s.revision = [node("gn_root", "R")]
    root, converted = doc(1, "R"), doc(6, "R")
    s.documents = [root, converted]
    s.add_line(root, key="gn_root", slot="requirement", owned=False)
    s.stale["gn_root"] = {root["doc_id"]}
    lines.adopt_result(FLOW, "gn_root", converted["doc_id"])
    assert s.supersedes == []


def test_t1_no_invalidated_attempt_reads_nothing_more(bound, monkeypatch):
    """Without a rewind / Stop the stale read finds nothing and the binding is what it was."""
    s = bound
    _t1, tr1 = task_card(s)
    tr2 = doc(5, "TR")
    s.documents.append(tr2)
    lines.adopt_result(FLOW, KEY, tr2["doc_id"])
    assert s.supersedes == []
    assert [r["doc_id"] for r in repository.rows(FLOW, [KEY]) if r["slot"] == "report"] == [tr2["doc_id"]]


# ── T#1: existing damaged data (an earlier result already on no line) ────────

def damaged(s):
    """The 0118-B state before this fix: the rework's TR₂ took the report line, TR₁ (invalidated attempt) stayed
    alive on no line and the Canvas drew it as a free 「검토 중」 card."""
    t1, tr1 = doc(2, "T"), doc(3, "TR", doc_review_status="pending_review")
    tr2 = doc(5, "TR")
    s.documents = [t1, tr1, tr2]
    s.add_line(t1, slot="instruction", owned=True)
    s.add_line(tr2, slot="report", owned=False, ordinal=1)
    s.stale[KEY] = {tr1["doc_id"]}
    return tr1, tr2


def test_t1_settle_displaced_repairs_the_existing_free_card(bound):
    s = bound
    tr1, tr2 = damaged(s)
    before, _v, _c = s.render("graph", "revision")
    assert before.free_docs == [f"doc:{tr1['doc_id']}"]   # the reported symptom
    kept = history(copy.deepcopy(tr1))
    assert lines.settle_displaced(FLOW, KEY) == [tr1["doc_id"]]
    assert tr1["superseded_by"] == tr2["doc_id"] and history(tr1) == kept
    assert lines.settle_displaced(FLOW, KEY) == []         # idempotent
    for state, mode in ROUTES:
        out, _view, _cards = s.render(state, mode)
        assert out.free_docs == []
        assert report(out)["doc_id"] == tr2["doc_id"]


def test_t1_the_next_binding_repairs_the_existing_free_card(bound):
    s = bound
    tr1, tr2 = damaged(s)
    tr3 = doc(7, "TR")
    s.documents.append(tr3)
    s.stale[KEY] = {tr1["doc_id"], tr2["doc_id"]}
    lines.adopt_result(FLOW, KEY, tr3["doc_id"])
    assert (tr2["doc_id"], tr3["doc_id"]) in s.supersedes and (tr1["doc_id"], tr3["doc_id"]) in s.supersedes
    out, _view, _cards = s.render("graph", "revision")
    assert out.free_docs == []


def test_t1_settle_displaced_leaves_everything_else(bound):
    s = bound
    tr1, tr2 = damaged(s)
    person = doc(8, "TR", title="사람 문서")                # alive, on no line, but no invalidated attempt made it
    memo = doc(9, "M")                                     # stale but the card has no line of its type
    cancelled = doc(10, "TR", status="cancelled")           # stale, cancelled: not alive
    other = doc(11, "TR")                                   # stale, still on another card's line
    s.documents += [person, memo, cancelled, other]
    s.draft = s.revision = [node(), node("gn_other")]
    s.add_line(other, key="gn_other", slot="report", owned=False, ordinal=1)
    s.stale[KEY] = {tr1["doc_id"], memo["doc_id"], cancelled["doc_id"], other["doc_id"]}
    assert lines.settle_displaced(FLOW, KEY) == [tr1["doc_id"]]
    assert all("superseded_by" not in d for d in (person, memo, cancelled, other, tr2))
    out, _view, _cards = s.render("graph", "revision")
    assert set(out.free_docs) == {f"doc:{person['doc_id']}", f"doc:{memo['doc_id']}"}


# ── T#2: the rewound card keeps its report line's title ──────────────────────

def report_lines(tr, written=True):
    return [{"slot": "instruction", "author": "instruction_ai_or_human", "doc_id": None},
            {"slot": "report", "author": "run", "doc_id": tr["doc_id"], "has_body": written}]


@pytest.mark.parametrize("mode", ["draft", "revision"])
def test_t2_a_rewound_card_keeps_the_title_of_its_report_line(mode):
    tr1 = doc(3, "TR", title="상태 필터 작업 보고")
    row = node()
    pending = {KEY: {"node_key": KEY, "status": "pending", "result_doc_id": None}} if mode == "revision" else {}
    view = GraphView(mode, nodes=[row], node_runs=pending, card_docs={KEY: report_lines(tr1)})
    out = project_graph(FLOW, "graph", [tr1], view)
    card = by_key(out)[KEY]
    assert card["title"] == "상태 필터 작업 보고"
    assert out.free_docs == []
    # the AI naming judgement reads the card as before: no node result -> its label
    assert card_title_source(row, card_document(view, row, {tr1["doc_id"]: tr1}))[:2] == ("label", KEY)


def test_t2_an_unwritten_report_draft_keeps_the_label():
    draft = doc(3, "TR", title="작업 레포트", status="draft", doc_review_status=None)
    view = GraphView("revision", nodes=[node()], card_docs={KEY: report_lines(draft, written=False)})
    out = project_graph(FLOW, "graph", [draft], view)
    assert by_key(out)[KEY]["title"] == KEY


def test_t2_a_title_state_and_a_bound_result_still_come_first():
    tr1, tr2 = doc(3, "TR", title="이전 보고"), doc(5, "TR", title="새 보고")
    named = GraphView("revision", nodes=[node()], card_docs={KEY: report_lines(tr1)},
                      title_states={KEY: {"title": "사람이 붙인 제목", "title_source": "human", "version": 2}})
    assert by_key(project_graph(FLOW, "graph", [tr1], named))[KEY]["title"] == "사람이 붙인 제목"
    running = GraphView("revision", nodes=[node()], card_docs={KEY: report_lines(tr1)},
                        node_runs={KEY: {"node_key": KEY, "status": "review", "result_doc_id": tr2["doc_id"]}})
    assert by_key(project_graph(FLOW, "graph", [tr1, tr2], running))[KEY]["title"] == "새 보고"


def test_t2_a_custom_card_keeps_its_name():
    tr1 = doc(3, "TR", title="이전 보고")
    custom = {**node(), "node_type": "custom", "label": "나의 카드"}
    view = GraphView("revision", nodes=[custom], card_docs={KEY: report_lines(tr1)})
    assert by_key(project_graph(FLOW, "graph", [tr1], view))[KEY]["title"] == "나의 카드"


def test_t2_reported_document_takes_only_a_live_written_report():
    tr1 = doc(3, "TR", title="보고")
    gone = doc(4, "TR", title="취소", status="cancelled")
    by_id = {d["doc_id"]: d for d in (tr1, gone)}
    assert reported_document(report_lines(tr1), by_id) is tr1
    assert reported_document(report_lines(gone), by_id) is None
    assert reported_document([{"slot": "deploy", "author": "run", "doc_id": tr1["doc_id"], "has_body": True}],
                             by_id) is None
    assert reported_document(None, by_id) is None


@pytest.mark.parametrize("state,mode", ROUTES)
def test_t2_canvas_read_after_rewind_and_reload(bound, state, mode):
    """The Canvas read (every ownership / view a reload may land on) after the rewind: the card shows its earlier
    report's title, and after the rework's result binds it shows the new one — never the palette label."""
    s = bound
    _t1, tr1 = task_card(s)
    run = {"revision_no": 1, "node_runs": [{"node_key": KEY, "status": "pending", "result_doc_id": None}]}
    out, _view, _cards = s.render(state, mode, run=run)
    assert by_key(out)[KEY]["title"] == "상태 필터 작업 보고"
    tr2 = doc(5, "TR", title="상태 필터 재작업 보고")
    s.documents.append(tr2)
    s.stale[KEY] = {tr1["doc_id"]}
    lines.adopt_result(FLOW, KEY, tr2["doc_id"])
    run["node_runs"][0].update(status="review", result_doc_id=tr2["doc_id"])
    out, _view, _cards = s.render(state, mode, run=run)
    assert by_key(out)[KEY]["title"] == "상태 필터 재작업 보고"
    assert out.free_docs == []
