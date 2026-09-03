"""flowgate.default.0259 T0004: per-scope default completion oracle (NR0003 권고 (B)).

B0001 was "무조건 실패하는 작업?" and the answer was yes, structurally. `start_run` pinned
docs_target=1 for every mode='single' run, and the judge only ever counted new non-draft
documents past the baseline seq — but the inbox scope guards make registering a document
IMPOSSIBLE for an edit/review token, so success was an unreachable state for those runs. A
worker that did its job perfectly still settled outcome='none' ("실패(문서 미등록)").

The regression these tests pin is the one NR0003 asked for: for every scope, a worker that
does its scope's real work settles 'complete' — and, so the fix cannot be "call everything
a success", a worker that does nothing still settles 'none'.

Collaborators are faked (no DB), following test_ai_invoke_0187.py's fake_env pattern.
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

from modules.flow_gate.services import ai_invoke_service as svc  # noqa: E402

GROUP = "flowgate.default.0259"
TARGET = "flowgate.default.0259.0001-B"
TOKEN_ID = "tok_20260717_000001"


class FakeWorld:
    """The three rows the scope oracles read: documents, review children, tokens."""

    def __init__(self):
        self.max_seq = 4
        self.docs: dict[str, dict] = {
            TARGET: {"doc_id": TARGET, "seq": 1, "status": "open",
                     "revision_no": 0, "branch": "main"},
        }
        self.reviews: dict[str, list[dict]] = {}
        self.token_doc_ref: str | None = TARGET

    # ── db_docs ──
    def get_group_max_seq(self, group_id):
        return self.max_seq

    def get_documents_by_group_id(self, group_id):
        return list(self.docs.values())

    def get_by_id(self, doc_id):
        return self.docs.get(doc_id)

    # ── db_reviews / db_tokens ──
    def list_by_doc(self, doc_id):
        return list(self.reviews.get(doc_id, []))

    def token_by_id(self, token_id):
        return {"token_id": token_id, "doc_ref": self.token_doc_ref, "action_scope": "edit"}

    # ── what a worker actually does through the inbox ──
    def do_edit(self, doc_id=TARGET):
        """inbox `_handle_edit`: UPDATE documents SET revision_no = revision_no + 1."""
        self.docs[doc_id]["revision_no"] += 1

    def do_review(self, doc_id=TARGET):
        """inbox `_handle_review`: INSERT one document_reviews child row."""
        self.reviews.setdefault(doc_id, []).append({"doc_id": doc_id, "verdict": "pass"})

    def do_register(self, seq=5, doc_type="TR", status="open"):
        """inbox `_handle_new`: a real new document lands in the group."""
        doc_id = f"{GROUP}.{seq:04d}-{doc_type}"
        self.max_seq = max(self.max_seq, seq)
        self.docs[doc_id] = {"doc_id": doc_id, "seq": seq, "status": status,
                             "revision_no": 0, "branch": "main"}


@pytest.fixture
def world(monkeypatch, tmp_path):
    w = FakeWorld()
    monkeypatch.setattr(svc, "ORACLE_SETTLE_SEC", 0)
    monkeypatch.setattr(svc, "_runs", {})
    monkeypatch.setattr(svc.db_docs, "get_group_max_seq", w.get_group_max_seq)
    monkeypatch.setattr(svc.db_docs, "get_documents_by_group_id", w.get_documents_by_group_id)
    monkeypatch.setattr(svc.db_docs, "get_by_id", w.get_by_id)
    monkeypatch.setattr(svc.db_reviews, "list_by_doc", w.list_by_doc)
    monkeypatch.setattr(svc.db_tokens, "get_by_id", w.token_by_id)
    monkeypatch.setattr(svc.db_projects, "get_by_id", lambda pid: {"project_name": "testproj"})
    monkeypatch.setattr(
        svc.ai_settings_service, "resolve_effective",
        lambda pid: {"ok": True, "source": "test",
                     "providers": [{"id": "p1", "name": "fake", "exec_type": "cli"}]},
    )
    monkeypatch.setattr(svc.ai_settings_service, "get_provider_secret", lambda scope, pid: None)
    monkeypatch.setattr(
        svc.token_service, "issue",
        lambda **kw: {"raw_token": "tok_raw_test", "token_id": TOKEN_ID,
                      "expires_at": "2026-07-18T00:00:00+00:00",
                      "scratch_dir": str(tmp_path / "tokwork")},
    )
    monkeypatch.setattr(svc.token_service, "revoke", lambda *a, **kw: None)
    monkeypatch.setattr(svc.storage_paths, "get_storage_root", lambda *a, **kw: tmp_path / "storage")
    monkeypatch.setattr(svc.storage_paths, "resolve_project_src_root",
                        lambda pid, branch, *, group_id: None)
    monkeypatch.setattr(svc.storage_paths, "to_storage_relative", lambda path, project=None: str(path))
    monkeypatch.setattr(svc, "_broadcast", lambda run, event_type, payload: None)
    return w


def _run(world, action_scope, work=None, doc_ref=TARGET, mode="single", group_id=GROUP, **kw):
    """Start a run whose 'AI' performs `work` (a perfect worker when work is not None)."""

    def _fake_cli(provider, prompt, run):
        if work is not None:
            work()
        return "started_ok", None

    # A clean exit with no registration errors, no tool misses and no turn exhaustion is
    # exactly the case NR0003 flagged: the better the worker behaved, the more emphatic
    # the false 'none' verdict became (it also set oracle_mismatch).
    import unittest.mock as mock
    with mock.patch.object(svc, "_cli_execute", side_effect=_fake_cli):
        res = svc.start_run(
            project_id="flowgate",
            module="default",
            group_id=group_id,
            doc_ref=doc_ref,
            action_scope=action_scope,
            mode=mode,
            continuation_target_seq=None,
            continuation_review_mode=False,
            continuation_instruction_mode=None,
            continuation_locale=None,
            issued_to="usr_admin",
            api_base_url="http://127.0.0.1:1/flowgate/api/v1",
            mention_builder=lambda raw, scratch: "## prompt\ndo the work\n",
            **kw,
        )
        run_id = res["run_id"]
        for _ in range(500):
            record = svc.get_run_record(run_id)
            if record and record["status"] == "finished":
                return record
            time.sleep(0.02)
    raise AssertionError("run did not finish")


# ── The bug: success was unreachable, not merely unlikely ────────────────────

class TestPerfectWorkerSucceeds:
    """NR0003 회귀 방어: 스코프별로 '완벽한 워커 → complete'를 고정한다."""

    def test_edit_run_that_revises_the_document_is_complete(self, world):
        run = _run(world, "edit", work=world.do_edit)
        assert run["docs_target"] == 0, "an edit run creates no document"
        assert run["outcome"] == "complete"
        assert run["oracle_mismatch"] is False
        # complete ⇒ scratch cleaned up immediately, not left as scratch_retained (§2.7).
        assert run["scratch_retained"] is None

    def test_review_run_that_writes_a_review_row_is_complete(self, world):
        run = _run(world, "review", work=world.do_review)
        assert run["docs_target"] == 0, "a review is a child row, not a document"
        assert run["outcome"] == "complete"
        assert run["oracle_mismatch"] is False

    def test_rework_and_vr_correction_still_reach_the_engine_as_edit(self, world):
        # Only chat moved to its append-only scope. Rework and VR correction continue
        # to revise the bound document under an edit token.
        run = _run(world, "edit", work=world.do_edit)
        assert run["outcome"] == "complete"


class TestNoWorkStillFails:
    """The fix must judge the work, not rename the verdict — 'none' has to survive."""

    def test_edit_run_that_changes_nothing_is_none(self, world):
        run = _run(world, "edit", work=None)
        assert run["outcome"] == "none"
        assert run["oracle_mismatch"] is True, "clean exit + nothing landed = the mismatch signal"
        assert run["scratch_retained"] is not None

    def test_review_run_that_writes_nothing_is_none(self, world):
        run = _run(world, "review", work=None)
        assert run["outcome"] == "none"

    def test_edit_of_a_different_document_does_not_count(self, world):
        # Only the bound document may be credited. The inbox enforces this with a 403;
        # the judge must not be looser than the guard.
        world.do_register(seq=5)
        run = _run(world, "edit", work=lambda: world.do_edit(f"{GROUP}.0005-TR"))
        assert run["outcome"] == "none"


class TestJudgeTargetComesFromTheToken:
    """AI review finding on NR0003: run doc_ref and token doc_ref are not always the same."""

    def test_oracle_follows_the_token_not_the_run_doc_ref(self, world):
        # The legacy Q&A follow-up (qa_routes) starts the run on the Q document while
        # qa_service.issue_followup_token binds the token to the PARENT work item. The
        # inbox honours the token, so the worker can only ever edit the parent — a judge
        # watching the run's doc_ref would watch a document the worker cannot touch and
        # would report 'none' forever, exactly the bug this group is fixing.
        world.docs["flowgate.default.0259.0009-Q"] = {
            "doc_id": "flowgate.default.0259.0009-Q", "seq": 9, "status": "open",
            "revision_no": 0, "branch": "main",
        }
        world.token_doc_ref = TARGET  # token → parent work item
        run = _run(world, "edit", work=world.do_edit,          # worker edits the parent
                   doc_ref="flowgate.default.0259.0009-Q")     # run → Q document
        assert run["outcome"] == "complete"

    def test_falls_back_to_run_doc_ref_when_the_token_has_none(self, world):
        world.token_doc_ref = None
        run = _run(world, "edit", work=world.do_edit)
        assert run["outcome"] == "complete"


# ── The document oracle must be untouched where it was already right ─────────

class TestDocumentScopesUnchanged:
    """NR0003 실측 대조군: `_oracle_new_docs` was never wrong — don't 'fix' new."""

    def test_new_run_still_targets_and_reaches_a_document(self, world):
        run = _run(world, "new", work=world.do_register)
        assert run["docs_target"] == 1, "a new run is still judged by documents"
        assert run["outcome"] == "complete"
        assert run["docs_reached"] == 1

    def test_new_run_that_registers_nothing_is_still_none(self, world):
        run = _run(world, "new", work=None)
        assert (run["docs_target"], run["outcome"], run["docs_reached"]) == (1, "none", 0)

    def test_caller_supplied_oracle_still_overrides_the_scope_default(self, world):
        # 0248's completion_oracle stays an override: an edit-scope run whose real product
        # is an answer row must be judged by the caller's predicate, not by revision_no.
        answered = {"v": False}
        run = _run(world, "edit", work=lambda: answered.__setitem__("v", True),
                   completion_oracle=lambda: answered["v"])
        assert (run["docs_target"], run["outcome"]) == (0, "complete")


