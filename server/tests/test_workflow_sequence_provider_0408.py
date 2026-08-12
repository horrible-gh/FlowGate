"""0408 T0012: workflow sequence provider persistence and unattended inheritance."""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("DB_TYPE", "sqlite")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
os.environ.setdefault("CONTEXT", "/flowgate")
os.environ.setdefault("ALLOWED_ORIGIN", "")

_SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SERVER_DIR))

from modules.flow_gate.api.v1 import workflow_decision_routes, workflow_head_routes  # noqa: E402
from modules.flow_gate.db import connection as db_connection  # noqa: E402
from modules.flow_gate.db import workflow_sequences as db_wfseq  # noqa: E402
from modules.flow_gate.services import ai_invoke_service as ai_svc  # noqa: E402
from modules.flow_gate.services import work_plan_apply_service as apply_svc  # noqa: E402
from modules.flow_gate.services import work_plan_sequence_service as plan_svc  # noqa: E402
from modules.flow_gate.services import workflow_decision_service as decision_svc  # noqa: E402
from routers.main import app  # noqa: E402

PROJECT = "flowgate"
GROUP = "flowgate.default.0408"
ROOT = "flowgate.default.0408.0001-B"
WP = "flowgate.default.0408.0004-WP"
AUTH = {"Authorization": "Bearer test-token"}
QUERY = "/flowgate/api/v1/workflow/sequence"
PROVIDER = "plan-provider"
PROVIDER_NAME = "Plan Provider"

SEED_SQL = f"""
INSERT OR IGNORE INTO projects(project_id, project_name, is_active, created_at, updated_at)
 VALUES('{PROJECT}', 'FlowGate', 1, datetime('now'), datetime('now'));
INSERT OR IGNORE INTO groups(group_id, project_id, module, title, status, created_at, updated_at)
 VALUES('{GROUP}', '{PROJECT}', 'default', 'provider persistence', 'OPEN', datetime('now'), datetime('now'));
INSERT OR IGNORE INTO documents(
 doc_id, project_id, module, group_id, type_code, seq, title, status, created_at, updated_at)
 VALUES('{ROOT}', '{PROJECT}', 'default', '{GROUP}', 'B', 1, 'provider persistence', 'open', datetime('now'), datetime('now')),
       ('{WP}', '{PROJECT}', 'default', '{GROUP}', 'WP', 4, 'provider plan', 'open', datetime('now'), datetime('now'));
"""


class SqliteStore:
    """Minimal real-SQL store: query text still comes from the production registry."""

    def __init__(self, path: str):
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")

    def _execute(self, sql, params=None):
        self._conn.execute(sql, params or [])
        self._conn.commit()

    def _fetch_one(self, sql, params=None):
        row = self._conn.execute(sql, params or []).fetchone()
        return dict(row) if row else None

    def _fetch_all(self, sql, params=None):
        return [dict(row) for row in self._conn.execute(sql, params or []).fetchall()]

    @contextmanager
    def transaction(self):
        yield self


def provider(pid=PROVIDER, name=PROVIDER_NAME, enabled=True):
    return {
        "id": pid, "name": name, "exec_type": "cli", "kind": "codex",
        "enabled": enabled, "cli_command": "unused", "api_base_url": None,
        "api_model": None, "api_key_set": False, "api_key_hint": None,
    }


@pytest.fixture
def real_sequence(migrated_sqlite_db):
    path = migrated_sqlite_db("workflow_sequence_provider_0408.db", seed_sql=SEED_SQL)
    store = SqliteStore(path)
    previous = db_connection.STORE
    db_connection.STORE = store
    db_wfseq.insert_sequence(ROOT)
    seq = db_wfseq.get_sequence_by_doc_id(ROOT)
    try:
        yield store, seq
    finally:
        db_connection.STORE = previous
        store._conn.close()


@pytest.fixture
def client(monkeypatch, real_sequence):
    auth = lambda request: {"_is_user_jwt": True, "issued_to": "usr_test", "is_admin": True}
    monkeypatch.setattr(workflow_decision_routes, "verify_bearer", auth)
    monkeypatch.setattr(workflow_head_routes, "verify_bearer", auth)
    monkeypatch.setattr(workflow_decision_routes, "_active_ai_run_response_for_user", lambda doc, auth: None)
    return TestClient(app, raise_server_exceptions=False)


