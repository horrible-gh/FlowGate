"""v02gui.0015 T4 (0006-NR §6-4): the integration gate's syntax check of an attempt's result commit.

Done criterion: a damaged result (a file that no longer parses) is never merged into the group branch, whichever
approval it had (a person's or the automatic one: both reach ``result_merge.integrate`` through ``defer_done``).
The check itself (``result_verify.check``) runs on real Git commits; ``integrate`` runs on the 0061 T#9 SQLite
fixture with the merge replaced by a probe (the gate must decide before any merge starts).
"""
from pathlib import Path
from unittest.mock import Mock
import shutil
import subprocess

import pytest

from tests.regression.graph_workflow.test_graph_attempt_runs_0061 import db  # noqa: F401 — shared SQLite execution fixture
from tests.regression.graph_attempt.test_graph_attempt_results_0061 import result_db  # noqa: F401 — T#8 result fixture
from tests.regression.graph_attempt.test_graph_attempt_integration_0061 import merge_db, status  # noqa: F401
from flowgate.graph_run import result_merge, result_verify, workspace
from modules.flow_gate.services import remote_write_guard


def git(root, *args):
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True,
                          text=True, encoding="utf-8").stdout.strip()


class Repo:
    def __init__(self, root: Path):
        self.root = root

    def commit(self, files: dict, message: str = "change") -> str:
        for path, content in files.items():
            target = self.root / path
            if content is None:
                target.unlink()
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))
        git(self.root, "add", "-A")
        git(self.root, "commit", "-q", "--allow-empty", "-m", message)
        return git(self.root, "rev-parse", "HEAD")


@pytest.fixture
def repo(tmp_path):
    if not shutil.which("git"):
        pytest.skip("Git not installed in TS environment")
    root = tmp_path / "group"
    root.mkdir()
    git(root, "init", "-q", "-b", "flow")
    git(root, "config", "user.name", "Verify Test")
    git(root, "config", "user.email", "verify@example.invalid")
    git(root, "config", "core.autocrlf", "false")
    return Repo(root)


BASE_FILES = {
    "server/app.py": "token: dict = {}\n\ndef f(x):\n    return x\n",
    "server/settings.json": '{"a": 1}\n',
    "server/template.py": "def {{ name }}():\n    pass\n",       # never parsed: a template
    "docs/readme.md": "# readme\n",
}


def masked_python():
    return "token: ***\n\ndef f(x):\n    return x\n"


# ── result_verify.check ───────────────────────────────────────────────────────

def test_masked_python_written_back_fails_with_location_and_no_source_text(repo):
    base = repo.commit(BASE_FILES, "base")
    result = repo.commit({"server/app.py": masked_python()})
    verdict = result_verify.check(repo.root, base, result)
    assert verdict["status"] == result_verify.FAILED and verdict["failure_count"] == 1
    failure = verdict["failures"][0]
    assert (failure["path"], failure["checker"], failure["line"]) == ("server/app.py", "python_compile", 1)
    assert "***" not in str(verdict) and "token" not in str(verdict)     # never the source line itself


def test_valid_changes_pass(repo):
    base = repo.commit(BASE_FILES, "base")
    result = repo.commit({"server/app.py": "token: dict = {'k': 1}\n", "server/settings.json": '{"a": 2}\n',
                          "server/new_mod.py": "VALUE = 1\n"})
    verdict = result_verify.check(repo.root, base, result)
    assert verdict["status"] == result_verify.PASSED and verdict["checked"] == 3 and not verdict["failures"]


def test_broken_json_that_parsed_before_fails(repo):
    base = repo.commit(BASE_FILES, "base")
    result = repo.commit({"server/settings.json": '{"a": ***}\n'})
    verdict = result_verify.check(repo.root, base, result)
    assert verdict["status"] == result_verify.FAILED
    assert verdict["failures"][0]["path"] == "server/settings.json"
    assert verdict["failures"][0]["checker"] == "json_parse"


def test_a_file_that_did_not_parse_before_is_not_the_attempts_fault(repo):
    base = repo.commit(BASE_FILES, "base")
    result = repo.commit({"server/template.py": "def {{ name }}():\n    return 1\n"})
    verdict = result_verify.check(repo.root, base, result)
    assert verdict["status"] == result_verify.PASSED and verdict["preexisting"] == 1


def test_new_python_file_that_does_not_compile_fails_but_new_json_only_warns(repo):
    base = repo.commit(BASE_FILES, "base")
    result = repo.commit({"server/new_mod.py": "def broken(:\n", "client/tsconfig.json": "// comment\n{}\n"})
    verdict = result_verify.check(repo.root, base, result)
    assert verdict["status"] == result_verify.FAILED
    assert [f["path"] for f in verdict["failures"]] == ["server/new_mod.py"]
    assert [w["path"] for w in verdict["warnings"]] == ["client/tsconfig.json"]
    only_json = repo.commit({"server/new_mod.py": None})
    assert result_verify.check(repo.root, result, only_json)["status"] == result_verify.PASSED


