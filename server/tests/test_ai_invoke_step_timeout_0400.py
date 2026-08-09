"""flowgate.default.0400 T0006 (R0001 / M0005): ContinuousWorkDialog's 기본 설정 탭 gained a
시간 section — the previously-fixed per-hop budget (HOP_TIMEOUT_SEC, always 60 minutes) is now
a user pick from a small fixed list (30/45/60/90/120/180/240 minutes), forwarded as
continuation_step_timeout_sec.

Covers:
  * _resolve_timeout_sec: an in-range pick wins; None / out-of-range / non-continuous mode all
    fall back to the old fixed HOP_TIMEOUT_SEC — nothing about the single-run formula changes.
  * POST /api/v1/ai-invoke/start: out-of-range values are rejected 422, an in-range value
    reaches ai_invoke_service.start_run unchanged, and omitting the field keeps working
    exactly as before (backward compatible default).
  * start_run stores the pick on the run dict, and the run's timeout_sec/deadline_at reflect
    it end to end (real subprocess, same shape as test_ai_invoke_continuation_note_0346.py).
  * _maybe_auto_resume_hop / _spawn_auto_resume: the pick rides the run forward across a hop
    re-spawn — the "first hop only" shape is the easiest way to get a session-scoped field
    wrong (0317 T0013 재발 방지 shape, mirrored from the provider-override/note coverage).

Pause/resume persistence (the paused-chain row round trip) is covered in
test_ai_invoke_pause_resume_0252.py, which already owns that fixture and pattern.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")

_SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SERVER_DIR))

from modules.flow_gate.api.v1 import ai_invoke_routes  # noqa: E402
from modules.flow_gate.services import ai_invoke_service as svc  # noqa: E402

PY = sys.executable
ROOT_DOC = "flowgate.default.0400.0001-R"
GROUP = "flowgate.default.0400"


# ── _resolve_timeout_sec (unit) ───────────────────────────────────────────────

class TestResolveTimeoutSec:
    def test_in_range_pick_wins_for_continuous(self):
        assert svc._resolve_timeout_sec("continuous", 1, False, 14400) == 14400

    @pytest.mark.parametrize("pick", [30 * 60, 45 * 60, 60 * 60, 90 * 60, 120 * 60, 180 * 60, 240 * 60])
    def test_every_dialog_option_is_accepted(self, pick):
        assert svc._resolve_timeout_sec("continuous", 1, False, pick) == pick

    def test_none_falls_back_to_hop_timeout(self):
        assert svc._resolve_timeout_sec("continuous", 1, False, None) == svc.HOP_TIMEOUT_SEC

    def test_out_of_range_falls_back_to_hop_timeout(self):
        assert svc._resolve_timeout_sec("continuous", 1, False, 60) == svc.HOP_TIMEOUT_SEC
        assert svc._resolve_timeout_sec("continuous", 1, False, 999999) == svc.HOP_TIMEOUT_SEC

    def test_single_mode_ignores_the_pick(self):
        # The single-run formula (RUN_TIMEOUT_BASE_SEC × docs_target, capped) is untouched —
        # a stray continuous-only field on a single request must not perturb it.
        assert svc._resolve_timeout_sec("single", 2, False, 14400) == min(
            svc.RUN_TIMEOUT_BASE_SEC * 2, svc.RUN_TIMEOUT_CAP_SEC
        )

    def test_target_to_end_still_honours_a_continuous_pick(self):
        # target_to_end only matters for the single-run formula below — a continuous run is
        # always ONE hop (0317 TR0011), pre-decision or not, so its budget is still the
        # per-hop pick, never RUN_TIMEOUT_CAP_SEC.
        assert svc._resolve_timeout_sec("continuous", 0, True, 1800) == 1800
        assert svc._resolve_timeout_sec("continuous", 0, True, None) == svc.HOP_TIMEOUT_SEC


# ── POST /api/v1/ai-invoke/start (route-level) ────────────────────────────────

_ITEMS = [
    {"id": 1, "item_seq": 1, "type": "T", "label": "작업지시",
     "result_doc_id": None, "result_doc_review_status": None},
    {"id": 2, "item_seq": 2, "type": "TR", "label": "작업레포트",
     "result_doc_id": None, "result_doc_review_status": None},
]


@pytest.fixture
def client(monkeypatch) -> TestClient:
    from modules.flow_gate.db import workflow_sequences as db_wfseq

    app = FastAPI()
    app.include_router(ai_invoke_routes.router)
    monkeypatch.setattr(
        ai_invoke_routes, "verify_bearer",
        lambda request: {"_is_user_jwt": True, "issued_to": "usr_admin", "is_admin": True},
    )
    monkeypatch.setattr(ai_invoke_routes.db_projects, "get_by_id", lambda pid: {"project_id": pid})
    monkeypatch.setattr(db_wfseq, "get_sequence_for_member_doc", lambda doc_id: {"id": 1})
    monkeypatch.setattr(db_wfseq, "get_sequence_items", lambda seq_id: list(_ITEMS))
    return TestClient(app, raise_server_exceptions=False)


def _body(**over):
    body = {
        "project": "flowgate", "module": "default", "group": "0400",
        "doc_ref": ROOT_DOC, "action_scope": "new", "mode": "continuous",
        "continuation_target_seq": 2,
    }
    body.update(over)
    return body


def _post(client, **over):
    return client.post("/api/v1/ai-invoke/start", json=_body(**over),
                       headers={"Authorization": "Bearer tok"})


class TestStartRouteValidation:
    def test_below_minimum_is_rejected_422(self, client, monkeypatch):
        monkeypatch.setattr(ai_invoke_routes.ai_invoke_service, "start_run",
                            lambda **kw: pytest.fail("must not reach start_run"))
        resp = _post(client, continuation_step_timeout_sec=60)
        assert resp.status_code == 422
        assert resp.json()["errors"][0]["loc"] == "continuation_step_timeout_sec"

    def test_above_maximum_is_rejected_422(self, client, monkeypatch):
        monkeypatch.setattr(ai_invoke_routes.ai_invoke_service, "start_run",
                            lambda **kw: pytest.fail("must not reach start_run"))
        resp = _post(client, continuation_step_timeout_sec=999999)
        assert resp.status_code == 422
        assert resp.json()["errors"][0]["loc"] == "continuation_step_timeout_sec"

    def test_in_range_value_reaches_start_run(self, client, monkeypatch):
        captured = {}

        def _fake_start_run(**kw):
            captured.update(kw)
            return {"run_id": "aiv_1", "status": "running"}
        monkeypatch.setattr(ai_invoke_routes.ai_invoke_service, "start_run", _fake_start_run)

        resp = _post(client, continuation_step_timeout_sec=14400)
        assert resp.status_code == 200
        assert captured["continuation_step_timeout_sec"] == 14400

    def test_omitted_field_defaults_to_none_and_still_starts(self, client, monkeypatch):
        # Backward compatible: an older client (or any non-ContinuousWorkDialog entry point)
        # that never sends this field must keep working exactly as before.
        captured = {}

        def _fake_start_run(**kw):
            captured.update(kw)
            return {"run_id": "aiv_1", "status": "running"}
        monkeypatch.setattr(ai_invoke_routes.ai_invoke_service, "start_run", _fake_start_run)

        resp = _post(client)
        assert resp.status_code == 200
        assert captured["continuation_step_timeout_sec"] is None


# ── start_run end-to-end (real subprocess, same shape as the 0346 note tests) ─

def _provider(pid="aip_test01", cmd=None):
    return {
        "id": pid, "name": "cli-1", "exec_type": "cli", "kind": "claude",
        "enabled": True, "cli_command": cmd,
        "api_base_url": None, "api_model": None, "api_key_set": False, "api_key_hint": None,
    }


class FakeWfseq:
    def __init__(self, head_item_seq=1, items=None):
        self.sequence = {"id": 1}
        self.head_item_seq = head_item_seq
        self.items = items if items is not None else list(_ITEMS)

    def get_sequence_for_member_doc(self, doc_id):
        return self.sequence

    def get_sequence_by_doc_id(self, doc_id):
        return self.sequence

    def get_sequence_items(self, seq_id):
        return list(self.items)

    def get_effective_head(self, seq_id):
        return next((dict(i) for i in self.items if i["item_seq"] == self.head_item_seq), None)


@pytest.fixture
def run_env(monkeypatch, tmp_path):
    wfseq = FakeWfseq()
    chain_holder = {"providers": [], "source": "system", "registered_count": 0}

    monkeypatch.setattr(svc, "ORACLE_SETTLE_SEC", 0)
    monkeypatch.setattr(svc.db_docs, "get_group_max_seq", lambda group_id: 2)
    monkeypatch.setattr(svc.db_docs, "get_documents_by_group_id", lambda group_id: [])
    monkeypatch.setattr(svc.db_docs, "get_by_id", lambda doc_id: {"doc_id": doc_id, "branch": "main"})
    monkeypatch.setattr(svc.db_wfseq, "get_sequence_for_member_doc", wfseq.get_sequence_for_member_doc)
    monkeypatch.setattr(svc.db_wfseq, "get_sequence_by_doc_id", wfseq.get_sequence_by_doc_id)
    monkeypatch.setattr(svc.db_wfseq, "get_sequence_items", wfseq.get_sequence_items)
    monkeypatch.setattr(svc.db_wfseq, "get_effective_head", wfseq.get_effective_head)
    monkeypatch.setattr(svc.db_projects, "get_by_id", lambda pid: {"project_name": "testproj"})
    monkeypatch.setattr(
        svc.ai_settings_service, "resolve_effective", lambda pid: {"ok": True, **chain_holder}
    )
    monkeypatch.setattr(svc.ai_settings_service, "get_provider_secret", lambda scope, pid: None)
    monkeypatch.setattr(
        svc.token_service, "issue",
        lambda **kw: {
            "raw_token": "tok_raw_test", "token_id": "tok_20260809_000001",
            "expires_at": "2026-08-10T00:00:00+00:00",
            "scratch_dir": str(tmp_path / "tokwork"),
        },
    )
    monkeypatch.setattr(svc.token_service, "revoke", lambda *a, **kw: None)
    monkeypatch.setattr(svc.storage_paths, "get_storage_root", lambda *a, **kw: tmp_path / "storage")
    src_root = tmp_path / "srcroot"
    src_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        svc.storage_paths, "resolve_project_src_root", lambda pid, branch, *, group_id: src_root
    )
    monkeypatch.setattr(svc.storage_paths, "to_storage_relative", lambda path, project=None: str(path))
    monkeypatch.setattr(svc, "_runs", {})
    monkeypatch.setattr(svc, "_broadcast", lambda run, event_type, payload: None)
    return {"wfseq": wfseq, "chain": chain_holder, "tmp": tmp_path}


def _wait_finished(run_id, timeout=30.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        run = svc.get_run_record(run_id)
        if run and run["status"] == "finished":
            return run
        time.sleep(0.05)
    raise AssertionError(f"run {run_id} did not finish within {timeout}s")


def _start(env, *, step_timeout_sec=None, target_seq=2):
    cmd = f'"{PY}" -c "import sys; sys.stdin.read()"'
    env["chain"]["providers"] = [_provider(cmd=cmd)]
    env["chain"]["registered_count"] = 1
    return svc.start_run(
        project_id="flowgate",
        module="default",
        group_id=GROUP,
        doc_ref=ROOT_DOC,
        action_scope="new",
        mode="continuous",
        continuation_target_seq=target_seq,
        continuation_review_mode=False,
        continuation_instruction_mode="auto_approved",
        continuation_locale="ko",
        issued_to="usr_admin",
        api_base_url="http://127.0.0.1:1/flowgate/api/v1",
        mention_builder=lambda raw, scratch: "## 지시\n문서를 작성하세요.\n",
        continuation_step_timeout_sec=step_timeout_sec,
    )


class TestStartRunEndToEnd:
    def test_picked_budget_becomes_the_runs_timeout_sec(self, run_env):
        res = _start(run_env, step_timeout_sec=10800)
        assert res["timeout_sec"] == 10800
        run = _wait_finished(res["run_id"])
        assert run["timeout_sec"] == 10800
        assert run["continuation_step_timeout_sec"] == 10800

    def test_no_pick_falls_back_to_hop_timeout_sec(self, run_env):
        res = _start(run_env, step_timeout_sec=None)
        assert res["timeout_sec"] == svc.HOP_TIMEOUT_SEC
        run = _wait_finished(res["run_id"])
        assert run["continuation_step_timeout_sec"] is None

    def test_pick_is_ignored_on_a_single_run(self, run_env):
        cmd = f'"{PY}" -c "import sys; sys.stdin.read()"'
        run_env["chain"]["providers"] = [_provider(cmd=cmd)]
        run_env["chain"]["registered_count"] = 1
        res = svc.start_run(
            project_id="flowgate", module="default", group_id=GROUP,
            doc_ref=ROOT_DOC, action_scope="new", mode="single",
            continuation_target_seq=None, continuation_review_mode=False,
            continuation_instruction_mode=None, continuation_locale=None,
            issued_to="usr_admin", api_base_url="http://127.0.0.1:1/flowgate/api/v1",
            mention_builder=lambda raw, scratch: "## 지시\n문서를 작성하세요.\n",
            continuation_step_timeout_sec=14400,
        )
        assert res["timeout_sec"] != 14400
        run = _wait_finished(res["run_id"])
        assert run["continuation_step_timeout_sec"] is None


# ── auto-resume handoff (mirrors test_ai_invoke_continuation_note_0346's shape) ─

class TestStepTimeoutRunDictAndAutoResume:
    @pytest.fixture(autouse=True)
    def _clean_registries(self):
        for _lock, _reg in ((svc._runs_lock, svc._runs), (svc._auto_resume_lock, svc._auto_resume)):
            with _lock:
                _reg.clear()
        yield
        for _lock, _reg in ((svc._runs_lock, svc._runs), (svc._auto_resume_lock, svc._auto_resume)):
            with _lock:
                _reg.clear()

    def test_maybe_resume_carries_step_timeout_forward(self, monkeypatch):
        calls = []
        monkeypatch.setattr(svc, "_spawn_auto_resume", lambda g, p: calls.append((g, p)))
        svc.request_auto_resume(GROUP, {
            "doc_ref": ROOT_DOC, "target_seq": 2,
            "review_mode": False, "instruction_mode": "auto_approved",
            "locale": "ko", "issued_to": "usr_admin", "api_base_url": "http://x/api/v1",
        })
        run = {
            "group_id": GROUP, "end_reason": "exited", "cancel_event": None,
            "continuation_provider_overrides": None,
            "continuation_note_overrides": None,
            "continuation_default_note": None,
            "continuation_step_timeout_sec": 14400,
        }
        svc._maybe_auto_resume_hop(run)
        assert len(calls) == 1
        _g, pending = calls[0]
        assert pending["step_timeout_sec"] == 14400

    def test_spawn_passes_step_timeout_to_start_run(self, monkeypatch):
        captured: dict = {}
        monkeypatch.setattr(svc, "start_run", lambda **kw: captured.update(kw))
        svc._spawn_auto_resume(GROUP, {
            "doc_ref": ROOT_DOC, "target_seq": 2,
            "review_mode": False, "instruction_mode": "auto_approved",
            "locale": "ko", "issued_to": "usr_admin", "api_base_url": "http://x/api/v1",
            "step_timeout_sec": 14400,
        })
        assert captured["continuation_step_timeout_sec"] == 14400

    def test_spawn_without_a_pick_forwards_none(self, monkeypatch):
        # A pending hop queued before this feature (or with no pick made) must not crash
        # _spawn_auto_resume — pending.get() degrades cleanly to None.
        captured: dict = {}
        monkeypatch.setattr(svc, "start_run", lambda **kw: captured.update(kw))
        svc._spawn_auto_resume(GROUP, {
            "doc_ref": ROOT_DOC, "target_seq": 2,
            "review_mode": False, "instruction_mode": "auto_approved",
            "locale": "ko", "issued_to": "usr_admin", "api_base_url": "http://x/api/v1",
        })
        assert captured["continuation_step_timeout_sec"] is None


# ── migration 079 (schema) ─────────────────────────────────────────────────────

def test_migration_079_adds_step_timeout_column(test_db):
    cols = {
        row["name"]: row
        for row in test_db.execute("PRAGMA table_info(ai_invoke_paused_chains)").fetchall()
    }
    assert "continuation_step_timeout_sec" in cols
    assert cols["continuation_step_timeout_sec"]["notnull"] == 0