def save_item(**overrides):
    item = {
        "type": "D", "label": "Basic design", "note": "from plan",
        "source_doc_id": WP, "source_revision_no": 8,
        "provider_id": PROVIDER, "provider_display_name": PROVIDER_NAME,
    }
    item.update(overrides)
    decision_svc.edit_workflow_pending(ROOT, [item])


def prepare_start(monkeypatch, *, providers=None):
    chain = providers or [provider(), provider("header-default", "Header Default")]
    effective = {"providers": chain, "source": "system", "registered_count": len(chain)}
    monkeypatch.setattr(ai_svc.db_docs, "get_group_max_seq", lambda group_id: 1)
    monkeypatch.setattr(ai_svc.db_docs, "get_by_id", lambda doc_id: {"doc_id": doc_id, "branch": "main"})
    monkeypatch.setattr(ai_svc.db_projects, "get_by_id", lambda pid: {"project_name": "flowgate"})
    monkeypatch.setattr(ai_svc.db_git, "get_config", lambda pid: None)
    monkeypatch.setattr(ai_svc.ai_settings_service, "resolve_effective", lambda pid: effective)
    monkeypatch.setattr(ai_svc.token_service, "issue", lambda **kw: {
        "raw_token": "tok_0408", "token_id": "tok_0408",
        "expires_at": "2026-08-12T00:00:00+00:00", "scratch_dir": "C:/tmp",
    })
    root = Path("C:/tmp")
    monkeypatch.setattr(ai_svc.storage_paths, "resolve_project_src_root", lambda pid, branch, *, group_id: root)
    monkeypatch.setattr(ai_svc, "_create_scratch", lambda pid, run_id: root)
    monkeypatch.setattr(ai_svc, "_git_status_paths", lambda root: set())
    monkeypatch.setattr(ai_svc, "_cleanup_retained_scratches", lambda pid: None)
    monkeypatch.setattr(ai_svc, "_worker", lambda run, chain, prompt: None)
    monkeypatch.setattr(ai_svc, "_runs", {})
    return effective


def start_continuous(**overrides):
    values = dict(
        project_id=PROJECT, module="default", group_id=GROUP, doc_ref=ROOT,
        action_scope="new", mode="continuous", continuation_target_seq=1,
        continuation_review_mode=False, continuation_instruction_mode="ai_direct",
        continuation_locale="ko", issued_to="worker",
        api_base_url="http://127.0.0.1:8089/flowgate/api/v1",
        mention_builder=lambda raw, scratch: "work", provider_id="header-default",
    )
    values.update(overrides)
    return ai_svc.start_run(**values)


def test_pour_patch_get_and_unattended_start_keep_provider(monkeypatch, real_sequence):
    """Acceptance chain: plan pour -> PATCH service -> canonical GET -> no-override start."""
    plan = {
        "provider_candidates": [{"provider_id": PROVIDER, "display_name": PROVIDER_NAME}],
        "steps": [{"key": "D#1", "type": "D", "note": "from plan", "provider_id": PROVIDER}],
    }
    poured, dropped, uid = plan_svc.plan_to_rows(plan, WP, 8)
    poured, _ = plan_svc.attach_auto_rows(poured, next_uid=uid)
    assert dropped == []
    row = poured[0]
    decision_svc.edit_workflow_pending(ROOT, [{
        "type": row["type"], "label": row["label"], "note": row["note"],
        "source_doc_id": row["source_doc_id"], "source_revision_no": row["source_revision_no"],
        "provider_id": row["provider_id"], "provider_display_name": row["provider_display_name"],
    }])
    monkeypatch.setattr(decision_svc, "provider_view_of", lambda pid: {
        "readable": True, "providers": {PROVIDER: provider()}
    })
    fetched = decision_svc.get_workflow_sequence(ROOT)["items"][0]
    prepare_start(monkeypatch)
    started = start_continuous(continuation_provider_overrides=None)
    evidence = {
        "poured": {"provider_id": row["provider_id"], "provider_display_name": row["provider_display_name"]},
        "patched": {"provider_id": db_wfseq.get_sequence_items(real_sequence[1]["id"])[0]["provider_id"],
                    "provider_display_name": db_wfseq.get_sequence_items(real_sequence[1]["id"])[0]["provider_display_name"]},
        "fetched": {"provider_id": fetched["provider_id"], "provider_display_name": fetched["provider_display_name"],
                    "provider_registered": fetched["provider_registered"]},
        "started": {"provider_id": started["provider"]["id"], "overrides_sent": False},
    }
    assert evidence["poured"]["provider_id"] == PROVIDER
    assert evidence["patched"] == evidence["poured"]
    assert evidence["fetched"]["provider_id"] == PROVIDER
    assert evidence["started"]["provider_id"] == PROVIDER
    print("PROVIDER_CHAIN_ROUNDTRIP=" + json.dumps(evidence, sort_keys=True))


