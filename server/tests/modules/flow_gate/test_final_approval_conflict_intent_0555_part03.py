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
    """C1–C11 plus the earlier implementation's extra guarantees, in sequence on one group."""

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

    # ── C5 ────────────────────────────────────────────────────────────────────

    # ── C6 ────────────────────────────────────────────────────────────────────

    # ── C7 ────────────────────────────────────────────────────────────────────

    # ── C11 ───────────────────────────────────────────────────────────────────

    # ── C10 ───────────────────────────────────────────────────────────────────

    # ── reconciling success branch ────────────────────────────
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
        # push_remote_unknown, the design
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
        assert approved["result"]["git_terminal"] is True
        assert approved["result"]["approval"] == {
            "approved": False, "stage": "not_required", "error": None, "approval_intent_id": None,
        }
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
        status, payload = _final_approve(ac_id)
        monkeypatch.undo()
        # the approval job records an unknown result (its lease is left to lapse and
        # the Sweeper reconciles it) instead of letting the exception escape the request
        assert status == 500, payload
        assert payload["error"]["code"] == "approval_job_error"

        # No half-built conflict: the session row went down with the file rows, so
        # there is no session for an intent to be missing from.
        assert db_git.get_open_session_by_group(self.GROUP) is None
        assert approval_intent.find_intent_session(self.GROUP) == (None, None)
        assert _review_status(ac_id) == "pending_review"
        assert held_locks(PROJECT) == []
