"""flowgate.v02.0137 T0004 §2/§3/§5-A: unsubmitted AI drafts survive and reach the next token.

Isolated: every directory lives under pytest's tmp_path (the storage root is patched), run
ids are synthetic (`aiv_29990101_*`), token rows / run rows are in-memory stubs. Nothing
here touches a real storage root, DB row, token or server.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

os.environ["TESTING"] = "1"
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
_SERVER_DIR = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(_SERVER_DIR))

from modules.flow_gate.db import tokens as db_tokens  # noqa: E402
from modules.flow_gate.services import ai_invoke_service as svc  # noqa: E402
from modules.flow_gate.services import token_scratch  # noqa: E402
from modules.flow_gate.services.ai_invoke import carryover, finalize, oracle, runtime, worker  # noqa: E402
from modules.flow_gate.services.ai_invoke import submission_ledger  # noqa: E402
from modules.flow_gate.settings import scratch_retention  # noqa: E402

try:  # flowgate.v02.0138 (merged into feature/v0.2.0 after this group's base): a run
    # scratch then needs a DB-registered owner and execution_nonce. Use 0138's own isolated
    # ownership fixture when it exists; on this group's base the import fails and nothing
    # changes. The fixtures are imported by name so pytest applies them to this module.
    from tests.integration.ai_run_identity.scratch_contract_fixture import (  # noqa: F401
        owned_nonce, scratch_identity)
    from tests.integration.ai_run_identity.test_ai_run_identity_0138 import identity_db  # noqa: F401
except ImportError:  # pragma: no cover - depends on the merge state
    owned_nonce = None

PROJECT = "project-0137"
NONCES: dict = {}
GROUP = "proj.v02.0137"
DOC = "proj.v02.0137.0003-NR"
USER = "usr_0137"


# ── isolation ────────────────────────────────────────────────────────────────

def _pass_through_write_gate(operation, *args, **kwargs):
    """The card-document write gate is out of this T's scope and needs a live document
    store to answer; the journal/manifest file writes under tmp_path are let through."""
    if operation == "paths":
        return args[1]()
    if operation == "paths_scope":
        return contextlib.nullcontext()
    raise AssertionError(f"unexpected write-gate operation {operation!r}")


@pytest.fixture
def storage(monkeypatch, tmp_path):
    from modules.flow_gate.graph_workflow.services import card_document_ports as ports

    handlers = dict(ports._handlers)
    handlers[ports.DOCUMENT_WRITE_GATE] = _pass_through_write_gate
    monkeypatch.setattr(ports, "_handlers", handlers)
    monkeypatch.setattr(scratch_retention, "effective_retention", lambda: timedelta(days=7))
    monkeypatch.setattr(svc.db_projects, "get_by_id", lambda _p: {"project_name": "P0137"})
    monkeypatch.setattr(svc.storage_paths, "get_storage_root", lambda *_a, **_k: tmp_path)
    tokens: dict[str, dict] = {}
    monkeypatch.setattr(db_tokens, "get_by_id", lambda token_id: tokens.get(token_id))
    return {"root": tmp_path, "tokens": tokens}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run_scratch(serial: int) -> tuple[str, Path]:
    run_id = f"aiv_29990101_{serial:06d}"
    nonce = owned_nonce(PROJECT, run_id) if owned_nonce else None
    NONCES[run_id] = nonce
    if nonce:
        return run_id, svc._create_scratch(PROJECT, run_id, nonce)
    return run_id, svc._create_scratch(PROJECT, run_id)


def _token(storage, serial: int, *, doc=DOC, issued_to=USER, run_id=None, consumed=False,
           revoked=False, expires_in=timedelta(hours=1), group=GROUP, created_at=None) -> tuple[str, Path]:
    token_id = f"tok_29990101_{serial:06d}"
    scratch = token_scratch._root(PROJECT) / token_id
    token_scratch.create(PROJECT, token_id, scratch)
    now = datetime.now(timezone.utc)
    storage["tokens"][token_id] = {
        "token_id": token_id, "project": PROJECT, "group_id": group, "doc_ref": doc,
        "issued_to": issued_to, "action_scope": "edit", "ai_run_id": run_id,
        "scratch_dir": str(scratch), "created_at": (created_at or now).isoformat(),
        "expires_at": (now + expires_in).isoformat(),
        "consumed_at": now.isoformat() if consumed else None,
        "revoked_at": now.isoformat() if revoked else None,
    }
    return token_id, scratch.resolve()


def _finished_run(run_id: str, scratch: Path, **extra) -> dict:
    run = {"project_id": PROJECT, "run_id": run_id, "scratch_dir": str(scratch),
           "outcome": "complete", "register_errors": [], "reached_doc_ids": [],
           "scratch_retained": None, "token_id": "tok_29990101_000099",
           "execution_nonce": NONCES.get(run_id)}
    if NONCES.get(run_id):
        # 0138 identity fields exactly as scratch_contract_fixture.owned_nonce claimed them.
        run.update(group_id="", started_at="2026-08-28T01:00:00+00:00")
    run.update(extra)
    return run


# ── §2 / §5-A1..3: deletion needs a settled submission ──────────────────────

def _fail(run, status=403, **kw):
    return submission_ledger.record(run, ok=False, status=status, via="inbox", **kw)


def _ok(run, doc_id=DOC, revision_no=4, **kw):
    kw.setdefault("token_doc_ref", DOC)
    return submission_ledger.record(run, ok=True, status=200, via="inbox", doc_id=doc_id,
                                    revision_no=revision_no, action=kw.pop("action", "edit"), **kw)


@pytest.fixture
def docs(monkeypatch):
    """The documents table as finalize reads it: doc_id -> current revision_no."""
    table = {DOC: 4}
    monkeypatch.setattr(finalize.db_docs, "get_by_id",
                        lambda doc_id: {"doc_id": doc_id, "revision_no": table[doc_id]}
                        if doc_id in table else None)
    return table


class TestSubmissionSettlement:
    def test_incident_shape_c1_is_retained_across_a_new_process_and_the_ttl_sweep(
            self, storage, monkeypatch):
        """§5-A1 C1: draft written -> 403 submit failure -> run judged `complete` anyway."""
        run_id, scratch = _run_scratch(1)
        draft = scratch / "draft_test.md"
        draft.write_text("# draft\n본문 — unsubmitted\n", encoding="utf-8")
        digest = _sha(draft)
        run = _finished_run(run_id, scratch, register_errors=[
            {"status": 403, "reason": "context_binding_mismatch", "boundary": "inbox"}])
        _fail(run, 403)

        finalize._finalize_scratch(run)

        assert draft.is_file() and _sha(draft) == digest
        assert run["scratch_retained"] is not None
        assert run["scratch_cleanup"]["action"] == "retained"
        assert run["scratch_cleanup"]["reason"] == "last_submission_failed"
        assert run["scratch_cleanup"]["register_errors"] == 1
        assert run["scratch_cleanup"]["last_failure_status"] == 403
        manifest = json.loads((scratch / svc.SCRATCH_MANIFEST_NAME).read_text(encoding="utf-8"))
        assert manifest["completed_at"] is not None

        # A brand-new interpreter sees the same bytes (nothing lived only in this process).
        probe = subprocess.run(
            [sys.executable, "-c",
             "import hashlib,sys;print(hashlib.sha256(open(sys.argv[1],'rb').read()).hexdigest())",
             str(draft)], capture_output=True, text=True, timeout=60)
        assert probe.returncode == 0 and probe.stdout.strip() == digest

        # The retained sweep just before the 7-day TTL keeps it ...
        monkeypatch.setattr(svc, "is_run_live", lambda _r: False)
        monkeypatch.setattr(runtime.db_group_ai_leases, "get_by_run_id", lambda _r: None)
        almost = datetime.now(timezone.utc) - timedelta(days=7) + timedelta(minutes=5)
        manifest["completed_at"] = almost.isoformat()
        (scratch / svc.SCRATCH_MANIFEST_NAME).write_text(json.dumps(manifest), encoding="utf-8")
        runtime._cleanup_retained_scratches(PROJECT)
        assert draft.is_file() and _sha(draft) == digest
        # ... and the TTL itself still ends it (no unbounded retention).
        manifest["completed_at"] = (almost - timedelta(minutes=10)).isoformat()
        (scratch / svc.SCRATCH_MANIFEST_NAME).write_text(json.dumps(manifest), encoding="utf-8")
        runtime._cleanup_retained_scratches(PROJECT)
        assert not scratch.exists()

    def test_clean_complete_run_is_still_deleted_c3(self, storage):
        run_id, scratch = _run_scratch(2)
        (scratch / "notes.md").write_text("x", encoding="utf-8")
        run = _finished_run(run_id, scratch)
        finalize._finalize_scratch(run)
        assert not scratch.exists()
        assert run["scratch_retained"] is None
        assert run["scratch_cleanup"] == {**run["scratch_cleanup"], "action": "deleted",
                                          "reason": "deleted", "submission": "no_registration_error"}

    def test_clean_submission_is_deleted(self, storage, docs):
        run_id, scratch = _run_scratch(12)
        run = _finished_run(run_id, scratch)
        _ok(run)
        finalize._finalize_scratch(run)
        assert not scratch.exists() and run["scratch_cleanup"]["submission"] == "submitted"

    @pytest.mark.parametrize("outcome", ["none", "partial"])
    def test_non_complete_outcomes_keep_their_reason(self, storage, outcome):
        run_id, scratch = _run_scratch(3)
        run = _finished_run(run_id, scratch, outcome=outcome)
        finalize._finalize_scratch(run)
        assert scratch.is_dir()
        assert run["scratch_cleanup"]["reason"] == "non_complete_outcome"

    def test_manifest_problem_is_named_not_non_complete_c4(self, storage):
        run_id, scratch = _run_scratch(4)
        (scratch / svc.SCRATCH_MANIFEST_NAME).unlink()
        run = _finished_run(run_id, scratch)
        finalize._finalize_scratch(run)
        assert scratch.is_dir()
        assert run["scratch_cleanup"]["reason"] == "manifest_missing"
        assert run["scratch_cleanup"]["manifest_updated"] is False

    # -- review finding 1: order decides, not presence ------------------------------

    @pytest.mark.parametrize("status", [403, 422, 401])
    def test_success_then_failure_is_retained(self, storage, docs, status):
        """'submit OK -> revise -> last submit fails' — the newest draft did not go in."""
        run_id, scratch = _run_scratch(8)
        (scratch / "rev2.md").write_text("newest, never accepted", encoding="utf-8")
        run = _finished_run(run_id, scratch, reached_doc_ids=[DOC],
                            register_errors=[{"status": status}] if status != 401 else [])
        _ok(run)
        _fail(run, status)
        finalize._finalize_scratch(run)
        assert (scratch / "rev2.md").is_file()
        assert run["scratch_cleanup"]["reason"] == "last_submission_failed"
        assert run["scratch_cleanup"]["last_failure_status"] == status

    def test_failure_then_verified_success_is_deleted(self, storage, docs):
        """§2-3: an earlier error is not a permanent failure once a later submission landed."""
        run_id, scratch = _run_scratch(13)
        run = _finished_run(run_id, scratch, register_errors=[{"status": 422}])
        _fail(run, 422)
        _ok(run, revision_no=4)
        finalize._finalize_scratch(run)
        assert not scratch.exists()
        assert run["scratch_cleanup"]["submission"] == "submitted_after_error"

    def test_success_failure_success_is_deleted_and_failure_success_failure_is_not(
            self, storage, docs):
        run_id, scratch = _run_scratch(14)
        run = _finished_run(run_id, scratch, register_errors=[{"status": 422}])
        _ok(run, revision_no=3)
        _fail(run, 422)
        _ok(run, revision_no=4)
        assert finalize._submission_settlement(run)[:2] == (True, "submitted_after_error")
        _fail(run, 403)
        assert finalize._submission_settlement(run)[:2] == (False, "last_submission_failed")

    @pytest.mark.parametrize("change,why", [
        ({"revision_no": 9}, "success_revision_rolled_back"),
        ({"doc_id": "proj.v02.0137.0009-T", "token_doc_ref": "proj.v02.0137.0009-T"},
         "success_document_missing"),
        ({"doc_id": "proj.v02.0137.0002-N", "token_doc_ref": DOC}, "success_for_other_document"),
    ])
    def test_success_after_error_must_still_be_true_in_the_documents_table(
            self, storage, docs, change, why):
        docs["proj.v02.0137.0002-N"] = 1
        run_id, scratch = _run_scratch(15)
        run = _finished_run(run_id, scratch, register_errors=[{"status": 422}])
        _fail(run, 422)
        _ok(run, **change)
        settled, reason, evidence = finalize._submission_settlement(run)
        assert (settled, reason) == (False, "submission_after_error_unverified")
        assert evidence["success_check"] == why

    def test_errors_the_ledger_never_saw_are_unordered_and_retained(self, storage, docs):
        """rev0 deleted on any reached document; an unordered error now keeps the scratch."""
        run_id, scratch = _run_scratch(16)
        run = _finished_run(run_id, scratch, register_errors=[{"status": 422}],
                            reached_doc_ids=[DOC])
        assert finalize._submission_settlement(run)[:2] == (False, "registration_error_unordered")
        run2 = _finished_run(run_id, scratch, register_errors=[{"status": 403}],
                             scope_oracle_run=False, completion_oracle=lambda: True)
        assert finalize._submission_settlement(run2)[:2] == (False, "registration_error_unordered")

    def test_only_the_current_hop_is_judged(self, storage, docs):
        run_id, scratch = _run_scratch(17)
        run = _finished_run(run_id, scratch)
        _fail(run, 403)                          # hop 0 (review) failed once ...
        submission_ledger.start_hop(run)         # ... the loop moved to rework
        _ok(run)
        assert finalize._submission_settlement(run)[:2] == (True, "submitted")
        _fail(run, 422)
        assert finalize._submission_settlement(run)[:2] == (False, "last_submission_failed")

    def test_success_without_document_needs_the_hops_own_judge(self, storage):
        state = {"landed": False}
        run_id, scratch = _run_scratch(9)
        run = _finished_run(run_id, scratch, register_errors=[{"status": 403}],
                            scope_oracle_run=True, hop_kind="work", oracle_hop_kind="work",
                            completion_oracle=lambda: state["landed"])
        _fail(run, 403)
        submission_ledger.record(run, ok=True, status=200, via="api_worker", action="chat")
        assert finalize._submission_settlement(run)[:2] == (False, "submission_after_error_unverified")
        state["landed"] = True
        assert finalize._submission_settlement(run)[:2] == (True, "submitted_after_error")
        run["oracle_hop_kind"] = "review"         # a stale judge is never evidence
        assert finalize._submission_settlement(run)[:2] == (False, "submission_after_error_unverified")

    def test_failed_last_rework_hop_after_a_review_verdict_is_retained(self, storage):
        """§5-A3: the incident loop — review verdict landed, the rework hop then failed."""
        run_id, scratch = _run_scratch(5)
        (scratch / "nr_rev1.md").write_text("draft", encoding="utf-8")
        run = _finished_run(
            run_id, scratch, hop_kind="rework", document_review_loop_checkpointed=True,
            register_errors=[{"status": 403}],
            document_review_loop={"current_stage": "stopped", "stop_reason": "retry_exhausted",
                                  "last_hop_kind": "rework", "last_hop_outcome": "failed"})
        finalize._finalize_scratch(run)
        assert (scratch / "nr_rev1.md").is_file()
        assert run["scratch_cleanup"]["reason"] == "review_loop_hop_failed"
        assert run["scratch_cleanup"]["loop_stop_reason"] == "retry_exhausted"

    def test_loop_hop_failure_then_success_is_deleted(self, storage, docs):
        run_id, scratch = _run_scratch(6)
        run = _finished_run(
            run_id, scratch, hop_kind="review", document_review_loop_checkpointed=True,
            register_errors=[{"status": 422}],
            document_review_loop={"current_stage": "stopped", "stop_reason": "review_passed",
                                  "last_hop_kind": "review", "last_hop_outcome": "succeeded"})
        _fail(run, 422)
        _ok(run, action="review")
        finalize._finalize_scratch(run)
        assert not scratch.exists()
        assert run["scratch_cleanup"]["submission"] == "review_loop_submitted_after_error"

    def test_loop_hop_success_then_failure_is_retained_even_if_checkpoint_succeeded(
            self, storage, docs):
        run_id, scratch = _run_scratch(18)
        run = _finished_run(
            run_id, scratch, hop_kind="rework", document_review_loop_checkpointed=True,
            register_errors=[{"status": 403}],
            document_review_loop={"current_stage": "stopped", "stop_reason": "review_passed",
                                  "last_hop_kind": "rework", "last_hop_outcome": "succeeded"})
        _ok(run)
        _fail(run, 403)
        finalize._finalize_scratch(run)
        assert scratch.is_dir()
        assert run["scratch_cleanup"]["reason"] == "review_loop_last_submission_failed"

    def test_unconfirmed_loop_checkpoint_is_retained(self, storage):
        run_id, scratch = _run_scratch(7)
        run = _finished_run(run_id, scratch, document_review_loop_checkpointed=False,
                            document_review_loop={"current_stage": "rework"})
        finalize._finalize_scratch(run)
        assert scratch.is_dir()
        assert run["scratch_cleanup"]["reason"] == "review_loop_hop_unconfirmed"

    def test_audit_line_is_written_at_warning_without_secrets(self, storage, caplog):
        run_id, scratch = _run_scratch(11)
        run = _finished_run(run_id, scratch, register_errors=[{"status": 403}],
                            raw_token="RAW-TOKEN-SENTINEL", mention="PROMPT-SENTINEL")
        _fail(run, 403)
        with caplog.at_level("WARNING"):
            finalize._finalize_scratch(run)
        lines = [r.getMessage() for r in caplog.records if "ai-invoke scratch audit" in r.getMessage()]
        assert len(lines) == 1
        payload = json.loads(lines[0].split("ai-invoke scratch audit ", 1)[1])
        assert payload["action"] == "retained" and payload["run_id"] == run_id
        assert "RAW-TOKEN-SENTINEL" not in lines[0] and "PROMPT-SENTINEL" not in lines[0]


class TestInboxLedgerHook:
    """The inbox feeds the ledger for every non-dry-run outcome of a live run's token."""

    @pytest.fixture
    def live(self, monkeypatch):
        run = {"run_id": "aiv_29990101_000090", "project_id": PROJECT, "issued_to": USER}
        monkeypatch.setattr(svc, "get_run_record",
                            lambda rid: run if rid == run["run_id"] else None)
        return run

    def _note(self, body, status, payload, token=None, raw="RAW"):
        from fastapi.responses import JSONResponse
        from modules.flow_gate.api import inbox_routes

        seen = {"raw": raw, "body": body, "token": token}
        inbox_routes._note_run_submission(seen, JSONResponse(status_code=status, content=payload))

    def _token(self, **kw):
        rec = {"token_id": "tok_29990101_000091", "project": PROJECT, "issued_to": USER,
               "doc_ref": DOC, "ai_run_id": "aiv_29990101_000090"}
        rec.update(kw)
        return rec

    def test_failures_and_successes_are_recorded_in_order(self, live):
        body = {"action": "edit", "doc_id": DOC}
        self._note(body, 422, {"ok": False, "error_message": "x"}, self._token())
        self._note(body, 200, {"ok": True, "doc_id": DOC, "revision_no": 5}, self._token())
        events = live[submission_ledger.EVENTS_KEY]
        assert [(e["ok"], e["status"]) for e in events] == [(False, 422), (True, 200)]
        assert events[1]["doc_id"] == DOC and events[1]["revision_no"] == 5
        assert events[0]["seq"] < events[1]["seq"]
        assert "RAW" not in json.dumps(events)

    def test_dry_run_other_requester_and_unbound_tokens_are_ignored(self, live):
        self._note({"action": "edit", "dry_run": True}, 200, {"ok": True}, self._token())
        self._note({"action": "edit"}, 422, {"ok": False}, self._token(issued_to="usr_other"))
        self._note({"action": "edit"}, 422, {"ok": False}, self._token(project="other"))
        self._note({"action": "edit"}, 422, {"ok": False}, self._token(ai_run_id=None))
        assert not live.get(submission_ledger.EVENTS_KEY)

    def test_spent_token_401_is_looked_up_and_recorded(self, live, monkeypatch):
        from modules.flow_gate.services import token_service

        monkeypatch.setattr(token_service, "_find_token_by_raw",
                            lambda raw: self._token() if raw == "RAW-SPENT" else None)
        self._note({"action": "edit"}, 401, {"ok": False}, token=None, raw="RAW-SPENT")
        assert [(e["ok"], e["status"]) for e in live[submission_ledger.EVENTS_KEY]] == [(False, 401)]