def test_note_only_patch_round_trip_keeps_provider(real_sequence):
    save_item()
    loaded = decision_svc.get_workflow_sequence(ROOT)["items"][0]
    loaded["note"] = "only the note changed"
    decision_svc.edit_workflow_pending(ROOT, [{key: loaded.get(key) for key in (
        "type", "label", "note", "source_doc_id", "source_revision_no",
        "provider_id", "provider_display_name",
    )}])
    after = db_wfseq.get_sequence_items(real_sequence[1]["id"])[0]
    assert (after["note"], after["provider_id"], after["provider_display_name"]) == (
        "only the note changed", PROVIDER, PROVIDER_NAME,
    )
    print("PROVIDER_NOTE_ONLY_ROUNDTRIP=" + json.dumps({
        "note": after["note"], "provider_id": after["provider_id"],
        "provider_display_name": after["provider_display_name"],
    }, sort_keys=True))


def test_auto_report_uses_direct_value_then_parent_and_tsr_is_empty():
    plan = {"steps": [
        {"key": "N#1", "type": "N", "provider_id": "parent", "provider_display_name": "Parent"},
        {"key": "NR#1", "type": "NR", "pair_key": "N#1", "provider_id": "direct", "provider_display_name": "Direct"},
        {"key": "T#1", "type": "T", "provider_id": "task", "provider_display_name": "Task"},
        {"key": "TS#1", "type": "TS", "provider_id": "test", "provider_display_name": "Test"},
    ]}
    rows, _, uid = plan_svc.plan_to_rows(plan, WP, 8)
    rows, _ = plan_svc.attach_auto_rows(rows, next_uid=uid)
    pairs = {row["type"]: row for row in rows}
    assert pairs["NR"]["provider_id"] == "direct"
    assert pairs["TR"]["provider_id"] == "task"
    assert pairs["TSR"]["provider_id"] is None


def test_plan_defaults_fill_instruction_and_other_rows_but_leave_tsr_empty():
    plan = {
        "provider_candidates": [{"provider_id": "default", "display_name": "Default"}],
        "defaults": {"provider_id": "default", "note": ""},
        "steps": [
            {"key": "N#1", "type": "N"},
            {"key": "NR#1", "type": "NR", "pair_key": "N#1"},
            {"key": "T#1", "type": "T"},
            {"key": "TR#1", "type": "TR", "pair_key": "T#1"},
            {"key": "TS#1", "type": "TS"},
            {"key": "D#1", "type": "D"},
        ],
    }
    rows, _, uid = plan_svc.plan_to_rows(plan, WP, 8)
    rows, _ = plan_svc.attach_auto_rows(rows, next_uid=uid)
    by_type = {row["type"]: row for row in rows}
    assert {code: by_type[code]["provider_id"] for code in ("N", "NR", "T", "TR", "TS", "D")} == {
        "N": "default", "NR": "default", "T": "default", "TR": "default",
        "TS": "default", "D": "default",
    }
    assert by_type["N"]["provider_display_name"] == "Default"
    assert by_type["TSR"]["provider_id"] is None


def test_plan_provider_priority_is_step_then_pair_then_default():
    plan = {
        "provider_candidates": [
            {"provider_id": "default", "display_name": "Default"},
            {"provider_id": "step", "display_name": "Step"},
            {"provider_id": "pair", "display_name": "Pair"},
        ],
        "defaults": {"provider_id": "default", "note": ""},
        "steps": [
            {"key": "N#1", "type": "N", "provider_id": "step"},
            {"key": "NR#1", "type": "NR", "pair_key": "N#1", "provider_id": "pair"},
            {"key": "T#1", "type": "T"},
            {"key": "TR#1", "type": "TR", "pair_key": "T#1", "provider_id": "pair"},
            {"key": "D#1", "type": "D"},
        ],
    }
    rows, _, uid = plan_svc.plan_to_rows(plan, WP, 8)
    rows, _ = plan_svc.attach_auto_rows(rows, next_uid=uid)
    by_type = {row["type"]: row for row in rows}
    assert by_type["N"]["provider_id"] == "step"
    assert by_type["NR"]["provider_id"] == "pair"
    assert by_type["T"]["provider_id"] == "pair"
    assert by_type["TR"]["provider_id"] == "pair"
    assert by_type["D"]["provider_id"] == "default"