@needs_git
class TestFinalApprovalCleanA11Retry0555:
    """A11-C1..C7 against a real clean merge and durable group-state evidence."""

    GROUP = f"{PROJECT}.default.0150"

    def test_clean_terminal_failure_refresh_and_approval_only_retry(
        self, origin_repo, monkeypatch,
    ):
        from fastapi import HTTPException

        from modules.flow_gate.db import connection as conn_mod
        from modules.flow_gate.db import documents as db_docs
        from modules.flow_gate.db import git_integration as db_git
        from modules.flow_gate.services import git_service as svc
        from modules.flow_gate.services.git import approval_intent
        from modules.flow_gate.storage.paths import src_root
        from modules.flow_gate.workflow import pipeline_service

        # The preceding atomic-session failure test intentionally interrupts a
        # conflict after Git has entered MERGE_HEAD. Restore this module fixture's
        # scratch base checkout before starting the independent clean-A11 case.
        base_root = src_root(PROJECT_NAME, "main")
        subprocess.run(
            ["git", "merge", "--abort"], cwd=base_root,
            capture_output=True, text=True, check=False,
        )
        _git(["reset", "--hard", "HEAD"], cwd=base_root)

        root_id, ac_id = _seed_final_approval_group(self.GROUP)
        assert svc.ensure_worktree(PROJECT, "default", self.GROUP) == "ok"
        branch = self.GROUP.replace(".", "_")
        (src_root(PROJECT_NAME, branch) / "a11_clean.py").write_text(
            'A11 = "clean terminal retry"\n', encoding="utf-8",
        )

        events: list[tuple[str, dict, str, str]] = []
        original_emit = svc._emit

        def _watched_emit(event, project_id, group_id, payload):
            events.append((event, payload, _review_status(ac_id), _review_status(root_id)))
            return original_emit(event, project_id, group_id, payload)

        original_cas = pipeline_service.db_docs.update_review_status_cas

        def _failing_cas(doc_id, expected, nxt):
            if nxt == "wf_done":
                raise RuntimeError("injected clean approval transaction failure")
            return original_cas(doc_id, expected, nxt)

        monkeypatch.setattr(svc, "_emit", _watched_emit)
        monkeypatch.setattr(pipeline_service.db_docs, "update_review_status_cas", _failing_cas)
        status, payload = _final_approve(ac_id)
        monkeypatch.undo()

        # unit 6b: Git is terminal, only the DB step failed -> the approval job stays queued
        # (retry_wait / db_finalize) and keeps retrying that one step; the request answers queued.
        assert status == 200, payload
        assert payload["approval"]["stage"] == "queued" and payload["approval"]["deferred"] is True
        assert payload["block_reason"]["code"] == "approval_commit_failed"
        assert payload["git"]["terminal"] is True
        assert payload["git"]["result"]["status"] == "merged"
        job = job_of(self.GROUP)
        assert job["status"] == "retry_wait" and job["phase"] == "db_finalize"
        approval_intent_id = json.loads(job["payload"])["approval_intent_id"]
        terminal_head = _origin_head(origin_repo)
        commits_after_terminal = int(_git(
            ["rev-list", "--count", "main"], cwd=origin_repo["bare"],
        ).strip())

        state, retry = approval_intent.find_clean_retry(self.GROUP)
        assert state["status"] == "merged"
        assert retry["source"] == "clean_final_approval"
        assert retry["terminal_status"] == "merged"
        assert retry["merge_commit"] == payload["git"]["result"]["merge_commit"]
        assert retry["approval_completed"] is False
        assert retry["intent"]["group_id"] == self.GROUP
        assert retry["intent"]["ac_doc_id"] == ac_id
        assert retry["intent"]["approval_intent_id"] == approval_intent_id
        assert retry["intent"]["git_action"] == "merge"
        assert db_git.get_open_session_by_group(self.GROUP) is None
        assert _review_status(ac_id) == "pending_review"
        assert _review_status(root_id) == "wf_in_progress"
        assert held_locks(PROJECT) == []

        # Both terminal refresh signals are emitted after the failed transaction
        # has rolled back, so subscribers see Git terminal + approval pending.
        pending_events = [e for e in events if e[0] == "git_pending_changed"]
        done_events = [e for e in events if e[0] == "git_finalize_done"]
        assert pending_events and done_events
        assert pending_events[-1][1]["status"] == "merged"
        assert pending_events[-1][2:] == ("pending_review", "wf_in_progress")
        assert done_events[-1][1]["approval"]["approved"] is False
        assert done_events[-1][2:] == ("pending_review", "wf_in_progress")

        # A fresh request context reconstructs every retry field from SQLite.
        conn_mod._request_cache.invalidate()
        fresh_state = svc.get_finalize_state(self.GROUP, preview_ac=True)["state"]
        assert fresh_state["status"] == "merged"
        assert fresh_state["approval_pending"] is True
        assert fresh_state["choices"] == []
        project_status = svc.project_git_status(PROJECT)["status"]
        assert self.GROUP not in {row["group_id"] for row in project_status["pending"]}

        # The approval-only retry route (no git_action) answers with the live job while one is
        # queued; the cases below are the route own rules, so take the job out of the way
        # (a job lost or abandoned by an operator) and keep its freeze claim cleared.
        _abandon_job(self.GROUP)

        # Another real AC in the same group cannot borrow this retry capability.
        other_ac_id = f"{self.GROUP}.0003-AC"
        db_docs.create({
            "doc_id": other_ac_id, "project_id": PROJECT, "module": "default",
            "group_id": self.GROUP, "type_code": "AC", "seq": 3,
            "title": "other final approval", "target_id": root_id,
        })
        db_docs.update(other_ac_id, {
            "doc_review_status": "pending_review", "target_id": root_id,
        })
        mismatch_status, mismatch_payload = _final_approve(other_ac_id, git_action=None)
        assert mismatch_status == 409
        assert mismatch_payload["error"]["code"] == "final_approval_retry_mismatch"
        assert _review_status(other_ac_id) == "pending_review"
        assert _review_status(ac_id) == "pending_review"

        # Tampered group identity is also fail-closed and leaves the valid AC pending.
        tampered = json.loads(json.dumps(retry))
        tampered["intent"]["group_id"] = f"{PROJECT}.default.9999"
        db_git.set_final_approval_retry(self.GROUP, tampered)
        mismatch_status, mismatch_payload = _final_approve(ac_id, git_action=None)
        assert mismatch_status == 409
        assert mismatch_payload["error"]["code"] == "final_approval_retry_mismatch"
        assert _review_status(ac_id) == "pending_review"
        db_git.set_final_approval_retry(self.GROUP, retry)

        # The real UI retry request carries no git_action. Prove the orchestrator
        # never re-enters finalize and no history-producing command runs.
        git_commands: list[list[str]] = []
        original_run_git = svc._run_git

        def _watched_run_git(args, **kwargs):
            git_commands.append(list(args))
            return original_run_git(args, **kwargs)

        monkeypatch.setattr(svc, "_run_git", _watched_run_git)
        status2, payload2 = _final_approve(ac_id, git_action=None)
        monkeypatch.undo()

        assert status2 == 200, payload2
        assert payload2["approval"]["approved"] is True
        assert payload2["git"]["result"]["approval_retry"] is True
        assert payload2["git"]["result"]["retry_source"] == "group_state"
        assert _origin_head(origin_repo) == terminal_head
        assert int(_git(
            ["rev-list", "--count", "main"], cwd=origin_repo["bare"],
        ).strip()) == commits_after_terminal
        history_commands = {
            command[0] for command in git_commands if command
            and command[0] in {"merge", "commit", "push", "stash"}
        }
        assert history_commands == set()
        assert _review_status(ac_id) == "approved"
        assert _review_status(root_id) == "wf_done"
        assert approval_intent.find_clean_retry(self.GROUP)[1] is None
        assert svc.get_finalize_state(self.GROUP)["state"]["approval_pending"] is False

        # A duplicate resend follows the existing already-approved/stale contract;
        # neither the approval transaction nor Git gets another chance to run.
        monkeypatch.setattr(
            pipeline_service, "commit_final_approval",
            lambda **kw: pytest.fail("duplicate retry must not commit approval"),
        )
        with pytest.raises(HTTPException) as duplicate:
            _final_approve(ac_id, git_action=None)
        monkeypatch.undo()
        assert duplicate.value.status_code in {409, 422}
        assert _origin_head(origin_repo) == terminal_head
        assert approval_intent.find_clean_retry(self.GROUP)[1] is None

    @pytest.mark.skip(reason="stash is not a final-approval action any more: APPROVAL_FINALIZE_ACTIONS "
                             "is merge/merge_only/push/commit_push and precheck_approve_git_action answers 422 "
                             "(same in the pre-0669 tree), so there is no stashed terminal to retry")
    def test_stash_terminal_failure_refresh_and_approval_only_retry(
        self, origin_repo, monkeypatch,
    ):
        from modules.flow_gate.api import inbox_routes
        from modules.flow_gate.db import connection as conn_mod
        from modules.flow_gate.services import git_service as svc
        from modules.flow_gate.services.git import approval_intent
        from modules.flow_gate.storage.paths import src_root
        from modules.flow_gate.workflow import pipeline_service

        group_id = f"{PROJECT}.default.0151"
        root_id, ac_id = _seed_final_approval_group(group_id)
        assert svc.ensure_worktree(PROJECT, "default", group_id) == "ok"
        branch = group_id.replace(".", "_")
        worktree = src_root(PROJECT_NAME, branch)
        (worktree / "a11_archive.txt").write_text(
            "preserve this uncommitted archive payload\n", encoding="utf-8",
        )

        original_cas = pipeline_service.db_docs.update_review_status_cas

        def _failing_cas(doc_id, expected, nxt):
            if nxt == "wf_done":
                raise RuntimeError("injected archive approval transaction failure")
            return original_cas(doc_id, expected, nxt)

        monkeypatch.setattr(
            pipeline_service.db_docs, "update_review_status_cas", _failing_cas,
        )
        status, payload = _final_approve(ac_id, git_action="stash")
        monkeypatch.undo()

        assert status == 500, payload
        assert payload["error"]["code"] == "approval_commit_failed"
        assert payload["git"]["terminal"] is True
        assert payload["git"]["result"]["status"] == "stashed"
        archive_before = inbox_routes._git_archive_record(group_id)
        assert archive_before is not None
        assert archive_before["status"] == "archived"
        assert archive_before["changed_file_count"] >= 1

        state, retry = approval_intent.find_clean_retry(group_id)
        assert state["status"] == "none"
        assert bool(state["worktree_registered"]) is True
        assert retry["source"] == "clean_final_approval"
        assert retry["terminal_status"] == "stashed"
        assert retry["terminal_git_action"] == "stash"
        assert retry["intent"]["ac_doc_id"] == ac_id
        assert retry["intent"]["approval_intent_id"] == payload["approval"]["approval_intent_id"]
        assert retry["approval_completed"] is False
        assert _review_status(ac_id) == "pending_review"
        assert _review_status(root_id) == "wf_in_progress"

        # Simulate refresh/restart: all admission facts must be reconstructed from DB.
        conn_mod._request_cache.invalidate()
        fresh = svc.get_finalize_state(group_id, preview_ac=True)["state"]
        assert fresh["status"] == "stashed"
        assert fresh["approval_pending"] is True
        assert fresh["choices"] == []

        # The no-git_action retry may only commit approval. Re-entering either the
        # generic finalize seam or archive implementation is a test failure.
        monkeypatch.setattr(
            svc, "run_approve_git_action",
            lambda *args, **kwargs: pytest.fail("approval-only retry re-entered finalize"),
        )
        monkeypatch.setattr(
            inbox_routes, "_archive_group_git",
            lambda *args, **kwargs: pytest.fail("approval-only retry re-ran archive"),
        )
        git_commands: list[list[str]] = []
        original_run_git = svc._run_git

        def _watched_run_git(args, **kwargs):
            git_commands.append(list(args))
            return original_run_git(args, **kwargs)

        monkeypatch.setattr(svc, "_run_git", _watched_run_git)
        status2, payload2 = _final_approve(ac_id, git_action=None)
        monkeypatch.undo()

        assert status2 == 200, payload2
        assert payload2["approval"]["approved"] is True
        assert payload2["git"]["result"]["approval_retry"] is True
        assert payload2["git"]["result"]["retry_source"] == "group_state"
        assert payload2["git"]["result"]["status"] == "stashed"
        assert not [cmd for cmd in git_commands if cmd and cmd[0] == "stash"]
        assert inbox_routes._git_archive_record(group_id) == archive_before
        assert _review_status(ac_id) == "approved"
        assert _review_status(root_id) == "wf_done"
        assert approval_intent.find_clean_retry(group_id)[1] is None
        final_state = svc.get_finalize_state(group_id)["state"]
        assert final_state.get("approval_pending", False) is False
        assert final_state["status"] == "none"
        stored_final, _ = approval_intent.find_clean_retry(group_id)
        assert not bool(stored_final["worktree_registered"])
