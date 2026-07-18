"""flowgate.default.0268 T0004: the last two 멘트복사-only scopes get an in-app AI 호출.

B0001 was "멘트복사와 ai 호출방식 두개 다 넣으라고 한지가 언젠데 아직도 멘트복사만 하는 화면이
존재한다". NR0003 found the gap was structural, not cosmetic: `workflow_sequence_edit` and
`test_run` were real token scopes with a [멘트복사] entrance but no member in the invoke
allowlist, so no amount of front-end wiring could have started a run for them.

Two things are pinned here:

1. `_ALLOWED_SCOPES` admits both scopes (the structural half of the fix).
2. Each scope is judged by the row ITS OWN token may write — the 0259 B0001 rule. Neither
   scope can register a document, so the document oracle would report 'none' for a perfect
   worker; and, symmetrically, a worker that does nothing must still settle 'none' rather
   than inherit a trivially-satisfied docs_target of 0.

Collaborators are faked (no DB), following test_ai_invoke_scope_oracle_0259.py's pattern.
"""
from __future__ import annotations

import os
import sys
import time
import unittest.mock as mock
from pathlib import Path

import pytest

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")

_SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SERVER_DIR))

from modules.flow_gate.api.v1 import ai_invoke_routes as routes  # noqa: E402
from modules.flow_gate.services import ai_invoke_service as svc  # noqa: E402

GROUP = "flowgate.default.0268"
TARGET = "flowgate.default.0268.0005-TS"
TOKEN_ID = "tok_20260718_000001"


class FakeWorld:
    """The rows the two new scope oracles read: test runs and workflow sequence items."""

    def __init__(self):
        self.docs = {
            TARGET: {"doc_id": TARGET, "seq": 5, "status": "open",
                     "revision_no": 0, "branch": "main"},
        }
        self.test_runs: dict[str, list[dict]] = {}
        # A decided sequence: two locked steps, so max_item_seq starts at 2.
        self.sequence = {"id": 77}
        self.max_item_seq = 2

    # ── db_docs / db_tokens ──
    def get_by_id(self, doc_id):
        return self.docs.get(doc_id)

    def get_group_max_seq(self, group_id):
        return 5

    def get_documents_by_group_id(self, group_id):
        return list(self.docs.values())

    def token_by_id(self, token_id):
        return {"token_id": token_id, "doc_ref": TARGET, "action_scope": "test_run"}

    # ── db_test_runs ──
    def list_by_doc(self, doc_id):
        return list(self.test_runs.get(doc_id, []))

    # ── db_wfseq ──
    def get_sequence_by_doc_id(self, doc_id):
        return self.sequence if doc_id == TARGET else None

    def get_max_item_seq(self, sequence_id):
        return self.max_item_seq

    # ── what a worker actually does with each token ──
    def do_test_run(self, doc_id=TARGET):
        """POST /documents/test-run: one run row lands on the bound TS."""
        self.test_runs.setdefault(doc_id, []).append({"run_id": "tr_1", "status": "passed"})

    def do_sequence_edit(self, added=3):
        """PATCH /workflow/sequence: edit_workflow_pending re-inserts at max_item_seq + n."""
        self.max_item_seq += added


@pytest.fixture
def world(monkeypatch, tmp_path):
    w = FakeWorld()
    monkeypatch.setattr(svc, "ORACLE_SETTLE_SEC", 0)
    monkeypatch.setattr(svc, "_runs", {})
    monkeypatch.setattr(svc.db_docs, "get_by_id", w.get_by_id)
    monkeypatch.setattr(svc.db_docs, "get_group_max_seq", w.get_group_max_seq)
    monkeypatch.setattr(svc.db_docs, "get_documents_by_group_id", w.get_documents_by_group_id)
    monkeypatch.setattr(svc.db_tokens, "get_by_id", w.token_by_id)
    monkeypatch.setattr(svc.db_test_runs, "list_by_doc", w.list_by_doc)
    monkeypatch.setattr(svc.db_wfseq, "get_sequence_by_doc_id", w.get_sequence_by_doc_id)
    monkeypatch.setattr(svc.db_wfseq, "get_max_item_seq", w.get_max_item_seq)
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
                      "expires_at": "2026-07-19T00:00:00+00:00",
                      "scratch_dir": str(tmp_path / "tokwork")},
    )
    monkeypatch.setattr(svc.token_service, "revoke", lambda *a, **kw: None)
    monkeypatch.setattr(svc.storage_paths, "get_storage_root", lambda *a, **kw: tmp_path / "storage")
    monkeypatch.setattr(svc.storage_paths, "resolve_project_src_root",
                        lambda pid, branch, *, group_id: None)
    monkeypatch.setattr(svc.storage_paths, "to_storage_relative", lambda path, project=None: str(path))
    monkeypatch.setattr(svc, "_broadcast", lambda run, event_type, payload: None)
    return w