def test_malformed_plan_default_provider_is_dropped_with_warning(caplog):
    plan = {
        "defaults": {"provider_id": "bad id", "note": ""},
        "steps": [{"key": "N#1", "type": "N"}],
    }
    with caplog.at_level(logging.WARNING):
        rows, _, uid = plan_svc.plan_to_rows(plan, WP, 8)
    rows, _ = plan_svc.attach_auto_rows(rows, next_uid=uid)
    assert [row["provider_id"] for row in rows] == [None, None]
    assert "dropping malformed workflow sequence provider id" in caplog.text


@pytest.mark.parametrize("mode", ["ai_direct", "auto_approved"])
def test_empty_instruction_uses_paired_report_provider_in_both_modes(monkeypatch, mode):
    items = [
        {"item_seq": 1, "type": "N", "provider_id": None, "provider_display_name": None, "result_doc_id": None},
        {"item_seq": 2, "type": "NR", "provider_id": "report", "provider_display_name": "Report", "result_doc_id": None},
    ]
    monkeypatch.setattr(ai_svc.db_wfseq, "get_sequence_for_member_doc", lambda doc: {"id": 408})
    monkeypatch.setattr(ai_svc.db_wfseq, "get_effective_head", lambda seq: dict(items[0]))
    monkeypatch.setattr(ai_svc.db_wfseq, "get_sequence_items", lambda seq: [dict(row) for row in items])
    assert ai_svc.stored_hop_provider(
        ROOT, continuation_instruction_mode=mode,
    ) == ("report", "Report", 2)


def test_ai_direct_start_uses_provider_from_paired_report(monkeypatch, real_sequence):
    decision_svc.edit_workflow_pending(ROOT, [
        {"type": "N", "label": "Instruction"},
        {
            "type": "NR", "label": "Report", "provider_id": "report",
            "provider_display_name": "Report",
        },
    ])
    prepare_start(monkeypatch, providers=[
        provider("report", "Report"), provider("header-default", "Header Default"),
    ])
    started = start_continuous(continuation_instruction_mode="ai_direct")
    assert started["provider"]["id"] == "report"


def test_star_query_effective_head_reads_provider_columns(real_sequence):
    save_item()
    head = db_wfseq.get_effective_head(real_sequence[1]["id"])
    assert (head["provider_id"], head["provider_display_name"]) == (PROVIDER, PROVIDER_NAME)


@pytest.mark.parametrize("payload,detail", [
    ({"provider_id": "bad id", "provider_display_name": "Bad"}, "provider_id_format_invalid"),
    ({"provider_id": None, "provider_display_name": "Orphan"}, "provider_display_name requires provider_id"),
    ({"provider_id": "valid", "provider_display_name": "x" * 192}, "provider_display_name_too_long"),
])
def test_patch_rejects_three_bad_provider_shapes_at_original_index(client, payload, detail):
    response = client.patch(QUERY, headers=AUTH, json={"doc_id": ROOT, "items": [
        {"type": "D", "label": "valid"}, {"type": "D", "label": "bad", **payload},
    ]})
    assert response.status_code == 422
    assert response.json() == {"error": "invalid_sequence_item", "detail": detail, "item_index": 1}


def test_inactive_provider_is_still_saved(real_sequence):
    save_item(provider_id="deleted-provider", provider_display_name="Deleted Snapshot")
    row = db_wfseq.get_sequence_items(real_sequence[1]["id"])[0]
    assert (row["provider_id"], row["provider_display_name"]) == ("deleted-provider", "Deleted Snapshot")


def test_provider_view_has_true_false_null_and_two_layer_names():
    active = decision_svc.resolve_row_provider(PROVIDER, "Old Snapshot", {
        "readable": True, "providers": {PROVIDER: {"id": PROVIDER, "name": "Current Name"}},
    })
    inactive = decision_svc.resolve_row_provider(PROVIDER, "Old Snapshot", {"readable": True, "providers": {}})
    unreadable = decision_svc.resolve_row_provider(PROVIDER, "Old Snapshot", {"readable": False, "providers": {}})
    assert (active["provider_registered"], active["provider_display_name"]) == (True, "Current Name")
    assert (inactive["provider_registered"], inactive["provider_display_name"]) == (False, "Old Snapshot")
    assert (unreadable["provider_registered"], unreadable["provider_display_name"]) == (None, "Old Snapshot")