# ── §2-4: the judge follows the hop ──────────────────────────────────────────

class TestHopOracle:
    @pytest.fixture
    def probes(self, monkeypatch, storage):
        state = {"reviews": 0, "revision": 0}
        monkeypatch.setitem(oracle._SCOPE_PROBES, "review", lambda _d: state["reviews"])
        monkeypatch.setitem(oracle._SCOPE_PROBES, "edit", lambda _d: state["revision"])
        storage["tokens"]["tok_review"] = {"doc_ref": DOC}
        storage["tokens"]["tok_edit"] = {"doc_ref": DOC}
        return state

    def test_rework_hop_is_not_credited_with_the_reviewers_verdict(self, probes):
        run = {"run_id": "aiv_29990101_000020", "doc_ref": DOC, "action_scope": "review",
               "scope_oracle_run": True}
        run["completion_oracle"] = oracle._scope_oracle("review", "tok_review", DOC)
        probes["reviews"] = 1                     # review hop wrote its verdict
        stale = run["completion_oracle"]
        assert stale() is True

        assert oracle.refresh_hop_oracle(run, "rework", "tok_edit") is True
        assert run["action_scope"] == "review"   # admission meaning preserved
        assert (run["oracle_scope"], run["oracle_hop_kind"], run["oracle_token_id"]) == \
            ("edit", "rework", "tok_edit")
        assert stale() is True                    # the old judge would still say yes ...
        assert run["completion_oracle"]() is False  # ... the hop's own judge does not
        probes["revision"] = 1
        assert run["completion_oracle"]() is True

        probes["reviews"] = 1
        oracle.refresh_hop_oracle(run, "review", "tok_review")
        assert run["completion_oracle"]() is False  # re-review needs a NEW verdict
        probes["reviews"] = 2
        assert run["completion_oracle"]() is True

    def test_rework_first_loop_maps_to_edit(self):
        assert oracle.loop_stage_oracle_scope("rework", "review") == "edit"
        assert oracle.loop_stage_oracle_scope("review", "edit") == "review"
        assert oracle.loop_stage_oracle_scope("stopped", "edit") == "edit"

    def test_caller_override_is_never_replaced(self, probes):
        judge = lambda: True  # noqa: E731
        run = {"run_id": "aiv_29990101_000021", "doc_ref": DOC, "action_scope": "edit",
               "scope_oracle_run": False, "completion_oracle": judge}
        assert oracle.refresh_hop_oracle(run, "review", "tok_review") is False
        assert run["completion_oracle"] is judge


