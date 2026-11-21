"""Independently selectable scenarios; shared fixtures remain in test_final_approval_conflict_intent_0555.py."""
from __future__ import annotations

from tests.modules.flow_gate.test_final_approval_conflict_intent_0555 import (
    PROJECT,
    PROJECT_NAME,
    USER,
    _abandon_job,
    _final_approve,
    _git,
    _intent,
    _make_conflict_material,
    _origin_head,
    _resolve,
    _review_status,
    _seed_final_approval_group,
    asyncio,
    held_locks,
    job_of,
    json,
    live_instance,
    needs_git,
    origin_repo,
    patch_store,
    project,
    pytest,
    subprocess,
    threading,
    tmp_db,
    uuid,
)

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
            seen["lock_at_create"] = sorted((r["domain"], r["holder_kind"]) for r in held_locks(PROJECT))
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


    # ── C2 ────────────────────────────────────────────────────────────────────

    # ── C3 ────────────────────────────────────────────────────────────────────

    # ── C8 (before the session closes: the record is what survives) ───────────

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
            observed["lock_at_commit"] = sorted((r["domain"], r["holder_kind"]) for r in held_locks(PROJECT))
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

        # the approval ran inside the merge review own domain locks (B of the target + R) ...
        assert observed["lock_at_commit"] == [("B", "review_action"), ("R", "publish")]
        assert observed["calls"] == 1
        # … and the lock is gone once the call returns
        assert held_locks(PROJECT) == []

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
        assert held_locks(PROJECT) == []

        # §9 recovery: approve again. No git re-run, no new merge commit — the
        # surviving intent is what the transaction consumes this time.
        merges_before = _git(["rev-list", "--count", "main"], cwd=origin_repo["bare"]).strip()
        status2, payload2 = _final_approve(ac_id, git_action=None)
        assert status2 == 200, payload2
        assert payload2["approval"]["approved"] is True
        assert payload2["git"]["result"].get("approval_retry") is True
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
        assert held_locks(PROJECT) == []

    # ── reconciling success branch (T0008 §5/§7/§9) ────────────────────────────


    # ── §15: the uncoupled flow ───────────────────────────────────────────────

    # ── §15: atomicity ────────────────────────────────────────────────────────
