"""flowgate.v02test.0068: what the AI resolver of a 받아오기 (group update) conflict is told and
what the server accepts from it.

Real Git + SQLite jobs, the shape of F80's conflict: the Flow rewrote a function the base added
log lines to (one conflict chunk) while the base ALSO changed a far part of the same file, which
git merged without a conflict.

- The mention names the sides (ours = this Flow, theirs = the base), gives both commits to read
  and says the accepted submission is the commit - there is no reviewer.
- A correct one-side resolution refused for the missing declaration is told the declaration would pass.
- The Flow's old whole file - which undoes the base's cleanly merged change - is refused even
  with a declaration.
- A resolution whose scope-floor record fails is undone, and the waiting run says why.
"""
import pytest

from manual_merge_0066_support import rev, needs_git
from test_fg_gate_intake_0085 import product, env, db_path, FA  # noqa: F401
from test_fg_gate_publish_0085 import git_env, PROJECT  # noqa: F401
from test_execution_update_conflict_0068 import (  # noqa: F401
    proj, drive, tick, waiting_on_rewrite, NAMING_BASE, NAMING_THEIRS, NAMING_OURS, KEEP_OURS,
)
from modules.flow_gate.services import git_service as gs
from modules.flow_gate.services.git import conflict, execution as ex, scope_base

FILLER = "".join(f"# stays {n}\n" for n in range(12))
TAIL_BASE = "def tail(flow):\n    return load(flow)\n"
TAIL_THEIRS = "def tail(flow):\n    audit_tail_read(flow)\n    return load(flow)\n"
AUTO_LINE = "    audit_tail_read(flow)"


@pytest.fixture
def parked(proj, monkeypatch):
    """A run parked on the update conflict: one chunk (the rewritten function) plus a base
    change further down that merged cleanly."""
    monkeypatch.setattr(
        "test_execution_update_conflict_0068.NAMING_BASE", NAMING_BASE + FILLER + TAIL_BASE)
    monkeypatch.setattr(
        "test_execution_update_conflict_0068.NAMING_THEIRS", NAMING_THEIRS + FILLER + TAIL_THEIRS)
    ours_old = NAMING_OURS + FILLER + TAIL_BASE          # the Flow's own copy, before the update
    gid, job_id, view = waiting_on_rewrite(proj, ours=ours_old)
    conflicted = (proj.wt(gid) / "naming.py").read_text(encoding="utf-8")
    assert conflicted.count("<<<<<<< ") == 1 and AUTO_LINE in conflicted
    merged = NAMING_OURS + FILLER + TAIL_THEIRS          # the Flow's function + the base's tail
    return gid, job_id, view["mergeId"], ours_old, merged


@needs_git
def test_update_conflict_names_both_commits_and_the_mention_names_the_sides(proj, parked):
    from modules.flow_gate.api import token_routes
    gid, _job_id, merge_id, _ours_old, _merged = parked
    session = gs.db_git.get_session(merge_id)
    refs = conflict.list_conflicts(gid, merge_id)["refs"]
    assert refs == {"ours": rev(proj.wt(gid), "HEAD"),
                    "theirs": gs.db_git.session_context(session)["source_sha"]}
    mention = token_routes._build_conflict_mention(
        group_id=gid, project_id=PROJECT, merge_id=merge_id,
        scratch_dir="/scratch/x", raw_token="tok_raw", api_base_url="http://fake-host/api/v1",
    )
    assert "THIS FLOW's" in mention and "is the BASE branch" in mention
    assert "There is NO human review step here" in mention
    assert "resolved_pending_review" not in mention and "a person reads" not in mention
    assert f"\"ref\": \"{refs['ours']}\" (ours, this Flow's branch)" in mention
    assert f"\"ref\": \"{refs['theirs']}\" (theirs, the base branch)" in mention