# ── §3 / §5-A4..6: carry-over to the next token ──────────────────────────────

def _carry_target(storage, serial, **kw):
    return _token(storage, serial, **kw)


class TestCarryOverCore:
    def test_copies_drafts_with_manifest_and_leaves_originals(self, storage):
        _src_id, src = _token(storage, 30, consumed=True)
        (src / "nr_rev1.md").write_text("개정본", encoding="utf-8")
        (src / "sub").mkdir()
        (src / "sub" / "body.json").write_text('{"a": 1}', encoding="utf-8")
        before = {p.name: _sha(p) for p in (src / "nr_rev1.md", src / "sub" / "body.json")}
        dst_id, dst = _carry_target(storage, 31)

        summary = token_scratch.carry_over(
            PROJECT, target_token_id=dst_id, target_scratch=dst, label="hop2_x",
            sources=[{"kind": "token_scratch", "id": _src_id, "path": str(src),
                      "exclude_top": carryover.TOKEN_SCRATCH_EXCLUDE}],
            meta={"doc_ref": DOC, "doc_revision_at_copy": 3})

        assert summary["status"] == "copied" and len(summary["files"]) == 2
        carried = Path(summary["dest"])
        record = json.loads((carried / token_scratch.CARRYOVER_MANIFEST).read_text(encoding="utf-8"))
        assert record["doc_revision_at_copy"] == 3 and record["target_token_id"] == dst_id
        copied = carried / f"token_scratch_{_src_id}" / "nr_rev1.md"
        assert copied.read_text(encoding="utf-8") == "개정본"
        assert {f["sha256"] for f in record["files"]} == set(before.values())
        # Originals untouched, nothing moved.
        assert {p.name: _sha(p) for p in (src / "nr_rev1.md", src / "sub" / "body.json")} == before
        # The carried file is submittable by the NEW token only (inbox doc_path jail).
        from modules.flow_gate.api.inbox_routes import validate_doc_path
        assert validate_doc_path(str(copied), str(dst)) is True
        assert validate_doc_path(str(src / "nr_rev1.md"), str(dst)) is False
        # Asked again for the same hop: nothing duplicated.
        again = token_scratch.carry_over(
            PROJECT, target_token_id=dst_id, target_scratch=dst, label="hop2_x",
            sources=[{"kind": "token_scratch", "id": _src_id, "path": str(src)}])
        assert again["status"] == "already_present"

    def test_excludes_infrastructure_secrets_and_limits(self, storage, monkeypatch):
        run_id, run_dir = _run_scratch(32)
        (run_dir / "tmp" / "junk.bin").write_bytes(b"x" * 10)
        (run_dir / "cache" / "c").write_bytes(b"x")
        (run_dir / "last_message.txt").write_text("m", encoding="utf-8")
        (run_dir / ".env").write_text("A=1", encoding="utf-8")
        (run_dir / "leak.sh").write_text("curl -H 'Bearer RAW-SECRET-TOKEN'", encoding="utf-8")
        (run_dir / "big.md").write_bytes(b"y" * 64)
        (run_dir / "ok.md").write_text("ok", encoding="utf-8")
        monkeypatch.setattr(token_scratch, "CARRYOVER_MAX_FILE_BYTES", 48)
        dst_id, dst = _carry_target(storage, 33)

        summary = token_scratch.carry_over(
            PROJECT, target_token_id=dst_id, target_scratch=dst, label="hop",
            sources=[{"kind": "run_scratch", "id": run_id, "path": str(run_dir),
                      "exclude_top": carryover.RUN_SCRATCH_EXCLUDE}],
            secrets=("RAW-SECRET-TOKEN",))

        names = sorted(Path(f["path"]).name for f in summary["files"])
        assert names == ["ok.md"]
        reasons = {Path(s["path"]).name: s["reason"] for s in summary["skipped"]}
        assert reasons == {".env": "secret_like_name", "leak.sh": "contains_token",
                           "big.md": "file_too_large"}
        carried = Path(summary["dest"])
        assert not any(p.name == "leak.sh" for p in carried.rglob("*"))
        assert (run_dir / "leak.sh").is_file()

    def test_count_and_total_caps(self, storage, monkeypatch):
        run_id, run_dir = _run_scratch(34)
        for i in range(5):
            (run_dir / f"f{i}.md").write_bytes(b"z" * 10)
        monkeypatch.setattr(token_scratch, "CARRYOVER_MAX_FILES", 3)
        monkeypatch.setattr(token_scratch, "CARRYOVER_MAX_TOTAL_BYTES", 25)
        dst_id, dst = _carry_target(storage, 35)
        summary = token_scratch.carry_over(
            PROJECT, target_token_id=dst_id, target_scratch=dst, label="caps",
            sources=[{"kind": "run_scratch", "id": run_id, "path": str(run_dir),
                      "exclude_top": carryover.RUN_SCRATCH_EXCLUDE}])
        assert len(summary["files"]) == 2 and summary["bytes"] == 20
        assert {s["reason"] for s in summary["skipped"]} == {"total_size_limit"}

    def test_symlinked_entries_are_refused(self, storage, tmp_path):
        run_id, run_dir = _run_scratch(36)
        outside = tmp_path / "outside.md"
        outside.write_text("secret", encoding="utf-8")
        try:
            os.symlink(outside, run_dir / "link.md")
        except (OSError, NotImplementedError):
            pytest.skip("symlink creation not permitted on this host")
        dst_id, dst = _carry_target(storage, 37)
        summary = token_scratch.carry_over(
            PROJECT, target_token_id=dst_id, target_scratch=dst, label="links",
            sources=[{"kind": "run_scratch", "id": run_id, "path": str(run_dir),
                      "exclude_top": carryover.RUN_SCRATCH_EXCLUDE}])
        assert summary["files"] == []
        assert {s["reason"] for s in summary["skipped"]} == {"unsafe_file"}

    def test_unowned_target_or_source_is_refused(self, storage, tmp_path):
        stray = tmp_path / "work" / "P0137" / "tok_29990101_000038"
        stray.mkdir(parents=True)      # no manifest -> not a managed token scratch
        src_id, src = _token(storage, 39)
        (src / "a.md").write_text("a", encoding="utf-8")
        refused = token_scratch.carry_over(
            PROJECT, target_token_id="tok_29990101_000038", target_scratch=stray, label="x",
            sources=[{"kind": "token_scratch", "id": src_id, "path": str(src)}])
        assert refused["status"] == "skipped" and refused["reason"] == "target_scratch_unowned"
        dst_id, dst = _carry_target(storage, 40)
        unowned = token_scratch.carry_over(
            PROJECT, target_token_id=dst_id, target_scratch=dst, label="y",
            sources=[{"kind": "token_scratch", "id": "tok_29990101_000038", "path": str(stray)}])
        assert unowned["status"] == "nothing_to_copy"
        assert unowned["skipped"] == [{"path": "tok_29990101_000038", "reason": "source_unowned"}]


