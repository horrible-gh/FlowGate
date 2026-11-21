"""Independently selectable scenarios; shared fixtures remain in test_manual_merge_0066.py."""
from __future__ import annotations

from tests.regression.manual_merge.test_manual_merge_0066 import (
    GitServiceError,
    SECRET,
    SECRET_KO,
    _Crash,
    _ctx,
    _dirty,
    _git,
    _hidden_conflict,
    _job_of,
    _local_unrecorded_merge,
    _mm,
    _recovery_row,
    _session,
    _svc,
    branch,
    drive_jobs,
    files_at,
    git_quoted,
    group,
    held_locks,
    manual,
    merge_sessions,
    needs_git,
    no_hidden,
    patch_store,
    proj,
    pytest,
    refused,
    reject_push,
    results,
    rev,
    spy_git,
    spy_locks,
    state,
    storage_dir,
    try_rev,
    uuid,
    work,
)

@needs_git
@pytest.mark.parametrize("secret", [SECRET, SECRET_KO], ids=["ascii", "non_ascii"])
@pytest.mark.parametrize("managed", [False, True])
def test_conflict_answer_and_stored_result_never_name_a_hidden_path(proj, monkeypatch, managed, secret):
    """Rework P1 (D#1 §3-1(3)): a committed hidden file in conflict is counted, never named,
    in the E9 answer and the job result; the conflict session keeps finalize's own line.
    Rework 2: a non-ASCII hidden name arrives Git-quoted and is judged by its real path."""
    from modules.flow_gate.db import git_integration as db_git
    from modules.flow_gate.services import tr2_file_policy
    if managed:
        monkeypatch.setattr(tr2_file_policy, "managed_paths", lambda _: {secret})
    gid = group(proj, 1)
    _hidden_conflict(proj, gid, secret)
    out = manual(gid, push=True)
    assert out["status"] == "conflict" and out["group_status"] == "conflict"
    assert out["conflict_files"] == ["shared.txt"] and out["hidden_conflict_count"] == 1
    no_hidden(out, secret)
    no_hidden(_job_of(out["job_id"]), secret)
    files = sorted(row["path"] for row in db_git.session_files(out["merge_id"]))
    # The session keeps the line finalize recorded (quoted for the non-ASCII name).
    assert files == sorted(["shared.txt", git_quoted(secret)])
    assert (git_quoted(secret) != secret) is (secret == SECRET_KO)
    assert state(gid)["status"] == "conflict"
@needs_git
@pytest.mark.parametrize("secret", [SECRET, SECRET_KO], ids=["ascii", "non_ascii"])
@pytest.mark.parametrize("managed", [False, True])
def test_resumed_conflict_answer_never_names_a_hidden_path(proj, monkeypatch, managed, secret):
    """The resume path (_resume_merge) answers through the same filter, from the session's
    (Git-quoted for a non-ASCII name) lines."""
    from test_git_merge_target_0594 import _recover_job_after_crash
    from modules.flow_gate.services import tr2_file_policy
    from modules.flow_gate.services.git import finalize_publish
    if managed:
        monkeypatch.setattr(tr2_file_policy, "managed_paths", lambda _: {secret})
    gid = group(proj, 1)
    _hidden_conflict(proj, gid, secret)
    with pytest.MonkeyPatch.context() as patch:
        def crash(ctx, result):
            raise _Crash()
        patch.setattr(finalize_publish, "_finish", crash)
        with pytest.raises(_Crash):
            manual(gid, push=True)
    assert state(gid)["status"] == "conflict"
    job = _recover_job_after_crash(gid)
    assert job["status"] == "succeeded", job
    no_hidden(job, secret)
    import json as _json
    result = job["result"] if isinstance(job["result"], dict) else _json.loads(job["result"])
    answer = result["result"]
    assert answer["resumed"] is True and answer["status"] == "conflict"
    assert answer["conflict_files"] == ["shared.txt"] and answer["hidden_conflict_count"] == 1
@pytest.mark.parametrize("line, real", [
    ("shared.txt", "shared.txt"),
    ("server/.env.local", "server/.env.local"),
    (git_quoted(SECRET_KO), SECRET_KO),
    (git_quoted("docs/\ud55c.txt"), "docs/\ud55c.txt"),
    ('"a\\tb\\"c\\\\d"', 'a\tb"c\\d'),
    ('"bad\\x"', None),
    ('"a"b"', None),
    ('"trail\\"', None),
])
def test_git_unquote_reads_finalize_conflict_lines(line, real):
    """Rework 2: Git's C quoting (octal UTF-8 bytes, C escapes) is undone exactly; a quoted
    line that is not valid Git quoting is None and counts as hidden (fail closed)."""
    mm = _mm()
    assert mm._git_unquote(line) == real
    if real is None:
        assert mm._hidden_conflict_path(line) is True