@needs_git
def test_correct_one_side_answer_is_told_the_declaration_would_pass_then_passes(proj, parked):
    gid, job_id, merge_id, _ours_old, merged = parked
    with pytest.raises(conflict.GitServiceError) as refused:
        conflict.resolve_conflicts(gid, merge_id, [{"path": "naming.py", "content": merged}], True,
                                   resolver_run_id="run-ai-1")
    assert refused.value.code == "conflict_side_dropped"
    assert refused.value.details["files"][0]["supersede_ready"] == {"side": "ours"}
    assert "resend the SAME content" in refused.value.message
    resolved = conflict.resolve_conflicts(
        gid, merge_id, [{"path": "naming.py", "content": merged, "supersede": KEEP_OURS}], True,
        resolver_run_id="run-ai-1")
    assert resolved["result"]["status"] == "updated"
    done = drive(gid, job_id, lambda v: v["status"] != "running")
    assert done["status"] == "complete", done
    assert proj.show("main", "naming.py") == merged


@needs_git
def test_old_whole_file_that_undoes_a_cleanly_merged_base_line_is_refused(proj, parked):
    gid, job_id, merge_id, ours_old, _merged = parked
    # Bare: the old file matches no chunk (the text after the chunk differs too), so it used to
    # pass as a hand-merged file. An AI run is refused with the line it would undo.
    with pytest.raises(conflict.GitServiceError) as bare:
        conflict.resolve_conflicts(gid, merge_id, [{"path": "naming.py", "content": ours_old}], True,
                                   resolver_run_id="run-ai-2")
    assert bare.value.code == "conflict_supersede_invalid"
    assert "replace only the marker blocks" in bare.value.message
    problem = bare.value.details["files"][0]
    assert problem["condition"] == "auto_merged_lines_removed"
    assert problem["removed_lines"] == [AUTO_LINE]
    # Declared: a declaration does not excuse it either.
    with pytest.raises(conflict.GitServiceError) as declared:
        conflict.resolve_conflicts(
            gid, merge_id, [{"path": "naming.py", "content": ours_old, "supersede": KEEP_OURS}], True,
            resolver_run_id="run-ai-2")
    assert declared.value.code == "conflict_supersede_invalid"
    lost = [row for row in declared.value.details["files"] if row["condition"] == "auto_merged_lines_removed"]
    assert len(lost) == 1 and lost[0]["removed_lines"] == [AUTO_LINE]
    attempt = conflict.list_conflicts(gid, merge_id)["resolver_attempt"]
    assert attempt["code"] == "conflict_supersede_invalid" and attempt["lines"] == [AUTO_LINE.strip()]
    assert ex.view(tick(job_id))["mergeId"] == merge_id      # still waiting, nothing committed


@needs_git
def test_resolution_whose_record_fails_is_undone_and_the_run_says_why(proj, parked, monkeypatch):
    gid, job_id, merge_id, _ours_old, merged = parked
    before = rev(proj.wt(gid), "HEAD")

    def refuse(*_args, **_kwargs):
        raise conflict.GitServiceError(
            409, scope_base.ERR_RECORD_FAILED, "the merged update source cannot become the scope floor",
            details={"reason": "floor_not_ancestor"})
    monkeypatch.setattr(scope_base, "record_update", refuse)
    with pytest.raises(conflict.GitServiceError) as failed:
        conflict.resolve_conflicts(
            gid, merge_id, [{"path": "naming.py", "content": merged, "supersede": KEEP_OURS}], True,
            resolver_run_id="run-ai-3")
    assert failed.value.code == scope_base.ERR_RECORD_FAILED
    session = gs.db_git.get_session(merge_id)
    assert session["status"] == "aborted"
    kept = conflict.update_record_failure(session)
    assert (kept["code"], kept["reason"]) == (scope_base.ERR_RECORD_FAILED, "floor_not_ancestor")
    assert rev(proj.wt(gid), "HEAD") == before             # the merge commit was undone
    stopped = drive(gid, job_id, lambda v: v["status"] != "running")
    assert stopped["status"] == "stopped" and stopped["error"] == scope_base.ERR_RECORD_FAILED, stopped