class TestReissueCarryOver:
    """§3-2/§3-3: same token -> untouched; new token in the same run -> copied + noted."""

    def _run(self, storage, monkeypatch, run_serial, old_token, new_issue):
        run_id, run_dir = _run_scratch(run_serial)
        (run_dir / "draft.md").write_text("cwd draft", encoding="utf-8")
        monkeypatch.setattr(worker.db_group_ai_leases, "update_token", lambda *a, **k: None)
        # 0136 (merged after this base) re-verifies a review loop's reissued RAW token; the
        # stub tokens here are rows only, so that adoption is taken as given (the real-token
        # path is test_unsubmitted_resubmit_e2e_0137). Harmless on this group's base.
        monkeypatch.setattr(worker, "_adopt_continuation_token", lambda *a, **k: True)
        old_id, old_dir = old_token
        return {
            "run_id": run_id, "project_id": PROJECT, "group_id": GROUP, "doc_ref": DOC,
            "issued_to": USER, "mode": "single", "action_scope": "review", "hop_kind": "review",
            "token_id": old_id, "token_scratch_dir": str(old_dir), "raw_token": "RAW-OLD",
            "scratch_dir": str(run_dir), "mention": "old prompt", "attempt_no": 1,
            "document_review_loop": {"current_stage": "rework"},
            "issue_builder": lambda ai_run_id=None: new_issue,
        }

    def test_same_token_retry_copies_nothing(self, storage, monkeypatch):
        old = _token(storage, 50)
        (old[1] / "verdict.json").write_text("{}", encoding="utf-8")
        run = self._run(storage, monkeypatch, 51, old, new_issue=None)
        prepared = worker._prepare_retry_token(run)
        assert prepared["reissued"] is False and prepared["token_id"] == old[0]
        assert not (old[1] / token_scratch.CARRYOVER_DIR).exists()
        assert run["token_scratch_dir"] == str(old[1])
        from modules.flow_gate.api.inbox_routes import validate_doc_path
        assert validate_doc_path(str(old[1] / "verdict.json"), run["token_scratch_dir"])

    def test_token_switch_copies_and_tells_the_next_worker(self, storage, monkeypatch):
        old = _token(storage, 52, consumed=True)
        (old[1] / "nr_rev1.md").write_text("rev1", encoding="utf-8")
        new_id, new_dir = _token(storage, 53)
        issue = {"token_id": new_id, "raw_token": "RAW-NEW", "scratch_dir": str(new_dir),
                 "mention": "## new prompt"}
        run = self._run(storage, monkeypatch, 54, old, issue)
        storage["tokens"][new_id]["ai_run_id"] = run["run_id"]

        prepared = worker._prepare_retry_token(run)

        assert prepared["reissued"] is True and run["token_scratch_dir"] == str(new_dir)
        carried = list((new_dir / token_scratch.CARRYOVER_DIR).rglob("*.md"))
        assert sorted(p.name for p in carried) == ["draft.md", "nr_rev1.md"]
        assert carryover.NOTICE_HEADING in prepared["mention"]
        assert str(new_dir) in prepared["mention"] and "RAW-OLD" not in prepared["mention"]
        assert run["carryovers"][-1]["status"] == "copied" and run["carryovers"][-1]["files"] == 2
        from modules.flow_gate.api.inbox_routes import validate_doc_path
        for path in carried:
            assert validate_doc_path(str(path), run["token_scratch_dir"]) is True

    @pytest.mark.parametrize("change,reason", [
        ({"issued_to": "usr_other"}, "requester_mismatch"),
        ({"project": "other-project"}, "project_mismatch"),
        ({"group_id": "proj.v02.9999"}, "group_mismatch"),
        ({"revoked_at": "2999-01-01T00:00:00+00:00"}, "target_token_not_live"),
        ({"expires_at": "2000-01-01T00:00:00+00:00"}, "target_token_expired"),
        ({"ai_run_id": "aiv_29990101_999999"}, "target_token_other_run"),
    ])
    def test_mismatched_new_token_gets_nothing(self, storage, monkeypatch, change, reason):
        old = _token(storage, 55, consumed=True)
        (old[1] / "nr_rev1.md").write_text("rev1", encoding="utf-8")
        new_id, new_dir = _token(storage, 56)
        storage["tokens"][new_id].update(change)
        issue = {"token_id": new_id, "raw_token": "RAW-NEW", "scratch_dir": str(new_dir),
                 "mention": "## new prompt"}
        run = self._run(storage, monkeypatch, 57, old, issue)
        mention = carryover.carry_into_reissued_token(
            run, previous_token_id=old[0], previous_token_scratch=str(old[1]),
            issue=issue, mention="## new prompt")
        assert mention == "## new prompt"
        assert not (new_dir / token_scratch.CARRYOVER_DIR).exists()
        assert run["carryovers"][-1] == {**run["carryovers"][-1], "status": "skipped", "reason": reason}

    def test_other_document_token_scratch_is_not_a_source(self, storage, monkeypatch):
        old = _token(storage, 58, consumed=True, doc="proj.v02.0137.0009-T")
        (old[1] / "foreign.md").write_text("x", encoding="utf-8")
        new_id, new_dir = _token(storage, 59)
        issue = {"token_id": new_id, "scratch_dir": str(new_dir)}
        run = self._run(storage, monkeypatch, 60, old, issue)
        carryover.carry_into_reissued_token(
            run, previous_token_id=old[0], previous_token_scratch=str(old[1]),
            issue=issue, mention="p")
        assert run["carryovers"][-1]["reason"] == "doc_mismatch"
        assert not (new_dir / token_scratch.CARRYOVER_DIR).exists()


