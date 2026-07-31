"""flowgate.default.0252 T: 실행 미니플레이어 — 경계 정지/재개/전역 활성 목록.

Covers the server half of D0007/P0008/L0009/DB0010:
  * pause_run admission (§2.1): continuous-only 422 guard, snapshot upsert, idempotency,
    pause_requested surfaced by get_status.
  * end_reason "user_paused" classification and the paused row's survival across the
    boundary stop, vs. the chain-termination cleanup for every other finish.
  * resume_chain ordering (§2.4): active-run 409, consumed-row 409 (resume_conflict),
    nothing_to_resume self-cleaning, row restore when the launch fails.
  * active_all (§2.8): issued_to scoping + paused rows with live pending_q derivation.
  * inbox boundary hook (§2.2): pause row withholds the next token before advance,
    target-reached termination deletes the row.

DB / settings / token layers are monkeypatched exactly like test_ai_invoke_0187 —
no database required; the paused store is a dict-backed fake with the same contract.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")

_SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SERVER_DIR))

from fastapi import HTTPException  # noqa: E402

from modules.flow_gate.db import ai_invoke_paused_chains as db_paused  # noqa: E402
from modules.flow_gate.services import ai_invoke_service as svc  # noqa: E402
from modules.flow_gate.services import workflow_decision_service as wds  # noqa: E402

PY = sys.executable
GROUP = "flowgate.default.0252"
DOC_REF = "flowgate.default.0252.0001-R"


def _provider(cmd, pid="aip_test01", name="cli-1"):
    return {
        "id": pid, "name": name, "exec_type": "cli", "kind": "claude",
        "enabled": True, "cli_command": cmd, "api_base_url": None,
        "api_model": None, "api_key_set": False, "api_key_hint": None,
    }


class FakePausedStore:
    """Dict-backed stand-in honouring the ai_invoke_paused_chains contract."""

    def __init__(self):
        self.rows: dict[str, dict] = {}

    def upsert(self, *, group_id, doc_ref, paused_by, paused_at,
               continuation_target_seq, docs_target, docs_reached,
               stop_kind="user", stop_code=None, stop_run_id=None,
               stop_last_message_excerpt=None):
        self.rows[group_id] = {
            "id": 1, "group_id": group_id, "doc_ref": doc_ref, "mode": "continuous",
            "paused_by": paused_by, "paused_at": paused_at,
            "continuation_target_seq": continuation_target_seq,
            "docs_target": docs_target, "docs_reached": docs_reached,
            # group 0359 DB0008 Q7 / L0007 §2.8: who parked this chain and why.
            "stop_kind": stop_kind, "stop_code": stop_code, "stop_run_id": stop_run_id,
            "stop_last_message_excerpt": stop_last_message_excerpt,
        }

    def get_by_group(self, group_id):
        row = self.rows.get(group_id)
        return dict(row) if row else None

    def exists(self, group_id):
        return group_id in self.rows

    def delete_and_return(self, group_id):
        row = self.rows.pop(group_id, None)
        return dict(row) if row else None

    def delete_by_group(self, group_id):
        self.rows.pop(group_id, None)

    def list_by_user(self, user_id):
        return [dict(r) for r in self.rows.values() if r["paused_by"] == user_id]


class FakeWfseq:
    def __init__(self):
        self.sequence: dict | None = {"id": 1}
        self.items: list[dict] = [
            {"item_seq": 1, "type": "D", "result_doc_id": "d-1",
             "result_doc_review_status": "approved"},
            {"item_seq": 2, "type": "P", "result_doc_id": None,
             "result_doc_review_status": None},
            {"item_seq": 3, "type": "L", "result_doc_id": None,
             "result_doc_review_status": None},
        ]

    def get_sequence_for_member_doc(self, doc_id):
        return self.sequence

    def get_sequence_by_doc_id(self, doc_id):
        return self.sequence

    def get_sequence_items(self, seq_id):
        return list(self.items)


class FakeDocs:
    def __init__(self, baseline_seq=1):
        self.max_seq = baseline_seq
        self.docs: list[dict] = []

    def get_group_max_seq(self, group_id):
        return self.max_seq

    def get_documents_by_group_id(self, group_id):
        return list(self.docs)

    def get_by_id(self, doc_id):
        return {"doc_id": doc_id, "branch": "main"}


@pytest.fixture
def fake_env(monkeypatch, tmp_path):
    docs = FakeDocs()
    wfseq = FakeWfseq()
    paused = FakePausedStore()
    chain_holder = {"providers": [], "source": "system"}

    monkeypatch.setattr(svc, "ORACLE_SETTLE_SEC", 0)
    monkeypatch.setattr(svc.db_docs, "get_group_max_seq", docs.get_group_max_seq)
    monkeypatch.setattr(svc.db_docs, "get_documents_by_group_id", docs.get_documents_by_group_id)
    monkeypatch.setattr(svc.db_docs, "get_by_id", docs.get_by_id)
    monkeypatch.setattr(svc.db_wfseq, "get_sequence_for_member_doc", wfseq.get_sequence_for_member_doc)
    monkeypatch.setattr(svc.db_wfseq, "get_sequence_by_doc_id", wfseq.get_sequence_by_doc_id)
    monkeypatch.setattr(svc.db_wfseq, "get_sequence_items", wfseq.get_sequence_items)
    monkeypatch.setattr(svc.db_projects, "get_by_id", lambda pid: {"project_name": "testproj"})
    monkeypatch.setattr(svc.ai_settings_service, "resolve_effective",
                        lambda pid: {"ok": True, **chain_holder})
    monkeypatch.setattr(svc.ai_settings_service, "get_provider_secret", lambda scope, pid: None)
    monkeypatch.setattr(svc.token_service, "issue", lambda **kw: {
        "raw_token": "tok_raw_test", "token_id": "tok_20260717_000001",
        "expires_at": "2026-07-18T00:00:00+00:00",
        "scratch_dir": str(tmp_path / "tokwork"),
    })
    monkeypatch.setattr(svc.token_service, "revoke", lambda *a, **kw: None)
    monkeypatch.setattr(svc.storage_paths, "get_storage_root", lambda *a, **kw: tmp_path / "storage")
    src_root = tmp_path / "srcroot"
    src_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(svc.storage_paths, "resolve_project_src_root",
                        lambda pid, branch, *, group_id: src_root)
    monkeypatch.setattr(svc.storage_paths, "to_storage_relative",
                        lambda path, project=None: str(path))
    monkeypatch.setattr(svc, "_runs", {})
    monkeypatch.setattr(svc, "_group_resume_locks", {})

    # Route every paused-store touch (service AND inbox hook) at the dict fake.
    for name in ("upsert", "get_by_group", "exists", "delete_and_return",
                 "delete_by_group", "list_by_user"):
        monkeypatch.setattr(db_paused, name, getattr(paused, name))

    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(svc, "_broadcast",
                        lambda run, event_type, payload: events.append((event_type, payload)))

    return {"docs": docs, "wfseq": wfseq, "paused": paused, "chain": chain_holder,
            "events": events, "tmp": tmp_path}


def _start(fake_env, mode="continuous", target=3, cmd=None):
    fake_env["chain"]["providers"] = [_provider(
        cmd or f'"{PY}" -c "import sys; sys.stdin.read(); print(\'DONE\')"',
    )]
    return svc.start_run(
        project_id="flowgate",
        module="default",
        group_id=GROUP,
        doc_ref=DOC_REF,
        action_scope="new",
        mode=mode,
        continuation_target_seq=target if mode == "continuous" else None,
        continuation_review_mode=False,
        continuation_instruction_mode=None,
        continuation_locale=None,
        issued_to="usr_admin",
        api_base_url="http://127.0.0.1:1/flowgate/api/v1",
        mention_builder=lambda raw, scratch: "## prompt\n",
    )


def _wait_finished(run_id, timeout=30.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        run = svc.get_run_record(run_id)
        if run and run["status"] == "finished":
            return run
        time.sleep(0.05)
    raise AssertionError(f"run {run_id} did not finish within {timeout}s")


def _slow_cmd(seconds=20):
    return f'"{PY}" -c "import sys, time; time.sleep({seconds})"'


# ── pause_run (L0009 §2.1) ────────────────────────────────────────────────────

class TestPauseRun:
    def test_single_mode_is_rejected_422(self, fake_env):
        res = _start(fake_env, mode="single", cmd=_slow_cmd())
        try:
            with pytest.raises(HTTPException) as exc:
                svc.pause_run(res["run_id"], "usr_admin")
            assert exc.value.status_code == 422
            assert exc.value.detail["code"] == "pause_not_supported"
            assert not fake_env["paused"].rows
        finally:
            svc.cancel_run(res["run_id"])
            _wait_finished(res["run_id"])

    def test_unknown_run_404(self, fake_env):
        with pytest.raises(HTTPException) as exc:
            svc.pause_run("aiv_nope", "usr_admin")
        assert exc.value.status_code == 404

    def test_pause_persists_snapshot_and_is_idempotent(self, fake_env):
        res = _start(fake_env, cmd=_slow_cmd())
        try:
            out = svc.pause_run(res["run_id"], "usr_admin")
            assert out["status"] == "pause_requested"
            assert out["effective_at"] == "step_boundary"

            row = fake_env["paused"].rows[GROUP]
            assert row["doc_ref"] == DOC_REF
            assert row["paused_by"] == "usr_admin"
            assert row["continuation_target_seq"] == 3
            assert row["docs_target"] == 2  # pending worker items (P, L)

            # get_status surfaces the accepted request (reload-proof card state).
            assert svc.get_status(res["run_id"])["status"] == "pause_requested"

            # Double-click pause: upsert-idempotent, same answer.
            again = svc.pause_run(res["run_id"], "usr_admin")
            assert again["status"] == "pause_requested"
            assert len(fake_env["paused"].rows) == 1
        finally:
            svc.cancel_run(res["run_id"])
            _wait_finished(res["run_id"])

    def test_boundary_stop_classifies_user_paused_and_keeps_row(self, fake_env):
        # A ~1s worker keeps the run alive across pause + boundary tagging (no race
        # with the fast-echo finish).
        res = _start(fake_env, cmd=_slow_cmd(1))
        svc.pause_run(res["run_id"], "usr_admin")
        # The inbox boundary hook fires while the run is still alive (the worker is
        # blocked on the inbox response at the boundary) — simulate that ordering.
        svc.mark_user_paused(GROUP)
        run = _wait_finished(res["run_id"])
        assert run["end_reason"] == "user_paused"
        # The paused row is the resume coordinate — the boundary stop must keep it.
        assert GROUP in fake_env["paused"].rows

    def test_no_output_finish_parks_a_system_row(self, fake_env):
        # 0359 L0007 §2.8 (was: "anything but user_paused deletes the row"). A hop that ends
        # having produced nothing is resumable, so the miniplayer must keep a card for it —
        # deleting it is precisely why NR0003 §6's 24 dead chains could not be resumed at all.
        res = _start(fake_env)
        run = _wait_finished(res["run_id"])
        assert (run["end_reason"], run["stop_code"]) == ("exited", "no_output_exhausted")
        assert run["resumable"] is True
        row = fake_env["paused"].rows[GROUP]
        assert row["stop_kind"] == "system"
        assert row["stop_code"] == "no_output_exhausted"
        assert row["stop_run_id"] == res["run_id"]
        # Whose chain it is, not who stopped it — otherwise it shows up on nobody's list.
        assert row["paused_by"] == "usr_admin"

    def test_user_row_outranks_the_system_row(self, fake_env):
        # Pause accepted, then the chain died before reaching any boundary. The row a HUMAN
        # put there is the one that survives; the system never overwrites it (L0007 §5).
        res = _start(fake_env, cmd=_slow_cmd(1))
        svc.pause_run(res["run_id"], "usr_admin")
        run = _wait_finished(res["run_id"])
        assert run["end_reason"] == "exited"
        assert fake_env["paused"].rows[GROUP]["stop_kind"] == "user"

    def test_cancelled_finish_still_cleans_row(self, fake_env):
        # A stop that is NOT resumable still drops the row: no ghost card for a chain a
        # person deliberately ended (L0007 §4.5, default branch — unchanged behaviour).
        res = _start(fake_env, cmd=_slow_cmd(20))
        svc.pause_run(res["run_id"], "usr_admin")
        svc.cancel_run(res["run_id"])
        run = _wait_finished(res["run_id"])
        assert run["stop_code"] == "cancelled"
        assert run["resumable"] is False
        assert GROUP not in fake_env["paused"].rows


# ── resume_chain (L0009 §2.4) ─────────────────────────────────────────────────

def _seed_paused(fake_env, target=3):
    fake_env["paused"].upsert(
        group_id=GROUP, doc_ref=DOC_REF, paused_by="usr_admin",
        paused_at="2026-07-17T00:00:00+09:00",
        continuation_target_seq=target, docs_target=3, docs_reached=1,
    )


def _patch_advance(monkeypatch, tmp_path, calls=None):
    def _advance(**kw):
        if calls is not None:
            calls.append(kw)
        return {
            "token": "tok_raw_resume", "token_id": "tok_20260717_000002",
            "expires_at": "2026-07-18T00:00:00+00:00",
            "scratch_dir": str(tmp_path / "tokwork2"),
            "mention": "## resume prompt\n",
        }
    monkeypatch.setattr(wds, "advance_workflow", _advance)


class TestResumeChain:
    def test_resume_consumes_row_and_starts_continuous_run(self, fake_env, monkeypatch):
        _seed_paused(fake_env)
        calls: list[dict] = []
        _patch_advance(monkeypatch, fake_env["tmp"], calls)
        fake_env["chain"]["providers"] = [_provider(
            f'"{PY}" -c "import sys; sys.stdin.read()"')]

        res = svc.resume_chain(group_id=GROUP, user_id="usr_admin",
                               api_base_url="http://127.0.0.1:1/flowgate/api/v1")
        assert res["ok"] is True
        assert res["mode"] == "continuous"
        assert res["docs_target"] == 2          # pending_only re-derivation (P, L)
        assert GROUP not in fake_env["paused"].rows
        # The resume rides the SAME advance_workflow path as every self-chain hop.
        assert calls and calls[0]["continuation_target_seq"] == 3
        _wait_finished(res["run_id"])

    def test_resume_conflict_when_row_already_consumed(self, fake_env):
        with pytest.raises(HTTPException) as exc:
            svc.resume_chain(group_id=GROUP, user_id="usr_admin",
                             api_base_url="http://x/api/v1")
        assert exc.value.status_code == 409
        assert exc.value.detail["code"] == "resume_conflict"

    def test_resume_blocked_by_active_run(self, fake_env):
        res = _start(fake_env, cmd=_slow_cmd())
        try:
            _seed_paused(fake_env)
            with pytest.raises(HTTPException) as exc:
                svc.resume_chain(group_id=GROUP, user_id="usr_admin",
                                 api_base_url="http://x/api/v1")
            assert exc.value.status_code == 409
            assert exc.value.detail["code"] == "run_already_active"
            assert exc.value.detail["run_id"] == res["run_id"]
            # The row was NOT consumed — the paused card stays valid.
            assert GROUP in fake_env["paused"].rows
        finally:
            svc.cancel_run(res["run_id"])
            _wait_finished(res["run_id"])

    def test_nothing_to_resume_self_cleans(self, fake_env):
        _seed_paused(fake_env)
        for item in fake_env["wfseq"].items:
            item["result_doc_id"] = f"d-{item['item_seq']}"
            item["result_doc_review_status"] = "approved"
        with pytest.raises(HTTPException) as exc:
            svc.resume_chain(group_id=GROUP, user_id="usr_admin",
                             api_base_url="http://x/api/v1")
        assert exc.value.status_code == 409
        assert exc.value.detail["code"] == "nothing_to_resume"
        assert GROUP not in fake_env["paused"].rows  # deliberate cleanup effect

    def test_failed_launch_restores_row(self, fake_env, monkeypatch):
        _seed_paused(fake_env)

        def _advance_boom(**kw):
            raise ValueError("head is not approvable")
        monkeypatch.setattr(wds, "advance_workflow", _advance_boom)
        fake_env["chain"]["providers"] = [_provider('"true"')]

        with pytest.raises(HTTPException) as exc:
            svc.resume_chain(group_id=GROUP, user_id="usr_admin",
                             api_base_url="http://x/api/v1")
        assert exc.value.status_code == 409
        # The paused card must survive a failed resume so the user can retry.
        assert GROUP in fake_env["paused"].rows

    def test_null_target_resolves_to_sequence_end(self, fake_env, monkeypatch):
        _seed_paused(fake_env, target=None)
        calls: list[dict] = []
        _patch_advance(monkeypatch, fake_env["tmp"], calls)
        fake_env["chain"]["providers"] = [_provider(
            f'"{PY}" -c "import sys; sys.stdin.read()"')]
        res = svc.resume_chain(group_id=GROUP, user_id="usr_admin",
                               api_base_url="http://x/api/v1")
        assert calls[0]["continuation_target_seq"] == 3  # max item_seq
        _wait_finished(res["run_id"])


# ── active_all (L0009 §2.8 / P0008 S1) ────────────────────────────────────────

class TestActiveAll:
    def test_scopes_runs_by_issuer_and_derives_pending_q(self, fake_env):
        res = _start(fake_env, cmd=_slow_cmd())
        try:
            other_group = "flowgate.default.9999"
            fake_env["paused"].upsert(
                group_id=other_group, doc_ref=f"{other_group}.0001-R",
                paused_by="usr_admin", paused_at="2026-07-17T00:00:00+09:00",
                continuation_target_seq=5, docs_target=4, docs_reached=2,
            )
            fake_env["docs"].docs = [
                {"doc_id": f"{other_group}.0005-Q", "seq": 5, "type_code": "Q",
                 "status": "open"},
                {"doc_id": f"{other_group}.0003-Q", "seq": 3, "type_code": "Q",
                 "status": "answered"},
                {"doc_id": f"{other_group}.0002-D", "seq": 2, "type_code": "D",
                 "status": "open"},
            ]

            mine = svc.active_all("usr_admin")
            assert [r["run_id"] for r in mine["runs"]] == [res["run_id"]]
            assert mine["runs"][0]["doc_ref"] == DOC_REF
            assert len(mine["paused"]) == 1
            paused = mine["paused"][0]
            assert paused["group_id"] == other_group
            assert paused["docs_reached"] == 2
            # Only the OPEN Q surfaces — answered ones are excluded live (DB0010 §4).
            assert paused["pending_q_doc_ids"] == [f"{other_group}.0005-Q"]

            theirs = svc.active_all("usr_other")
            assert theirs["runs"] == []
            assert theirs["paused"] == []
        finally:
            svc.cancel_run(res["run_id"])
            _wait_finished(res["run_id"])


# ── inbox boundary hook (L0009 §2.2) ─────────────────────────────────────────

class TestInboxBoundaryHook:
    @staticmethod
    def _call_chain(monkeypatch, fake_env, target_seq, completed_seq):
        from modules.flow_gate.api import inbox_routes

        monkeypatch.setattr(
            "modules.flow_gate.db.workflow_sequences.get_item_by_result_doc_id",
            lambda doc_id: {"item_seq": completed_seq},
        )
        monkeypatch.setattr(inbox_routes.db_docs, "get_by_id",
                            lambda doc_id: {"doc_id": doc_id, "id": 1, "group_id": GROUP})
        token_rec = {
            "continuation_target_seq": target_seq,
            "continuation_review_mode": 0,
            "continuation_instruction_mode": None,
            "continuation_locale": "ko",
            "doc_ref": DOC_REF,
            "issued_to": "usr_admin",
            "group_id": GROUP,
        }
        # doc_type "M" is in AUTO_COMPLETE_TYPES → the auto-approve leg is skipped and
        # the test pins exactly the boundary decision, not the approval machinery.
        return inbox_routes._continuation_self_chain(
            request=None, token_rec=token_rec, project="flowgate",
            canonical_doc_id=f"{GROUP}.0009-M", doc_type="M",
        )

    def test_pause_row_withholds_next_token(self, fake_env, monkeypatch):
        _seed_paused(fake_env)
        res = _start(fake_env, cmd=_slow_cmd())
        try:
            envelope = self._call_chain(monkeypatch, fake_env, target_seq=6, completed_seq=4)
            assert envelope["continuation_paused"] is True
            assert "paused by user" in envelope["continuation_reason"]
            assert "next_token" not in envelope
            # The live run got tagged so its finish classifies as user_paused.
            assert svc.get_run_record(res["run_id"])["user_paused"] is True
            # The row is NOT consumed at the boundary — resume owns that (L0009 §2.4).
            assert GROUP in fake_env["paused"].rows
        finally:
            svc.cancel_run(res["run_id"])
            _wait_finished(res["run_id"])

    def test_target_reached_terminates_and_cleans_row(self, fake_env, monkeypatch):
        _seed_paused(fake_env)
        envelope = self._call_chain(monkeypatch, fake_env, target_seq=4, completed_seq=4)
        assert envelope.get("continuation_done") is True
        # Natural termination wins over a boundary-less pause: the row is gone.
        assert GROUP not in fake_env["paused"].rows
