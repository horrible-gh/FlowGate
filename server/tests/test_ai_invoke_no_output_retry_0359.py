"""flowgate.default.0359 T0012 (2번 묶음): 헛돈 홉을 다시 시도하게 만들기.

NR0003 §3 found the hop loop had exactly ONE edge — "a document was registered" — and no
branch at all for "the hop ran and produced nothing". A worker that hit an environment fault,
said so politely and exited 0 therefore ended the whole unmanned chain in silence, with 11
configured providers never contacted, no failure record, no notification and no resume card.

This file pins the branch that closes it (L0007 §2.1~2.8, §4.1~4.3):
  * a no-output hop opens another attempt on the NEXT provider, same run_id, same token;
  * the retry stops at 3 attempts / no providers left / no budget left, and the stop is
    recorded, notified and parked as a resumable card;
  * cancel / timeout / user pause are never retried — a person or the clock decided those;
  * a document that lands late cancels the retry rather than producing a second one;
  * a continuous provider pin re-orders the chain instead of collapsing it (§2.1.1), which is
    what left the dying hop with no fallback tail to retry into.

Environment is monkeypatched exactly like test_ai_invoke_pause_resume_0252 — no database.
"""
from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

import pytest

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")

_SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SERVER_DIR))

from modules.flow_gate.db import ai_invoke_paused_chains as db_paused  # noqa: E402
from modules.flow_gate.db import ai_invoke_runs as db_runs  # noqa: E402
from modules.flow_gate.services import ai_invoke_service as svc  # noqa: E402
from modules.flow_gate.workflow import event_logger  # noqa: E402

GROUP = "flowgate.default.0359"
DOC_REF = "flowgate.default.0359.0001-B"
FAR_FUTURE = "2099-01-01T00:00:00+00:00"


def _provider(pid, name):
    return {
        "id": pid, "name": name, "exec_type": "cli", "kind": "claude",
        "enabled": True, "cli_command": "noop", "api_base_url": None,
        "api_model": None, "api_key_set": False, "api_key_hint": None,
    }


P1 = _provider("aip_1", "OpenAI Codex CLI")
P2 = _provider("aip_2", "Anthropic Claude Opus 5")
P3 = _provider("aip_3", "GitHub Copilot CLI")


class FakePausedStore:
    def __init__(self):
        self.rows: dict[str, dict] = {}
        self.deleted: list[str] = []

    def upsert(self, *, group_id, doc_ref, paused_by, paused_at,
               continuation_target_seq, docs_target, docs_reached,
               stop_kind="user", stop_code=None, stop_run_id=None,
               stop_last_message_excerpt=None,
               continuation_base_provider_id=None, continuation_provider_pinned=None,
               continuation_provider_overrides=None, continuation_default_note=None,
               continuation_note_overrides=None,
               # 0352 T0004 §3.6: unlike provider/note preferences (not sent on a SYSTEM
               # row unless 0435's explicit provider pin is active), the N/T authoring
               # mode + its per-item_seq selection ARE sent on every
               # upsert call, system rows included — that is the pause->resume mode-loss
               # bug fix this group's TR ships.
               continuation_instruction_mode=None, continuation_auto_approve_item_seqs=None,
               # flowgate.default.0400 M0005: sent on every upsert call, system rows included
               # — same "policy, not preference" treatment as instruction_mode above.
               continuation_step_timeout_sec=None):
        self.rows[group_id] = {
            "group_id": group_id, "doc_ref": doc_ref, "paused_by": paused_by,
            "paused_at": paused_at, "continuation_target_seq": continuation_target_seq,
            "docs_target": docs_target, "docs_reached": docs_reached,
            "stop_kind": stop_kind, "stop_code": stop_code, "stop_run_id": stop_run_id,
            "stop_last_message_excerpt": stop_last_message_excerpt,
            "continuation_base_provider_id": continuation_base_provider_id,
            "continuation_provider_pinned": bool(continuation_provider_pinned),
            "continuation_provider_overrides": continuation_provider_overrides,
            "continuation_default_note": continuation_default_note,
            "continuation_note_overrides": continuation_note_overrides,
            "continuation_instruction_mode": continuation_instruction_mode,
            "continuation_auto_approve_item_seqs": continuation_auto_approve_item_seqs,
            "continuation_step_timeout_sec": continuation_step_timeout_sec,
        }

    def get_by_group(self, group_id):
        row = self.rows.get(group_id)
        return dict(row) if row else None

    def delete_by_group(self, group_id):
        self.deleted.append(group_id)
        self.rows.pop(group_id, None)

    def delete_and_return(self, group_id):
        row = self.rows.pop(group_id, None)
        return dict(row) if row else None

    def list_by_user(self, user_id):
        return [dict(r) for r in self.rows.values() if r["paused_by"] == user_id]

    def exists(self, group_id):
        return group_id in self.rows


class FakeDocs:
    """Group documents, with a scripted reveal so a LATE registration can be simulated."""

    def __init__(self):
        self.docs: list[dict] = []
        self.reveal_after_calls: int | None = None
        self.pending: list[dict] = []
        self.calls = 0
        # 0446 T0008 §4-2: the `edit` scope is judged by `_probe_doc_revision`, i.e. by this
        # number going up — the FakeDocs counterpart of the `revision_no = revision_no + 1`
        # the inbox's `_handle_edit` performs. A rework worker's ONLY visible product.
        self.revision_no = 3
        self.get_by_id_calls = 0
        self._late_revision_in: int | None = None

    def arm_late_revision(self, after_calls: int = 1) -> None:
        """Raise the revision after `after_calls` more reads.

        The scoped equivalent of `reveal_after_calls`: a write that lands between the judge's
        probe and the moment a second worker would start (L0007 §5 / T0008 §3-4).
        """
        self._late_revision_in = after_calls

    def get_documents_by_group_id(self, group_id):
        self.calls += 1
        if self.reveal_after_calls is not None and self.calls > self.reveal_after_calls:
            self.docs = self.docs + self.pending
            self.pending = []
            self.reveal_after_calls = None
        return list(self.docs)

    def get_group_max_seq(self, group_id):
        return max((d.get("seq") or 0 for d in self.docs), default=1)

    def get_by_id(self, doc_id):
        self.get_by_id_calls += 1
        if self._late_revision_in is not None:
            if self._late_revision_in <= 0:
                self.revision_no += 1
                self._late_revision_in = None
            else:
                self._late_revision_in -= 1
        return {"doc_id": doc_id, "id": 77, "branch": "main", "group_id": GROUP,
                "revision_no": self.revision_no}


