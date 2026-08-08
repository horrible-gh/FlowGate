"""flowgate.default.0317 (R0001 -> NR0003 -> D0004 -> T0005 구현): per-document-type AI
provider assignment for the continuous chain.

Covers the storage/resolver/API contract:
  • save/get round-trip + doc_type normalization (upper) + full-replace/clear
  • validation (duplicate type, empty type, unknown/disabled provider -> 422 array)
  • resolve_doctype_provider (the hop decider): mapped+enabled -> id; unmapped/disabled -> None
  • FK cascade: a provider dropped from the routing chain drops its assignment
  • ai_invoke hop wiring: _prioritize_chain (배정 우선 + 폴백 tail) and
    _resolve_continuation_hop_provider (auto-approved N/T -> paired report assignment)

Uses TESTING=1 with a file-backed SQLite DB, mirroring test_ai_settings_api.py.
"""
from __future__ import annotations

import os
import sqlite3
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

os.environ["TESTING"] = "1"
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")

_SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SERVER_DIR))


@pytest.fixture(scope="module")
def test_db_path(tmp_path_factory):
    db_path = str(tmp_path_factory.mktemp("db") / "test_doctype_map.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    _migrations_dir = _SERVER_DIR / "sql" / "migrations" / "sqlite"
    for _sql_file in sorted(_migrations_dir.glob("*.sql")):
        try:
            conn.executescript(_sql_file.read_text(encoding="utf-8"))
        except sqlite3.OperationalError:
            pass
    conn.executescript(
        """
        INSERT OR IGNORE INTO projects(project_id,project_name,is_active,created_at,updated_at)
            VALUES('__SYSTEM__','[System]',1,datetime('now'),datetime('now')),
                  ('proj_001','TestProject',1,datetime('now'),datetime('now'));
        INSERT OR IGNORE INTO users(user_id,username,email,password,is_active,is_admin,first_login_required,created_at,updated_at)
            VALUES('usr_admin','admin','admin@test.com','hashed_pw',1,1,0,datetime('now'),datetime('now'));
    """
    )
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture(autouse=True)
def mock_db(test_db_path):
    class TestStore:
        def __init__(self, db_path):
            self._conn = sqlite3.connect(db_path, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA foreign_keys = ON")

        def _execute(self, sql, params=None):
            self._conn.execute(sql, params or [])
            self._conn.commit()

        def _fetch_one(self, sql, params=None):
            cur = self._conn.execute(sql, params or [])
            row = cur.fetchone()
            return dict(row) if row else None

        def _fetch_all(self, sql, params=None):
            cur = self._conn.execute(sql, params or [])
            return [dict(r) for r in cur.fetchall()]

        @contextmanager
        def transaction(self):
            yield self

    store = TestStore(test_db_path)
    import importlib

    import modules.flow_gate.db.connection as _conn
    _real_get_store = _conn.get_store
    _modules = [
        importlib.import_module(_name)
        for _name in (
            "modules.flow_gate.db.connection",
            "modules.flow_gate.db.system_settings",
            "modules.flow_gate.db.projects",
            "modules.flow_gate.db.ai_providers",
            "modules.flow_gate.db.ai_provider_doctype_map",
            "modules.flow_gate.rbac.decorators",
        )
    ]
    for _m in _modules:
        _m.get_store = lambda store=store: store
    try:
        yield store
    finally:
        for _m in _modules:
            _m.get_store = _real_get_store


@pytest.fixture(autouse=True)
def clean_tables(mock_db):
    mock_db._execute("DELETE FROM ai_provider_doctype_map")
    mock_db._execute("DELETE FROM ai_providers")
    mock_db._execute("DELETE FROM project_settings WHERE project_id = 'proj_001'")
    yield


def _cli(name="claude cli", command="claude -p", **kw):
    p = {
        "id": None, "name": name, "exec_type": "cli", "kind": "claude",
        "enabled": True, "cli_command": command,
        "api_base_url": None, "api_model": None, "api_key": None,
    }
    p.update(kw)
    return p


def _make_three_providers():
    """Register three enabled custom providers on proj_001; return their ids by name."""
    from modules.flow_gate.settings.ai_settings_service import save_project_settings

    res = save_project_settings(
        "proj_001", "custom",
        [_cli(name="Fable"), _cli(name="Opus"), _cli(name="GPT")],
        None, 0,
    )
    return {p["name"]: p["id"] for p in res["providers"]}


class TestSaveGet:
    def test_empty_initial_state(self):
        from modules.flow_gate.settings.ai_settings_service import get_doctype_providers

        _make_three_providers()
        res = get_doctype_providers("proj_001")
        assert res["ok"] is True
        assert res["assignments"] == []
        assert {p["name"] for p in res["providers"]} == {"Fable", "Opus", "GPT"}

    def test_save_roundtrip_and_normalizes_doctype(self):
        from modules.flow_gate.settings.ai_settings_service import (
            get_doctype_providers, save_doctype_providers,
        )

        ids = _make_three_providers()
        saved = save_doctype_providers("proj_001", [
            {"doc_type": "nr", "provider_id": ids["Fable"]},
            {"doc_type": "TR", "provider_id": ids["Opus"]},
        ])
        got = {a["doc_type"]: a["provider_id"] for a in saved["assignments"]}
        # doc_type stored upper-cased and readable back via GET.
        assert got == {"NR": ids["Fable"], "TR": ids["Opus"]}
        assert get_doctype_providers("proj_001")["assignments"] == saved["assignments"]

    def test_save_replaces_and_empty_clears(self):
        from modules.flow_gate.settings.ai_settings_service import (
            get_doctype_providers, save_doctype_providers,
        )

        ids = _make_three_providers()
        save_doctype_providers("proj_001", [{"doc_type": "NR", "provider_id": ids["Fable"]}])
        save_doctype_providers("proj_001", [{"doc_type": "TR", "provider_id": ids["GPT"]}])
        assert [a["doc_type"] for a in get_doctype_providers("proj_001")["assignments"]] == ["TR"]
        save_doctype_providers("proj_001", [])
        assert get_doctype_providers("proj_001")["assignments"] == []

    def test_unknown_project_raises_lookup(self):
        from modules.flow_gate.settings.ai_settings_service import get_doctype_providers

        with pytest.raises(LookupError):
            get_doctype_providers("does_not_exist")


class TestValidation:
    def test_duplicate_doctype_rejected(self):
        from modules.flow_gate.settings.ai_settings_service import (
            AiSettingsValidationError, save_doctype_providers,
        )

        ids = _make_three_providers()
        with pytest.raises(AiSettingsValidationError) as exc:
            save_doctype_providers("proj_001", [
                {"doc_type": "NR", "provider_id": ids["Fable"]},
                {"doc_type": "nr", "provider_id": ids["Opus"]},
            ])
        assert any(e["reason"] == "duplicate_doc_type" for e in exc.value.errors)

    def test_empty_doctype_rejected(self):
        from modules.flow_gate.settings.ai_settings_service import (
            AiSettingsValidationError, save_doctype_providers,
        )

        ids = _make_three_providers()
        with pytest.raises(AiSettingsValidationError) as exc:
            save_doctype_providers("proj_001", [{"doc_type": "  ", "provider_id": ids["Fable"]}])
        assert any(e["field"] == "doc_type" and e["reason"] == "required" for e in exc.value.errors)

    def test_unknown_provider_rejected(self):
        from modules.flow_gate.settings.ai_settings_service import (
            AiSettingsValidationError, save_doctype_providers,
        )

        _make_three_providers()
        with pytest.raises(AiSettingsValidationError) as exc:
            save_doctype_providers("proj_001", [{"doc_type": "NR", "provider_id": "aip_ghost"}])
        assert any(e["reason"] == "unknown_provider" for e in exc.value.errors)

    def test_disabled_provider_rejected(self):
        from modules.flow_gate.settings.ai_settings_service import (
            AiSettingsValidationError, save_doctype_providers, save_project_settings,
        )

        res = save_project_settings(
            "proj_001", "custom",
            [_cli(name="On"), _cli(name="Off", enabled=False)], None, 0,
        )
        off_id = next(p["id"] for p in res["providers"] if p["name"] == "Off")
        with pytest.raises(AiSettingsValidationError) as exc:
            save_doctype_providers("proj_001", [{"doc_type": "NR", "provider_id": off_id}])
        assert any(e["reason"] == "unknown_provider" for e in exc.value.errors)


class TestResolver:
    def test_mapped_enabled_returns_id(self):
        from modules.flow_gate.settings.ai_settings_service import (
            resolve_doctype_provider, save_doctype_providers,
        )

        ids = _make_three_providers()
        save_doctype_providers("proj_001", [{"doc_type": "TR", "provider_id": ids["Opus"]}])
        assert resolve_doctype_provider("proj_001", "TR") == ids["Opus"]
        # Case-insensitive on the query side too.
        assert resolve_doctype_provider("proj_001", "tr") == ids["Opus"]

    def test_unmapped_returns_none(self):
        from modules.flow_gate.settings.ai_settings_service import (
            resolve_doctype_provider, save_doctype_providers,
        )

        ids = _make_three_providers()
        save_doctype_providers("proj_001", [{"doc_type": "TR", "provider_id": ids["Opus"]}])
        assert resolve_doctype_provider("proj_001", "NR") is None
        assert resolve_doctype_provider("proj_001", "") is None

    def test_assignment_falls_back_to_none_when_provider_disabled_later(self):
        from modules.flow_gate.settings.ai_settings_service import (
            resolve_doctype_provider, save_doctype_providers, save_project_settings,
        )

        ids = _make_three_providers()
        save_doctype_providers("proj_001", [{"doc_type": "TR", "provider_id": ids["Opus"]}])
        # Disable Opus after the assignment; the resolver must degrade to default (None),
        # not pin a provider the run engine cannot launch (D0004 §3).
        save_project_settings(
            "proj_001", "custom",
            [_cli(name="Fable", id=ids["Fable"]),
             _cli(name="Opus", id=ids["Opus"], enabled=False),
             _cli(name="GPT", id=ids["GPT"])],
            None, 0,
        )
        assert resolve_doctype_provider("proj_001", "TR") is None


class TestFkCascade:
    def test_dropping_provider_drops_assignment(self):
        from modules.flow_gate.settings.ai_settings_service import (
            get_doctype_providers, save_doctype_providers, save_project_settings,
        )

        ids = _make_three_providers()
        save_doctype_providers("proj_001", [
            {"doc_type": "NR", "provider_id": ids["Fable"]},
            {"doc_type": "TR", "provider_id": ids["Opus"]},
        ])
        # Drop "Opus" from the routing chain entirely; its assignment must cascade away.
        save_project_settings(
            "proj_001", "custom",
            [_cli(name="Fable", id=ids["Fable"]), _cli(name="GPT", id=ids["GPT"])],
            None, 0,
        )
        remaining = {a["doc_type"] for a in get_doctype_providers("proj_001")["assignments"]}
        assert remaining == {"NR"}


class TestHopWiring:
    def test_prioritize_chain_orders_assigned_first_keeps_tail(self):
        from modules.flow_gate.services.ai_invoke_service import _prioritize_chain

        chain = [{"id": "a"}, {"id": "b"}, {"id": "c"}]
        assert [p["id"] for p in _prioritize_chain(chain, "c")] == ["c", "a", "b"]
        # Unknown provider leaves the chain untouched.
        assert _prioritize_chain(chain, "zzz") == chain

    def test_auto_approved_resolve_hop_maps_instruction_head_to_report_type(self, monkeypatch):
        from modules.flow_gate.services import ai_invoke_service as svc
        from modules.flow_gate.settings.ai_settings_service import save_doctype_providers

        ids = _make_three_providers()
        # Assign the report type TR (the worker deliverable paired with a T instruction head).
        save_doctype_providers("proj_001", [{"doc_type": "TR", "provider_id": ids["Opus"]}])

        monkeypatch.setattr(svc.db_wfseq, "get_sequence_for_member_doc", lambda _d: {"id": 1})
        # Effective head is the T instruction step; AUTO_REPORT_MAP folds T -> TR.
        monkeypatch.setattr(svc.db_wfseq, "get_effective_head", lambda _s: {"type": "T"})
        assert svc._resolve_continuation_hop_provider(
            "proj_001",
            "proj_001.default.0317.0001-R",
            continuation_instruction_mode="auto_approved",
        ) == ids["Opus"]

    def test_resolve_hop_none_when_no_sequence(self, monkeypatch):
        from modules.flow_gate.services import ai_invoke_service as svc

        monkeypatch.setattr(svc.db_wfseq, "get_sequence_for_member_doc", lambda _d: None)
        assert svc._resolve_continuation_hop_provider("proj_001", "x") is None

    def test_ai_direct_selected_item_seq_resolve_hop_maps_to_report_type(self, monkeypatch):
        # 0352 T0004 §2/§3.5: an ai_direct N/T head that IS in the auto-approve selection
        # folds to its paired report type for doc-type provider resolution too — matching
        # what auto_approved does for every N/T, just scoped to this one selected step.
        from modules.flow_gate.services import ai_invoke_service as svc
        from modules.flow_gate.settings.ai_settings_service import save_doctype_providers

        ids = _make_three_providers()
        save_doctype_providers("proj_001", [{"doc_type": "TR", "provider_id": ids["Opus"]}])

        monkeypatch.setattr(svc.db_wfseq, "get_sequence_for_member_doc", lambda _d: {"id": 1})
        monkeypatch.setattr(svc.db_wfseq, "get_effective_head",
                            lambda _s: {"type": "T", "item_seq": 3})
        assert svc._resolve_continuation_hop_provider(
            "proj_001",
            "proj_001.default.0317.0001-R",
            continuation_instruction_mode="ai_direct",
            continuation_auto_approve_item_seqs=[3],
        ) == ids["Opus"]

    def test_ai_direct_unselected_item_seq_resolve_hop_uses_own_type(self, monkeypatch):
        from modules.flow_gate.services import ai_invoke_service as svc
        from modules.flow_gate.settings.ai_settings_service import save_doctype_providers

        ids = _make_three_providers()
        # Assign the INSTRUCTION type itself (T), not the report — only reachable when the
        # head does NOT fold (i.e. is not in the selection).
        save_doctype_providers("proj_001", [{"doc_type": "T", "provider_id": ids["Fable"]}])

        monkeypatch.setattr(svc.db_wfseq, "get_sequence_for_member_doc", lambda _d: {"id": 1})
        monkeypatch.setattr(svc.db_wfseq, "get_effective_head",
                            lambda _s: {"type": "T", "item_seq": 1})
        assert svc._resolve_continuation_hop_provider(
            "proj_001",
            "proj_001.default.0317.0001-R",
            continuation_instruction_mode="ai_direct",
            continuation_auto_approve_item_seqs=[3],
        ) == ids["Fable"]


class TestPerHopRespawn:
    """0317 TR0011 (Q153 opt-1): the unmanned continuous chain re-spawns a worker per hop so
    each step re-resolves its OWN provider, instead of one provider process self-continuing
    through every hop (the rejection: 3 documents all "Anthropic Claude Sonnet 5"). These
    exercise the boundary handoff — has_active_run (engine vs copy-mention), the auto-resume
    queue, and _maybe_auto_resume_hop's re-spawn gating."""

    @pytest.fixture(autouse=True)
    def _clean_registries(self):
        from modules.flow_gate.services import ai_invoke_service as svc

        for _lock, _reg in ((svc._runs_lock, svc._runs), (svc._auto_resume_lock, svc._auto_resume)):
            with _lock:
                _reg.clear()
        yield
        for _lock, _reg in ((svc._runs_lock, svc._runs), (svc._auto_resume_lock, svc._auto_resume)):
            with _lock:
                _reg.clear()

    def test_has_active_run_distinguishes_engine_vs_copy_mention(self):
        from modules.flow_gate.services import ai_invoke_service as svc

        gid = "flowgate.default.0317"
        # No engine run -> copy-mention flow keeps next_token self-continuation.
        assert svc.has_active_run(gid) is False
        assert svc.has_active_run(None) is False
        with svc._runs_lock:
            svc._runs["run_x"] = {"run_id": "run_x", "group_id": gid, "status": "running"}
        assert svc.has_active_run(gid) is True
        # A finished run does not count as active (the re-spawn's start_run guard is clear).
        with svc._runs_lock:
            svc._runs["run_x"]["status"] = "finished"
        assert svc.has_active_run(gid) is False

    def test_auto_resume_queue_peek_then_pop(self):
        from modules.flow_gate.services import ai_invoke_service as svc

        gid = "flowgate.default.0317"
        assert svc.peek_auto_resume(gid) is None
        svc.request_auto_resume(gid, {"doc_ref": "d", "target_seq": 3})
        # peek does NOT consume (settle judge relies on this); pop does.
        assert svc.peek_auto_resume(gid)["target_seq"] == 3
        assert svc.peek_auto_resume(gid)["target_seq"] == 3
        assert svc.pop_auto_resume(gid)["doc_ref"] == "d"
        assert svc.peek_auto_resume(gid) is None

    def test_maybe_resume_spawns_next_hop_carrying_overrides_and_mode(self, monkeypatch):
        from modules.flow_gate.services import ai_invoke_service as svc

        gid = "flowgate.default.0317"
        calls = []
        monkeypatch.setattr(svc, "_spawn_auto_resume", lambda g, p: calls.append((g, p)))
        svc.request_auto_resume(gid, {
            "doc_ref": "flowgate.default.0317.0001-R", "target_seq": 3,
            "review_mode": False, "instruction_mode": "ai_direct",
            "locale": "ko", "issued_to": "usr_admin", "api_base_url": "http://x/api/v1",
        })
        run = {
            "group_id": gid, "end_reason": "exited", "cancel_event": None,
            "continuation_provider_overrides": {"2": "aip_opus"},
        }
        svc._maybe_auto_resume_hop(run)
        assert len(calls) == 1
        g, pending = calls[0]
        assert g == gid
        # [지시서 작성 후 진행](ai_direct) is preserved per hop -> N/T is NOT auto-approved away
        # (the first rejection: 단계 건너뜀).
        assert pending["instruction_mode"] == "ai_direct"
        # The session per-step override map rides the run forward, hop to hop.
        assert pending["provider_overrides"] == {"2": "aip_opus"}
        assert svc.peek_auto_resume(gid) is None  # queue consumed

    def test_maybe_resume_skips_and_drops_queue_on_unclean_exit(self, monkeypatch):
        from modules.flow_gate.services import ai_invoke_service as svc

        gid = "flowgate.default.0317"
        calls = []
        monkeypatch.setattr(svc, "_spawn_auto_resume", lambda g, p: calls.append((g, p)))
        svc.request_auto_resume(gid, {"doc_ref": "d", "target_seq": 3})
        # A timeout / cancel / provider-exhaustion hop is a real stop: do not continue...
        svc._maybe_auto_resume_hop({"group_id": gid, "end_reason": "timeout", "cancel_event": None})
        assert calls == []
        # ...and the stale queue is consumed so it cannot fire on a later run.
        assert svc.peek_auto_resume(gid) is None

    def test_maybe_resume_noop_without_queue(self, monkeypatch):
        from modules.flow_gate.services import ai_invoke_service as svc

        calls = []
        monkeypatch.setattr(svc, "_spawn_auto_resume", lambda g, p: calls.append((g, p)))
        svc._maybe_auto_resume_hop({"group_id": "g", "end_reason": "exited", "cancel_event": None})
        assert calls == []

    def test_maybe_resume_carries_base_provider_id(self, monkeypatch):
        # 0317 T0013 결함 ③: the header default pin must ride the run forward so an
        # override-less re-spawned hop still runs on the user's chosen default.
        from modules.flow_gate.services import ai_invoke_service as svc

        gid = "flowgate.default.0317"
        calls = []
        monkeypatch.setattr(svc, "_spawn_auto_resume", lambda g, p: calls.append((g, p)))
        svc.request_auto_resume(gid, {
            "doc_ref": "flowgate.default.0317.0001-R", "target_seq": 3,
            "review_mode": False, "instruction_mode": "auto_approved",
            "locale": "ko", "issued_to": "usr_admin", "api_base_url": "http://x/api/v1",
        })
        run = {
            "group_id": gid, "end_reason": "exited", "cancel_event": None,
            "continuation_provider_overrides": {"2": "aip_opus"},
            "continuation_base_provider_id": "aip_default",
        }
        svc._maybe_auto_resume_hop(run)
        assert len(calls) == 1
        _g, pending = calls[0]
        assert pending["base_provider_id"] == "aip_default"
        assert pending["provider_overrides"] == {"2": "aip_opus"}

    def test_spawn_passes_base_provider_id_and_overrides_to_start_run(self, monkeypatch):
        # 0317 T0013 결함 ③: _spawn_auto_resume must hand the base pin to start_run as
        # provider_id so an override-less step resolves to it (not doc-type / default chain).
        from modules.flow_gate.services import ai_invoke_service as svc

        captured: dict = {}
        monkeypatch.setattr(svc, "start_run", lambda **kw: captured.update(kw))
        svc._spawn_auto_resume("flowgate.default.0317", {
            "doc_ref": "flowgate.default.0317.0001-R", "target_seq": 4,
            "review_mode": False, "instruction_mode": "auto_approved",
            "locale": "ko", "issued_to": "usr_admin", "api_base_url": "http://x/api/v1",
            "provider_overrides": {"2": "aip_opus"}, "base_provider_id": "aip_default",
        })
        assert captured["provider_id"] == "aip_default"
        assert captured["continuation_provider_overrides"] == {"2": "aip_opus"}
        # And the instruction mode is still preserved across the re-spawn (결함 ② stays fixed).
        assert captured["continuation_instruction_mode"] == "auto_approved"


class TestPerStepOverrideOffByOne:
    """0317 T0013 auto-approved regression: the override lookup folds an instruction
    head to its paired report row before keying the override map. _expand_auto_reports lays
    each report step (TR) right after its instruction step (T), each with its OWN item_seq;
    at a hop boundary the effective head is the T slot, but the worker fills the following TR
    slot — the row ContinuousWorkDialog shows and the user keys the override on. Without the
    fold the lookup uses the T slot's seq and every report-row override misses ("매번 안 됨")."""

    # Three T/TR hop pairs: instruction on odd item_seq, its report on the next even item_seq.
    _ITEMS = [
        {"item_seq": 1, "type": "T"}, {"item_seq": 2, "type": "TR"},
        {"item_seq": 3, "type": "T"}, {"item_seq": 4, "type": "TR"},
        {"item_seq": 5, "type": "T"}, {"item_seq": 6, "type": "TR"},
    ]

    def _wire(self, monkeypatch, head_item_seq, head_type="T"):
        from modules.flow_gate.services import ai_invoke_service as svc

        monkeypatch.setattr(svc.db_wfseq, "get_sequence_for_member_doc", lambda _d: {"id": 1})
        monkeypatch.setattr(svc.db_wfseq, "get_effective_head",
                            lambda _s: {"item_seq": head_item_seq, "type": head_type})
        monkeypatch.setattr(svc.db_wfseq, "get_sequence_items", lambda _s: list(self._ITEMS))
        return svc

    def test_auto_approved_report_row_override_is_found_via_head_fold(self, monkeypatch):
        svc = self._wire(monkeypatch, head_item_seq=1)  # head = T@1 → worker fills TR@2
        chain = [{"id": "aip_fable"}, {"id": "aip_opus"}]
        # Keyed on the visible TR row (item_seq 2). Pre-fix this missed (lookup used seq 1).
        assert svc._resolve_continuation_hop_override(
            "flowgate.default.0317.0001-R",
            {"2": "aip_opus"},
            chain,
            continuation_instruction_mode="auto_approved",
        ) == "aip_opus"

    def test_auto_approved_instruction_row_override_does_not_resolve(self, monkeypatch):
        svc = self._wire(monkeypatch, head_item_seq=1)
        chain = [{"id": "aip_fable"}, {"id": "aip_opus"}]
        # The instruction slot is not the worker's deliverable, so an override on it is not
        # applied — the fold targets the report row the worker actually produces.
        assert svc._resolve_continuation_hop_override(
            "flowgate.default.0317.0001-R",
            {"1": "aip_opus"},
            chain,
            continuation_instruction_mode="auto_approved",
        ) is None

    def test_three_hops_resolve_three_distinct_providers(self, monkeypatch):
        # The literal user ask — "각 단계별로 다른 프로바이더" — keyed on the three TR rows,
        # resolved hop after hop as start_run would at each re-spawn.
        overrides = {"2": "aip_fable", "4": "aip_opus", "6": "aip_gpt"}
        chain = [{"id": "aip_fable"}, {"id": "aip_opus"}, {"id": "aip_gpt"}]
        picked = []
        for head_seq in (1, 3, 5):
            svc = self._wire(monkeypatch, head_item_seq=head_seq)
            picked.append(svc._resolve_continuation_hop_override(
                "flowgate.default.0317.0001-R",
                overrides,
                chain,
                continuation_instruction_mode="auto_approved",
            ))
        assert picked == ["aip_fable", "aip_opus", "aip_gpt"]

    def test_non_instruction_head_uses_its_own_seq(self, monkeypatch):
        # A head with no AUTO_REPORT_MAP pairing (already a report/standalone slot) keys on its
        # own item_seq — no fold.
        svc = self._wire(monkeypatch, head_item_seq=2, head_type="TR")
        chain = [{"id": "aip_fable"}, {"id": "aip_opus"}]
        assert svc._resolve_continuation_hop_override(
            "flowgate.default.0317.0001-R",
            {"2": "aip_opus"},
            chain,
            continuation_instruction_mode="auto_approved",
        ) == "aip_opus"

    def test_disabled_override_degrades_to_none(self, monkeypatch):
        svc = self._wire(monkeypatch, head_item_seq=1)
        chain = [{"id": "aip_fable"}]  # aip_opus no longer enabled
        assert svc._resolve_continuation_hop_override(
            "flowgate.default.0317.0001-R",
            {"2": "aip_opus"},
            chain,
            continuation_instruction_mode="auto_approved",
        ) is None