class TestNewRunCarryOver:
    """§3-4/§5-A4..7: a follow-up run picks up a retained run's drafts — verified or not at all."""

    def _retained(self, storage, *, run_serial=70, token_serial=71, skew=timedelta(0),
                  finished_ago=timedelta(minutes=30), **row_overrides):
        run_id, run_dir = _run_scratch(run_serial)
        (run_dir / "nr_rev1.md").write_text("draft from run", encoding="utf-8")
        # One consistent timeline: started -> token minted -> finished (finished_ago).
        finished = datetime.now(timezone.utc) - finished_ago
        created = finished - timedelta(minutes=20)
        manifest = json.loads((run_dir / svc.SCRATCH_MANIFEST_NAME).read_text(encoding="utf-8"))
        manifest["created_at"] = created.isoformat()
        manifest["completed_at"] = finished.isoformat()
        (run_dir / svc.SCRATCH_MANIFEST_NAME).write_text(json.dumps(manifest), encoding="utf-8")
        tok_id, tok_dir = _token(storage, token_serial, consumed=True, run_id=run_id,
                                 created_at=created + timedelta(seconds=1))
        (tok_dir / "submit_body.json").write_text("{}", encoding="utf-8")
        # TR0005 rev1: the run scratch names its run's token / document / requester.
        assert carryover.bind_run_scratch(PROJECT, run_id, run_dir, group_id=GROUP, doc_ref=DOC,
                                          issued_to=USER, token_id=tok_id)
        row = {"run_id": run_id, "status": "finished", "project_id": PROJECT, "group_id": GROUP,
               "doc_ref": DOC, "issued_to": USER, "outcome": "complete",
               "scratch_retained": svc.storage_paths.to_storage_relative(run_dir, PROJECT),
               "started_at": (created + skew).isoformat(), "finished_at": finished.isoformat(),
               "token_id": tok_id, "execution_nonce": NONCES.get(run_id)}
        row.update(row_overrides)
        return row, run_dir, tok_dir

    def _start(self, storage, monkeypatch, row, *, run_id="aiv_29990101_000080",
               new_serial=81, issued_to=USER, live=lambda _r: False):
        monkeypatch.setattr(carryover, "_previous_run_row", lambda _g, _d: row)
        new_id, new_dir = _token(storage, new_serial, run_id=run_id)
        issue = {"token_id": new_id, "raw_token": "RAW-NEW", "scratch_dir": str(new_dir)}
        mention, record = carryover.carry_into_new_run(
            project_id=PROJECT, group_id=GROUP, doc_ref=DOC, issued_to=issued_to, run_id=run_id,
            action_scope="edit", issue=issue, mention="## prompt", run_live=live)
        return mention, record, new_dir

    def test_verified_retained_run_is_carried(self, storage, monkeypatch):
        row, run_dir, tok_dir = self._retained(storage)
        before = _sha(run_dir / "nr_rev1.md")
        mention, record, new_dir = self._start(storage, monkeypatch, row)
        assert record["status"] == "copied" and record["files"] == 2
        assert carryover.NOTICE_HEADING in mention and row["run_id"] in mention
        names = sorted(p.name for p in (new_dir / "carryover").rglob("*") if p.is_file())
        assert names == ["CARRYOVER.json", "nr_rev1.md", "submit_body.json"]   # no owner record
        assert _sha(run_dir / "nr_rev1.md") == before          # copy, not move

    @pytest.mark.parametrize("overrides,reason", [
        ({"issued_to": "usr_other"}, "requester_mismatch"),
        ({"group_id": "proj.v02.0001"}, "owner_mismatch"),
        ({"doc_ref": "proj.v02.0137.0001-B"}, "owner_mismatch"),
        ({"status": "running"}, "previous_not_finished"),
        ({"scratch_retained": None}, "previous_settled"),
    ])
    def test_unrelated_or_settled_runs_are_skipped(self, storage, monkeypatch, overrides, reason):
        row, _run_dir, _tok = self._retained(storage, **overrides)
        mention, record, new_dir = self._start(storage, monkeypatch, row)
        assert mention == "## prompt" and record["reason"] == reason
        assert not (new_dir / "carryover").exists()

    def test_expired_retention_is_skipped(self, storage, monkeypatch):
        row, *_ = self._retained(storage, finished_ago=timedelta(days=8))
        _m, record, _d = self._start(storage, monkeypatch, row)
        assert record["reason"] == "previous_expired"

    def test_run_id_collision_is_never_carried(self, storage, monkeypatch):
        """0138: this run's own id already names a finished row — that row is someone else's."""
        row, *_ = self._retained(storage)
        _m, record, new_dir = self._start(storage, monkeypatch, row, run_id=row["run_id"])
        assert record["reason"] == "run_id_collision"
        assert not (new_dir / "carryover").exists()

    def test_same_id_live_elsewhere_is_never_carried(self, storage, monkeypatch):
        row, *_ = self._retained(storage)
        _m, record, _d = self._start(storage, monkeypatch, row, live=lambda _r: True)
        assert record["reason"] == "previous_id_live"

    def test_scratch_that_does_not_match_its_row_in_time_is_not_used(self, storage, monkeypatch):
        """0138: same id, different run — the directory's manifest disagrees with the row."""
        row, run_dir, _tok = self._retained(storage, skew=timedelta(hours=3))
        _m, record, new_dir = self._start(storage, monkeypatch, row)
        # the run scratch is refused; the token minted inside the row's window is out too
        assert record["status"] == "skipped" and record["reason"] == "no_verified_source"
        assert set(record["notes"]) == {"run_scratch_identity_unproven",
                                        "source_token_outside_run_window"}
        assert (run_dir / "nr_rev1.md").is_file()

    def test_missing_original_is_reported_not_invented(self, storage, monkeypatch):
        """§3-8/§5-A6: nothing saved -> nothing 'recovered'."""
        row, run_dir, tok_dir = self._retained(storage)
        (run_dir / "nr_rev1.md").unlink()
        (tok_dir / "submit_body.json").unlink()
        mention, record, new_dir = self._start(storage, monkeypatch, row)
        assert record["status"] == "nothing_to_copy" and record["files"] == 0
        assert carryover.NOTICE_HEADING not in mention


