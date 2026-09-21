"""Final approval deferred across a merge conflict — flowgate.default.0555 T0008 (T#2).

D0005 §3.5/§3.6/§3.8/§3.9/§3.11/§3.12's conflict contract, driven against a REAL
git origin: the approval a conflict interrupts must stay unapproved through
resolution, rejection and re-review, and must be committed exactly once, inside the
merge review's own project Git lock, at the moment the merge really terminates.

The harness (temporary sqlite with the real migrations, a bare local origin, the
``needs_git`` capability gate) is the one test_git_integration_0115.py already owns;
importing its fixtures keeps a single definition of "a real git project" rather than
a second, slowly diverging copy.
"""
from __future__ import annotations

import asyncio
import json
import subprocess
import threading
import uuid

import pytest

from test_git_integration_0115 import (  # noqa: F401 — fixtures are used by name
    _git,
    needs_git,
    patch_store,
    tmp_db,
)

USER = {"user_id": "reviewer", "is_admin": True}
# Its OWN project, not the one test_git_integration_0115 drives. FLOWGATE_STORAGE_DIR
# is a single directory for the whole session while each module builds its own bare
# origin, so sharing a project would leave one module's base checkout pointing at the
# other module's deleted origin — a provisioning failure with nothing to say about
# final approval. A separate project id keeps the two source roots apart.
PROJECT = "faprj"
PROJECT_NAME = "FaProj"


@pytest.fixture(scope="module")
def project(patch_store, tmp_db):
    from modules.flow_gate.db import projects

    projects.create({"project_id": PROJECT, "project_name": PROJECT_NAME})
    yield


@pytest.fixture(scope="class")
def origin_repo(project):
    """A bare origin with one commit on main, plus an enabled git config."""
    import shutil
    import tempfile
    from pathlib import Path

    from modules.flow_gate.services import git_service as svc

    tmp = Path(tempfile.mkdtemp(prefix="fg-fa-origin-"))
    bare = tmp / "origin.git"
    seedwt = tmp / "seedwt"
    _git(["init", "--bare", "-b", "main", str(bare)])
    _git(["init", "-b", "main", str(seedwt)])
    (seedwt / "README.md").write_text("hello\n", encoding="utf-8")
    (seedwt / "shared.py").write_text('"line1"\n', encoding="utf-8")
    _git(["add", "-A"], cwd=seedwt)
    _git(["commit", "-m", "init"], cwd=seedwt)
    _git(["remote", "add", "origin", str(bare)], cwd=seedwt)
    _git(["push", "origin", "main"], cwd=seedwt)

    svc.save_config(PROJECT, {
        "repo_url": bare.as_uri(),
        "provider": "generic",
        "base_branch": "main",
        "default_finalize_action": "merge",
        "enabled": True,
    })
    yield {"bare": bare, "seedwt": seedwt, "tmp": tmp}
    svc.delete_config(PROJECT)
    shutil.rmtree(tmp, ignore_errors=True)


def _seed_final_approval_group(group_id: str) -> tuple[str, str]:
    """A group shaped exactly as a final approval needs it: R root in progress,
    one pending AC pointing at it, and a workflow sequence with no open slot."""
    from modules.flow_gate.db import documents as db_docs
    from modules.flow_gate.db import groups as db_groups
    from modules.flow_gate.db import workflow_sequences as db_wfseq
    from modules.flow_gate.db.connection import get_store, now_iso

    # The approval transaction writes a state-changed event, and `events.actor_user_id`
    # is a real FK to `users` — without the row the whole (correct) transaction rolls
    # back on an unrelated integrity error.
    store = get_store()
    if store._fetch_one("SELECT 1 AS ok FROM users WHERE user_id = ?", [USER["user_id"]]) is None:
        now = now_iso()
        store._execute(
            "INSERT INTO users (user_id, username, email, password, is_active, is_admin,"
            " first_login_required, created_at, updated_at)"
            " VALUES (?, ?, ?, 'x', 1, 1, 0, ?, ?)",
            [USER["user_id"], USER["user_id"], f'{USER["user_id"]}@test', now, now],
        )
    if db_groups.get_by_id(group_id) is None:
        db_groups.create({
            "group_id": group_id, "project_id": PROJECT,
            "module": "default", "title": "final approval",
        })
    root_id = f"{group_id}.0001-R"
    ac_id = f"{group_id}.0002-AC"
    if db_docs.get_by_id(root_id) is None:
        db_docs.create({
            "doc_id": root_id, "project_id": PROJECT, "module": "default",
            "group_id": group_id, "type_code": "R", "seq": 1, "title": "root",
            "file_path": f"documents/{group_id}/0001-R.md",
        })
    db_docs.update(root_id, {"doc_review_status": "wf_in_progress"})
    if db_wfseq.get_sequence_by_doc_id(root_id) is None:
        db_wfseq.insert_sequence(root_id)
    if db_docs.get_by_id(ac_id) is None:
        db_docs.create({
            "doc_id": ac_id, "project_id": PROJECT, "module": "default",
            "group_id": group_id, "type_code": "AC", "seq": 2,
            "title": "final approval", "target_id": root_id,
        })
    db_docs.update(ac_id, {"doc_review_status": "pending_review", "target_id": root_id})
    return root_id, ac_id