def test_unchecked_kinds_and_deleted_files_are_not_judged(repo):
    base = repo.commit(BASE_FILES, "base")
    result = repo.commit({"docs/readme.md": "***\n", "server/data.txt": "token: ***\n", "server/app.py": None})
    verdict = result_verify.check(repo.root, base, result)
    assert verdict["status"] == result_verify.PASSED and verdict["checked"] == 0


def test_renamed_file_is_checked_as_an_addition(repo):
    base = repo.commit(BASE_FILES, "base")
    git(repo.root, "mv", "server/app.py", "server/moved.py")
    result = repo.commit({"server/moved.py": masked_python()})
    verdict = result_verify.check(repo.root, base, result)
    assert [f["path"] for f in verdict["failures"]] == ["server/moved.py"]


def test_paths_with_spaces_and_non_ascii_names(repo):
    base = repo.commit({**BASE_FILES, "server/my mod.py": "A = 1\n", "server/한글.py": "B = 1\n"}, "base")
    result = repo.commit({"server/my mod.py": "A = ***\n", "server/한글.py": "B = 2\n"})
    verdict = result_verify.check(repo.root, base, result)
    assert [f["path"] for f in verdict["failures"]] == ["server/my mod.py"] and verdict["checked"] == 2


def test_crlf_and_bom_python_sources_pass(repo):
    base = repo.commit(BASE_FILES, "base")
    result = repo.commit({"server/crlf.py": b"A = 1\r\nB = 2\r\n", "server/bom.py": b"\xef\xbb\xbfC = 3\n"})
    assert result_verify.check(repo.root, base, result)["status"] == result_verify.PASSED


def test_files_above_the_check_limit_are_skipped(repo, monkeypatch):
    base = repo.commit(BASE_FILES, "base")
    result = repo.commit({"server/app.py": masked_python()})
    monkeypatch.setattr(remote_write_guard, "CHECK_LIMIT_BYTES", 4)
    verdict = result_verify.check(repo.root, base, result)
    assert verdict["status"] == result_verify.PASSED and verdict["skipped"] == 1


def test_failures_reported_are_clipped(repo, monkeypatch):
    base = repo.commit(BASE_FILES, "base")
    result = repo.commit({f"server/m{i}.py": "x = ***\n" for i in range(5)})
    monkeypatch.setattr(result_verify, "MAX_REPORTED", 2)
    verdict = result_verify.check(repo.root, base, result)
    assert verdict["failure_count"] == 5 and len(verdict["failures"]) == 2


def test_unreadable_commits_are_unavailable_never_failed(repo, tmp_path):
    base = repo.commit(BASE_FILES, "base")
    result = repo.commit({"server/app.py": masked_python()})
    assert result_verify.check(repo.root, base, "f" * 40)["status"] == result_verify.UNAVAILABLE
    assert result_verify.check(None, base, result)["status"] == result_verify.UNAVAILABLE
    assert result_verify.check(repo.root, None, result)["status"] == result_verify.UNAVAILABLE
    assert result_verify.check(tmp_path / "missing", base, result)["status"] == result_verify.UNAVAILABLE
    assert result_verify.check(repo.root, base, base)["status"] == result_verify.PASSED


# ── result_merge.integrate: the gate before any merge ────────────────────────

def armed(merge_db, monkeypatch, repo, result_files):
    """A pending attempt whose input is the group's base and whose result commit is ``result_files`` on top."""
    base = repo.commit(BASE_FILES, "base")
    result = repo.commit(result_files, "result")
    git(repo.root, "reset", "-q", "--hard", base)               # the group branch stays at the base
    merge_db._execute("UPDATE graph_node_attempts SET input_commit=?, integration_status='pending', "
                      "result_commit=? WHERE attempt_id=?", [base, result, merge_db.attempt])
    monkeypatch.setattr(result_merge.result_commit, "commit_result", Mock(return_value={"result_commit": result}))
    monkeypatch.setattr(result_merge, "_group_root", lambda row: ("project", repo.root, "flow"))
    merge = Mock(side_effect=AssertionError("a damaged result is never merged"))
    begin = Mock(side_effect=AssertionError("no integration starts for a damaged result"))
    monkeypatch.setattr(result_merge, "_merge", merge)
    monkeypatch.setattr(result_merge, "_begin", begin)
    return base, result, merge, begin


def test_damaged_result_is_failed_and_never_merged(merge_db, monkeypatch, repo):
    base, _result, merge, begin = armed(merge_db, monkeypatch, repo, {"server/app.py": masked_python()})
    assert result_merge.integrate(merge_db.attempt) == "failed"
    assert status(merge_db) == ("failed", "verification_failed")
    merge.assert_not_called()
    begin.assert_not_called()
    assert git(repo.root, "rev-parse", "HEAD") == base               # the group branch did not move
    _row, recorded, reason = result_merge._note.call_args.args
    assert (recorded, reason) == ("failed", "verification_failed")
    verification = result_merge._note.call_args.kwargs["verification"]
    assert [f["path"] for f in verification["failures"]] == ["server/app.py"]
    result_merge._after.assert_called_once_with(merge_db.attempt)


