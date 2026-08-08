"""Continuous-chain N/T authoring mode — group 0230 R0001 / T0005.

R0001: a continuous run may choose, before it starts, how instruction steps (N/T) are
handled — the current "승인문서 자동 발급" (server auto-creates + auto-approves the N/T from a
fixed template) OR "AI 직접 작성" (the AI authors the N/T itself, like it already does for TS).
The choice rides the continuation token as ``continuation_instruction_mode`` (``auto_approved``
= legacy default / ``ai_direct`` = AI writes N/T) and must persist on every hop of the unmanned
self-chain so the whole run keeps the same policy.

The service-level gating (advance_workflow skips _auto_complete_instruction_heads when the mode
is ai_direct → the head reaches the worker as an N/T) and the route/body wiring are covered by
test_continuous_instruction_skip_0092.py and test_continuous_routes_0086.py. This module pins the
remaining pieces T0005 called out:

  • (§5.3 b) the worker mention for an ai_direct N/T head carries an "Instruction authoring"
    guide section (WI-7); the managed report path (auto_approved) never shows it.
  • (§5.3 c) the inbox self-chain carries continuation_instruction_mode from the consumed token
    onto the next hop's advance (WI-6).
  • (WI-5) a test auto-recovery repair token inherits the mode too.
  • (§5.3 d) migration 063 adds the tokens.continuation_instruction_mode column.
"""
from __future__ import annotations

from unittest.mock import MagicMock

from modules.flow_gate.services import mention_service


# ── (§5.3 b / WI-7) mention carries the N/T authoring guide only for ai_direct N/T heads ──

def _nt_mention(head_type: str, locale: str = "ko") -> str:
    return mention_service.build_mention_from_token_rec(
        token_rec={
            "project": "flowgate",
            "group_id": "flowgate.default.0230",
            "scratch_dir": "/scratch/tok_001",
        },
        head_type=head_type,
        head_status="pending",
        parent_doc={
            "doc_id": "flowgate.default.0230.0001-R",
            "type_code": "R", "seq": 1, "title": "연속모드",
            "module": "default", "project_id": "flowgate",
        },
        api_base_url="http://h/flow_gate/api/v1",
        raw_token="RAW",
        continuous=True,
        locale=locale,
    )


def test_mention_for_ai_direct_T_head_includes_authoring_guide():
    mention = _nt_mention("T")
    assert "## Instruction authoring (T)" in mention
    # group 0372 set 3 (D-0003 §3-2): the mention now carries a pointer, not the full
    # T-specific grammar (that content moved behind the authoring_guide help item and
    # is covered separately by mention_service._nt_authoring_section, still exercised
    # by test_nt_authoring_guide_follows_locale below and by test_help_items_0372.py).
    assert "help/items/authoring_guide/T" in mention


def test_mention_for_ai_direct_N_head_includes_authoring_guide():
    mention = _nt_mention("N")
    assert "## Instruction authoring (N)" in mention
    assert "help/items/authoring_guide/N" in mention


def test_mention_report_head_has_no_authoring_guide():
    # A report head (TR) is the managed auto_approved path's worker step; the N/T authoring
    # guide must NOT appear there — it is exclusive to the ai_direct N/T authoring path.
    mention = _nt_mention("TR")
    assert "Instruction authoring" not in mention


def test_nt_and_ts_authoring_are_mutually_exclusive_by_head():
    # The N/T guide and the TS guide key off distinct head types; an N/T head shows the N/T
    # guide and not the TS one, and vice-versa (no cross-contamination).
    t_mention = _nt_mention("T")
    assert "Instruction authoring (T)" in t_mention
    assert "Test scenario authoring (TS)" not in t_mention
    assert "help/items/authoring_guide/T" in t_mention
    assert "help/items/authoring_guide/TS" not in t_mention
    ts_mention = _nt_mention("TS")
    assert "Test scenario authoring (TS)" in ts_mention
    assert "Instruction authoring" not in ts_mention
    assert "help/items/authoring_guide/TS" in ts_mention


# ── N/T + TS authoring guides now follow the worker's requested locale. group 0372 set 3
# reduced the mention's own copy of this to a one-line pointer (D-0003 §3-2), so the
# locale check here is against that pointer sentence; the full grammar's own locale
# fidelity is unchanged and still covered directly against mention_service._ts_authoring_section /
# _nt_authoring_section by test_continuation_instruction_mode_0230's sibling suite
# test_server_korean_leak_0355.py and by test_help_items_0372.py (the authoring_guide
# help item literally reuses those same functions). ──