@pytest.mark.parametrize("mode,expected", [("ai_direct", 1), ("auto_approved", 2)])
def test_stored_provider_uses_the_same_mode_aware_fold(monkeypatch, mode, expected):
    items = [
        {"item_seq": 1, "type": "T", "provider_id": "head", "provider_display_name": "Head", "result_doc_id": None},
        {"item_seq": 2, "type": "TR", "provider_id": "report", "provider_display_name": "Report", "result_doc_id": None},
    ]
    monkeypatch.setattr(ai_svc.db_wfseq, "get_sequence_for_member_doc", lambda doc: {"id": 408})
    monkeypatch.setattr(ai_svc.db_wfseq, "get_effective_head", lambda seq: dict(items[0]))
    monkeypatch.setattr(ai_svc.db_wfseq, "get_sequence_items", lambda seq: [dict(row) for row in items])
    pid, name, item_seq = ai_svc.stored_hop_provider(ROOT, continuation_instruction_mode=mode)
    assert item_seq == expected
    assert pid == ("head" if expected == 1 else "report")


def test_folded_empty_report_falls_back_to_head(monkeypatch):
    items = [
        {"item_seq": 1, "type": "N", "provider_id": "head", "provider_display_name": "Head", "result_doc_id": None},
        {"item_seq": 2, "type": "NR", "provider_id": None, "provider_display_name": None, "result_doc_id": None},
    ]
    monkeypatch.setattr(ai_svc.db_wfseq, "get_sequence_for_member_doc", lambda doc: {"id": 408})
    monkeypatch.setattr(ai_svc.db_wfseq, "get_effective_head", lambda seq: dict(items[0]))
    monkeypatch.setattr(ai_svc.db_wfseq, "get_sequence_items", lambda seq: [dict(row) for row in items])
    assert ai_svc.stored_hop_provider(ROOT, continuation_instruction_mode="auto_approved") == ("head", "Head", 1)


def test_override_beats_stored_and_stored_beats_request_default(monkeypatch, real_sequence):
    save_item()
    prepare_start(monkeypatch, providers=[
        provider("override", "Override"), provider(), provider("header-default", "Header Default")
    ])
    first = start_continuous(continuation_provider_overrides={"1": "override"})
    ai_svc.get_run_record(first["run_id"])["status"] = "finished"
    # Release the real lease before the second admission in this focused priority test.
    ai_svc.db_group_ai_leases.release(GROUP, first["run_id"])
    second = start_continuous(continuation_provider_overrides={})
    assert first["provider"]["id"] == "override"
    assert second["provider"]["id"] == PROVIDER


def test_inactive_stored_provider_falls_back_once_with_warning(monkeypatch, real_sequence, caplog):
    save_item(provider_id="inactive-provider", provider_display_name="Inactive Snapshot")
    prepare_start(monkeypatch, providers=[provider("header-default", "Header Default")])
    expected = (
        f"continuation hop provider fallback: inactive-provider not active for {ROOT} "
        "item_seq 1, falling back to header-default"
    )
    with caplog.at_level(logging.WARNING):
        result = start_continuous(continuation_provider_overrides=None)
    assert result["provider"]["id"] == "header-default"
    assert caplog.messages.count(expected) == 1


def test_old_null_row_falls_back_without_provider_warning(monkeypatch, real_sequence, caplog):
    decision_svc.edit_workflow_pending(ROOT, [{"type": "D", "label": "old row"}])
    prepare_start(monkeypatch, providers=[provider("header-default", "Header Default")])
    with caplog.at_level(logging.WARNING):
        result = start_continuous(continuation_provider_overrides=None)
    assert result["provider"]["id"] == "header-default"
    assert "continuation hop provider fallback" not in caplog.text