# ── TR0005 rev1: owner binding, chained relay, content secrets ───────────────

class TestRunOwnerBinding:
    """Review finding 2: same run id, seconds apart, other document / requester."""

    def _row_and_dir(self, storage, *, serial, owner, token_serial):
        run_id, run_dir = _run_scratch(serial)
        (run_dir / "theirs.md").write_text("another run's draft", encoding="utf-8")
        now = datetime.now(timezone.utc)
        manifest = json.loads((run_dir / svc.SCRATCH_MANIFEST_NAME).read_text(encoding="utf-8"))
        manifest["created_at"] = (now - timedelta(minutes=20)).isoformat()
        manifest["completed_at"] = (now - timedelta(minutes=1)).isoformat()
        (run_dir / svc.SCRATCH_MANIFEST_NAME).write_text(json.dumps(manifest), encoding="utf-8")
        tok_id, _tok_dir = _token(storage, token_serial, consumed=True, run_id=run_id,
                                  created_at=now - timedelta(minutes=20) + timedelta(seconds=5))
        if owner is not None:
            assert carryover.bind_run_scratch(PROJECT, run_id, run_dir, **{
                "group_id": GROUP, "doc_ref": DOC, "issued_to": USER, "token_id": tok_id, **owner})
        row = {"run_id": run_id, "status": "finished", "project_id": PROJECT, "group_id": GROUP,
               "doc_ref": DOC, "issued_to": USER, "outcome": "complete",
               "scratch_retained": svc.storage_paths.to_storage_relative(run_dir, PROJECT),
               # 5 seconds apart: rev0's 10-minute window accepted this
               "started_at": (now - timedelta(minutes=20) + timedelta(seconds=5)).isoformat(),
               "finished_at": (now - timedelta(minutes=1)).isoformat(), "token_id": tok_id,
               "execution_nonce": NONCES.get(run_id)}
        return row, run_dir

    @pytest.mark.parametrize("owner,reason", [
        (None, "run_scratch_owner_unproven"),
        ({"doc_ref": "proj.v02.0137.0009-T"}, "run_scratch_owner_mismatch"),
        ({"issued_to": "usr_other"}, "run_scratch_owner_mismatch"),
        ({"group_id": "proj.v02.0001"}, "run_scratch_owner_mismatch"),
        ({"token_id": "tok_29990101_000777"}, "run_scratch_owner_mismatch"),
    ])
    def test_near_time_same_id_without_matching_owner_is_not_a_source(
            self, storage, monkeypatch, owner, reason):
        row, run_dir = self._row_and_dir(storage, serial=200, owner=owner, token_serial=201)
        before = _sha(run_dir / "theirs.md")
        sources, notes = carryover._previous_run_sources(row, PROJECT)
        assert [s["kind"] for s in sources] == ["token_scratch"]   # the row's own token only
        assert {"path": row["run_id"], "reason": reason} in notes
        # the whole new-run path: nothing from that directory reaches the new token
        monkeypatch.setattr(carryover, "_previous_run_row", lambda _g, _d: row)
        new_id, new_dir = _token(storage, 202, run_id="aiv_29990101_000203")
        _m, record = carryover.carry_into_new_run(
            project_id=PROJECT, group_id=GROUP, doc_ref=DOC, issued_to=USER,
            run_id="aiv_29990101_000203", action_scope="edit",
            issue={"token_id": new_id, "scratch_dir": str(new_dir)}, mention="p",
            run_live=lambda _r: False)
        assert not any(p.name == "theirs.md" for p in new_dir.rglob("*"))
        assert _sha(run_dir / "theirs.md") == before and record["status"] != "failed"

    def test_execution_nonce_must_agree_when_either_side_has_one(self, storage):
        row, run_dir = self._row_and_dir(storage, serial=207, owner={}, token_serial=208)
        row["execution_nonce"] = "00000000-0000-4000-8000-000000000000"   # another execution
        sources, notes = carryover._previous_run_sources(row, PROJECT)
        assert [s["kind"] for s in sources] == ["token_scratch"]
        assert {"path": row["run_id"], "reason": "run_scratch_nonce_mismatch"} in notes

    def test_matching_owner_is_a_source_and_reissued_tokens_are_added(self, storage):
        row, run_dir = self._row_and_dir(storage, serial=204, owner={}, token_serial=205)
        sources, notes = carryover._previous_run_sources(row, PROJECT)
        assert sorted(s["kind"] for s in sources) == ["run_scratch", "token_scratch"]
        run = {"project_id": PROJECT, "run_id": row["run_id"], "scratch_dir": str(run_dir)}
        assert carryover.bind_run_token(run, "tok_29990101_000206") is True
        owner = json.loads((run_dir / carryover.RUN_OWNER_NAME).read_text(encoding="utf-8"))
        assert owner["token_ids"] == [row["token_id"], "tok_29990101_000206"]
        assert len(owner["run_nonce"]) == 32
        # the owner record is infrastructure: never carried
        assert carryover.bind_run_scratch(PROJECT, row["run_id"], run_dir, group_id=GROUP,
                                          doc_ref=DOC, issued_to=USER, token_id="x") is False


