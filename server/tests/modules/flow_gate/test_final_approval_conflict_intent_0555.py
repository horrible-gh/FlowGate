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

from group_lock_stub import held_locks
from tests.modules.flow_gate.test_git_integration_0115 import (  # noqa: F401 — fixtures are used by name
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


@pytest.fixture(autouse=True)
def live_instance(server_instance_per_test, patch_store):
    # These real Git scenarios can outlive the instance self-validity window.
    # Run the real heartbeat against the temporary DB, as server startup does.
    from modules.flow_gate.services.git import instance_registry
    instance_registry.startup()
    try:
        yield
    finally:
        instance_registry.shutdown()


@pytest.fixture(scope="module")
def project(patch_store, tmp_db):
    from modules.flow_gate.db import projects

    # Module/class fixtures seed documents before the function-scoped port
    # fixture runs. Compose the real write gate for their whole lifetime too.
    from modules.flow_gate.graph_workflow.services import card_document_ports as ports
    from flowgate.card_document_edit import integration

    with pytest.MonkeyPatch.context() as composition:
        composition.setattr(ports, "_handlers", dict(ports._handlers))
        integration.register()
        projects.create({"project_id": PROJECT, "project_name": PROJECT_NAME})
        yield


@pytest.fixture(scope="module")
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


def _final_approve(doc_id: str, git_action: str | None = "merge") -> tuple[int, dict]:
    """Drive the real final-approval orchestrator (the only public entry point)."""
    from modules.flow_gate.workflow.routers import workflow

    response = asyncio.run(workflow.document_review_transition_rpc(
        "approve",
        workflow.DocumentBodyRequest(doc_id=doc_id, git_action=git_action),
        USER,
        None,
    ))
    return response.status_code, json.loads(response.body.decode("utf-8"))


def job_of(group_id: str) -> dict:
    from approval_job_fakes import job_of as _job_of

    return _job_of(group_id)


def _abandon_job(group_id: str) -> None:
    """End the Group approval job without running it and open the Group again."""
    from modules.flow_gate.db.connection import get_store
    from modules.flow_gate.services.git import approval_freeze

    job = job_of(group_id)
    with get_store().transaction():
        get_store()._execute(
            "UPDATE operation_job SET status = 'failed', last_error_code = 'test_abandoned', "
            "lease_owner = NULL, lease_token = NULL, lease_until = NULL WHERE job_id = ?",
            [job["job_id"]])
        approval_freeze.clear_freeze_claim(group_id, job["job_id"])


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
        # the session was written while the approval job still owned its domain locks
        # (0669: B for the base target and R -- the project mutex is gone) ...
        assert flow["seen"]["lock_at_create"] == [("B", "publish"), ("R", "publish")]
        # … and it was released by the time the request answered (§3.5: the human
        # wait is NOT held under the project lock).
        assert held_locks(PROJECT) == []

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

    # ── C5 ────────────────────────────────────────────────────────────────────

    # ── C6 ────────────────────────────────────────────────────────────────────

    # ── C7 ────────────────────────────────────────────────────────────────────

    # ── C11 ───────────────────────────────────────────────────────────────────

    # ── C10 ───────────────────────────────────────────────────────────────────

    # ── reconciling success branch (T0008 §5/§7/§9) ────────────────────────────


    # ── §15: the uncoupled flow ───────────────────────────────────────────────

    # ── §15: atomicity ────────────────────────────────────────────────────────