def test_nt_authoring_guide_follows_locale():
    ko = _nt_mention("T", locale="ko")
    assert "도움말 항목에 있습니다" in ko

    en = _nt_mention("T", locale="en")
    assert "is in the help item" in en
    assert "도움말" not in en

    ja = _nt_mention("N", locale="ja")
    assert "ヘルプ項目にあります" in ja
    assert "도움말" not in ja


def test_ts_authoring_guide_follows_locale():
    ko = _nt_mention("TS", locale="ko")
    assert "도움말 항목에 있습니다" in ko

    en = _nt_mention("TS", locale="en")
    assert "is in the help item" in en

    ja = _nt_mention("TS", locale="ja")
    assert "ヘルプ項目にあります" in ja


def test_ts_authoring_full_grammar_still_locale_correct():
    """The FULL TS grammar (fetched via the authoring_guide help item) is unchanged by
    the set-3 mention reduction — pin its locale fidelity directly against the function
    the help item calls, since the mention itself no longer carries this text."""
    from modules.flow_gate.services import mention_service

    ko = mention_service._ts_authoring_section("ko")
    assert "## 테스트 준비" in ko and "## 테스트 케이스" in ko

    en = mention_service._ts_authoring_section("en")
    assert "## Setup" in en and "## Test Cases" in en
    assert "Write this TS as an executable spec" in en

    ja = mention_service._ts_authoring_section("ja")
    assert "## Setup" in ja and "## Test Cases" in ja
    assert "実行可能なスペック" in ja


# ── (§5.3 c / WI-6) inbox self-chain carries the mode onto the next hop ───────────────

class _FakeRequest:
    def __init__(self):
        self.headers = {"x-locale": "ko"}
        self.base_url = "http://h/"


def _wire_self_chain(monkeypatch, *, item_seq=2, is_admin=1):
    from modules.flow_gate.db import workflow_sequences as wfseq
    from modules.flow_gate.db import users as db_users
    from modules.flow_gate.workflow import pipeline_service
    from modules.flow_gate.services import workflow_decision_service as wds
    from modules.flow_gate.api import inbox_routes

    monkeypatch.setattr(wfseq, "get_item_by_result_doc_id", lambda _d: {"item_seq": item_seq})
    monkeypatch.setattr(db_users, "get_by_id", lambda _uid: {"user_id": _uid, "is_admin": is_admin})
    monkeypatch.setattr(pipeline_service, "transition_document_review", MagicMock())
    captured: dict = {}

    def _fake_advance(**k):
        captured.update(k)
        return {"token": "NEXTRAW", "token_id": "tok-next", "mention": "NEXT-MENTION",
                "expires_at": "2026-06-20", "continuation_remaining": 3}

    monkeypatch.setattr(wds, "advance_workflow", _fake_advance)
    monkeypatch.setattr(inbox_routes, "_inbox_api_base", lambda _r: "http://h/flow_gate/api/v1")
    return captured


def test_self_chain_carries_ai_direct_instruction_mode(monkeypatch):
    from modules.flow_gate.api import inbox_routes
    captured = _wire_self_chain(monkeypatch)
    env = inbox_routes._continuation_self_chain(
        _FakeRequest(),
        {"doc_ref": "flowgate.default.0230.0001-R", "issued_to": "pm",
         "continuation_target_seq": 6, "continuation_review_mode": 0,
         "continuation_instruction_mode": "ai_direct"},
        "flowgate", "flowgate.default.0230.0003-NR", "NR",
    )
    # The next hop's advance inherits the mode — the run keeps AI-authoring N/T end to end.
    assert captured["continuation_instruction_mode"] == "ai_direct"
    assert env["continuation_instruction_mode"] == "ai_direct"
    assert env["next_token"] == "NEXTRAW"


def test_self_chain_carries_auto_approve_item_seqs(monkeypatch):
    # 0352 T0004 §3.4: db/tokens.py hands the consumed token record back with the
    # per-item_seq selection already decoded to a plain list — the self-chain must forward
    # it onto the next hop's advance and report it in the envelope, exactly like the mode.
    from modules.flow_gate.api import inbox_routes
    captured = _wire_self_chain(monkeypatch)
    env = inbox_routes._continuation_self_chain(
        _FakeRequest(),
        {"doc_ref": "flowgate.default.0230.0001-R", "issued_to": "pm",
         "continuation_target_seq": 6, "continuation_review_mode": 0,
         "continuation_instruction_mode": "ai_direct",
         "continuation_auto_approve_item_seqs": [3]},
        "flowgate", "flowgate.default.0230.0003-NR", "NR",
    )
    assert captured["continuation_auto_approve_item_seqs"] == [3]
    assert env["continuation_auto_approve_item_seqs"] == [3]


