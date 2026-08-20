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
               chain_id=None, chain_docs_target=None, chain_docs_reached=0,
               stop_kind="user", stop_code=None, stop_run_id=None,
               stop_last_message_excerpt=None,
               continuation_base_provider_id=None, continuation_provider_pinned=None,
               continuation_provider_overrides=None,
               continuation_default_note=None, continuation_note_overrides=None,
               # 0352 T0004 §3.6: the N/T authoring mode + its per-item_seq auto-approve
               # selection — the pause->resume mode-loss bug fix under test in this file.
               continuation_instruction_mode=None, continuation_auto_approve_item_seqs=None,
               # flowgate.default.0400 M0005: the per-hop budget pick, same "rides every
               # upsert call, including system rows" treatment as instruction_mode above.
               continuation_step_timeout_sec=None,
               # flowgate.default.0443 T0002: same treatment as the budget pick above.
               continuation_restart_max_attempts=None):
        self.rows[group_id] = {
            "id": 1, "group_id": group_id, "doc_ref": doc_ref, "mode": "continuous",
            "paused_by": paused_by, "paused_at": paused_at,
            "continuation_target_seq": continuation_target_seq,
            "docs_target": docs_target, "docs_reached": docs_reached,
            # group 0357 T0004: chain-lifetime progress across the per-hop runs.
            "chain_id": chain_id, "chain_docs_target": chain_docs_target,
            "chain_docs_reached": chain_docs_reached,
            # group 0359 DB0008 Q7 / L0007 §2.8: who parked this chain and why.
            "stop_kind": stop_kind, "stop_code": stop_code, "stop_run_id": stop_run_id,
            "stop_last_message_excerpt": stop_last_message_excerpt,
            # 0365 DB0004: stored exactly as the real columns store them — normalized
            # text — so the resume path is exercised through the production decoders.
            "continuation_base_provider_id": continuation_base_provider_id or None,
            "continuation_provider_pinned": bool(continuation_provider_pinned),
            "continuation_provider_overrides": db_paused.dump_json_map(
                continuation_provider_overrides),
            "continuation_default_note": (continuation_default_note or "").strip() or None,
            "continuation_note_overrides": db_paused.dump_json_map(
                continuation_note_overrides),
            # 0352 T0004 §3.6: same normalized-text storage contract as the fields above.
            "continuation_instruction_mode": (continuation_instruction_mode or "").strip() or None,
            "continuation_auto_approve_item_seqs": db_paused.dump_json_list(
                continuation_auto_approve_item_seqs),
            "continuation_step_timeout_sec": continuation_step_timeout_sec,
            "continuation_restart_max_attempts": continuation_restart_max_attempts,
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

    def delete_system_stop(self, group_id, stop_run_id):
        row = self.rows.get(group_id)
        if (row and row.get("stop_kind") == "system"
                and row.get("stop_run_id") == stop_run_id):
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
                 "delete_by_group", "delete_system_stop", "list_by_user"):
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
            assert row["chain_id"] == res["chain_id"]
            assert row["chain_docs_target"] == 2
            assert row["chain_docs_reached"] == 0

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
        # The in-flight hop completes after the request snapshot but before the
        # boundary stop. Its document must be included in the persisted chain count.
        fake_env["docs"].docs.append({
            "doc_id": f"{GROUP}.0002-P", "seq": 2, "status": "open",
        })
        # The inbox boundary hook fires while the run is still alive (the worker is
        # blocked on the inbox response at the boundary) — simulate that ordering.
        assert svc.mark_user_paused(GROUP, res["run_id"]) is True
        run = _wait_finished(res["run_id"])
        assert run["end_reason"] == "user_paused"
        # The paused row is the resume coordinate — the boundary stop must keep it.
        row = fake_env["paused"].rows[GROUP]
        assert row["chain_docs_reached"] == 1
        assert row["chain_docs_target"] == 2

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

    def test_no_output_system_row_preserves_instruction_mode_and_selection(self, fake_env):
        # 0352 T0004 §3.6: unlike the provider/note preference columns, instruction_mode is
        # the chain's actual running policy — a system stop (e.g. no_output_exhausted) that
        # dropped it would resume an ai_direct chain back to auto_approved and silently
        # auto-approve N/T the user chose to author themselves. Written on EVERY system row,
        # not just the user-pause refresh.
        fake_env["chain"]["providers"] = [_provider(
            f'"{PY}" -c "import sys; sys.stdin.read(); print(\'DONE\')"',
        )]
        res = svc.start_run(
            project_id="flowgate", module="default", group_id=GROUP, doc_ref=DOC_REF,
            action_scope="new", mode="continuous", continuation_target_seq=3,
            continuation_review_mode=False, continuation_instruction_mode="ai_direct",
            continuation_locale=None, issued_to="usr_admin",
            api_base_url="http://127.0.0.1:1/flowgate/api/v1",
            mention_builder=lambda raw, scratch: "## prompt\n",
            continuation_auto_approve_item_seqs=[3],
        )
        run = _wait_finished(res["run_id"])
        assert (run["end_reason"], run["stop_code"]) == ("exited", "no_output_exhausted")
        row = fake_env["paused"].rows[GROUP]
        assert row["stop_kind"] == "system"
        assert row["continuation_instruction_mode"] == "ai_direct"
        assert db_paused.load_json_list(
            row["continuation_auto_approve_item_seqs"]) == [3]

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

    def test_pause_persists_provider_and_note_selections(self, fake_env):
        # 0365 B0001/DB0004 §5-3 case 1/4: the values chosen when the run was started
        # must be the values written into the paused row, not lost between memory
        # and storage.
        fake_env["chain"]["providers"] = [
            _provider(_slow_cmd(), pid="aip_default", name="default"),
            _provider(_slow_cmd(), pid="aip_picked", name="picked"),
        ]
        res = svc.start_run(
            project_id="flowgate", module="default", group_id=GROUP, doc_ref=DOC_REF,
            action_scope="new", mode="continuous", continuation_target_seq=3,
            continuation_review_mode=False, continuation_instruction_mode="ai_direct",
            continuation_locale=None, issued_to="usr_admin",
            api_base_url="http://127.0.0.1:1/flowgate/api/v1",
            mention_builder=lambda raw, scratch: "## prompt\n",
            provider_id="aip_picked",
            provider_pinned=True,
            continuation_provider_overrides={"3": "aip_default"},
            continuation_default_note="공통멘트",
            continuation_note_overrides={"3": "개별멘트"},
            continuation_auto_approve_item_seqs=[3],
            continuation_step_timeout_sec=10800,
        )
        try:
            svc.pause_run(res["run_id"], "usr_admin")
            row = fake_env["paused"].rows[GROUP]
            assert row["continuation_base_provider_id"] == "aip_picked"
            assert row["continuation_provider_pinned"] is True
            assert db_paused.load_json_map(
                row["continuation_provider_overrides"]) == {"3": "aip_default"}
            assert row["continuation_default_note"] == "공통멘트"
            assert db_paused.load_json_map(
                row["continuation_note_overrides"]) == {"3": "개별멘트"}
            # 0352 T0004 §3.6: the N/T authoring mode + its per-item_seq selection must
            # survive the pause exactly like the provider/note selections above.
            assert row["continuation_instruction_mode"] == "ai_direct"
            assert db_paused.load_json_list(
                row["continuation_auto_approve_item_seqs"]) == [3]
            # flowgate.default.0400 M0005: same "survives the pause" contract.
            assert row["continuation_step_timeout_sec"] == 10800
        finally:
            svc.cancel_run(res["run_id"])
            _wait_finished(res["run_id"])

    def test_boundary_stop_preserves_provider_selection_through_w2(self, fake_env):
        # 0365 DB0004 §5-3 case 2 / invariant I3: the run-end upsert (end_reason ==
        # "user_paused") must re-send the same values pause_run just stored, or W2
        # wipes them the moment the boundary-stop cleanup upsert runs.
        fake_env["chain"]["providers"] = [_provider(_slow_cmd(1), pid="aip_picked")]
        res = svc.start_run(
            project_id="flowgate", module="default", group_id=GROUP, doc_ref=DOC_REF,
            action_scope="new", mode="continuous", continuation_target_seq=3,
            continuation_review_mode=False, continuation_instruction_mode="ai_direct",
            continuation_locale=None, issued_to="usr_admin",
            api_base_url="http://127.0.0.1:1/flowgate/api/v1",
            mention_builder=lambda raw, scratch: "## prompt\n",
            provider_id="aip_picked",
            provider_pinned=True,
            continuation_default_note="공통멘트",
            continuation_auto_approve_item_seqs=[3],
        )
        svc.pause_run(res["run_id"], "usr_admin")
        fake_env["docs"].docs.append({
            "doc_id": f"{GROUP}.0002-P", "seq": 2, "status": "open",
        })
        assert svc.mark_user_paused(GROUP, res["run_id"]) is True
        run = _wait_finished(res["run_id"])
        assert run["end_reason"] == "user_paused"
        row = fake_env["paused"].rows[GROUP]
        assert row["continuation_base_provider_id"] == "aip_picked"
        assert row["continuation_provider_pinned"] is True
        assert row["continuation_default_note"] == "공통멘트"
        # 0352 T0004 §3.6: the same "refresh, don't erase" contract now covers the mode +
        # selection too — the boundary-stop upsert must re-send what pause_run just stored.
        assert row["continuation_instruction_mode"] == "ai_direct"
        assert db_paused.load_json_list(
            row["continuation_auto_approve_item_seqs"]) == [3]

    def test_pause_upsert_db_failure_does_not_500(self, fake_env, monkeypatch):
        # If the paused-row upsert raises (e.g. schema drift from an unapplied
        # migration), the pause request must still be accepted in-memory instead
        # of surfacing as a 500 — persistence failure downgrades to a logged
        # warning, matching _finish_run_record and resume_chain._restore_row.
        res = _start(fake_env, cmd=_slow_cmd())
        try:
            def _boom(**kw):
                raise RuntimeError("simulated db failure")
            monkeypatch.setattr(db_paused, "upsert", _boom)

            out = svc.pause_run(res["run_id"], "usr_admin")
            assert out["status"] == "pause_requested"
            assert GROUP not in fake_env["paused"].rows
        finally:
            svc.cancel_run(res["run_id"])
            _wait_finished(res["run_id"])