class TestChainedCarryOver:
    """Review finding 3: the newest draft survives T1 -> T2 -> T3 and run1 -> run2 -> run3."""

    def _reissue(self, storage, monkeypatch, run, new_serial):
        storage["tokens"][run["token_id"]]["consumed_at"] = datetime.now(timezone.utc).isoformat()
        new_id, new_dir = _token(storage, new_serial, run_id=run["run_id"])
        issue = {"token_id": new_id, "raw_token": f"RAW-{new_serial}", "scratch_dir": str(new_dir),
                 "mention": "## prompt"}
        run["issue_builder"] = lambda ai_run_id=None: issue
        prepared = worker._prepare_retry_token(run)
        return new_id, new_dir, prepared

    def test_reissue_chain_keeps_the_newest_draft_flat(self, storage, monkeypatch):
        monkeypatch.setattr(worker.db_group_ai_leases, "update_token", lambda *a, **k: None)
        run_id, run_dir = _run_scratch(210)
        t1_id, t1_dir = _token(storage, 211, run_id=run_id)
        (t1_dir / "draft.md").write_text("v1 from T1", encoding="utf-8")
        run = {"run_id": run_id, "project_id": PROJECT, "group_id": GROUP, "doc_ref": DOC,
               "issued_to": USER, "mode": "single", "action_scope": "edit", "hop_kind": "work",
               "token_id": t1_id, "token_scratch_dir": str(t1_dir), "raw_token": "RAW-211",
               "scratch_dir": str(run_dir), "mention": "p", "attempt_no": 1}
        t2_id, t2_dir, p2 = self._reissue(storage, monkeypatch, run, 212)
        carried_t2 = t2_dir / "carryover" / f"hop2_{t1_id}" / f"token_scratch_{t1_id}" / "draft.md"
        assert carried_t2.read_text(encoding="utf-8") == "v1 from T1"
        # the T2 worker edits the carried draft in place, then the token is replaced again
        carried_t2.write_text("v2 edited under T2", encoding="utf-8")
        os.utime(carried_t2, (carried_t2.stat().st_atime, carried_t2.stat().st_mtime + 5))
        run["attempt_no"] = 2
        t3_id, t3_dir, p3 = self._reissue(storage, monkeypatch, run, 213)

        assert run["carryovers"][-1]["status"] == "copied"
        assert carryover.NOTICE_HEADING in p3["mention"] and "relayed" in p3["mention"]
        dest = t3_dir / "carryover" / f"hop3_{t2_id}"
        newest = dest / f"token_scratch_{t1_id}" / "draft.md"
        assert newest.read_text(encoding="utf-8") == "v2 edited under T2"
        record = json.loads((dest / "CARRYOVER.json").read_text(encoding="utf-8"))
        entry = [f for f in record["files"] if f["path"] == f"token_scratch_{t1_id}/draft.md"][0]
        assert entry["sha256"] == _sha(newest) and entry["via"] == f"carryover/hop2_{t1_id}"
        # flat: no carryover/ folder inside a carry-over
        assert not [p for p in dest.rglob("*") if p.is_dir() and p.name == "carryover"]
        from modules.flow_gate.api.inbox_routes import validate_doc_path
        assert validate_doc_path(str(newest), run["token_scratch_dir"]) is True
        assert validate_doc_path(str(carried_t2), run["token_scratch_dir"]) is False
        assert carried_t2.read_text(encoding="utf-8") == "v2 edited under T2"   # source kept

    def test_unverified_carryover_folder_is_not_relayed(self, storage):
        src_id, src = _token(storage, 214, consumed=True)
        fake = src / "carryover" / "planted"
        fake.mkdir(parents=True)
        (fake / "x.md").write_text("no manifest", encoding="utf-8")
        dst_id, dst = _token(storage, 215)
        summary = token_scratch.carry_over(
            PROJECT, target_token_id=dst_id, target_scratch=dst, label="l",
            sources=[{"kind": "token_scratch", "id": src_id, "path": str(src),
                      "exclude_top": carryover.TOKEN_SCRATCH_EXCLUDE, "relay_carryover": True}])
        assert summary["files"] == []
        assert {"path": "carryover/planted", "reason": "carryover_unverified"} in summary["skipped"]

    def test_failed_run_chain_keeps_the_newest_draft(self, storage, monkeypatch):
        def retained(run_serial, tok_serial, files):
            run_id, run_dir = _run_scratch(run_serial)
            for name, text in files.items():
                (run_dir / name).write_text(text, encoding="utf-8")
            now = datetime.now(timezone.utc)
            manifest = json.loads((run_dir / svc.SCRATCH_MANIFEST_NAME).read_text(encoding="utf-8"))
            manifest["created_at"] = (now - timedelta(minutes=10)).isoformat()
            manifest["completed_at"] = now.isoformat()
            (run_dir / svc.SCRATCH_MANIFEST_NAME).write_text(json.dumps(manifest), encoding="utf-8")
            tok_id, tok_dir = _token(storage, tok_serial, run_id=run_id,
                                     created_at=now - timedelta(minutes=9))
            carryover.bind_run_scratch(PROJECT, run_id, run_dir, group_id=GROUP, doc_ref=DOC,
                                       issued_to=USER, token_id=tok_id)
            row = {"run_id": run_id, "status": "finished", "project_id": PROJECT,
                   "group_id": GROUP, "doc_ref": DOC, "issued_to": USER, "outcome": "none",
                   "scratch_retained": svc.storage_paths.to_storage_relative(run_dir, PROJECT),
                   "started_at": manifest["created_at"], "finished_at": manifest["completed_at"],
                   "token_id": tok_id, "execution_nonce": NONCES.get(run_id)}
            return row, tok_id, tok_dir

        def start(row, tok_id, tok_dir, run_id):
            monkeypatch.setattr(carryover, "_previous_run_row", lambda _g, _d: row)
            issue = {"token_id": tok_id, "scratch_dir": str(tok_dir)}
            return carryover.carry_into_new_run(
                project_id=PROJECT, group_id=GROUP, doc_ref=DOC, issued_to=USER, run_id=run_id,
                action_scope="edit", issue=issue, mention="p", run_live=lambda _r: False)

        row1, _t1, _d1 = retained(220, 221, {"nr_rev1.md": "run1 draft"})
        row2, t2, d2 = retained(222, 223, {})
        _m, rec2 = start(row1, t2, d2, row2["run_id"])        # run2 receives run1's draft
        assert rec2["status"] == "copied"
        got = d2 / "carryover" / f"run_{row1['run_id']}" / f"run_scratch_{row1['run_id']}" / "nr_rev1.md"
        got.write_text("run2 improved, still unsubmitted", encoding="utf-8")   # run2 fails too
        os.utime(got, (got.stat().st_atime, got.stat().st_mtime + 5))
        t3, d3 = _token(storage, 224, run_id="aiv_29990101_000225")
        mention, rec3 = start(row2, t3, d3, "aiv_29990101_000225")
        assert rec3["status"] == "copied", rec3
        final = d3 / "carryover" / f"run_{row2['run_id']}" / f"run_scratch_{row1['run_id']}" / "nr_rev1.md"
        assert final.read_text(encoding="utf-8") == "run2 improved, still unsubmitted"
        assert carryover.NOTICE_HEADING in mention