def test_legacy_get_route_returns_note_source_and_provider_contract(monkeypatch, client, real_sequence):
    save_item()
    monkeypatch.setattr(workflow_head_routes, "provider_view_of", lambda pid: {
        "readable": True, "providers": {PROVIDER: provider()}
    })
    response = client.get(f"/flowgate/api/v1/workflow/{ROOT}/sequence", headers=AUTH)
    assert response.status_code == 200, response.text
    row = response.json()["sequence"][0]
    assert {"note", "source_doc_id", "source_revision_no", "provider_id",
            "provider_display_name", "provider_registered"} <= set(row)
    assert (row["provider_id"], row["provider_registered"]) == (PROVIDER, True)


def test_plan_pour_gives_every_row_its_own_note_and_a_provider():
    plan = {
        "provider_candidates": [{"provider_id": PROVIDER, "display_name": PROVIDER_NAME}],
        "defaults": {"provider_id": PROVIDER, "note": "Default handoff"},
        "steps": [
            {"key": "N#1", "type": "N", "note": "N plan handoff"},
            {"key": "T#1", "type": "T"},
        ],
    }
    rows, _, uid = plan_svc.plan_to_rows(plan, WP, 8)
    rows, _ = plan_svc.attach_auto_rows(rows, next_uid=uid)
    by_type = {row["type"]: row for row in rows}

    # 0408 M0019 재반려 2: the report rows are steps of their own. With nothing written for
    # them in the plan they take the common note — never the instruction's sentence.
    assert {code: by_type[code]["note"] for code in ("N", "NR", "T", "TR")} == {
        "N": "N plan handoff", "NR": "Default handoff",
        "T": "Default handoff", "TR": "Default handoff",
    }
    assert {code: by_type[code]["provider_id"] for code in ("N", "NR", "T", "TR")} == {
        "N": PROVIDER, "NR": PROVIDER, "T": PROVIDER, "TR": PROVIDER,
    }
    assert by_type["T"]["note_source"] == "defaults"


def wire_hop_note_rows(monkeypatch, rows, *, head_seq=1):
    monkeypatch.setattr(ai_svc.db_wfseq, "get_sequence_for_member_doc", lambda doc: {"id": 408})
    monkeypatch.setattr(
        ai_svc.db_wfseq, "get_effective_head",
        lambda seq: dict(next(row for row in rows if row["item_seq"] == head_seq)),
    )
    monkeypatch.setattr(ai_svc.db_wfseq, "get_sequence_items", lambda seq: [dict(row) for row in rows])
    monkeypatch.setattr(
        ai_svc.invoke_mention_service, "prepend_messages_section",
        lambda mention, notes, locale: "|".join([*notes, mention]),
    )


def inject_hop_note(*, mode="auto_approved", overrides=None, default=None, fold=True):
    return ai_svc._inject_hop_notes(
        "MENTION", ROOT, default_note=default, note_overrides=overrides,
        instruction_mode=mode, locale="ko", fold_worker_item_seq=fold,
    )


@pytest.mark.parametrize(
    "mode,expected",
    [("auto_approved", "NR step note|MENTION"), ("ai_direct", "N step note|MENTION")],
)
def test_each_mode_delivers_the_note_of_the_row_it_actually_runs(monkeypatch, mode, expected):
    """0408 M0019 재반려 2 — 홉은 자기가 도는 줄의 멘트를 싣는다.

    [자동 승인]에서 도는 줄은 NR이고 [지시서 작성 후 진행]에서 도는 줄은 N이다. 두 줄은
    계획이 따로 적어 준 서로 다른 문장을 갖는다.
    """
    rows = [
        {"item_seq": 1, "type": "N", "note": "N step note"},
        {"item_seq": 2, "type": "NR", "note": "NR step note"},
    ]
    wire_hop_note_rows(monkeypatch, rows)
    assert inject_hop_note(mode=mode) == expected


def test_report_row_never_borrows_the_instruction_note(monkeypatch):
    """The row that runs has no note of its own — and stays silent rather than speaking N's."""
    rows = [
        {"item_seq": 1, "type": "N", "note": "instruction only"},
        {"item_seq": 2, "type": "NR", "note": ""},
    ]
    wire_hop_note_rows(monkeypatch, rows)
    assert inject_hop_note(mode="auto_approved") == "MENTION"
    assert inject_hop_note(mode="ai_direct") == "instruction only|MENTION"


def test_hop_note_prefers_worker_row_over_paired_row(monkeypatch):
    rows = [
        {"item_seq": 1, "type": "N", "note": "paired instruction"},
        {"item_seq": 2, "type": "NR", "note": "worker report"},
    ]
    wire_hop_note_rows(monkeypatch, rows)
    assert inject_hop_note() == "worker report|MENTION"