# ── Route coverage (NR0003 영향 범위 전수 조사) ───────────────────────────────

class TestEveryWireScopeHasAJudge:
    """The point of putting the default in the engine: no scope can be left behind.

    0248 built the `completion_oracle` hook and then migrated exactly one call site, so
    five wire scopes silently kept the unreachable document oracle. A registry keyed by
    token scope only helps if every scope the route accepts actually lands on a judge that
    can see its work — that is what this pins, and it is the assertion that fails first if
    someone adds a seventh scope without deciding how it is judged.
    """

    # NR0003's 전수 조사, as an executable table: wire scope → does it make a document?
    # 0405 P0004: work_plan_proposal joins the row. It is not an edit of an existing plan
    # (that is work_plan_fill, which has its own probe) — it hands the worker a 'new' token
    # through advance_workflow and the worker registers a WP document, so the document
    # oracle is exactly the right judge for it.
    DOCUMENT_PRODUCING = {"new", "next_step_message", "design_handoff", "work_plan_proposal"}
    SPECIAL_CASED = {"workflow_decide", "resolve_conflict"}  # own judge branch in _settle_and_judge

    def test_no_wire_scope_silently_inherits_the_document_oracle(self):
        from modules.flow_gate.api.v1 import ai_invoke_routes as routes

        for wire_scope in routes._ALLOWED_SCOPES:
            token_scope = routes._TOKEN_SCOPE.get(wire_scope, wire_scope)
            judged_by_documents = (
                token_scope not in svc._SCOPE_PROBES and wire_scope not in self.SPECIAL_CASED
            )
            if judged_by_documents:
                assert wire_scope in self.DOCUMENT_PRODUCING, (
                    f"'{wire_scope}' is judged by the document oracle but does not register a "
                    f"document — it can never succeed. Give token scope '{token_scope}' a probe "
                    f"in _SCOPE_PROBES, or a completion_oracle at its call site."
                )

    def test_the_six_defective_paths_now_land_on_a_scope_probe(self):
        # review / edit / rework / vr_correction / chat, plus the legacy Q follow-up which
        # reaches start_run as 'edit' from qa_routes. These are exactly the rows NR0003
        # tabulated as "무조건 none".
        from modules.flow_gate.api.v1 import ai_invoke_routes as routes

        for wire_scope in ("review", "edit", "rework", "vr_correction", "chat"):
            token_scope = routes._TOKEN_SCOPE.get(wire_scope, wire_scope)
            assert token_scope in svc._SCOPE_PROBES, wire_scope

    def test_chat_has_its_own_append_only_scope_probe(self):
        # 0351 T3: chat no longer revises the document through inbox/edit. The engine
        # receives the literal chat scope and judges success by the conversation head.
        from modules.flow_gate.api.v1 import ai_invoke_routes as routes

        assert routes._TOKEN_SCOPE["chat"] == "chat"
        assert svc._SCOPE_PROBES["chat"] is svc._probe_conversation_head