class TestContentSecrets:
    """Review finding 4: a follow-up run never copies a file holding an older credential."""

    def _retained_with(self, storage, files, *, run_serial, tok_serial):
        run_id, run_dir = _run_scratch(run_serial)
        now = datetime.now(timezone.utc)
        manifest = json.loads((run_dir / svc.SCRATCH_MANIFEST_NAME).read_text(encoding="utf-8"))
        manifest["created_at"] = (now - timedelta(minutes=10)).isoformat()
        manifest["completed_at"] = now.isoformat()
        (run_dir / svc.SCRATCH_MANIFEST_NAME).write_text(json.dumps(manifest), encoding="utf-8")
        tok_id, tok_dir = _token(storage, tok_serial, consumed=True, run_id=run_id,
                                 created_at=now - timedelta(minutes=9))
        for name, text in files.items():
            (tok_dir / name).write_text(text, encoding="utf-8")
        carryover.bind_run_scratch(PROJECT, run_id, run_dir, group_id=GROUP, doc_ref=DOC,
                                   issued_to=USER, token_id=tok_id)
        return {"run_id": run_id, "status": "finished", "project_id": PROJECT, "group_id": GROUP,
                "doc_ref": DOC, "issued_to": USER, "outcome": "none",
                "scratch_retained": svc.storage_paths.to_storage_relative(run_dir, PROJECT),
                "started_at": manifest["created_at"], "finished_at": manifest["completed_at"],
                "token_id": tok_id, "execution_nonce": NONCES.get(run_id)}, tok_dir

    def test_previous_runs_bearer_file_is_refused_everywhere(self, storage, monkeypatch, caplog):
        old = "OLDTOKENSENTINEL0123456789abcdefABCDEFxyz12"     # 43 chars, token-shaped
        known = {old}
        from modules.flow_gate.services import token_service
        monkeypatch.setattr(token_service, "_find_token_by_raw",
                            lambda raw: {"token_id": "tok_old"} if raw in known else None)
        row, tok_dir = self._retained_with(storage, {
            "request.json": '{"headers": {"Authorization": "Bearer OLD-TOKEN-SENTINEL-1234"}}',
            "run.sh": f"export FLOWGATE_TOKEN={old}\n",
            "bare.txt": f"token was {old} here\n",
            "draft.md": "# clean draft\n",
        }, run_serial=230, tok_serial=231)
        monkeypatch.setattr(carryover, "_previous_run_row", lambda _g, _d: row)
        new_id, new_dir = _token(storage, 232, run_id="aiv_29990101_000233")
        with caplog.at_level("WARNING"):
            mention, record = carryover.carry_into_new_run(
                project_id=PROJECT, group_id=GROUP, doc_ref=DOC, issued_to=USER,
                run_id="aiv_29990101_000233", action_scope="edit",
                issue={"token_id": new_id, "raw_token": "RAW-NEW", "scratch_dir": str(new_dir)},
                mention="p", run_live=lambda _r: False)
        assert record["status"] == "copied" and record["files"] == 1
        names = sorted(p.name for p in (new_dir / "carryover").rglob("*") if p.is_file())
        assert names == ["CARRYOVER.json", "draft.md"]
        manifest_text = next(new_dir.rglob("CARRYOVER.json")).read_text(encoding="utf-8")
        reasons = {Path(s["path"]).name: s["reason"] for s in json.loads(manifest_text)["skipped"]}
        assert reasons == {"request.json": "contains_credential", "run.sh": "contains_credential",
                           "bare.txt": "contains_token"}
        exposed = mention + manifest_text + "\n".join(r.getMessage() for r in caplog.records)
        assert "OLD-TOKEN-SENTINEL" not in exposed and old not in exposed
        assert (tok_dir / "request.json").is_file()            # original kept

    def test_probe_failure_refuses_the_file(self, storage):
        src_id, src = _token(storage, 234, consumed=True)
        (src / "a.txt").write_text("x" * 5 + " " + "A" * 43 + "\n", encoding="utf-8")
        dst_id, dst = _token(storage, 235)

        def boom(_raw):
            raise RuntimeError("db down")

        summary = token_scratch.carry_over(
            PROJECT, target_token_id=dst_id, target_scratch=dst, label="p",
            sources=[{"kind": "token_scratch", "id": src_id, "path": str(src)}], token_probe=boom)
        assert summary["files"] == [] and summary["skipped"][0]["reason"] == "secret_check_failed"

    def test_reissue_also_refuses_credentials(self, storage, monkeypatch):
        monkeypatch.setattr(worker.db_group_ai_leases, "update_token", lambda *a, **k: None)
        run_id, run_dir = _run_scratch(236)
        (run_dir / "call.ps1").write_text("-H 'Authorization: Bearer abcdefghijklmnop'",
                                          encoding="utf-8")
        t1_id, t1_dir = _token(storage, 237, run_id=run_id, consumed=True)
        t2_id, t2_dir = _token(storage, 238, run_id=run_id)
        issue = {"token_id": t2_id, "raw_token": "RAW-238", "scratch_dir": str(t2_dir),
                 "mention": "## prompt"}
        run = {"run_id": run_id, "project_id": PROJECT, "group_id": GROUP, "doc_ref": DOC,
               "issued_to": USER, "mode": "single", "action_scope": "edit", "token_id": t1_id,
               "token_scratch_dir": str(t1_dir), "raw_token": "RAW-237",
               "scratch_dir": str(run_dir), "mention": "p", "attempt_no": 1,
               "issue_builder": lambda ai_run_id=None: issue}
        worker._prepare_retry_token(run)
        assert not any(p.name == "call.ps1" for p in t2_dir.rglob("*"))