# ── resume_chain (L0009 §2.4) ─────────────────────────────────────────────────

def _seed_paused(fake_env, target=3, base_provider_id=None, provider_pinned=None, overrides=None,
                 default_note=None, note_overrides=None,
                 instruction_mode=None, auto_approve_item_seqs=None,
                 step_timeout_sec=None):
    fake_env["paused"].upsert(
        group_id=GROUP, doc_ref=DOC_REF, paused_by="usr_admin",
        paused_at="2026-07-17T00:00:00+09:00",
        continuation_target_seq=target, docs_target=3, docs_reached=1,
        chain_id="aiv_paused_chain", chain_docs_target=3, chain_docs_reached=1,
        continuation_base_provider_id=base_provider_id,
        continuation_provider_pinned=provider_pinned,
        continuation_provider_overrides=overrides,
        continuation_default_note=default_note,
        continuation_note_overrides=note_overrides,
        continuation_instruction_mode=instruction_mode,
        continuation_auto_approve_item_seqs=auto_approve_item_seqs,
        continuation_step_timeout_sec=step_timeout_sec,
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
        assert res["chain_id"] == "aiv_paused_chain"
        assert res["chain_docs_target"] == 3
        assert res["chain_docs_reached"] == 1
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

    def test_resume_launches_with_pinned_provider_not_default(self, fake_env, monkeypatch):
        # 0365 B0001/DB0004 §5-3 case 3: the paused chain's pin must win over the
        # project default chain's first (most expensive) entry — this reproduces
        # B0001's exact symptom if it regresses.
        _seed_paused(fake_env, base_provider_id="aip_picked", provider_pinned=True)
        _patch_advance(monkeypatch, fake_env["tmp"])
        fake_env["chain"]["providers"] = [
            _provider(f'"{PY}" -c "import sys; sys.stdin.read()"', pid="aip_default", name="default"),
            _provider(f'"{PY}" -c "import sys; sys.stdin.read()"', pid="aip_picked", name="picked"),
        ]
        res = svc.resume_chain(group_id=GROUP, user_id="usr_admin",
                               api_base_url="http://x/api/v1")
        # The launch itself carries the verdict: the resumed chain LEADS with the pin.
        assert res["provider"]["id"] == "aip_picked"
        run = _wait_finished(res["run_id"])
        # 0359's no-output retry may hand the hop to the fallback tail afterwards, so the
        # final provider_id is not the pin's guarantee — the ORDER of attempts is. What
        # must never happen is the pinned provider being skipped in favour of the default.
        attempted = [entry.get("provider_id") for entry in run["fallback_history"]]
        attempted.append(run["provider_id"])
        assert attempted[0] == "aip_picked"

    def test_resume_forwards_step_overrides_and_notes_to_start_run(self, fake_env, monkeypatch):
        # 0365 DB0004 §5-3 case 4: per-step provider overrides and [전달멘트] values
        # round-trip the same way the header pin does.
        overrides_in = {"3": "aip_other"}
        note_overrides_in = {"3": "개별멘트"}
        _seed_paused(fake_env, base_provider_id="aip_picked", provider_pinned=True,
                     overrides=overrides_in, default_note="공통멘트",
                     note_overrides=note_overrides_in)
        fake_env["chain"]["providers"] = [_provider('"true"', pid="aip_picked")]
        captured = {}

        def _fake_start_run(**kw):
            captured.update(kw)
            return {"ok": True, "run_id": "aiv_fake"}
        monkeypatch.setattr(svc, "start_run", _fake_start_run)

        res = svc.resume_chain(group_id=GROUP, user_id="usr_admin",
                               api_base_url="http://x/api/v1")
        assert res == {"ok": True, "run_id": "aiv_fake"}
        assert captured["provider_id"] == "aip_picked"
        assert captured["provider_pinned"] is True
        assert captured["continuation_provider_overrides"] == overrides_in
        assert captured["continuation_default_note"] == "공통멘트"
        assert captured["continuation_note_overrides"] == note_overrides_in

    def test_resume_restores_instruction_mode_and_selection_to_start_run(self, fake_env, monkeypatch):
        # 0352 T0004 §3.6: the pause->resume mode-loss bug, execution-creation path.
        # resume_chain used to hard-code continuation_instruction_mode="auto_approved" on
        # this exact call — an ai_direct chain resumed as if the user had never chosen
        # ai_direct. Both the mode AND its per-item_seq selection must round-trip.
        _seed_paused(fake_env, instruction_mode="ai_direct", auto_approve_item_seqs=[3])
        fake_env["chain"]["providers"] = [_provider('"true"', pid="aip_picked")]
        captured = {}

        def _fake_start_run(**kw):
            captured.update(kw)
            return {"ok": True, "run_id": "aiv_fake"}
        monkeypatch.setattr(svc, "start_run", _fake_start_run)

        res = svc.resume_chain(group_id=GROUP, user_id="usr_admin",
                               api_base_url="http://x/api/v1")
        assert res == {"ok": True, "run_id": "aiv_fake"}
        assert captured["continuation_instruction_mode"] == "ai_direct"
        assert captured["continuation_auto_approve_item_seqs"] == [3]

    def test_resume_forwards_step_timeout_to_start_run(self, fake_env, monkeypatch):
        # flowgate.default.0400 M0005: the per-hop budget pick is exactly as perishable as
        # the provider/note selections above — the paused row is the only place it survives
        # a pause, so resume_chain must read it back and hand it to start_run.
        _seed_paused(fake_env, step_timeout_sec=14400)
        fake_env["chain"]["providers"] = [_provider('"true"', pid="aip_picked")]
        captured = {}

        def _fake_start_run(**kw):
            captured.update(kw)
            return {"ok": True, "run_id": "aiv_fake"}
        monkeypatch.setattr(svc, "start_run", _fake_start_run)

        res = svc.resume_chain(group_id=GROUP, user_id="usr_admin",
                               api_base_url="http://x/api/v1")
        assert res == {"ok": True, "run_id": "aiv_fake"}
        assert captured["continuation_step_timeout_sec"] == 14400

    def test_resume_restores_instruction_mode_to_the_token_advance_path(self, fake_env, monkeypatch):
        # 0352 T0004 §3.6: the pause->resume mode-loss bug, TOKEN-issuance path (the other
        # half of the fix — _issue_resume never forwarded continuation_instruction_mode to
        # advance_workflow at all, so even a correctly-restored run-level mode would still
        # mint a token that silently normalized back to auto_approved on the FIRST hop).
        _seed_paused(fake_env, instruction_mode="ai_direct", auto_approve_item_seqs=[3])
        calls: list[dict] = []
        _patch_advance(monkeypatch, fake_env["tmp"], calls)
        fake_env["chain"]["providers"] = [_provider(
            f'"{PY}" -c "import sys; sys.stdin.read()"')]

        res = svc.resume_chain(group_id=GROUP, user_id="usr_admin",
                               api_base_url="http://127.0.0.1:1/flowgate/api/v1")
        assert res["ok"] is True
        assert calls and calls[0]["continuation_instruction_mode"] == "ai_direct"
        assert calls[0]["continuation_auto_approve_item_seqs"] == [3]
        _wait_finished(res["run_id"])

    def test_resume_falls_back_when_unpinned_stored_provider_no_longer_enabled(self, fake_env, monkeypatch):
        # A legacy stored preference without the explicit pin bit may still degrade to the default.
        _seed_paused(fake_env, base_provider_id="aip_removed")
        _patch_advance(monkeypatch, fake_env["tmp"])
        fake_env["chain"]["providers"] = [
            _provider(f'"{PY}" -c "import sys; sys.stdin.read()"', pid="aip_default"),
        ]
        res = svc.resume_chain(group_id=GROUP, user_id="usr_admin",
                               api_base_url="http://x/api/v1")
        assert res["ok"] is True
        run = _wait_finished(res["run_id"])
        assert run["provider_id"] == "aip_default"

    def test_resume_rejects_an_unavailable_explicit_pin_without_substitution(
            self, fake_env, monkeypatch):
        _seed_paused(fake_env, base_provider_id="aip_removed", provider_pinned=True)
        _patch_advance(monkeypatch, fake_env["tmp"])
        fake_env["chain"]["providers"] = [
            _provider(f'"{PY}" -c "import sys; sys.stdin.read()"', pid="aip_default"),
        ]

        with pytest.raises(HTTPException) as exc:
            svc.resume_chain(group_id=GROUP, user_id="usr_admin",
                             api_base_url="http://x/api/v1")

        assert exc.value.status_code == 422
        assert exc.value.detail["code"] == "provider_unavailable"
        restored = fake_env["paused"].rows[GROUP]
        assert restored["continuation_base_provider_id"] == "aip_removed"
        assert restored["continuation_provider_pinned"] is True

    def test_failed_launch_restores_provider_and_note_selections(self, fake_env, monkeypatch):
        # 0365 DB0004 §5-3 case 5 (W3): a retry after a failed resume must still find
        # the user's provider/note selections on the restored row.
        overrides_in = {"3": "aip_other"}
        note_overrides_in = {"3": "개별멘트"}
        _seed_paused(fake_env, base_provider_id="aip_picked", overrides=overrides_in,
                     default_note="공통멘트", note_overrides=note_overrides_in,
                     instruction_mode="ai_direct", auto_approve_item_seqs=[3])

        def _advance_boom(**kw):
            raise ValueError("head is not approvable")
        monkeypatch.setattr(wds, "advance_workflow", _advance_boom)
        fake_env["chain"]["providers"] = [_provider('"true"', pid="aip_picked")]

        with pytest.raises(HTTPException) as exc:
            svc.resume_chain(group_id=GROUP, user_id="usr_admin",
                             api_base_url="http://x/api/v1")
        assert exc.value.status_code == 409
        row = fake_env["paused"].rows[GROUP]
        assert row["continuation_base_provider_id"] == "aip_picked"
        assert db_paused.load_json_map(
            row["continuation_provider_overrides"]) == overrides_in
        assert row["continuation_default_note"] == "공통멘트"
        assert db_paused.load_json_map(
            row["continuation_note_overrides"]) == note_overrides_in
        # 0352 T0004 §3.6: a retry after a failed resume must still find the mode +
        # selection restored too — not just the provider/note preferences.
        assert row["continuation_instruction_mode"] == "ai_direct"
        assert db_paused.load_json_list(
            row["continuation_auto_approve_item_seqs"]) == [3]

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


class TestPauseIdentityAndRestoreRegression:
    def test_failed_resume_restores_all_system_stop_metadata(self, fake_env, monkeypatch):
        _seed_paused(fake_env)
        original = {
            "stop_kind": "system",
            "stop_code": "no_output_exhausted",
            "stop_run_id": "aiv_old_chain",
            "stop_last_message_excerpt": "old chain produced no output",
        }
        fake_env["paused"].rows[GROUP].update(original)
        monkeypatch.setattr(
            wds, "advance_workflow",
            lambda **_kw: (_ for _ in ()).throw(ValueError("head is not approvable")),
        )
        fake_env["chain"]["providers"] = [_provider('"true"')]

        with pytest.raises(HTTPException) as exc:
            svc.resume_chain(group_id=GROUP, user_id="usr_admin",
                             api_base_url="http://x/api/v1")

        assert exc.value.status_code == 409
        assert exc.value.detail["code"] == "resume_advance_blocked"
        assert exc.value.detail["restored"] is True
        assert {key: fake_env["paused"].rows[GROUP][key] for key in original} == original

    def test_stale_system_row_cannot_tag_a_single_run(self, fake_env):
        res = _start(fake_env, mode="single", cmd=_slow_cmd())
        fake_env["paused"].upsert(
            group_id=GROUP, doc_ref=DOC_REF, paused_by="usr_admin",
            paused_at="2026-08-03T13:56:40+09:00",
            continuation_target_seq=3, docs_target=1, docs_reached=0,
            stop_kind="system", stop_code="no_output_exhausted",
            stop_run_id="aiv_old_chain",
        )
        try:
            assert svc.mark_user_paused(GROUP, res["run_id"]) is False
            assert svc.get_run_record(res["run_id"])["user_paused"] is False
        finally:
            svc.cancel_run(res["run_id"])
            _wait_finished(res["run_id"])


# ── active_all (L0009 §2.8 / P0008 S1) ────────────────────────────────────────

class TestActiveAll:
    def test_reload_discards_reopened_workflow_system_stop_after_single_success(
            self, fake_env, monkeypatch):
        from modules.flow_gate.db import ai_invoke_runs as db_runs

        single = _start(fake_env, mode="single")
        _wait_finished(single["run_id"])
        fake_env["paused"].upsert(
            group_id=GROUP, doc_ref=DOC_REF, paused_by="usr_admin",
            paused_at="2026-08-03T13:56:40+09:00",
            continuation_target_seq=3, docs_target=1, docs_reached=0,
            stop_kind="system", stop_code="no_output_exhausted",
            stop_run_id="aiv_old_chain",
        )
        # The old chain stopped on item 3. Reopen moved the current head back to item 2.
        monkeypatch.setattr(db_runs, "get", lambda _run_id: {"hop_item_seq": 3})

        mine = svc.active_all("usr_admin")

        assert mine["paused"] == []
        assert GROUP not in fake_env["paused"].rows


    def test_scopes_runs_by_issuer_and_derives_pending_q(
            self, fake_env, monkeypatch):
        res = _start(fake_env, cmd=_slow_cmd())
        try:
            other_group = "flowgate.default.9999"
            fake_env["paused"].upsert(
                group_id=other_group, doc_ref=f"{other_group}.0001-R",
                paused_by="usr_admin", paused_at="2026-07-17T00:00:00+09:00",
                continuation_target_seq=5, docs_target=4, docs_reached=2,
            )
            pending_doc = f"{other_group}.0005-D"
            answered_doc = f"{other_group}.0003-D"
            fake_env["docs"].docs = [
                {"doc_id": pending_doc, "seq": 5, "type_code": "D", "status": "open"},
                {"doc_id": answered_doc, "seq": 3, "type_code": "D", "status": "open"},
            ]
            containers = {
                pending_doc: {"id": 5, "doc_id": pending_doc},
                answered_doc: {"id": 3, "doc_id": answered_doc},
            }
            monkeypatch.setattr(
                svc.db_questions, "get_container_by_doc", containers.get,
            )
            monkeypatch.setattr(
                svc.db_question_items,
                "list_unanswered",
                lambda container_id: [{"id": 50}] if container_id == 5 else [],
            )

            mine = svc.active_all("usr_admin")
            assert [r["run_id"] for r in mine["runs"]] == [res["run_id"]]
            assert mine["runs"][0]["doc_ref"] == DOC_REF
            assert "pending_q_doc_ids" in mine["runs"][0]
            assert len(mine["paused"]) == 1
            paused = mine["paused"][0]
            assert paused["group_id"] == other_group
            assert paused["docs_reached"] == 2
            # Host type is D: pending state comes from the real Q container/items model.
            assert paused["pending_q_doc_ids"] == [pending_doc]

            theirs = svc.active_all("usr_other")
            assert theirs["runs"] == []
            assert theirs["paused"] == []
        finally:
            svc.cancel_run(res["run_id"])
            _wait_finished(res["run_id"])


# ── inbox boundary hook (L0009 §2.2) ─────────────────────────────────────────

class TestInboxBoundaryHook:
    @staticmethod
    def _call_chain(monkeypatch, fake_env, target_seq, completed_seq, ai_run_id=None):
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
            "ai_run_id": ai_run_id,
        }
        # doc_type "M" is in AUTO_COMPLETE_TYPES → the auto-approve leg is skipped and
        # the test pins exactly the boundary decision, not the approval machinery.
        return inbox_routes._continuation_self_chain(
            request=None, token_rec=token_rec, project="flowgate",
            canonical_doc_id=f"{GROUP}.0009-M", doc_type="M",
        )

    def test_pause_row_withholds_next_token(self, fake_env, monkeypatch):
        res = _start(fake_env, cmd=_slow_cmd())
        svc.pause_run(res["run_id"], "usr_admin")
        try:
            envelope = self._call_chain(
                monkeypatch, fake_env, target_seq=6, completed_seq=4,
                ai_run_id=res["run_id"],
            )
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
