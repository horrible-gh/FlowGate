"""flowgate.default.0551 T#1: auto-dispatch the Q responder for a `question_pending` stop.

NR0003 §11 제안 1/2 found two missing connections and nothing else: a responder-priority
resolver (reviewer -> header/default selected provider -> the existing provider chain /
default policy) and the wiring between a `question_pending` park and the existing
`q_answer_invoke_service.dispatch_answer_run` — the same dispatch a human's own
[AI 답변 요청] click already performs. WP0004 T#1 explicitly rules out building a new AI
execution/dispatch engine, so every test below exercises the real production functions
(`review.resolve_question_responder`, `finalize._dispatch_question_responder`,
`finalize._finalize_run`) and never a reimplementation of them.

  * TestResolveQuestionResponder — the pure priority resolver in isolation.
  * TestDispatchQuestionResponder — the dispatch wiring (container/item lookup, item_seq
    resolution, provider handoff, the "one item only" multi-Q policy, and failure
    swallowing) against `finalize._dispatch_question_responder` directly, with every
    collaborator it reaches through a function-local import monkeypatched at its own
    source module.
  * TestFinalizeRunHookPlacement — end to end through the real engine (reusing
    test_ai_invoke_no_output_retry_0359's `env` fixture and continuous-mode harness),
    proving the hook fires exactly on a `question_pending` stop and only after the group
    lease `dispatch_answer_run` needs has actually been released.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")

_SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SERVER_DIR))

from modules.flow_gate.db import group_ai_leases as db_group_ai_leases  # noqa: E402
from modules.flow_gate.db import question_items as db_question_items  # noqa: E402
from modules.flow_gate.db import questions as db_questions  # noqa: E402
from modules.flow_gate.services import q_answer_invoke_service  # noqa: E402
from modules.flow_gate.services import q_service  # noqa: E402
from modules.flow_gate.services.ai_invoke import finalize  # noqa: E402
from modules.flow_gate.services.ai_invoke import review  # noqa: E402

import test_ai_invoke_no_output_retry_0359 as base  # noqa: E402
from test_ai_invoke_no_output_retry_0359 import env  # noqa: E402,F401  (pytest fixture)


def _provider(pid, name):
    return {
        "id": pid, "name": name, "exec_type": "cli", "kind": "claude",
        "enabled": True, "cli_command": "noop", "api_base_url": None,
        "api_model": None, "api_key_set": False, "api_key_hint": None,
    }


REVIEWER = _provider("aip_reviewer", "Reviewer Claude")
HEADER = _provider("aip_header", "Header Codex")
CHAIN = [REVIEWER, HEADER]


class TestResolveQuestionResponder:
    """review.resolve_question_responder — pure priority logic (WP0004 T#1 note):
    1) the step's valid reviewer, 2) the run's header/default selected provider,
    3) neither -> None, leaving dispatch_answer_run's own default-chain policy to decide.
    """

    @pytest.fixture(autouse=True)
    def _chain(self, monkeypatch):
        monkeypatch.setattr(review.ai_settings_service, "resolve_effective",
                             lambda pid: {"providers": CHAIN})

    def test_a_valid_reviewer_outranks_everything(self):
        assert review.resolve_question_responder(
            {"3": "aip_reviewer"}, 3, "aip_header", "flowgate") == "aip_reviewer"

    def test_no_reviewer_entry_falls_to_the_header_pick(self):
        assert review.resolve_question_responder(
            None, 3, "aip_header", "flowgate") == "aip_header"

    def test_a_disabled_reviewer_falls_to_the_header_pick_not_the_project_default(self):
        # aip_gone is not in CHAIN. CHAIN[0] (aip_reviewer) must NOT win here -- the header
        # pick sits between step 1 and the project default (unlike resolve_reviewer's own
        # fallback, which would jump straight past the header pick to the project default).
        assert review.resolve_question_responder(
            {"3": "aip_gone"}, 3, "aip_header", "flowgate") == "aip_header"

    def test_a_disabled_header_pick_falls_through_to_none(self):
        assert review.resolve_question_responder(
            None, 3, "aip_gone", "flowgate") is None

    def test_neither_reviewer_nor_header_returns_none_for_the_caller_default(self):
        assert review.resolve_question_responder(None, 3, None, "flowgate") is None

    def test_reviewer_keyed_for_a_different_item_seq_does_not_apply(self):
        assert review.resolve_question_responder(
            {"5": "aip_reviewer"}, 3, "aip_header", "flowgate") == "aip_header"

    def test_reviewer_accepts_an_integer_key_too(self):
        # _map_lookup (oracle.py) accepts both string and int keys; the resolver must not
        # narrow that back down to string-only.
        assert review.resolve_question_responder(
            {3: "aip_reviewer"}, 3, "aip_header", "flowgate") == "aip_reviewer"


ANCHOR_DOC = "flowgate.default.0551.0001-T"
DOC = {
    "doc_id": ANCHOR_DOC, "project_id": "flowgate", "group_id": "flowgate.default.0551",
    "title": "작업지시", "file_path": None,
}


def _run(**overrides):
    row = {
        "doc_ref": "flowgate.default.0551.0002-N",
        "project_id": "flowgate",
        "issued_to": "u-worker",
        "api_base_url": "http://127.0.0.1:8089/flowgate/api/v1",
        "continuation_reviewer_overrides": None,
        "continuation_base_provider_id": None,
        "continuation_instruction_mode": None,
        "continuation_auto_approve_item_seqs": None,
    }
    row.update(overrides)
    return row


class TestDispatchQuestionResponder:
    """finalize._dispatch_question_responder — the wiring itself (NR0003 제안 2), called
    directly with every function-local-imported collaborator monkeypatched at its own
    source module (exactly what a `from x import y` inside the function body re-reads on
    every call)."""

    @pytest.fixture(autouse=True)
    def _wire(self, monkeypatch):
        self.calls: list[dict] = []
        monkeypatch.setattr(q_service, "resolve_question_anchor", lambda doc_id: ANCHOR_DOC)
        monkeypatch.setattr(finalize.db_docs, "get_by_id",
                             lambda doc_id: dict(DOC) if doc_id == ANCHOR_DOC else None)
        monkeypatch.setattr(finalize.admission, "continuation_hop_item_seq",
                             lambda *a, **kw: 3)
        monkeypatch.setattr(
            q_answer_invoke_service, "resolve_item",
            lambda doc_id, item_id: {
                "id": item_id, "seq": item_id, "title": "Q", "body": "b", "options": [],
            },
        )

        def _dispatch(**kw):
            self.calls.append(kw)
            return {"run_id": "aiv_responder", "status": "running"}

        monkeypatch.setattr(q_answer_invoke_service, "dispatch_answer_run", _dispatch)

    def test_no_doc_ref_short_circuits_before_any_lookup(self, monkeypatch):
        def _boom(doc_id):
            raise AssertionError("should not be reached when doc_ref is missing")

        monkeypatch.setattr(db_questions, "get_container_by_doc", _boom)
        finalize._dispatch_question_responder(_run(doc_ref=None))
        assert self.calls == []

    def test_no_container_does_not_dispatch(self, monkeypatch):
        monkeypatch.setattr(db_questions, "get_container_by_doc", lambda doc_id: None)
        finalize._dispatch_question_responder(_run())
        assert self.calls == []

    def test_a_done_container_does_not_dispatch(self, monkeypatch):
        monkeypatch.setattr(db_questions, "get_container_by_doc",
                             lambda doc_id: {"id": 1, "status": "done"})
        finalize._dispatch_question_responder(_run())
        assert self.calls == []

    def test_a_pending_container_with_no_unanswered_rows_does_not_dispatch(self, monkeypatch):
        monkeypatch.setattr(db_questions, "get_container_by_doc",
                             lambda doc_id: {"id": 1, "status": "pending"})
        monkeypatch.setattr(db_question_items, "list_unanswered", lambda qpk: [])
        finalize._dispatch_question_responder(_run())
        assert self.calls == []

    def test_an_unresolvable_document_does_not_dispatch(self, monkeypatch):
        monkeypatch.setattr(db_questions, "get_container_by_doc",
                             lambda doc_id: {"id": 1, "status": "pending"})
        monkeypatch.setattr(db_question_items, "list_unanswered",
                             lambda qpk: [{"id": 201, "seq": 1}])
        monkeypatch.setattr(finalize.db_docs, "get_by_id", lambda doc_id: None)
        finalize._dispatch_question_responder(_run())
        assert self.calls == []

    def test_multiple_pending_questions_dispatch_only_the_earliest_seq(self, monkeypatch):
        # T#1's multi-Q boundary: one AI run answers exactly one item, and the group lease
        # allows only one run per group at a time, so firing one per pending item would only
        # manufacture run_in_progress failures for every item after the first.
        monkeypatch.setattr(db_questions, "get_container_by_doc",
                             lambda doc_id: {"id": 1, "status": "pending"})
        monkeypatch.setattr(db_question_items, "list_unanswered",
                             lambda qpk: [{"id": 202, "seq": 2}, {"id": 201, "seq": 1}])
        finalize._dispatch_question_responder(_run())
        assert len(self.calls) == 1
        assert self.calls[0]["item"]["id"] == 201

    def test_the_resolved_provider_reaches_dispatch_answer_run(self, monkeypatch):
        monkeypatch.setattr(db_questions, "get_container_by_doc",
                             lambda doc_id: {"id": 1, "status": "pending"})
        monkeypatch.setattr(db_question_items, "list_unanswered",
                             lambda qpk: [{"id": 201, "seq": 1}])
        monkeypatch.setattr(
            review, "resolve_question_responder",
            lambda reviewer_overrides, item_seq, base_provider_id, project_id: "aip_reviewer",
        )
        finalize._dispatch_question_responder(
            _run(continuation_reviewer_overrides={"3": "aip_reviewer"}))
        assert self.calls[0]["provider_id"] == "aip_reviewer"

    def test_reviewer_overrides_item_seq_and_base_provider_reach_the_resolver(self, monkeypatch):
        monkeypatch.setattr(db_questions, "get_container_by_doc",
                             lambda doc_id: {"id": 1, "status": "pending"})
        monkeypatch.setattr(db_question_items, "list_unanswered",
                             lambda qpk: [{"id": 201, "seq": 1}])
        seen: dict = {}

        def _resolver(reviewer_overrides, item_seq, base_provider_id, project_id):
            seen.update(reviewer_overrides=reviewer_overrides, item_seq=item_seq,
                        base_provider_id=base_provider_id, project_id=project_id)
            return None

        monkeypatch.setattr(review, "resolve_question_responder", _resolver)
        finalize._dispatch_question_responder(_run(
            continuation_reviewer_overrides={"3": "aip_x"},
            continuation_base_provider_id="aip_header",
        ))
        assert seen == {
            "reviewer_overrides": {"3": "aip_x"}, "item_seq": 3,
            "base_provider_id": "aip_header", "project_id": "flowgate",
        }

    def test_no_resolved_provider_still_dispatches_with_none(self, monkeypatch):
        # Priority 3 (T#1 WP note): dispatch_answer_run's own provider chain / default
        # policy decides, exactly as a manual [AI 답변 요청] click with nothing selected.
        monkeypatch.setattr(db_questions, "get_container_by_doc",
                             lambda doc_id: {"id": 1, "status": "pending"})
        monkeypatch.setattr(db_question_items, "list_unanswered",
                             lambda qpk: [{"id": 201, "seq": 1}])
        finalize._dispatch_question_responder(_run())
        assert len(self.calls) == 1
        assert self.calls[0]["provider_id"] is None

    def test_a_dispatch_admission_failure_is_swallowed_not_raised(self, monkeypatch):
        # Stand-in for "responder 실행 불가" (NR0003 §10): no_enabled_provider /
        # provider_unavailable / run_in_progress all arrive as HTTPException from
        # dispatch_answer_run's own admission path -- a successful question_pending stop
        # must never become an unhandled exception because of it.
        monkeypatch.setattr(db_questions, "get_container_by_doc",
                             lambda doc_id: {"id": 1, "status": "pending"})
        monkeypatch.setattr(db_question_items, "list_unanswered",
                             lambda qpk: [{"id": 201, "seq": 1}])

        def _boom(**kw):
            from fastapi import HTTPException
            raise HTTPException(status_code=409, detail={"code": "no_enabled_provider"})

        monkeypatch.setattr(q_answer_invoke_service, "dispatch_answer_run", _boom)
        finalize._dispatch_question_responder(_run())  # must not raise

    def test_a_container_lookup_failure_is_also_swallowed(self, monkeypatch):
        def _boom(doc_id):
            raise RuntimeError("db unavailable")

        monkeypatch.setattr(db_questions, "get_container_by_doc", _boom)
        finalize._dispatch_question_responder(_run())  # must not raise
        assert self.calls == []


class TestDispatchQuestionResponderRealExecution:
    """T#1's boundary conditions provider "실행불가" and "timeout" (WP0004 T#1 note),
    driven through the REAL `q_answer_invoke_service.dispatch_answer_run` ->
    `ai_invoke_service.start_run` admission/execution path — unlike
    `test_a_dispatch_admission_failure_is_swallowed_not_raised` above, neither
    `dispatch_answer_run` nor its admission is stubbed here. Reuses 0359's `env`
    fixture/engine (`base.svc`) so the run really is admitted, leased and judged by the
    production code, with only the CLI subprocess itself faked (`base._scripted_worker`,
    exactly as every other real-engine test in this suite does).

    Each test also proves the REQUIRED fallback/recovery policy (NR0003 §11 제안2's own
    words): the question stays exactly as unanswered as it was before the failed/timed-out
    attempt (nothing here can mark it answered), and a subsequent dispatch on the same,
    unmodified code path is admitted and runs for real — the failure left no stuck
    lease/token for a retry (automatic or a human's own [AI 답변 요청]) to trip over.
    """

    @pytest.fixture(autouse=True)
    def _wire(self, monkeypatch, env):
        # Must run AFTER `env` (declared as a dependency, not just a sibling parameter):
        # `env` patches db_questions.get_container_by_doc to "nothing ever pending" by
        # default, and an autouse fixture with no declared dependency on `env` is not
        # guaranteed to run after it — it would silently win and every test below would
        # short-circuit on "no container" before ever reaching dispatch_answer_run.
        monkeypatch.setattr(q_service, "resolve_question_anchor", lambda doc_id: ANCHOR_DOC)
        monkeypatch.setattr(finalize.db_docs, "get_by_id",
                             lambda doc_id: dict(DOC) if doc_id == ANCHOR_DOC else None)
        monkeypatch.setattr(finalize.admission, "continuation_hop_item_seq",
                             lambda *a, **kw: 3)
        monkeypatch.setattr(db_questions, "get_container_by_doc",
                             lambda doc_id: {"id": 1, "status": "pending"})
        monkeypatch.setattr(db_question_items, "list_unanswered",
                             lambda qpk: [{"id": 201, "seq": 1}])
        monkeypatch.setattr(
            q_answer_invoke_service, "resolve_item",
            lambda doc_id, item_id: {
                "id": item_id, "seq": item_id, "title": "Q", "body": "b", "options": [],
            },
        )
        # Everything below is what dispatch_answer_run/issue_answer_token need once
        # admission actually admits the run (never reached by the admission-failure case,
        # which raises before any of it) — the same collaborators
        # test_q_answer_lease_owner_0389 stubs for a real dispatch_answer_run call.
        monkeypatch.setattr(q_answer_invoke_service, "_ai_answer_count", lambda item_id: 0)
        monkeypatch.setattr(q_answer_invoke_service, "_source_tool_block", lambda *a, **k: [])
        monkeypatch.setattr(q_answer_invoke_service, "_document_lookup_block",
                             lambda *a, **k: [])
        monkeypatch.setattr(q_answer_invoke_service.db_groups, "get_by_id",
                             lambda gid: {"title": "그룹"})

    def test_a_real_no_enabled_provider_admission_failure_is_swallowed_and_then_recovers(
            self, env, monkeypatch):
        # "실행불가": a project with providers registered but none enabled is exactly what
        # the REAL admission path (admission.py's `if not chain:` gate) raises 409
        # no_enabled_provider for — distinct from the empty-install "no_provider_registered"
        # case (0292 T0003), and the one that actually matches "provider ... 실행불가".
        env["chain"]["providers"] = []
        env["chain"]["registered_count"] = 2
        launches = base._scripted_worker(env, monkeypatch, [(None, False)])

        real_dispatch = q_answer_invoke_service.dispatch_answer_run
        seen_errors: list[Exception] = []

        def _spy(**kw):
            try:
                return real_dispatch(**kw)
            except Exception as exc:  # noqa: BLE001 — recorded, then re-raised untouched
                seen_errors.append(exc)
                raise

        monkeypatch.setattr(q_answer_invoke_service, "dispatch_answer_run", _spy)

        finalize._dispatch_question_responder(_run())  # must not raise

        # The real admission code path was exercised (not some other, unrelated failure):
        assert len(seen_errors) == 1
        assert getattr(seen_errors[0], "status_code", None) == 409
        assert seen_errors[0].detail.get("code") == "no_enabled_provider"
        # ...and admission refused before any worker was ever launched, so nothing here
        # touched the pending question — state preserved.
        assert launches == []
        assert db_question_items.list_unanswered(1) == [{"id": 201, "seq": 1}]

        # Recovery: once a provider is available again, the SAME unmodified dispatch call
        # is admitted and a real worker launches (asynchronously, on the worker thread the
        # admitted run just spawned — hence the wait rather than an immediate assert).
        env["chain"]["providers"] = [base.P1]
        finalize._dispatch_question_responder(_run())
        base._wait_until(lambda: launches == ["aip_1"], message="responder relaunch")

    def test_a_real_execution_timeout_leaves_the_question_unanswered_and_then_recovers(
            self, env, monkeypatch):
        # "timeout": the run is genuinely admitted and reaches worker.py's real
        # end-reason classification — only the CLI subprocess itself is faked, exactly
        # the way `base._scripted_worker` fakes "the worker exited", except this fake
        # reports what a watchdog-killed attempt reports (0187 test_timeout_kills_and_
        # classifies_timeout): `timed_out` true, no exit code, no last message.
        def _timeout_cli(provider, prompt, run):
            run["timed_out"] = True
            run["exit_code"] = None
            run["last_message"] = None
            run["last_message_received"] = False
            return "started_ok", None

        monkeypatch.setattr(base.svc, "_cli_execute", _timeout_cli)

        real_dispatch = q_answer_invoke_service.dispatch_answer_run
        captured: dict = {}

        def _spy(**kw):
            result = real_dispatch(**kw)
            captured.update(result)
            return result

        monkeypatch.setattr(q_answer_invoke_service, "dispatch_answer_run", _spy)

        finalize._dispatch_question_responder(_run())  # must not raise
        assert captured.get("run_id")
        run = base._wait_finished(captured["run_id"])

        # The clock decided — the real classification worker.py reaches, not an assumption.
        assert run["end_reason"] == "timeout"
        assert run["stop_code"] == "timeout"
        # No AI answer landed (the oracle this run was judged by was never satisfied), so
        # the question is exactly as unanswered as it was before the attempt — state
        # preserved, the same as the admission-failure case above.
        assert db_question_items.list_unanswered(1) == [{"id": 201, "seq": 1}]

        # Recovery: a fresh dispatch after the timed-out attempt still reaches a real
        # worker on the same, unmodified code path (asynchronously, hence the wait).
        launches = base._scripted_worker(env, monkeypatch, [(None, False)])
        finalize._dispatch_question_responder(_run())
        base._wait_until(lambda: launches == ["aip_1"], message="responder relaunch")


class TestFinalizeRunHookPlacement:
    """End to end through the real engine (reusing 0359's continuous-mode harness): the
    responder dispatch fires exactly on a `question_pending` stop, and only after the
    group lease `dispatch_answer_run` needs has actually been released -- a second
    concurrent run for the same group is refused (`run_in_progress`) for as long as this
    hop's own lease is still held."""

    def test_a_question_pending_stop_dispatches_only_after_the_lease_is_released(
            self, env, monkeypatch):
        monkeypatch.setattr(base.svc.q_service, "resolve_question_anchor",
                             lambda doc_id: base.ANCHOR)
        monkeypatch.setattr(
            base.svc.db_questions, "get_container_by_doc",
            base._container_lookup({base.ANCHOR: {"id": 9, "status": "pending"}}),
        )
        monkeypatch.setattr(db_question_items, "list_unanswered",
                             lambda qpk: [{"id": 501, "seq": 1}])
        monkeypatch.setattr(
            q_answer_invoke_service, "resolve_item",
            lambda doc_id, item_id: {
                "id": item_id, "seq": 1, "title": "Q", "body": "b", "options": [],
            },
        )

        calls: list[dict] = []

        def _dispatch(**kw):
            # T#1: record whether THIS hop's own group lease is still held at the moment
            # the responder dispatch fires -- it must already be free (see the docstring).
            calls.append({**kw, "lease_active_at_dispatch": db_group_ai_leases.get_active(base.GROUP)})
            return {"run_id": "aiv_responder", "status": "running"}

        monkeypatch.setattr(q_answer_invoke_service, "dispatch_answer_run", _dispatch)

        base._scripted_worker(env, monkeypatch, [
            ("작업 전 확인이 필요해 Q를 등록했습니다.", False),
        ])
        res = base._start(env, provider_id="aip_1")
        run = base._wait_finished(res["run_id"])
        base._wait_until(lambda: len(calls) == 1, message="responder auto-dispatch")

        assert run["stop_code"] == "question_pending"
        assert calls[0]["item"]["id"] == 501
        assert calls[0]["lease_active_at_dispatch"] is None
        # NR0003 §4: no reviewer configured for this step -> T#1 priority 2 (the run's
        # header/default selected provider) is what the dispatch actually receives.
        assert calls[0]["provider_id"] == "aip_1"

    def test_a_clean_finish_never_dispatches_a_responder(self, env, monkeypatch):
        calls: list[dict] = []
        monkeypatch.setattr(q_answer_invoke_service, "dispatch_answer_run",
                             lambda **kw: calls.append(kw))
        base._scripted_worker(env, monkeypatch, [
            ("문서를 등록했습니다.", True),
        ])
        res = base._start(env)
        run = base._wait_finished(res["run_id"])

        assert run["stop_code"] != "question_pending"
        assert calls == []