# ── Fast-fail (NR0003 §3) ────────────────────────────────────────────────────

class TestFastFailUsesTheRunsOwnJudge:
    def test_landed_scope_work_is_not_discarded_as_a_startup_failure(self, world):
        # `_work_landed` decides whether a nonzero exit inside the fast-fail window may be
        # retried on the next provider. It used to ask "did new documents appear?", which is
        # False for an edit run however well it went — so a worker that finished its edit and
        # then exited nonzero got re-run on the next provider, duplicating the work.
        run = {"group_id": GROUP, "baseline_seq": 4, "run_id": "aiv_test",
               "completion_oracle": lambda: True}
        assert svc._work_landed(run) is True

    def test_document_scopes_still_use_the_seq_delta(self, world):
        run = {"group_id": GROUP, "baseline_seq": 4, "run_id": "aiv_test",
               "completion_oracle": None}
        assert svc._work_landed(run) is False
        world.do_register(seq=5)
        assert svc._work_landed(run) is True


# ── T0011 새 판정 행: resolve_base_dirty (project_id probe key, not doc_ref) ──

class TestResolveBaseDirtyScopeProbe:
    """`resolve_base_dirty` is judged by tracked base-dirty file count, not documents.

    The probe is keyed by project_id (there is no bound document for this scope), and
    the shared `current > baseline` rule in `_scope_oracle` is untouched — only the
    fallback oracle-key selection routes this scope to project_id instead of doc_ref.
    """

    def test_registered_in_scope_probes_and_allowed_scopes(self):
        from modules.flow_gate.api.v1 import ai_invoke_routes as routes

        assert "resolve_base_dirty" in routes._ALLOWED_SCOPES
        assert svc._SCOPE_PROBES["resolve_base_dirty"] is svc._probe_base_dirty

    def test_doc_ref_project_id_fallback_key_reaches_the_project_probe(self, monkeypatch):
        """`ai_invoke_routes.start_ai_invoke` passes `body.project` as `doc_ref` for this
        scope (routes.py:808) precisely so `_oracle_doc_id`'s fallback lands on project_id,
        not a document id. Exercised here through the real `_scope_oracle` factory, the same
        one `start_run` calls — not a re-implementation of the routing decision."""
        from modules.flow_gate.services import git_service

        counts = iter([["a.py", "b.py"], ["a.py"]])
        monkeypatch.setattr(
            git_service, "project_git_status",
            lambda pid: {"status": {"base_dirty": {"files": next(counts)}}} if pid == "flowgate" else (_ for _ in ()).throw(AssertionError("wrong probe key")),
        )
        oracle = svc._scope_oracle("resolve_base_dirty", token_id=None, doc_ref="flowgate")
        assert oracle() is True   # 2 files -> 1 file: progress settles complete

    def test_no_output_retry_machinery_stays_closed_for_this_scope(self):
        """L0008/T0011 give `resolve_base_dirty` its own oracle but explicitly do not open
        the 0446 T0008 no-output-retry path to it — that machinery is deliberately narrowed
        to `edit`/single (see `_scope_oracle_retry_open`'s own docstring). A 'none' outcome
        here must be reported once, not silently re-run on the next provider."""
        assert svc._scope_oracle_retry_open("single", "resolve_base_dirty", True) is False
        # Contrast with the one scope that IS open, so this is a real assertion and not a
        # tautology of "everything is False".
        assert svc._scope_oracle_retry_open("single", "edit", True) is True

    def test_work_landed_uses_the_scope_oracle_not_a_seq_delta(self, monkeypatch):
        """Matrix row alongside `TestFastFailUsesTheRunsOwnJudge`: a resolve_base_dirty run
        that reduced the tracked-dirty count must not be discarded as a startup failure just
        because no document landed — and a run that changed nothing must not be credited."""
        from modules.flow_gate.services import git_service

        counts = iter([["a.py", "b.py"], ["a.py"]])
        monkeypatch.setattr(
            git_service, "project_git_status",
            lambda _p: {"status": {"base_dirty": {"files": next(counts)}}},
        )
        oracle = svc._scope_oracle("resolve_base_dirty", token_id=None, doc_ref="flowgate")
        run = {"group_id": None, "baseline_seq": 0, "run_id": "aiv_bd", "completion_oracle": oracle}
        assert svc._work_landed(run) is True

    def test_work_landed_is_false_when_the_dirty_count_does_not_shrink(self, monkeypatch):
        from modules.flow_gate.services import git_service

        monkeypatch.setattr(
            git_service, "project_git_status",
            lambda _p: {"status": {"base_dirty": {"files": ["a.py", "b.py"]}}},
        )
        oracle = svc._scope_oracle("resolve_base_dirty", token_id=None, doc_ref="flowgate")
        run = {"group_id": None, "baseline_seq": 0, "run_id": "aiv_bd", "completion_oracle": oracle}
        assert svc._work_landed(run) is False

    def test_probe_returns_negative_file_count(self, monkeypatch):
        from modules.flow_gate.services import git_service

        monkeypatch.setattr(
            git_service, "project_git_status",
            lambda _p: {"status": {"base_dirty": {"files": ["a.py", "b.py", "c.py"]}}},
        )
        assert svc._probe_base_dirty("flowgate") == -3

    def test_file_count_decrease_settles_complete(self, monkeypatch):
        from modules.flow_gate.services import git_service

        counts = iter([["a.py", "b.py"], ["b.py"]])   # baseline 2 files, then 1 remains
        monkeypatch.setattr(
            git_service, "project_git_status",
            lambda _p: {"status": {"base_dirty": {"files": next(counts)}}},
        )
        baseline = svc._probe_base_dirty("flowgate")
        current = svc._probe_base_dirty("flowgate")
        assert current > baseline   # partial progress still settles complete (L0008 §2.7)

    def test_same_or_increased_file_count_settles_none(self, monkeypatch):
        from modules.flow_gate.services import git_service

        counts = iter([["a.py", "b.py"], ["a.py", "b.py", "c.py"]])
        monkeypatch.setattr(
            git_service, "project_git_status",
            lambda _p: {"status": {"base_dirty": {"files": next(counts)}}},
        )
        baseline = svc._probe_base_dirty("flowgate")
        current = svc._probe_base_dirty("flowgate")
        assert not (current > baseline)