def _run(world, action_scope, work=None):
    """Start a single run whose 'AI' performs `work`, and wait for its verdict."""

    def _fake_cli(provider, prompt, run):
        if work is not None:
            work()
        return "started_ok", None

    with mock.patch.object(svc, "_cli_execute", side_effect=_fake_cli):
        res = svc.start_run(
            project_id="flowgate",
            module="default",
            group_id=GROUP,
            doc_ref=TARGET,
            action_scope=action_scope,
            mode="single",
            continuation_target_seq=None,
            continuation_review_mode=False,
            continuation_instruction_mode=None,
            continuation_locale=None,
            issued_to="usr_admin",
            api_base_url="http://127.0.0.1:1/flowgate/api/v1",
            mention_builder=lambda raw, scratch: "## prompt\ndo the work\n",
        )
        run_id = res["run_id"]
        for _ in range(500):
            record = svc.get_run_record(run_id)
            if record and record["status"] == "finished":
                return record
            time.sleep(0.02)
    raise AssertionError("run did not finish")


# ── The structural half: the allowlist was the actual blocker ────────────────

class TestScopesAreInvokable:
    """NR0003 §2.2: both surfaces were copy-only because these scopes were not admitted."""

    def test_sequence_edit_and_test_run_are_allowed_invoke_scopes(self):
        assert "workflow_sequence_edit" in routes._ALLOWED_SCOPES
        assert "test_run" in routes._ALLOWED_SCOPES

    def test_both_keep_their_own_token_scope(self):
        # Unlike chat/rework/design_handoff (which borrow an edit/new grant), each of these
        # is minted by a dedicated service that also builds its mention, so the identity
        # fallthrough of _TOKEN_SCOPE.get must be preserved — mapping them onto 'new' or
        # 'edit' would hand the worker a grant its target endpoint rejects.
        assert routes._TOKEN_SCOPE.get("workflow_sequence_edit", "workflow_sequence_edit") \
            == "workflow_sequence_edit"
        assert routes._TOKEN_SCOPE.get("test_run", "test_run") == "test_run"

    def test_continuous_mode_stays_closed_to_both(self):
        # Neither scope self-chains; continuous is restricted to new/edit/workflow_decide.
        assert "workflow_sequence_edit" not in ("new", "edit", "workflow_decide")
        assert "test_run" not in ("new", "edit", "workflow_decide")


# ── The judging half: 0259 B0001's rule applied to the two new scopes ────────

class TestPerfectWorkerSucceeds:

    def test_test_run_worker_that_starts_a_run_is_complete(self, world):
        run = _run(world, "test_run", work=world.do_test_run)
        assert run["docs_target"] == 0, "a test_run token cannot register a document"
        assert run["outcome"] == "complete"
        assert run["oracle_mismatch"] is False

    def test_sequence_edit_worker_that_replaces_the_tail_is_complete(self, world):
        run = _run(world, "workflow_sequence_edit", work=world.do_sequence_edit)
        assert run["docs_target"] == 0, "a sequence edit registers no document"
        assert run["outcome"] == "complete"
        assert run["oracle_mismatch"] is False


class TestNoWorkStillFails:
    """The fix must judge the work, not rename the verdict (0259 B0001의 규칙)."""

    def test_test_run_worker_that_starts_nothing_is_none(self, world):
        run = _run(world, "test_run", work=None)
        assert run["outcome"] == "none"
        assert run["oracle_mismatch"] is True, "clean exit + nothing landed = the mismatch signal"

    def test_sequence_edit_worker_that_changes_nothing_is_none(self, world):
        # Without a probe this is the case that would silently report 'complete':
        # docs_target 0 makes `docs_reached >= docs_target` trivially true.
        run = _run(world, "workflow_sequence_edit", work=None)
        assert run["outcome"] == "none"
        assert run["oracle_mismatch"] is True

    def test_test_run_on_a_different_document_does_not_count(self, world):
        # Only the bound document may be credited — the judge must not be looser than
        # the token guard that restricts the worker to it.
        run = _run(world, "test_run", work=lambda: world.do_test_run("flowgate.default.0268.0009-TS"))
        assert run["outcome"] == "none"


class TestSequenceEditProbeShape:
    """Why max_item_seq and not a plain count (NR0003 → _probe_sequence_max_item)."""

    def test_probe_reads_the_max_item_seq_of_the_bound_sequence(self, world):
        assert svc._probe_sequence_max_item(TARGET) == 2
        world.do_sequence_edit(added=3)
        assert svc._probe_sequence_max_item(TARGET) == 5

    def test_probe_is_zero_when_the_workflow_is_not_decided(self, world):
        # request_sequence_edit refuses an undecided workflow, so this is the "worker could
        # not have done anything" case — it must read 0, not raise.
        assert svc._probe_sequence_max_item("flowgate.default.0268.0009-TS") == 0

    def test_probe_survives_a_lookup_failure(self, world, monkeypatch):
        def _boom(doc_id):
            raise RuntimeError("db down")

        monkeypatch.setattr(svc.db_wfseq, "get_sequence_by_doc_id", _boom)
        # A failing probe must degrade to 'cannot confirm', not take the run down with it.
        assert svc._probe_sequence_max_item(TARGET) == 0