@pytest.fixture
def env(monkeypatch, tmp_path):
    docs = FakeDocs()
    paused = FakePausedStore()
    chain_holder = {"providers": [P1, P2, P3], "source": "system"}
    records: list[dict] = []
    signals: list[dict] = []
    events: list[tuple[str, dict]] = []

    monkeypatch.setattr(svc, "ORACLE_SETTLE_SEC", 0)
    monkeypatch.setattr(svc, "_runs", {})
    monkeypatch.setattr(svc, "_auto_resume", {})
    monkeypatch.setattr(svc.db_docs, "get_documents_by_group_id", docs.get_documents_by_group_id)
    monkeypatch.setattr(svc.db_docs, "get_group_max_seq", docs.get_group_max_seq)
    monkeypatch.setattr(svc.db_docs, "get_by_id", docs.get_by_id)
    # No question ever pending by default (NR0003 후속 조치 제안 1) — individual tests
    # override this to exercise the pending-question guard. The anchor mock defaults to
    # identity; the guard tests below supply their own anchor + container fakes to prove
    # the reanchored lookup is what actually gets queried, not the raw spine doc_ref.
    monkeypatch.setattr(svc.q_service, "resolve_question_anchor", lambda doc_id: doc_id)
    monkeypatch.setattr(svc.db_questions, "get_container_by_doc", lambda doc_id: None)
    monkeypatch.setattr(svc.db_wfseq, "get_sequence_for_member_doc", lambda d: {"id": 1})
    monkeypatch.setattr(svc.db_wfseq, "get_effective_head", lambda s: {
        "item_seq": 1, "type": "N",
    })
    monkeypatch.setattr(svc.db_wfseq, "get_sequence_items", lambda s: [
        {"item_seq": 1, "type": "N", "result_doc_id": "d-N",
         "result_doc_review_status": "approved"},
        {"item_seq": 2, "type": "NR", "result_doc_id": None,
         "result_doc_review_status": None},
    ])
    monkeypatch.setattr(svc.db_projects, "get_by_id", lambda pid: {"project_name": "testproj"})
    monkeypatch.setattr(svc.ai_settings_service, "resolve_effective",
                        lambda pid: {"ok": True, **chain_holder})
    monkeypatch.setattr(svc.ai_settings_service, "get_provider_secret", lambda scope, pid: None)
    monkeypatch.setattr(svc.token_service, "issue", lambda **kw: {
        "raw_token": "tok_raw", "token_id": "tok_20260731_000001",
        "expires_at": FAR_FUTURE, "scratch_dir": str(tmp_path / "tokwork"),
    })
    monkeypatch.setattr(svc.token_service, "revoke", lambda *a, **kw: None)
    # The dead hop's token is normally still unconsumed — that is the whole reason the retry
    # can reuse it (NR0003 §6 measured 24 such tokens).
    monkeypatch.setattr(svc.db_tokens, "get_by_id", lambda tid: {
        "token_id": tid, "consumed_at": None, "revoked_at": None, "expires_at": FAR_FUTURE,
    })
    monkeypatch.setattr(svc.storage_paths, "get_storage_root", lambda *a, **kw: tmp_path / "storage")
    src_root = tmp_path / "srcroot"
    src_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(svc.storage_paths, "resolve_project_src_root",
                        lambda pid, branch, *, group_id: src_root)
    monkeypatch.setattr(svc.storage_paths, "to_storage_relative",
                        lambda path, project=None: str(path))
    for name in ("upsert", "get_by_group", "delete_by_group", "delete_and_return",
                 "exists", "list_by_user"):
        monkeypatch.setattr(db_paused, name, getattr(paused, name))
    monkeypatch.setattr(db_runs, "upsert", lambda row: records.append(dict(row)))
    monkeypatch.setattr(db_runs, "maybe_purge", lambda: None)
    monkeypatch.setattr(event_logger, "log_continuous_work_failed",
                        lambda **kw: signals.append(kw) or {})
    monkeypatch.setattr(svc, "_broadcast",
                        lambda run, event_type, payload: events.append((event_type, payload)))

    return {"docs": docs, "paused": paused, "chain": chain_holder, "records": records,
            "signals": signals, "events": events, "tmp": tmp_path}


def _start(env, *, target=2, provider_id=None, provider_pinned=None,
           provider_overrides=None, review_mode=False):
    return svc.start_run(
        project_id="flowgate",
        module="default",
        group_id=GROUP,
        doc_ref=DOC_REF,
        action_scope="new",
        mode="continuous",
        continuation_target_seq=target,
        continuation_review_mode=review_mode,
        continuation_instruction_mode=None,
        continuation_locale=None,
        issued_to="usr_admin",
        api_base_url="http://127.0.0.1:1/flowgate/api/v1",
        mention_builder=lambda raw, scratch: "## prompt\n",
        provider_id=provider_id,
        provider_pinned=provider_pinned,
        continuation_provider_overrides=provider_overrides,
    )


def _start_rework(env, **kw):
    """A 반려 재작업 run exactly as `ai_invoke_routes` starts one (0446 T0008).

    `_TOKEN_SCOPE` folds rework / vr_correction / work_plan_fill / plain edit all into the
    `edit` token scope (T0008 §3-9), so from the engine's side there is one shape and this
    helper is it: mode="single", action_scope="edit", no caller `completion_oracle`, and —
    for rework specifically — no `issue_builder` (the route declares none, §3-6).
    """
    return svc.start_run(
        project_id="flowgate",
        module="default",
        group_id=GROUP,
        doc_ref=DOC_REF,
        action_scope="edit",
        mode="single",
        continuation_target_seq=None,
        continuation_review_mode=False,
        continuation_instruction_mode=None,
        continuation_locale=None,
        issued_to="usr_admin",
        api_base_url="http://127.0.0.1:1/flowgate/api/v1",
        mention_builder=lambda raw, scratch: "## prompt\n",
        **kw,
    )