def test_requeued_damaged_result_fails_again_without_a_merge(merge_db, monkeypatch, repo):
    """The next command requeues a failed integration (``_requeue``); the result commit stays, so it fails again —
    only a new attempt (a Stop, then a run) carries a new result."""
    _base, _result, merge, _begin = armed(merge_db, monkeypatch, repo, {"server/app.py": masked_python()})
    assert result_merge.integrate(merge_db.attempt) == "failed"
    assert result_merge._requeue(merge_db.attempt) and status(merge_db) == ("pending", "retry")
    assert result_merge.integrate(merge_db.attempt) == "failed"
    assert status(merge_db) == ("failed", "verification_failed")
    merge.assert_not_called()


def test_sound_result_goes_on_to_the_merge(merge_db, monkeypatch, repo):
    armed(merge_db, monkeypatch, repo, {"server/app.py": "token: dict = {'k': 1}\n"})
    monkeypatch.setattr(result_merge, "_begin", Mock(return_value=True))
    monkeypatch.setattr(result_merge, "_merge", Mock(return_value=("pending", "git_busy")))
    assert result_merge.integrate(merge_db.attempt) == "pending"
    assert status(merge_db)[1] != "verification_failed"
    result_merge._merge.assert_called_once()


def test_preexisting_breakage_does_not_hold_the_merge(merge_db, monkeypatch, repo):
    armed(merge_db, monkeypatch, repo, {"server/template.py": "def {{ other }}():\n    pass\n"})
    monkeypatch.setattr(result_merge, "_begin", Mock(return_value=True))
    monkeypatch.setattr(result_merge, "_merge", Mock(return_value=("pending", "git_busy")))
    assert result_merge.integrate(merge_db.attempt) == "pending"
    result_merge._merge.assert_called_once()


def test_unreadable_result_does_not_block(merge_db, monkeypatch, repo):
    armed(merge_db, monkeypatch, repo, {"server/app.py": masked_python()})
    monkeypatch.setattr(result_merge, "_group_root", lambda row: (None, None, None))
    monkeypatch.setattr(result_merge, "_begin", Mock(return_value=True))
    monkeypatch.setattr(result_merge, "_merge", Mock(return_value=("failed", "no_group_worktree")))
    assert result_merge.integrate(merge_db.attempt) == "failed"
    assert status(merge_db)[1] != "verification_failed"
    result_merge._merge.assert_called_once()


def test_attempt_that_moved_meanwhile_is_skipped(merge_db, monkeypatch, repo):
    armed(merge_db, monkeypatch, repo, {"server/app.py": masked_python()})
    real = result_verify.check

    def check_then_stop(*args):
        verdict = real(*args)
        merge_db._execute("UPDATE graph_node_attempts SET status='invalidated' WHERE attempt_id=?",
                          [merge_db.attempt])                     # a Stop invalidated it meanwhile
        return verdict
    monkeypatch.setattr(result_verify, "check", check_then_stop)
    assert result_merge.integrate(merge_db.attempt) == "skipped"
    result_merge._note.assert_not_called()


def test_unchanged_result_is_not_checked(merge_db, monkeypatch, repo):
    base = repo.commit(BASE_FILES, "base")
    merge_db._execute("UPDATE graph_node_attempts SET input_commit=?, integration_status='pending', "
                      "result_commit=? WHERE attempt_id=?", [base, base, merge_db.attempt])
    monkeypatch.setattr(result_merge.result_commit, "commit_result", Mock(return_value={"result_commit": base}))
    monkeypatch.setattr(result_merge, "_group_root", lambda row: ("project", repo.root, "flow"))
    monkeypatch.setattr(result_verify, "check", Mock(side_effect=AssertionError("nothing to check")))
    finish = Mock(return_value="integrated")
    monkeypatch.setattr(result_merge, "_finish", finish)
    assert result_merge.integrate(merge_db.attempt) == "integrated"
    finish.assert_called_once_with(merge_db.attempt, base, ("pending",))


def test_failed_verification_holds_the_run_like_any_failed_integration(merge_db, monkeypatch, repo):
    """``failed`` is HELD: the node stays in review, nothing below opens, the run goes idle (``merge_conflict``) once
    nothing else works; a Stop invalidates the attempt (0061 T#7) and the next run opens a new one."""
    armed(merge_db, monkeypatch, repo, {"server/app.py": masked_python()})
    assert result_merge.integrate(merge_db.attempt) == "failed"
    assert result_merge.held("run") and not result_merge.in_progress("run")
    assert result_merge.integration_waiting("run", "node")


def test_a_check_that_raises_does_not_stop_the_integration(merge_db, monkeypatch, repo):
    armed(merge_db, monkeypatch, repo, {"server/app.py": masked_python()})
    monkeypatch.setattr(result_merge, "_group_root", Mock(side_effect=RuntimeError("no project row")))
    monkeypatch.setattr(result_merge, "_begin", Mock(return_value=True))
    monkeypatch.setattr(result_merge, "_merge", Mock(return_value=("pending", "git_busy")))
    assert result_merge.integrate(merge_db.attempt) == "pending"
    result_merge._merge.assert_called_once()