def _make_conflict_material(group_id: str, origin, path: str, ours: str, theirs: str) -> None:
    """Give the group's worktree and origin/main two different versions of `path`."""
    from modules.flow_gate.services import git_service as svc
    from modules.flow_gate.storage.paths import src_root

    assert svc.ensure_worktree(PROJECT, "default", group_id) == "ok"
    branch = group_id.replace(".", "_")
    (src_root(PROJECT_NAME, branch) / path).write_text(ours, encoding="utf-8")
    seedwt = origin["seedwt"]
    _git(["pull", "origin", "main"], cwd=seedwt)
    (seedwt / path).write_text(theirs, encoding="utf-8")
    _git(["add", "-A"], cwd=seedwt)
    _git(["commit", "-m", f"mainline {path}"], cwd=seedwt)
    _git(["push", "origin", "main"], cwd=seedwt)


def _final_approve(doc_id: str, git_action: str = "merge") -> tuple[int, dict]:
    """Drive the real final-approval orchestrator (the only public entry point)."""
    from modules.flow_gate.workflow.routers import workflow

    response = asyncio.run(workflow.document_review_transition_rpc(
        "approve",
        workflow.DocumentBodyRequest(doc_id=doc_id, git_action=git_action),
        USER,
        None,
    ))
    return response.status_code, json.loads(response.body.decode("utf-8"))


def _review_status(doc_id: str) -> str:
    from modules.flow_gate.db import documents as db_docs

    return (db_docs.get_by_id(doc_id) or {}).get("doc_review_status")


def _intent(merge_id: int):
    from modules.flow_gate.db import git_integration as db_git
    from modules.flow_gate.services.git import approval_intent

    return approval_intent.intent_of_session(db_git.get_session(merge_id))


def _origin_head(origin) -> str:
    return _git(["rev-parse", "main"], cwd=origin["bare"]).strip()


def _resolve(group_id: str, merge_id: int, path: str, content: str) -> dict:
    from modules.flow_gate.services import git_service as svc

    out = svc.resolve_conflicts(group_id, merge_id, [{"path": path, "content": content}], True)
    return out["result"]