def _wait_finished(run_id, timeout=30.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        run = svc.get_run_record(run_id)
        if run and run["status"] == "finished":
            return run
        time.sleep(0.02)
    raise AssertionError(f"run {run_id} did not finish within {timeout}s")


def _scripted_worker(env, monkeypatch, script):
    """Replace the CLI adapter with a scripted one: script[i] runs for the i-th launch.

    Each entry is (last_message, registers_document). Every launch reports started_ok and
    exit code 0 — the exact shape of the incident: the worker did not crash, it gave up.
    """
    launches: list[str] = []

    def _fake(provider, prompt, run):
        index = len(launches)
        launches.append(provider["id"])
        message, registers = script[min(index, len(script) - 1)]
        run["exit_code"] = 0
        run["last_message"] = message
        run["last_message_received"] = bool(message)
        if registers:
            env["docs"].docs.append(
                {"doc_id": f"{GROUP}.0002-NR", "seq": 2, "status": "open"})
        return "started_ok", None

    monkeypatch.setattr(svc, "_cli_execute", _fake)
    return launches


def _scripted_rework_worker(env, monkeypatch, script):
    """The rework twin of `_scripted_worker` (0446 T0008 §4-2).

    An `edit` token cannot register a document, so "the work landed" here means the bound
    document's revision went up. Entries are (last_message, action) with action in
    (None, "revise", "late_revise") — "late_revise" arms the write to appear on the NEXT
    probe, i.e. after the judge looked and before a second worker could start.

    The oracle itself is never replaced: `start_run` builds the real `_scope_oracle` over
    the real `_probe_doc_revision`, which is what keeps §3-1's `scope_oracle_run` wiring
    under test rather than assumed.
    """
    launches: list[str] = []

    def _fake(provider, prompt, run):
        index = len(launches)
        launches.append(provider["id"])
        message, action = script[min(index, len(script) - 1)]
        run["exit_code"] = 0
        run["last_message"] = message
        run["last_message_received"] = bool(message)
        if action == "revise":
            env["docs"].revision_no += 1
        elif action == "late_revise":
            env["docs"].arm_late_revision(1)
        return "started_ok", None

    monkeypatch.setattr(svc, "_cli_execute", _fake)
    return launches


# ── The fix: a wasted lap becomes another attempt (L0007 §2.1 / P0006 [핵심]) ──

class TestNoOutputRetry:
    def test_same_provider_retry_gets_the_hop_and_saves_the_chain(self, env, monkeypatch):
        launches = _scripted_worker(env, monkeypatch, [
            ("작업을 진행할 수 없는 실행환경 장애가 발생했습니다.", False),   # the incident
            ("NR0003 등록 완료.", True),                                      # the rescue
        ])
        res = _start(env)
        run = _wait_finished(res["run_id"])

        assert launches == ["aip_1", "aip_1"]      # the selected provider owns both attempts
        assert run["attempts_used"] == 2
        assert (run["outcome"], run["docs_reached"]) == ("complete", 1)
        # Same run, same identity — P0006 [핵심] 1: a retry must not look like a second run.
        assert run["run_id"] == res["run_id"]

        switches = [p for t, p in env["events"] if t == "ai_invoke_provider_switched"]
        assert len(switches) == 1
        assert switches[0]["reason"] == "no_output"
        assert switches[0]["retry_kind"] == "no_output"
        assert switches[0]["token_reissued"] is False   # the dead hop's token was still good
        assert "실행환경 장애" in switches[0]["detail"]
        # started fires once per RUN, not once per attempt (P0006 부록 D).
        assert len([t for t, _ in env["events"] if t == "ai_invoke_started"]) == 1

        history = run["fallback_history"]
        assert [h["reason"] for h in history] == ["no_output"]
        assert history[0]["provider_id"] == "aip_1"
        assert history[0]["exit_code"] == 0
        # A rescued hop is not a failure: nobody is woken up for it.
        assert env["signals"] == []

    def test_individual_override_retries_same_once_then_stops(self, env, monkeypatch):
        launches = _scripted_worker(env, monkeypatch, [
            ("개별 프로바이더 첫 시도 결과 없음", False),
            ("개별 프로바이더 두 번째 시도 결과 없음", False),
        ])
        res = _start(
            env,
            provider_id="aip_3",
            provider_overrides={"2": "aip_2"},
        )
        run = _wait_finished(res["run_id"])

        assert launches == ["aip_2", "aip_2"]
        assert run["attempts_used"] == svc.NO_OUTPUT_MAX_ATTEMPTS == 2
        assert run["stop_code"] == "no_output_exhausted"


    def test_all_attempts_empty_stops_records_notifies_and_parks(self, env, monkeypatch):
        launches = _scripted_worker(env, monkeypatch, [
            ("- `CreateProcessAsUserW failed: 5 (Access denied)`\n- 재시도 4회 실패", False),
        ])
        res = _start(env, provider_id="aip_1", provider_pinned=False)
        run = _wait_finished(res["run_id"])

        assert launches == ["aip_1", "aip_1"]
        assert run["attempts_used"] == svc.NO_OUTPUT_MAX_ATTEMPTS == 2
        assert run["stop_code"] == "no_output_exhausted"
        assert run["resumable"] is True
        assert '"OpenAI Codex CLI" produced no document in 2 attempts' in run["stop_reason"]

        # 1. it survives a server restart (NR0003 §4: nothing was persisted at all before)
        assert len(env["records"]) == 1
        record = env["records"][0]
        assert record["run_id"] == res["run_id"]
        assert (record["stop_code"], record["resumable"]) == ("no_output_exhausted", True)
        assert record["attempts_used"] == 2
        assert record["hop_item_seq"] == 2
        assert record["token_id"] == "tok_20260731_000001"
        assert "CreateProcessAsUserW" in record["last_message_excerpt"]
        # The excerpt is one line: no list markers, no backticks, no newlines.
        assert "\n" not in record["last_message_excerpt"]
        assert "`" not in record["last_message_excerpt"]

        # 2. it reaches a human who was not watching at that second
        assert len(env["signals"]) == 1
        signal = env["signals"][0]
        assert signal["run_id"] == res["run_id"]
        assert signal["extra"]["stop_code"] == "no_output_exhausted"
        assert signal["extra"]["attempts_used"] == 2
        assert signal["extra"]["item_seq"] == 2
        assert "CreateProcessAsUserW" in signal["error"]
        # It lands on the last place the chain actually got to, never on nothing.
        assert signal["document_id"] == 77
        assert signal["doc_id"] == "d-N"

        # 3. it can be picked up again from the miniplayer
        row = env["paused"].rows[GROUP]
        assert (row["stop_kind"], row["stop_code"]) == ("system", "no_output_exhausted")
        assert row["stop_run_id"] == res["run_id"]
        assert "CreateProcessAsUserW" in row["stop_last_message_excerpt"]
        assert row["continuation_base_provider_id"] is None
        assert row["continuation_provider_pinned"] is False

    def test_single_provider_project_retries_once_then_stops(self, env, monkeypatch):
        env["chain"]["providers"] = [P1]
        launches = _scripted_worker(env, monkeypatch, [("아무것도 못 했습니다.", False)])
        res = _start(env)
        run = _wait_finished(res["run_id"])

        # Even a one-provider project gets exactly the one same-provider retry promised by
        # 0435 T0004, then stops loudly without inventing a fallback.
        assert launches == ["aip_1", "aip_1"]
        assert run["attempts_used"] == 2
        assert run["stop_code"] == "no_output_exhausted"
        assert len(env["signals"]) == 1

    def test_late_registration_cancels_the_retry(self, env, monkeypatch):
        # L0007 §5: the document lands after the judge looked but before a second worker
        # would start. Two documents from one hop is worse than one wasted hop.
        launches = _scripted_worker(env, monkeypatch, [("등록 시도 중...", False)])
        env["docs"].reveal_after_calls = 1
        env["docs"].pending = [{"doc_id": f"{GROUP}.0002-NR", "seq": 2, "status": "open"}]
        res = _start(env)
        run = _wait_finished(res["run_id"])

        assert launches == ["aip_1"]
        assert run["docs_reached"] == 1
        assert run["outcome"] == "complete"
        assert run["stop_code"] != "no_output_exhausted"
        assert env["signals"] == []

    def test_last_message_of_the_earlier_attempt_survives(self, env, monkeypatch):
        # A later attempt may say nothing at all; the sentence that explains the failure is
        # usually the one the first attempt left behind (L0007 §2.6).
        script = [("첫 시도가 남긴 원인 설명", False), (None, False), (None, False)]
        _scripted_worker(env, monkeypatch, script)
        res = _start(env)
        run = _wait_finished(res["run_id"])
        assert run["last_message"] == "첫 시도가 남긴 원인 설명"
        assert run["last_message_received"] is True


# ── What must NOT be retried (L0007 §2.4 / P0006 [엣지]) ─────────────────────

def _judged_run(**over):
    run = {
        "mode": "continuous", "cancel_event": threading.Event(), "end_reason": "exited",
        "pause_requested": False, "completion_oracle": None, "action_scope": "new",
        "docs_target": 1, "docs_reached": 0, "attempts_used": 1, "group_id": GROUP,
        "started_mono": time.monotonic(), "timeout_sec": 3600, "outcome": "none",
    }
    run.update(over)
    return run


class TestRetryEligibility:
    def test_the_incident_shape_is_eligible(self):
        assert svc._retry_eligible(_judged_run()) is True

    @pytest.mark.parametrize("over, why", [
        # 0446 T0008 §4-1: this used to read "a single run has no chain to save". It is now
        # split in two, because the mode alone no longer decides. (a) A single run with NO
        # engine-planted scope oracle is still judged by documents and still blocked; the
        # positive half (b) is `test_scope_oracle_rework_run_is_eligible` below.
        ({"mode": "single"},
         "a single run without the engine's scope oracle is judged by documents, so a "
         "retry has nothing new to measure"),
        ({"mode": "single", "action_scope": "chat", "scope_oracle_run": True,
          "completion_oracle": (lambda: False), "docs_target": 0},
         "T0008 §3-2 opens the `edit` scope only — chat/review/test_run/"
         "workflow_sequence_edit keep the 0259/0268 contract untouched"),
        ({"mode": "single", "action_scope": "edit", "scope_oracle_run": True,
          "completion_oracle": (lambda: True), "docs_target": 0, "outcome": "complete"},
         "T0008 §3-2: the scope oracle was satisfied — this rework DID raise the revision, "
         "and retrying it would write a second one over the worker's own"),
        ({"end_reason": "timeout"}, "the clock decided"),
        ({"end_reason": "user_paused"}, "the user decided"),
        ({"end_reason": "cancelled"}, "the user decided"),
        ({"end_reason": "all_providers_failed"}, "the provider walk already gave its verdict"),
        ({"pause_requested": True}, "a pause is pending"),
        # 0446 T0008 §4-1: still blocked, and now for a stated reason. `scope_oracle_run`
        # is pinned False so this case cannot silently turn green if `_judged_run`'s
        # defaults ever change: what stays blocked is a CALLER's override (0248 B0001, the
        # legacy Q&A follow-up), whose success criterion lives outside the engine.
        ({"completion_oracle": (lambda: True), "scope_oracle_run": False},
         "success is not measured in documents, and a caller's oracle is not the engine's "
         "to re-ask"),
        ({"action_scope": "workflow_decide"}, "not a document-producing hop"),
        ({"action_scope": "resolve_conflict"}, "not a document-producing hop"),
        ({"docs_target": 0}, "nothing was targeted"),
        ({"docs_reached": 1}, "partial output is still output"),
        ({"attempts_used": 2}, "the cap"),
        ({"timeout_sec": 60}, "less budget left than a hop can use"),
    ])
    def test_blocked(self, over, why):
        assert svc._retry_eligible(_judged_run(**over)) is False, why

    def test_cancel_signal_blocks_even_on_a_clean_exit(self):
        run = _judged_run()
        run["cancel_event"].set()
        assert svc._retry_eligible(run) is False

    def test_scope_oracle_rework_run_is_eligible(self):
        """0446 T0008 §4-1 (b) / §5-4: all four gates open at once, or nothing happens.

        NR0003 §6-2 named three (mode, docs_target, attempts_max). The fourth —
        `completion_oracle is not None` — is invisible from the outside because the ENGINE
        plants that oracle itself for every scoped run, so a rework never reaches this
        function with it unset.
        """
        run = _judged_run(mode="single", action_scope="edit", scope_oracle_run=True,
                          completion_oracle=(lambda: False), docs_target=0, outcome="none")
        assert svc._retry_eligible(run) is True

    def test_unwiring_the_scope_oracle_flag_closes_the_fourth_gate_again(self):
        # §5-4's evidence: with `scope_oracle_run` back to False (i.e. §3-1's wiring
        # reverted) the run is identical in every other respect and blocked again — so the
        # flag, not one of the other three edits, is what opens it.
        run = _judged_run(mode="single", action_scope="edit", scope_oracle_run=False,
                          completion_oracle=(lambda: False), docs_target=0, outcome="none")
        assert svc._retry_eligible(run) is False

    def test_a_pending_question_is_reached_and_named_on_a_rework_run(self, monkeypatch):
        # §3-2: the new outcome gate sits ABOVE `_has_pending_question`, so a hop that only
        # registered a Q (outcome "none") still reaches it and is labelled — the half of
        # this T that turns "quietly dead" into "waiting on a human".
        monkeypatch.setattr(svc.q_service, "resolve_question_anchor", lambda doc_id: ANCHOR)
        monkeypatch.setattr(svc.db_questions, "get_container_by_doc",
                            _container_lookup({ANCHOR: {"status": "pending"}}))
        run = _judged_run(mode="single", action_scope="edit", scope_oracle_run=True,
                          completion_oracle=(lambda: False), docs_target=0, outcome="none",
                          doc_ref=DOC_REF)
        assert svc._retry_eligible(run) is False
        assert run["retry_block_reason"] == "question_pending"

    def test_registration_errors_do_not_block(self):
        # "tried to register and failed" is still zero documents, and another AI may get
        # through — these three deliberately do NOT veto a retry (L0007 §2.4).
        assert svc._retry_eligible(_judged_run(
            register_errors=[{"status": 409}], tool_call_misses=2, turn_limit_exhausted=True,
        )) is True


class TestRetryProviderChain:
    def test_returns_only_the_selected_provider_on_attempt_one(self, env):
        run = {"project_id": "flowgate", "run_id": "aiv_x", "attempts_used": 1,
               "continuation_selected_provider_id": "aip_2"}
        assert [p["id"] for p in svc._retry_provider_chain(run)] == ["aip_2"]

    def test_empty_after_the_single_retry(self, env):
        run = {"project_id": "flowgate", "run_id": "aiv_x", "attempts_used": 2,
               "continuation_selected_provider_id": "aip_2"}
        assert svc._retry_provider_chain(run) == []

    def test_does_not_substitute_when_selected_provider_is_inactive(self, env):
        run = {"project_id": "flowgate", "run_id": "aiv_x", "attempts_used": 1,
               "continuation_selected_provider_id": "aip_removed"}
        assert svc._retry_provider_chain(run) == []


class TestRetryToken:
    def _run(self, tmp_path, **over):
        run = {"run_id": "aiv_x", "doc_ref": DOC_REF, "token_id": "tok_a",
               "mention": "## prompt\n", "issue_builder": None, "raw_token": "raw_a"}
        run.update(over)
        return run

    def test_unconsumed_token_is_reused(self, env, monkeypatch, tmp_path):
        prepared = svc._prepare_retry_token(self._run(tmp_path))
        assert prepared == {"mention": "## prompt\n", "token_id": "tok_a",
                            "token_id_before": "tok_a", "reissued": False}

    def test_expiring_token_is_reissued_for_the_same_step(self, env, monkeypatch, tmp_path):
        monkeypatch.setattr(svc.db_tokens, "get_by_id", lambda tid: {
            "token_id": tid, "consumed_at": None, "revoked_at": None,
            "expires_at": "2000-01-01T00:00:00+00:00",
        })

        def _issue(ai_run_id=None):
            return {"raw_token": "raw_b", "token_id": "tok_b", "mention": "## fresh\n",
                    "ai_run_id": ai_run_id}

        run = self._run(tmp_path, issue_builder=_issue)
        prepared = svc._prepare_retry_token(run)
        assert prepared["reissued"] is True
        assert (prepared["token_id"], prepared["token_id_before"]) == ("tok_b", "tok_a")
        assert run["token_id"] == "tok_b" and run["raw_token"] == "raw_b"

    def test_consumed_token_without_a_reissue_path_blocks_the_retry(self, env, monkeypatch,
                                                                    tmp_path):
        monkeypatch.setattr(svc.db_tokens, "get_by_id", lambda tid: {
            "token_id": tid, "consumed_at": "2026-07-31T00:00:00+00:00",
            "revoked_at": None, "expires_at": FAR_FUTURE,
        })
        assert svc._prepare_retry_token(self._run(tmp_path)) is None


# ── Stop classification (L0007 §4.1 ~ §4.3) ─────────────────────────────────

class TestStopCode:
    def test_no_output_is_named(self):
        run = _judged_run()
        assert svc._resolve_stop_code(run, respawn_pending=False) == "no_output_exhausted"

    def test_handoff_wins_over_no_output(self):
        # The hop registered nothing itself but the next hop is queued — that is a handoff,
        # not a failure (this is what an instruction-head hop looks like).
        run = _judged_run()
        assert svc._resolve_stop_code(run, respawn_pending=True) == "hop_handoff"

    def test_cancel_outranks_an_inbox_tag(self):
        run = _judged_run(end_reason="cancelled", inbox_stop_code="head_slot_mismatch")
        assert svc._resolve_stop_code(run, respawn_pending=False) == "cancelled"

    def test_inbox_tag_is_used_when_the_engine_has_no_verdict(self):
        run = _judged_run(inbox_stop_code="approve_denied")
        assert svc._resolve_stop_code(run, respawn_pending=False) == "approve_denied"

    def test_single_mode_without_a_scope_oracle_has_no_stop_code(self):
        # 0446 T0008 §4-1: was `test_single_mode_has_no_stop_code`. Half of it survives
        # unchanged — a plain single run still ends nameless — and the other half moved to
        # the two cases below, where the mode is the same and the verdict is not.
        run = _judged_run(mode="single")
        assert svc._resolve_stop_code(run, respawn_pending=False) is None

    def test_scope_oracle_rework_no_output_is_named(self):
        # §3-7: docs_target is 0 by construction here, so the name has to come from the axis
        # this run actually has — it exited on its own and its judge said "none".
        run = _judged_run(mode="single", action_scope="edit", scope_oracle_run=True,
                          docs_target=0, outcome="none", end_reason="exited")
        assert svc._resolve_stop_code(run, respawn_pending=False) == "no_output_exhausted"

    def test_a_rework_that_raised_the_revision_keeps_a_null_stop_code(self):
        # §3-7 regression pin: success must stay nameless, or every completed rework would
        # start reporting a stop.
        run = _judged_run(mode="single", action_scope="edit", scope_oracle_run=True,
                          docs_target=0, outcome="complete", end_reason="exited")
        assert svc._resolve_stop_code(run, respawn_pending=False) is None

    def test_a_blocked_retry_reason_reaches_the_persisted_sentence(self):
        # §3-6: `retry_block_reason` has no column in `ai_invoke_runs` and is absent from
        # `finished_payload`, so `stop_reason` is the only durable place it can be read.
        run = _judged_run(mode="single", action_scope="edit", scope_oracle_run=True,
                          docs_target=0, outcome="none", end_reason="exited",
                          attempts_used=1, retry_block_reason="token_unavailable",
                          continuation_selected_provider_name="OpenAI Codex CLI")
        text = svc._stop_reason_text("no_output_exhausted", run)
        assert "token_unavailable" in text

    @pytest.mark.parametrize("code, resumable", [
        ("no_output_exhausted", True), ("providers_exhausted", True), ("timeout", True),
        ("user_paused", True), ("question_pending", True), ("cancelled", False),
        ("head_slot_mismatch", False),
        ("approve_denied", False), ("approve_failed", False), ("advance_blocked", False),
        ("chain_completed", False), ("hop_handoff", False), ("review_hold", False),
        (None, False),
    ])
    def test_resumable_table(self, code, resumable):
        assert svc.is_resumable(code) is resumable

    def test_mark_chain_stop_needs_a_live_run(self, env):
        assert svc.mark_chain_stop(GROUP, "head_slot_mismatch") is False


# ── flowgate.default.0435: a human pin wins; no-output never changes provider ─

class TestSelectedProviderContract0435:
    def test_pinned_provider_outranks_a_different_stored_provider(self, env, monkeypatch):
        captured: dict = {}
        done = threading.Event()
        monkeypatch.setattr(
            svc,
            "stored_hop_provider",
            lambda *a, **kw: ("aip_2", "Anthropic Claude Opus 5", 2),
        )

        def _fake_worker(run, chain, prompt):
            captured["chain"] = chain
            run["status"] = "finished"
            done.set()

        monkeypatch.setattr(svc, "_worker", _fake_worker)
        _start(env, provider_id="aip_3", provider_pinned=True)
        assert done.wait(10)
        assert [provider["id"] for provider in captured["chain"]] == ["aip_3"]

    def test_inactive_explicit_pin_fails_without_substitution(self, env):
        with pytest.raises(Exception) as exc_info:
            _start(env, provider_id="aip_removed", provider_pinned=True)

        error = exc_info.value
        assert getattr(error, "status_code", None) == 422
        assert getattr(error, "detail", {}).get("code") == "provider_unavailable"

    def test_stored_provider_no_output_retries_the_same_provider_once(self, env, monkeypatch):
        monkeypatch.setattr(
            svc,
            "stored_hop_provider",
            lambda *a, **kw: ("aip_2", "Anthropic Claude Opus 5", 2),
        )
        launches = _scripted_worker(env, monkeypatch, [
            ("stored provider produced no document", False),
            ("stored provider retry also produced no document", False),
        ])
        res = _start(env)
        run = _wait_finished(res["run_id"])

        assert launches == ["aip_2", "aip_2"]
        assert run["attempts_used"] == 2
        assert run["stop_code"] == "no_output_exhausted"

    def test_second_no_output_stops_without_auto_resuming(self, env, monkeypatch):
        spawned: list[tuple] = []
        monkeypatch.setattr(svc, "_spawn_auto_resume", lambda *a, **kw: spawned.append((a, kw)))
        launches = _scripted_worker(env, monkeypatch, [
            ("selected provider produced no document", False),
            ("selected provider retry also produced no document", False),
        ])
        res = _start(env, provider_id="aip_1", provider_pinned=True)
        run = _wait_finished(res["run_id"])

        assert launches == ["aip_1", "aip_1"]
        assert run["stop_code"] == "no_output_exhausted"
        assert spawned == []
        row = env["paused"].rows[GROUP]
        assert row["continuation_base_provider_id"] == "aip_1"
        assert row["continuation_provider_pinned"] is True


# ── §2.1.1: an unpinned continuous header default keeps the fallback tail ────

class TestUnpinnedHeaderKeepsTheStartupTail:
    def _capture_chain(self, env, monkeypatch, **kw):
        captured: dict = {}
        done = threading.Event()

        def _fake_worker(run, chain, prompt):
            captured["chain"] = chain
            run["status"] = "finished"
            done.set()

        monkeypatch.setattr(svc, "_worker", _fake_worker)
        _start(env, **kw)
        assert done.wait(10)
        return captured["chain"]

    def test_unpinned_header_default_leads_the_chain_and_keeps_the_rest(self, env, monkeypatch):
        chain = self._capture_chain(env, monkeypatch, provider_id="aip_2")
        # An unpinned header value keeps legacy startup ordering; retries still stay on aip_2.
        assert [p["id"] for p in chain] == ["aip_2", "aip_1", "aip_3"]

    def test_single_pin_still_collapses(self, env, monkeypatch):
        captured: dict = {}
        done = threading.Event()
        monkeypatch.setattr(svc, "_worker", lambda run, chain, prompt: (
            captured.__setitem__("chain", chain), run.__setitem__("status", "finished"),
            done.set()))
        svc.start_run(
            project_id="flowgate", module="default", group_id=GROUP, doc_ref=DOC_REF,
            action_scope="new", mode="single", continuation_target_seq=None,
            continuation_review_mode=False, continuation_instruction_mode=None,
            continuation_locale=None, issued_to="usr_admin",
            api_base_url="http://127.0.0.1:1/flowgate/api/v1",
            mention_builder=lambda raw, scratch: "## prompt\n", provider_id="aip_2",
        )
        assert done.wait(10)
        # A human pointing at one provider for one run still means exactly that.
        assert [p["id"] for p in captured["chain"]] == ["aip_2"]


# ── §2.13 hop budget + §2.10.4 excerpt ──────────────────────────────────────

class TestHopBudget:
    def test_continuous_hop_is_a_flat_hour(self):
        # The old formula gave the LAST hop the SMALLEST budget (min(3600 × 남은 슬롯, 14400)).
        assert svc._resolve_timeout_sec("continuous", 1, False) == svc.HOP_TIMEOUT_SEC == 3600
        assert svc._resolve_timeout_sec("continuous", 5, True) == 3600

    def test_single_formula_is_untouched(self):
        assert svc._resolve_timeout_sec("single", 1, False) == svc.RUN_TIMEOUT_BASE_SEC
        assert svc._resolve_timeout_sec("single", 99, False) == svc.RUN_TIMEOUT_CAP_SEC

    def test_deadline_rides_the_start_response(self, env, monkeypatch):
        monkeypatch.setattr(svc, "_worker", lambda run, chain, prompt: None)
        res = _start(env)
        assert res["timeout_sec"] == 3600
        assert res["deadline_at"] > res["started_at"]


# -- NR0003 후속 조치 제안 1~3: a Q registered mid-hop is not a silent failure ----

class TestReviewModeDocsTarget:
    def test_review_mode_hop_targets_zero_documents(self, env, monkeypatch):
        monkeypatch.setattr(svc, "_worker", lambda run, chain, prompt: (
            run.__setitem__("status", "finished")))
        res = _start(env, review_mode=True)
        assert res["docs_target"] == 0
        assert res["chain_docs_target"] == 0

    def test_review_mode_question_only_hop_completes_without_retry(self, env, monkeypatch):
        # mention_service._CONTINUOUS_REVIEW_TEXT tells the worker to register a Q (even a
        # "no blockers" ack Q) and create nothing else — this is that hop's whole shape.
        launches = _scripted_worker(env, monkeypatch, [
            ("검토 완료 — 막는 의문 없음 — 이대로 진행해도 되는지 확인 요청 Q를 등록했습니다.", False),
        ])
        res = _start(env, review_mode=True)
        run = _wait_finished(res["run_id"])

        assert launches == ["aip_1"]           # no fallback burned on a hop that behaved
        assert run["attempts_used"] == 1
        assert (run["outcome"], run["docs_reached"]) == ("complete", 0)
        assert run["stop_code"] is None
        assert env["signals"] == []


def _judged_run_with_doc_ref(**over):
    return _judged_run(doc_ref=DOC_REF, **over)


# The reanchored work-context document (the TR/NR draft a user is actually looking at) —
# deliberately different from DOC_REF (the run's spine) so these tests fail if the guard
# regresses to querying the spine directly instead of going through resolve_question_anchor,
# exactly the bug NR0003 found (group 0389).
ANCHOR = "flowgate.default.0359.0003-TR"


def _container_lookup(mapping):
    return lambda doc_id: mapping.get(doc_id)


class TestPendingQuestionGuard:
    def test_blocked_while_a_question_is_still_open(self, monkeypatch):
        monkeypatch.setattr(svc.q_service, "resolve_question_anchor", lambda doc_id: ANCHOR)
        monkeypatch.setattr(svc.db_questions, "get_container_by_doc",
                             _container_lookup({ANCHOR: {"status": "pending"}}))
        run = _judged_run_with_doc_ref()
        assert svc._retry_eligible(run) is False
        assert run["retry_block_reason"] == "question_pending"

    def test_querying_the_spine_directly_would_miss_it(self, monkeypatch):
        # NR0003's actual bug: the container the router wrote lives under the reanchored
        # doc, never under the spine. The mapping below has no DOC_REF key, so a guard that
        # regressed to querying run["doc_ref"] as-is would see nothing and wrongly retry.
        assert DOC_REF != ANCHOR
        monkeypatch.setattr(svc.q_service, "resolve_question_anchor", lambda doc_id: ANCHOR)
        monkeypatch.setattr(svc.db_questions, "get_container_by_doc",
                             _container_lookup({ANCHOR: {"status": "pending"}}))
        run = _judged_run_with_doc_ref()
        assert svc._retry_eligible(run) is False
        assert run["retry_block_reason"] == "question_pending"

    def test_not_blocked_once_the_question_is_answered(self, monkeypatch):
        monkeypatch.setattr(svc.q_service, "resolve_question_anchor", lambda doc_id: ANCHOR)
        monkeypatch.setattr(svc.db_questions, "get_container_by_doc",
                             _container_lookup({ANCHOR: {"status": "done"}}))
        assert svc._retry_eligible(_judged_run_with_doc_ref()) is True

    def test_not_blocked_when_no_question_was_ever_registered(self, monkeypatch):
        monkeypatch.setattr(svc.q_service, "resolve_question_anchor", lambda doc_id: ANCHOR)
        monkeypatch.setattr(svc.db_questions, "get_container_by_doc", _container_lookup({}))
        assert svc._retry_eligible(_judged_run_with_doc_ref()) is True

    def test_probe_failure_does_not_block_the_retry(self, monkeypatch):
        monkeypatch.setattr(svc.q_service, "resolve_question_anchor", lambda doc_id: ANCHOR)

        def _boom(doc_id):
            raise RuntimeError("db unavailable")

        monkeypatch.setattr(svc.db_questions, "get_container_by_doc", _boom)
        assert svc._retry_eligible(_judged_run_with_doc_ref()) is True

    def test_anchor_resolution_failure_does_not_block_the_retry(self, monkeypatch):
        def _boom(doc_id):
            raise RuntimeError("sequence lookup unavailable")

        monkeypatch.setattr(svc.q_service, "resolve_question_anchor", _boom)
        assert svc._retry_eligible(_judged_run_with_doc_ref()) is True

    def test_partial_output_still_wins_over_a_stale_open_question(self, monkeypatch):
        # docs_reached >= 1 must block the retry on its own account — an unrelated open
        # question must not relabel a hop that already produced its document.
        monkeypatch.setattr(svc.q_service, "resolve_question_anchor", lambda doc_id: ANCHOR)
        monkeypatch.setattr(svc.db_questions, "get_container_by_doc",
                             _container_lookup({ANCHOR: {"status": "pending"}}))
        run = _judged_run_with_doc_ref(docs_reached=1)
        assert svc._retry_eligible(run) is False
        assert run.get("retry_block_reason") != "question_pending"


class TestQuestionPendingStopCode:
    def test_question_pending_is_named(self):
        run = _judged_run(retry_block_reason="question_pending")
        assert svc._resolve_stop_code(run, respawn_pending=False) == "question_pending"

    def test_question_pending_outranks_no_output_exhausted(self):
        # Same docs_target/docs_reached/outcome shape as the no_output_exhausted branch —
        # only retry_block_reason tells them apart, and it must win.
        run = _judged_run(retry_block_reason="question_pending")
        assert svc._resolve_stop_code(run, respawn_pending=False) != "no_output_exhausted"

    def test_question_pending_is_resumable(self):
        assert svc.is_resumable("question_pending") is True

    def test_question_pending_never_raises_the_failure_notification(self):
        assert "question_pending" not in svc.ENGINE_NOTIFY_STOP_CODES

    def test_question_pending_has_a_stop_reason_sentence(self):
        run = _judged_run(retry_block_reason="question_pending")
        text = svc._stop_reason_text("question_pending", run)
        assert text and "waiting" in text.lower()


class TestPendingQuestionEndToEnd:
    def test_question_only_hop_stops_without_burning_retries_or_alerting(self, env, monkeypatch):
        monkeypatch.setattr(svc.q_service, "resolve_question_anchor", lambda doc_id: ANCHOR)
        monkeypatch.setattr(svc.db_questions, "get_container_by_doc",
                             _container_lookup({ANCHOR: {"status": "pending"}}))
        launches = _scripted_worker(env, monkeypatch, [
            ("작업 전 확인이 필요해 Q를 등록했습니다.", False),
        ])
        res = _start(env)
        run = _wait_finished(res["run_id"])

        assert launches == ["aip_1"]            # never fell back to aip_2/aip_3
        assert run["attempts_used"] == 1
        assert run["outcome"] == "none"
        assert run["stop_code"] == "question_pending"
        assert run["resumable"] is True
        # The whole point: a hop waiting on a human answer must not read as a failure.
        assert env["signals"] == []

        row = env["paused"].rows[GROUP]
        assert (row["stop_kind"], row["stop_code"]) == ("system", "question_pending")


# -- 0446 T0008 (NR0003 R2~R4): 반려 재작업(single/edit) gets the recovery machinery ----
#
# NR0003 measured 27 of 242 rework runs registering nothing at all, and the 15 that ended
# cleanly all shared one record shape: attempts_used 1, stop_code NULL, zero notifications.
# Everything below is that shape, run end to end through the real engine.


class TestSingleReworkNoOutputRecovery0446:
    def _no_sequence(self, monkeypatch):
        """A rejected document is usually not a workflow sequence member (§3-8).

        The fixture pins a sequence so the continuous tests have a hop anchor; clearing it
        here is what makes `_anchor_document` fall through to `run["doc_ref"]`, and the
        assertion below is that the notification then lands on the rejected document itself.
        """
        monkeypatch.setattr(svc.db_wfseq, "get_sequence_for_member_doc", lambda d: None)

    def test_no_output_rework_retries_once_names_the_stop_and_alerts(self, env, monkeypatch):
        """§4-2 case 1 — the whole point of this T, in one run."""
        self._no_sequence(monkeypatch)
        launches = _scripted_rework_worker(env, monkeypatch, [
            ("반려 2건 다 고쳤고 백그라운드 전체 스위트 결과를 기다리는 중입니다.", None),
        ])
        res = _start_rework(env, provider_id="aip_1", provider_pinned=True)
        run = _wait_finished(res["run_id"])

        # 1. the retry happened at all — the four gates of §3-1/§3-2 are open
        assert launches == ["aip_1", "aip_1"]
        assert run["attempts_used"] == svc.NO_OUTPUT_MAX_ATTEMPTS == 2
        # §3-3: the advertised ceiling now matches the one the engine enforces
        assert run["attempts_max"] == 2
        # §3-5: the retry had a chain to walk because the head was recorded
        assert run["continuation_selected_provider_id"] == "aip_1"
        switches = [p for t, p in env["events"] if t == "ai_invoke_provider_switched"]
        assert len(switches) == 1
        assert (switches[0]["reason"], switches[0]["retry_kind"]) == ("no_output", "no_output")
        # §3-6: reuse only. rework declares no issue_builder, and NR0003 measured 15/15 of
        # these tokens still unconsumed, so the reissue path is never needed.
        assert switches[0]["token_reissued"] is False
        assert run["docs_reached"] == 0            # an edit token registers nothing, ever

        # 2. the failure has a name — 15 of 15 measured runs had stop_code NULL
        assert run["stop_code"] == "no_output_exhausted"
        assert run["outcome"] == "none"
        assert run["oracle_mismatch"] is True
        assert run["resumable"] is True
        assert '"OpenAI Codex CLI" produced no document in 2 attempts' in run["stop_reason"]
        assert len(env["records"]) == 1
        record = env["records"][0]
        assert (record["stop_code"], record["attempts_used"]) == ("no_output_exhausted", 2)
        assert record["attempts_max"] == 2
        assert "백그라운드" in record["last_message_excerpt"]

        # 3. a human who was not watching that minute still finds out
        assert len(env["signals"]) == 1
        signal = env["signals"][0]
        assert signal["run_id"] == res["run_id"]
        assert signal["extra"]["stop_code"] == "no_output_exhausted"
        assert signal["extra"]["attempts_used"] == 2
        # §3-8: same event type as a dead continuous chain, told apart by these two
        assert (signal["extra"]["mode"], signal["extra"]["action_scope"]) == ("single", "edit")
        # §3-8: with no sequence to walk, the notification anchors on the rejected document
        assert signal["doc_id"] == DOC_REF

        # 4. §3-7: `resumable` is True but `_apply_stop_row` still returns on the first line
        # for a non-continuous run, so no ghost [resume] card is created.
        assert GROUP not in env["paused"].rows

    def test_a_question_only_rework_is_named_question_pending_and_stays_silent(
            self, env, monkeypatch):
        """§4-2 case 2 — NR0003's 4 legitimate stops, told apart from the 11 dead ones."""
        self._no_sequence(monkeypatch)
        monkeypatch.setattr(svc.q_service, "resolve_question_anchor", lambda doc_id: ANCHOR)
        monkeypatch.setattr(svc.db_questions, "get_container_by_doc",
                            _container_lookup({ANCHOR: {"status": "pending"}}))
        launches = _scripted_rework_worker(env, monkeypatch, [
            ("원격 도구 503. Q item_id=202 등록. 불완전한 TR은 제출하지 않았습니다.", None),
        ])
        res = _start_rework(env, provider_id="aip_1", provider_pinned=True)
        run = _wait_finished(res["run_id"])

        assert launches == ["aip_1"]               # no attempt burned on a hop that behaved
        assert run["attempts_used"] == 1
        assert run["retry_block_reason"] == "question_pending"
        assert run["stop_code"] == "question_pending"
        assert run["resumable"] is True
        # The half of this T that R2 asked for: waiting on a human is not a failure, so
        # nobody is woken up. `question_pending` is deliberately not in ENGINE_NOTIFY_STOP_CODES.
        assert env["signals"] == []
        assert env["records"][0]["stop_code"] == "question_pending"
        assert GROUP not in env["paused"].rows

    def test_a_rework_that_raised_the_revision_is_a_plain_success(self, env, monkeypatch):
        """§4-2 case 3 — the positive control. Absence assertions alone prove nothing."""
        self._no_sequence(monkeypatch)
        launches = _scripted_rework_worker(env, monkeypatch, [
            ("반려 사유 3건을 모두 반영해 TR을 수정했습니다.", "revise"),
        ])
        before = env["docs"].revision_no
        res = _start_rework(env, provider_id="aip_1", provider_pinned=True)
        run = _wait_finished(res["run_id"])

        assert launches == ["aip_1"]               # §3-2's outcome gate: success is not retried
        assert run["attempts_used"] == 1
        assert run["outcome"] == "complete"
        assert run["oracle_mismatch"] is False
        assert run["stop_code"] is None
        assert run["resumable"] is False
        assert env["signals"] == []
        assert env["docs"].revision_no == before + 1     # written exactly once
        assert GROUP not in env["paused"].rows

    def test_a_late_revision_cancels_the_retry_instead_of_double_writing(
            self, env, monkeypatch):
        """§4-2 case 4 / §3-4 — the duplicate-write guard, which was a constant True."""
        self._no_sequence(monkeypatch)
        launches = _scripted_rework_worker(env, monkeypatch, [
            ("등록 중...", "late_revise"),
        ])
        before = env["docs"].revision_no
        res = _start_rework(env, provider_id="aip_1", provider_pinned=True)
        run = _wait_finished(res["run_id"])

        # The judge looked and saw nothing; `_recheck_no_output` asked the scope oracle again
        # and saw the write. Without §3-4 the document query would have said "still empty"
        # (it always does for an edit run) and a second worker would have started.
        assert launches == ["aip_1"]
        assert run["outcome"] == "complete"
        assert run["docs_reached"] == 0
        assert run["oracle_mismatch"] is False
        assert run["stop_code"] is None
        assert env["signals"] == []
        assert env["docs"].revision_no == before + 1     # exactly one revision, not two

    def test_a_spent_token_blocks_the_retry_and_says_so(self, env, monkeypatch):
        """§3-6 — no reissue path for rework, so the reason must be visible instead."""
        self._no_sequence(monkeypatch)
        monkeypatch.setattr(svc.db_tokens, "get_by_id", lambda tid: {
            "token_id": tid, "consumed_at": "2026-08-19T00:00:00+00:00",
            "revoked_at": None, "expires_at": FAR_FUTURE,
        })
        launches = _scripted_rework_worker(env, monkeypatch, [("아무것도 못 했습니다.", None)])
        res = _start_rework(env, provider_id="aip_1", provider_pinned=True)
        run = _wait_finished(res["run_id"])

        assert launches == ["aip_1"]
        assert run["retry_block_reason"] == "token_unavailable"
        # The stop is still named and still notified — "why was there no retry?" has to be
        # answerable, and `retry_block_reason` itself is not persisted anywhere else.
        assert run["stop_code"] == "no_output_exhausted"
        assert "token_unavailable" in run["stop_reason"]
        assert env["records"][0]["stop_reason"] == run["stop_reason"]
        assert len(env["signals"]) == 1

    def test_an_edit_scope_with_an_issue_builder_reissues_the_spent_token(
            self, env, monkeypatch):
        """§3-9 — `_TOKEN_SCOPE` folds four invoke scopes into `edit`, so all four open.

        rework and plain `edit` declare no `issue_builder` (`ai_invoke_routes` assigns one
        only for review / workflow_sequence_edit / work_plan_fill / work_plan_proposal /
        test_run / workflow_decide / the continuous first hop), so for them a spent token
        ends the retry — `test_a_spent_token_blocks_the_retry_and_says_so` above. Of the
        four edit-folded scopes only `work_plan_fill` has one, and this is that shape: the
        reissue path opens and the retry survives a spent token.
        """
        self._no_sequence(monkeypatch)
        monkeypatch.setattr(svc.db_tokens, "get_by_id", lambda tid: {
            "token_id": tid, "consumed_at": "2026-08-19T00:00:00+00:00",
            "revoked_at": None, "expires_at": FAR_FUTURE,
        })

        def _issue(ai_run_id=None):
            return {"raw_token": "raw_b", "token_id": "tok_b", "mention": "## fresh\n",
                    "scratch_dir": str(env["tmp"] / "tokwork"), "ai_run_id": ai_run_id}

        launches = _scripted_rework_worker(env, monkeypatch, [
            ("작업계획 칸을 채우지 못했습니다.", None),
        ])
        res = _start_rework(env, provider_id="aip_1", provider_pinned=True,
                            issue_builder=_issue)
        run = _wait_finished(res["run_id"])

        assert launches == ["aip_1", "aip_1"]
        switches = [p for t, p in env["events"] if t == "ai_invoke_provider_switched"]
        assert switches[0]["token_reissued"] is True
        assert run["token_id"] == "tok_b"
        assert run["stop_code"] == "no_output_exhausted"
        assert len(env["signals"]) == 1

    def test_the_duplicate_write_guard_holds_for_every_edit_scope(self, env, monkeypatch):
        """§3-9 — the same late write, on the reissue-capable shape.

        `_recheck_no_output` is asked before `_prepare_retry_token` is ever called, so the
        guard lands identically whether the scope can reissue a token or not: one revision,
        one launch, no second worker.
        """
        self._no_sequence(monkeypatch)

        def _issue(ai_run_id=None):
            return {"raw_token": "raw_b", "token_id": "tok_b", "mention": "## fresh\n",
                    "scratch_dir": str(env["tmp"] / "tokwork"), "ai_run_id": ai_run_id}

        launches = _scripted_rework_worker(env, monkeypatch, [("저장 중...", "late_revise")])
        before = env["docs"].revision_no
        res = _start_rework(env, provider_id="aip_1", provider_pinned=True,
                            issue_builder=_issue)
        run = _wait_finished(res["run_id"])

        assert launches == ["aip_1"]
        assert run["outcome"] == "complete"
        assert run["stop_code"] is None
        assert env["docs"].revision_no == before + 1
        assert env["signals"] == []

    def test_a_caller_supplied_oracle_run_keeps_the_old_single_shape(self, env, monkeypatch):
        """§3-1 — the legacy Q&A follow-up (`q_answer_invoke_service`) must not change.

        Same scope and mode as a rework, but the oracle came from the CALLER, so
        `_uses_scope_oracle` is False, nothing is retried and nothing is named.
        """
        self._no_sequence(monkeypatch)
        launches = _scripted_rework_worker(env, monkeypatch, [("답변을 남기지 못했습니다.", None)])
        res = _start_rework(env, provider_id="aip_1", provider_pinned=True,
                            completion_oracle=(lambda: False))
        run = _wait_finished(res["run_id"])

        assert launches == ["aip_1"]
        assert run["attempts_used"] == 1
        assert run["attempts_max"] == 1
        assert run["scope_oracle_run"] is False
        assert run["stop_code"] is None
        assert env["signals"] == []


class TestExcerpt:
    def test_markdown_becomes_one_readable_line(self):
        text = "작업을 진행할 수 없습니다.\n\n- 첫 `GET` 4회 실패\n- 권한 없음\n"
        assert svc.excerpt(text) == "작업을 진행할 수 없습니다. 첫 GET 4회 실패 권한 없음"

    def test_cut_never_breaks_a_character(self):
        out = svc.excerpt("가" * 400, max_bytes=20)
        assert out.endswith("…")
        assert len(out.encode("utf-8")) <= 20
        assert "�" not in out

    def test_blank_is_none(self):
        assert svc.excerpt(None) is None
        assert svc.excerpt("   \n  ") is None