def test_self_chain_defaults_missing_auto_approve_item_seqs_to_empty(monkeypatch):
    # A legacy/no-selection token carries no set at all — the self-chain must not choke on
    # its absence and must report "no selection" (empty list), not None or a KeyError.
    from modules.flow_gate.api import inbox_routes
    captured = _wire_self_chain(monkeypatch)
    env = inbox_routes._continuation_self_chain(
        _FakeRequest(),
        {"doc_ref": "flowgate.default.0230.0001-R", "issued_to": "pm",
         "continuation_target_seq": 6, "continuation_review_mode": 0},
        "flowgate", "flowgate.default.0230.0003-NR", "NR",
    )
    assert captured["continuation_auto_approve_item_seqs"] == []
    assert env["continuation_auto_approve_item_seqs"] == []


def test_self_chain_defaults_missing_mode_to_auto_approved(monkeypatch):
    # A legacy continuation token (no continuation_instruction_mode) still advances; the
    # envelope reports the legacy auto_approved policy (backward compatible).
    from modules.flow_gate.api import inbox_routes
    _wire_self_chain(monkeypatch)
    env = inbox_routes._continuation_self_chain(
        _FakeRequest(),
        {"doc_ref": "flowgate.default.0230.0001-R", "issued_to": "pm",
         "continuation_target_seq": 6, "continuation_review_mode": 0},
        "flowgate", "flowgate.default.0230.0003-NR", "NR",
    )
    assert env["continuation_instruction_mode"] == "auto_approved"


# ── (WI-5) test auto-recovery repair token inherits the mode ─────────────────────────

def test_repair_token_inherits_instruction_mode(monkeypatch):
    from modules.flow_gate.services import engine_recipe_service as ers
    from modules.flow_gate.services import token_service
    from modules.flow_gate.workflow import event_logger

    issue_kw: dict = {}
    monkeypatch.setattr(
        token_service, "issue",
        lambda **k: issue_kw.update(k) or {"raw_token": "RAW", "token_id": "tok",
                                           "scratch_dir": "/s", "expires_at": "2026-06-20"},
    )
    monkeypatch.setattr(ers, "_build_repair_mention", lambda *a, **k: "REPAIR")
    monkeypatch.setattr(event_logger, "log_test_run_repair", lambda **k: None)

    ers._emit_repair(
        doc={"doc_id": "flowgate.default.0230.0006-TS", "project_id": "flowgate",
             "group_id": "flowgate.default.0230"},
        run={"run_id": "r1", "error": None},
        items=[],
        token_rec={"issued_to": "pm", "continuation_target_seq": 6,
                   "continuation_review_mode": 0, "continuation_locale": "ko",
                   "continuation_instruction_mode": "ai_direct",
                   "continuation_auto_approve_item_seqs": [3]},
        attempt=1,
    )
    assert issue_kw["continuation_instruction_mode"] == "ai_direct"
    # 0352 T0004 §3.4: the per-item_seq selection rides the repair token too, not just mode.
    assert issue_kw["continuation_auto_approve_item_seqs"] == [3]


# ── (§5.3 d) migration 063 adds the column with a safe default ────────────────────────

def test_migration_063_adds_instruction_mode_column(test_db):
    cols = {row["name"]: row for row in test_db.execute("PRAGMA table_info(tokens)").fetchall()}
    assert "continuation_instruction_mode" in cols
    # Additive + nullable (no NOT NULL): legacy rows read NULL → normalized to auto_approved.
    assert cols["continuation_instruction_mode"]["notnull"] == 0


# ── (0352 T0004 §3.1) migration 078 adds the per-item_seq selection + paused-chain mode ──

def test_migration_078_adds_auto_approve_item_seqs_and_paused_mode_columns(test_db):
    token_cols = {
        row["name"]: row for row in test_db.execute("PRAGMA table_info(tokens)").fetchall()
    }
    assert "continuation_auto_approve_item_seqs" in token_cols
    assert token_cols["continuation_auto_approve_item_seqs"]["notnull"] == 0

    paused_cols = {
        row["name"]: row
        for row in test_db.execute("PRAGMA table_info(ai_invoke_paused_chains)").fetchall()
    }
    # NR0003's root cause of the pause->resume mode-loss bug: this column did not exist at
    # all before migration 078, so nothing could ever be read back on resume.
    assert "continuation_instruction_mode" in paused_cols
    assert paused_cols["continuation_instruction_mode"]["notnull"] == 0
    assert "continuation_auto_approve_item_seqs" in paused_cols
    assert paused_cols["continuation_auto_approve_item_seqs"]["notnull"] == 0