@pytest.mark.parametrize(
    "overrides,expected",
    [
        ({"2": ""}, "MENTION"),
        ({}, "stored report|MENTION"),
        ({"2": "report override"}, "report override|MENTION"),
        # A key for the instruction row is not this hop's key and changes nothing.
        ({"1": "instruction override"}, "stored report|MENTION"),
    ],
)
def test_hop_note_overrides_are_keyed_by_the_running_row(monkeypatch, overrides, expected):
    rows = [
        {"item_seq": 1, "type": "N", "note": "stored instruction"},
        {"item_seq": 2, "type": "NR", "note": "stored report"},
    ]
    wire_hop_note_rows(monkeypatch, rows)
    assert inject_hop_note(overrides=overrides) == expected


def test_common_and_step_hop_notes_keep_the_existing_order(monkeypatch):
    rows = [
        {"item_seq": 1, "type": "T", "note": ""},
        {"item_seq": 2, "type": "TR", "note": "step handoff"},
    ]
    wire_hop_note_rows(monkeypatch, rows)
    assert inject_hop_note(default="common handoff") == "common handoff|step handoff|MENTION"


def test_tsr_is_not_a_note_candidate_for_ts(monkeypatch):
    rows = [
        {"item_seq": 1, "type": "TS", "note": ""},
        {"item_seq": 2, "type": "TSR", "note": "must not leak"},
    ]
    wire_hop_note_rows(monkeypatch, rows)
    assert inject_hop_note() == "MENTION"


# 0408 M0019 재반려 3 — "문서에서 멘트와 프로바이더를 변경했는데 왜 다이얼로그에 적용되지 않는거지?"
# The dialog reads the plan's projection onto the live sequence. L0010 §2.5 counted "the Nth
# row of type X" over the whole sequence, so a finished N/NR pair from an earlier round stole
# the mapping from the pair this plan actually poured.
PLAN_STEPS_PAIR = [
    {"key": "N#1", "type": "N", "ordinal": 1, "pair_key": "NR#1", "note": "N now", "provider_id": PROVIDER},
    {"key": "NR#1", "type": "NR", "ordinal": 1, "pair_key": "N#1", "note": "NR now", "provider_id": PROVIDER},
]
REGISTRY = {"providers": [{"id": PROVIDER, "name": PROVIDER_NAME, "enabled": True}]}


def _row(item_seq, type_, *, status="pending", source=None, note=""):
    return {
        "item_seq": item_seq, "type": type_, "label": type_, "status": status, "note": note,
        "source_doc_id": source, "result_doc_id": None if status == "pending" else "doc",
    }


def test_plan_maps_onto_the_rows_it_poured_not_an_older_finished_pair():
    items = [
        _row(2, "N", status="done"), _row(3, "NR", status="done"),
        _row(11, "N", source=WP), _row(12, "NR"),
    ]
    mapping = apply_svc.build_step_map(PLAN_STEPS_PAIR, items, WP)

    assert [(row["key"], row["item_seq"], row["status"]) for row in mapping] == [
        ("N#1", 11, "pending"), ("NR#1", 12, "pending"),
    ]
    projection = apply_svc.project(PLAN_STEPS_PAIR, mapping, items, "auto_approved", REGISTRY)
    # [자동 승인]: the report row runs, so it keeps its OWN sentence and the instruction's
    # folded value fills nothing.
    assert projection["note_overrides"] == {"12": "NR now"}
    assert projection["provider_overrides"] == {"12": PROVIDER}


def test_a_plan_that_never_poured_here_keeps_the_positional_rule():
    items = [_row(2, "N", status="done"), _row(3, "NR", status="done")]
    mapping = apply_svc.build_step_map(PLAN_STEPS_PAIR, items, WP)

    assert [(row["key"], row["item_seq"]) for row in mapping] == [("N#1", 2), ("NR#1", 3)]
    assert apply_svc.build_step_map(PLAN_STEPS_PAIR, items) == mapping


def test_nonfolded_single_hop_reads_the_head_row_only(monkeypatch):
    rows = [
        {"item_seq": 1, "type": "N", "note": ""},
        {"item_seq": 2, "type": "NR", "note": "report only"},
    ]
    wire_hop_note_rows(monkeypatch, rows)
    assert inject_hop_note(fold=False) == "MENTION"