@needs_git
class TestFinalApprovalConflictIntent0555:
    """C1–C11 plus T0008 §15's extra guarantees, in sequence on one group."""

    GROUP = f"{PROJECT}.default.0140"
    PATH = "shared.py"
    OURS = '"intent group version"\n'
    THEIRS = '"intent mainline version"\n'
    RESOLUTION = '"intent group version"\n"intent mainline version"\n'

    @pytest.fixture(scope="class")
    def flow(self, origin_repo):
        """C1 once, then C2→C5 continue the same conflict the way a person would."""
        from modules.flow_gate.db import git_integration as db_git

        root_id, ac_id = _seed_final_approval_group(self.GROUP)
        _make_conflict_material(self.GROUP, origin_repo, self.PATH, self.OURS, self.THEIRS)

        seen: dict = {}
        original_create = db_git.create_session

        def _watched_create(*args, **kwargs):
            # C1's real question: is the project Git lock STILL held at the instant
            # the session (and with it the intent) is written? §3.5 says the whole
            # create happens inside the orchestrator's lock, which is what makes a
            # concurrent second session structurally impossible.
            seen["lock_at_create"] = db_git.get_lock(PROJECT)
            return original_create(*args, **kwargs)

        db_git.create_session = _watched_create
        try:
            status, payload = _final_approve(ac_id)
        finally:
            db_git.create_session = original_create
        return {
            "root_id": root_id, "ac_id": ac_id, "status": status,
            "payload": payload, "seen": seen, "origin": origin_repo,
        }

    # ── C1 ────────────────────────────────────────────────────────────────────
    def test_c1_conflict_parks_the_intent_inside_the_git_lock(self, flow):
        from modules.flow_gate.db import git_integration as db_git

        assert flow["status"] == 200, flow["payload"]
        approval = flow["payload"]["approval"]
        assert approval["approved"] is False
        assert approval["deferred"] is True
        assert approval["stage"] == "git_finalize"
        assert flow["payload"]["git"]["result"]["status"] == "conflict"

        merge_id = flow["payload"]["git"]["result"]["merge_id"]
        assert db_git.get_state(self.GROUP)["status"] == "conflict"
        # the session was written while the orchestrator still owned the mutex …
        assert (flow["seen"]["lock_at_create"] or {}).get("holder", "").startswith("approval:")
        # … and it was released by the time the request answered (§3.5: the human
        # wait is NOT held under the project lock).
        assert db_git.get_lock(PROJECT) is None

        intent = _intent(merge_id)
        assert intent is not None
        assert intent["approval_intent_id"] == approval["approval_intent_id"]
        assert intent["ac_doc_id"] == flow["ac_id"]
        assert intent["group_id"] == self.GROUP
        assert intent["requested_by"] == USER["user_id"]
        assert intent["git_action"] == "merge"
        assert intent["created_at"]
        # the intent id is its own fact, not a restatement of the session id
        assert intent["approval_intent_id"] != str(merge_id)

        assert _review_status(flow["ac_id"]) == "pending_review"
        assert _review_status(flow["root_id"]) == "wf_in_progress"

    def test_c1_bound_group_refuses_a_public_finalize(self, flow):
        """§10 / §3.11 — the coupling marker is enforced, not merely displayed."""
        from modules.flow_gate.services import git_service as svc

        state = svc.get_finalize_state(self.GROUP)["state"]
        assert state["final_approval_bound"] is True

        with pytest.raises(svc.GitServiceError) as exc:
            svc.finalize(self.GROUP, "merge")
        assert exc.value.code == "final_approval_bound"
        assert _review_status(flow["ac_id"]) == "pending_review"

    # ── C2 ────────────────────────────────────────────────────────────────────
    def test_c2_resolution_submitted_still_approves_nothing(self, flow):
        merge_id = flow["payload"]["git"]["result"]["merge_id"]
        before = _origin_head(flow["origin"])

        result = _resolve(self.GROUP, merge_id, self.PATH, self.RESOLUTION)
        assert result["status"] == "resolved_pending_review"

        assert _origin_head(flow["origin"]) == before
        assert _review_status(flow["ac_id"]) == "pending_review"
        assert _review_status(flow["root_id"]) == "wf_in_progress"
        assert _intent(merge_id)["approval_intent_id"] == \
            flow["payload"]["approval"]["approval_intent_id"]

    # ── C3 ────────────────────────────────────────────────────────────────────
    def test_c3_reject_and_re_resolve_keep_the_intent(self, flow):
        from modules.flow_gate.services import git_service as svc

        merge_id = flow["payload"]["git"]["result"]["merge_id"]
        intent_id = flow["payload"]["approval"]["approval_intent_id"]

        rejected = svc.reject_merge_review(
            self.GROUP, merge_id, reason="다시 확인해 주세요 — 이 사유는 충분히 깁니다.",
            provider_id=None, provider_pinned=False, start_run=lambda _m: None,
        )
        assert rejected["result"]["status"] == "returned_to_resolver"
        # a rejection is NOT a cancelled final approval (§7)
        assert _intent(merge_id)["approval_intent_id"] == intent_id
        assert _review_status(flow["ac_id"]) == "pending_review"
        assert _review_status(flow["root_id"]) == "wf_in_progress"

        again = _resolve(self.GROUP, merge_id, self.PATH, self.RESOLUTION)
        assert again["status"] == "resolved_pending_review"
        assert _intent(merge_id)["approval_intent_id"] == intent_id
        assert _review_status(flow["ac_id"]) == "pending_review"
        flow["fingerprint"] = again["review_fingerprint"]

    # ── C8 (before the session closes: the record is what survives) ───────────
    def test_c8_intent_survives_a_fresh_read_of_the_session(self, flow):
        from modules.flow_gate.db import connection as conn_mod
        from modules.flow_gate.services.git import approval_intent

        merge_id = flow["payload"]["git"]["result"]["merge_id"]
        # A restart keeps nothing in memory: drop every cached read and rebuild the
        # intent from the durable row alone.
        conn_mod._request_cache.invalidate()
        session, intent = approval_intent.find_intent_session(self.GROUP)
        assert session["merge_id"] == merge_id
        assert intent["approval_intent_id"] == flow["payload"]["approval"]["approval_intent_id"]
        assert intent["group_id"] == self.GROUP
        assert intent["ac_doc_id"] == flow["ac_id"]
        # and it never reaches out to a different group
        assert approval_intent.find_intent_session(f"{PROJECT}.default.0149") == (None, None)

    # ── C4 + C9 ───────────────────────────────────────────────────────────────
    def test_c4_review_approve_commits_the_parked_approval_under_the_lock(self, flow, monkeypatch):
        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services import git_service as svc
        from modules.flow_gate.workflow import pipeline_service

        merge_id = flow["payload"]["git"]["result"]["merge_id"]
        before = _origin_head(flow["origin"])
        observed: dict = {}
        events: list = []

        original_commit = pipeline_service.commit_final_approval

        def _watched_commit(**kwargs):
            observed["lock_at_commit"] = db_git.get_lock(PROJECT)
            observed["calls"] = observed.get("calls", 0) + 1
            return original_commit(**kwargs)

        monkeypatch.setattr(pipeline_service, "commit_final_approval", _watched_commit)

        original_emit = svc._emit

        def _watched_emit(event, project_id, group_id, payload):
            # C9: whatever a client refreshes on after this event must already see
            # both facts. Read the document state AT EMIT TIME, not afterwards.
            events.append((event, payload, _review_status(flow["ac_id"]),
                           _review_status(flow["root_id"])))
            return original_emit(event, project_id, group_id, payload)

        monkeypatch.setattr(svc, "_emit", _watched_emit)

        attempt_id = str(uuid.uuid4())
        flow["attempt_id"] = attempt_id
        approved = svc.approve_merge_review(
            self.GROUP, merge_id, attempt_id=attempt_id,
            review_fingerprint=flow["fingerprint"], authority="human",
        )
        assert approved["result"]["status"] == "merged"
        assert approved["result"]["approval"]["approved"] is True
        assert approved["result"]["approval"]["stage"] == "complete"

        # the approval ran inside the merge review's own project Git lock …
        assert (observed["lock_at_commit"] or {}).get("holder", "").startswith("review:")
        assert observed["calls"] == 1
        # … and the lock is gone once the call returns
        assert db_git.get_lock(PROJECT) is None

        assert _origin_head(flow["origin"]) != before
        assert db_git.get_state(self.GROUP)["status"] == "merged"
        assert _review_status(flow["ac_id"]) == "approved"
        assert _review_status(flow["root_id"]) == "wf_done"

        # intent consumed exactly once, in that same transaction
        assert _intent(merge_id) is None
        context = db_git.session_context(db_git.get_session(merge_id))
        assert context["final_approval_intent_consumed"]["ac_doc_id"] == flow["ac_id"]
        assert db_git.get_session(merge_id)["status"] == "done"

        # C9: the completion event went out AFTER the approval was already committed
        done = [e for e in events if e[0] == "git_finalize_done"]
        assert done, [e[0] for e in events]
        assert done[-1][1]["approval"]["approved"] is True
        assert (done[-1][2], done[-1][3]) == ("approved", "wf_done")
        # T0008 §12 / C9's other half: `_set_status`'s OWN git_pending_changed
        # broadcast is not exempt from the same rule. A subscriber that refreshes
        # off THIS event, not git_finalize_done, must not be able to observe
        # "merged" next to an AC still pending_review either.
        pending = [e for e in events if e[0] == "git_pending_changed"]
        assert pending, [e[0] for e in events]
        assert pending[-1][1]["status"] == "merged"
        assert (pending[-1][2], pending[-1][3]) == ("approved", "wf_done")
        flow["merged_head"] = _origin_head(flow["origin"])

    # ── C5 ────────────────────────────────────────────────────────────────────
    def test_c5_same_attempt_id_resend_adds_no_commit_and_no_second_approval(self, flow, monkeypatch):
        from modules.flow_gate.services import git_service as svc
        from modules.flow_gate.workflow import pipeline_service

        merge_id = flow["payload"]["git"]["result"]["merge_id"]
        monkeypatch.setattr(
            pipeline_service, "commit_final_approval",
            lambda **kw: pytest.fail("a completed session must not approve again"),
        )
        again = svc.approve_merge_review(
            self.GROUP, merge_id, attempt_id=flow["attempt_id"],
            review_fingerprint=flow["fingerprint"], authority="human",
        )
        assert again["result"]["status"] == "already_applied"
        # and a DIFFERENT attempt on a finished session is refused outright, so
        # neither shape of a resend can produce a second commit or approval.
        with pytest.raises(svc.GitServiceError) as exc:
            svc.approve_merge_review(
                self.GROUP, merge_id, attempt_id=str(uuid.uuid4()),
                review_fingerprint=flow["fingerprint"], authority="human",
            )
        assert exc.value.code == "review_not_ready"
        assert _origin_head(flow["origin"]) == flow["merged_head"]
        assert _review_status(flow["ac_id"]) == "approved"
        assert _intent(merge_id) is None

    # ── C6 ────────────────────────────────────────────────────────────────────
    def test_c6_abort_discards_the_intent(self, origin_repo):
        """abort throws the parked approval away instead of carrying it over."""
        self.GROUP = f"{PROJECT}.default.0141"
        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services import git_service as svc

        root_id, ac_id = _seed_final_approval_group(self.GROUP)
        _make_conflict_material(
            self.GROUP, origin_repo, "shared.py",
            '"abort group version"\n', '"abort mainline version"\n',
        )
        status, payload = _final_approve(ac_id)
        assert status == 200 and payload["git"]["result"]["status"] == "conflict"
        merge_id = payload["git"]["result"]["merge_id"]
        assert _intent(merge_id) is not None

        out = svc.abort_merge(self.GROUP, merge_id)
        assert out["result"]["status"] == "waiting"
        assert out["result"]["final_approval_intent_discarded"] is True

        assert _intent(merge_id) is None
        context = db_git.session_context(db_git.get_session(merge_id))
        assert context["final_approval_intent_discarded"]["reason"] == "merge_abort"
        assert _review_status(ac_id) == "pending_review"
        assert _review_status(root_id) == "wf_in_progress"
        assert db_git.get_state(self.GROUP)["status"] == "waiting"
        assert svc.get_finalize_state(self.GROUP)["state"]["final_approval_bound"] is False

    # ── C7 ────────────────────────────────────────────────────────────────────
    def test_c7_target_mismatch_leaves_git_merged_and_approves_nothing(self, origin_repo):
        """The intent is re-validated, never guessed around."""
        self.GROUP = f"{PROJECT}.default.0142"
        from modules.flow_gate.db import documents as db_docs
        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services import git_service as svc

        root_id, ac_id = _seed_final_approval_group(self.GROUP)
        _make_conflict_material(
            self.GROUP, origin_repo, "shared.py",
            '"mismatch group version"\n', '"mismatch mainline version"\n',
        )
        status, payload = _final_approve(ac_id)
        assert status == 200 and payload["git"]["result"]["status"] == "conflict"
        merge_id = payload["git"]["result"]["merge_id"]
        resolved = _resolve(
            self.GROUP, merge_id, "shared.py",
            '"mismatch group version"\n"mismatch mainline version"\n',
        )
        before = _origin_head(origin_repo)

        # the AC moves out from under the intent while the conflict is being reviewed
        db_docs.update(ac_id, {"doc_review_status": "rejected"})

        approved = svc.approve_merge_review(
            self.GROUP, merge_id, attempt_id=str(uuid.uuid4()),
            review_fingerprint=resolved["review_fingerprint"], authority="human",
        )
        assert approved["result"]["status"] == "merged"
        assert approved["result"]["approval"]["approved"] is False
        assert approved["result"]["approval"]["stage"] == "intent_mismatch"
        assert approved["result"]["approval"]["error"]["code"] == "intent_document_not_pending"

        # Git is never undone, and no other AC is approved in its place
        assert _origin_head(origin_repo) != before
        assert db_git.get_state(self.GROUP)["status"] == "merged"
        assert _review_status(ac_id) == "rejected"
        assert _review_status(root_id) == "wf_in_progress"
        # the intent is left behind, not consumed: a person decides what happens next
        assert _intent(merge_id) is not None

    # ── C11 ───────────────────────────────────────────────────────────────────
    def test_c11_failed_transaction_keeps_git_and_intent_then_reapproves(
        self, origin_repo, monkeypatch,
    ):
        """A merged Git with a failed approval transaction, and its recovery."""
        self.GROUP = f"{PROJECT}.default.0143"
        from modules.flow_gate.db import documents as db_docs
        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services import git_service as svc
        from modules.flow_gate.workflow import pipeline_service

        root_id, ac_id = _seed_final_approval_group(self.GROUP)
        _make_conflict_material(
            self.GROUP, origin_repo, "shared.py",
            '"retry group version"\n', '"retry mainline version"\n',
        )
        status, payload = _final_approve(ac_id)
        assert status == 200 and payload["git"]["result"]["status"] == "conflict"
        merge_id = payload["git"]["result"]["merge_id"]
        intent_id = payload["approval"]["approval_intent_id"]
        resolved = _resolve(
            self.GROUP, merge_id, "shared.py",
            '"retry group version"\n"retry mainline version"\n',
        )
        before = _origin_head(origin_repo)

        original_cas = pipeline_service.db_docs.update_review_status_cas

        def _failing_cas(doc_id, expected, nxt):
            if nxt == "wf_done":
                raise RuntimeError("injected approval transaction failure")
            return original_cas(doc_id, expected, nxt)

        monkeypatch.setattr(pipeline_service.db_docs, "update_review_status_cas", _failing_cas)
        approved = svc.approve_merge_review(
            self.GROUP, merge_id, attempt_id=str(uuid.uuid4()),
            review_fingerprint=resolved["review_fingerprint"], authority="human",
        )
        monkeypatch.undo()

        assert approved["result"]["status"] == "merged"
        assert approved["result"]["approval"]["approved"] is False
        assert approved["result"]["approval"]["stage"] == "approval_commit"
        merged_head = _origin_head(origin_repo)
        assert merged_head != before
        assert db_git.get_state(self.GROUP)["status"] == "merged"
        assert _review_status(ac_id) == "pending_review"
        assert _review_status(root_id) == "wf_in_progress"
        assert _intent(merge_id)["approval_intent_id"] == intent_id
        assert db_git.get_lock(PROJECT) is None

        # §9 recovery: approve again. No git re-run, no new merge commit — the
        # surviving intent is what the transaction consumes this time.
        merges_before = _git(["rev-list", "--count", "main"], cwd=origin_repo["bare"]).strip()
        status2, payload2 = _final_approve(ac_id)
        assert status2 == 200, payload2
        assert payload2["approval"]["approved"] is True
        assert payload2["git"]["result"].get("terminal_retry") is True
        assert _origin_head(origin_repo) == merged_head
        assert _git(["rev-list", "--count", "main"], cwd=origin_repo["bare"]).strip() == merges_before
        assert _review_status(ac_id) == "approved"
        assert _review_status(root_id) == "wf_done"
        assert _intent(merge_id) is None
        assert db_docs.get_by_id(ac_id)["doc_review_status"] == "approved"

    # ── C10 ───────────────────────────────────────────────────────────────────
    def test_c10_concurrent_review_approve_commits_once(self, origin_repo):
        """Two simultaneous review approvals still approve exactly once."""
        self.GROUP = f"{PROJECT}.default.0144"
        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services import git_service as svc

        root_id, ac_id = _seed_final_approval_group(self.GROUP)
        _make_conflict_material(
            self.GROUP, origin_repo, "shared.py",
            '"race group version"\n', '"race mainline version"\n',
        )
        status, payload = _final_approve(ac_id)
        merge_id = payload["git"]["result"]["merge_id"]
        resolved = _resolve(
            self.GROUP, merge_id, "shared.py",
            '"race group version"\n"race mainline version"\n',
        )
        fingerprint = resolved["review_fingerprint"]
        commits_before = int(_git(
            ["rev-list", "--count", "--first-parent", "main"], cwd=origin_repo["bare"],
        ).strip())

        barrier = threading.Barrier(2)
        results: list = []
        lock = threading.Lock()

        def _approve():
            barrier.wait()
            try:
                out = svc.approve_merge_review(
                    self.GROUP, merge_id, attempt_id=str(uuid.uuid4()),
                    review_fingerprint=fingerprint, authority="human",
                )
                outcome = out["result"]
            except svc.GitServiceError as exc:
                outcome = {"status": "error", "code": exc.code}
            with lock:
                results.append(outcome)

        threads = [threading.Thread(target=_approve) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=180)

        assert len(results) == 2, results
        approvals = [r for r in results if (r.get("approval") or {}).get("approved")]
        # exactly one request approved; the other is serialized away
        assert len(approvals) == 1, results
        losers = [r for r in results if r not in approvals]
        # Whichever refusal the loser meets — the lock (git_busy), the finished
        # session (review_not_ready) or the idempotent replay (already_applied) —
        # the fact that matters is that it approved nothing.
        assert losers[0].get("code") in (None, "git_busy", "review_not_ready") or \
            losers[0].get("status") in ("already_applied", "merged"), losers
        assert (losers[0].get("approval") or {}).get("approved") is not True

        commits_after = int(_git(
            ["rev-list", "--count", "--first-parent", "main"], cwd=origin_repo["bare"],
        ).strip())
        # exactly ONE merge commit landed on main, not two
        assert commits_after == commits_before + 1
        assert _review_status(ac_id) == "approved"
        assert _review_status(root_id) == "wf_done"
        assert _intent(merge_id) is None
        assert db_git.get_lock(PROJECT) is None

    # ── reconciling success branch (T0008 §5/§7/§9) ────────────────────────────
    def test_reconcile_completes_the_deferred_approval_when_the_push_actually_landed(
        self, origin_repo, monkeypatch,
    ):
        """A push whose result this process never learned, but that DID land on
        the remote, must still finish through the common completer — the
        `observed == merge_commit` branch used to close Git without ever calling
        `commit_deferred_approval()`, leaving the AC parked pending_review forever
        with no open session left for a retry to find."""
        self.GROUP = f"{PROJECT}.default.0149"
        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services import git_service as svc

        root_id, ac_id = _seed_final_approval_group(self.GROUP)
        _make_conflict_material(
            self.GROUP, origin_repo, "shared.py",
            '"reconcile group version"\n', '"reconcile mainline version"\n',
        )
        status, payload = _final_approve(ac_id)
        assert status == 200 and payload["git"]["result"]["status"] == "conflict"
        merge_id = payload["git"]["result"]["merge_id"]
        intent_id = payload["approval"]["approval_intent_id"]
        resolved = _resolve(
            self.GROUP, merge_id, "shared.py",
            '"reconcile group version"\n"reconcile mainline version"\n',
        )

        # Let the push really happen, then tell the caller it timed out — exactly
        # the "result unknown" gap `_conditionally_push_or_reconcile` classifies as
        # push_remote_unknown, D0006 §3.6.
        original_run_git = svc._run_git

        def _push_result_lost(args, **kwargs):
            result = original_run_git(args, **kwargs)
            if args and args[0] == "push":
                return subprocess.CompletedProcess(args, -1, result.stdout, "timeout")
            return result

        monkeypatch.setattr(svc, "_run_git", _push_result_lost)
        approved = svc.approve_merge_review(
            self.GROUP, merge_id, attempt_id=str(uuid.uuid4()),
            review_fingerprint=resolved["review_fingerprint"], authority="human",
        )
        monkeypatch.undo()
        assert approved["result"]["status"] == "reconciling"
        assert approved["result"]["reconciliation_kind"] == "push_remote_unknown"
        stuck_context = db_git.session_context(db_git.get_session(merge_id))
        # the push this process "lost" really did land — remote already has the
        # exact commit this session recorded as its own
        assert _origin_head(origin_repo) == stuck_context["merge_commit"]
        assert db_git.get_state(self.GROUP)["status"] == "conflict"
        assert _review_status(ac_id) == "pending_review"
        assert _intent(merge_id)["approval_intent_id"] == intent_id

        events: list = []
        original_emit = svc._emit

        def _watched_emit(event, project_id, group_id, payload):
            events.append((event, payload, _review_status(ac_id), _review_status(root_id)))
            return original_emit(event, project_id, group_id, payload)

        monkeypatch.setattr(svc, "_emit", _watched_emit)
        outcome = svc.reconcile_push_session(merge_id, trigger="server_startup")
        monkeypatch.undo()

        assert outcome["result"]["status"] == "merged"
        assert outcome["result"]["approval"]["approved"] is True
        assert outcome["result"]["approval"]["stage"] == "complete"
        assert db_git.get_state(self.GROUP)["status"] == "merged"
        assert _review_status(ac_id) == "approved"
        assert _review_status(root_id) == "wf_done"
        # the intent was actually consumed, not left behind for a session that no
        # longer has an open slot for a retry to reach
        assert _intent(merge_id) is None
        context = db_git.session_context(db_git.get_session(merge_id))
        assert context["final_approval_intent_consumed"]["ac_doc_id"] == ac_id
        assert db_git.get_session(merge_id)["status"] == "done"
        assert svc.get_finalize_state(self.GROUP)["state"]["final_approval_bound"] is False

        # both broadcasts (the badge AND the completion) went out only once Git
        # AND the approval were both already terminal, same as the direct path.
        done = [e for e in events if e[0] == "git_finalize_done"]
        assert done, [e[0] for e in events]
        assert done[-1][1]["approval"]["approved"] is True
        assert (done[-1][2], done[-1][3]) == ("approved", "wf_done")
        pending = [e for e in events if e[0] == "git_pending_changed"]
        assert pending, [e[0] for e in events]
        assert pending[-1][1]["status"] == "merged"
        assert (pending[-1][2], pending[-1][3]) == ("approved", "wf_done")

    def test_intent_consume_is_conditional(self, origin_repo):
        """§15 — a consume only lands for the exact intent that is still there."""
        from modules.flow_gate.services import git_service as svc
        from modules.flow_gate.services.git import approval_intent

        _seed_final_approval_group(f"{PROJECT}.default.0145")
        _make_conflict_material(
            f"{PROJECT}.default.0145", origin_repo, "shared.py",
            '"consume group version"\n', '"consume mainline version"\n',
        )
        status, payload = _final_approve(f"{PROJECT}.default.0145.0002-AC")
        merge_id = payload["git"]["result"]["merge_id"]
        intent_id = payload["approval"]["approval_intent_id"]

        assert approval_intent.consume_intent(merge_id, "not-the-recorded-id") is False
        assert _intent(merge_id) is not None
        assert approval_intent.consume_intent(merge_id, intent_id) is True
        assert approval_intent.consume_intent(merge_id, intent_id) is False
        assert approval_intent.consume_intent(999999, intent_id) is False
        # Free the base checkout for the next group: one open merge session per
        # project holds it (a pre-existing rule this test is not about).
        svc.abort_merge(f"{PROJECT}.default.0145", merge_id)

    # ── §15: the uncoupled flow ───────────────────────────────────────────────
    def test_manual_conflict_session_carries_no_intent(self, origin_repo):
        """A finalize nobody's approval is riding on keeps its old behaviour."""
        self.GROUP = f"{PROJECT}.default.0146"
        from modules.flow_gate.db import documents as db_docs
        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services import git_service as svc

        root_id, _ac_id = _seed_final_approval_group(self.GROUP)
        db_docs.update(root_id, {"doc_review_status": "wf_done"})
        _make_conflict_material(
            self.GROUP, origin_repo, "shared.py",
            '"manual group version"\n', '"manual mainline version"\n',
        )
        db_git.set_status(self.GROUP, "awaiting_choice")

        out = svc.finalize(self.GROUP, "merge")
        assert out["result"]["status"] == "conflict"
        merge_id = out["result"]["merge_id"]
        assert out["result"]["approval_intent_id"] is None
        assert _intent(merge_id) is None
        assert svc.get_finalize_state(self.GROUP)["state"]["final_approval_bound"] is False

        resolved = _resolve(
            self.GROUP, merge_id, "shared.py",
            '"manual group version"\n"manual mainline version"\n',
        )
        approved = svc.approve_merge_review(
            self.GROUP, merge_id, attempt_id=str(uuid.uuid4()),
            review_fingerprint=resolved["review_fingerprint"], authority="human",
        )
        # unchanged shape: no approval block is invented for an uncoupled session
        assert approved["result"]["status"] == "merged"
        assert "approval" not in approved["result"]
        assert db_git.get_session(merge_id)["status"] == "done"

    # ── §15: atomicity ────────────────────────────────────────────────────────
    def test_session_and_intent_are_written_by_one_transaction(self, origin_repo, monkeypatch):
        """"Session exists, intent does not" must be unconstructible."""
        self.GROUP = f"{PROJECT}.default.0147"
        from modules.flow_gate.db import connection as conn_mod
        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services.git import approval_intent

        _root_id, ac_id = _seed_final_approval_group(self.GROUP)
        _make_conflict_material(
            self.GROUP, origin_repo, "shared.py",
            '"atomic group version"\n', '"atomic mainline version"\n',
        )
        store = conn_mod.get_store()
        original_execute = store._execute

        def _fail_on_file_rows(sql, params=None):
            # The conflict FILE rows are the second write of create_session's single
            # transaction. Breaking them rolls the session row back too — so the
            # failure cannot leave a session whose intent never made it.
            if "git_merge_session_file" in sql:
                raise RuntimeError("injected session write failure")
            return original_execute(sql, params)

        monkeypatch.setattr(store, "_execute", _fail_on_file_rows)
        with pytest.raises(RuntimeError):
            _final_approve(ac_id)
        monkeypatch.undo()

        # No half-built conflict: the session row went down with the file rows, so
        # there is no session for an intent to be missing from.
        assert db_git.get_open_session_by_group(self.GROUP) is None
        assert approval_intent.find_intent_session(self.GROUP) == (None, None)
        assert _review_status(ac_id) == "pending_review"
        assert db_git.get_lock(PROJECT) is None
