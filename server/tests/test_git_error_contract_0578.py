import asyncio
import os
import sys
from pathlib import Path

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
os.environ.setdefault("ALLOWED_ORIGIN", "http://localhost:5173")
os.environ.setdefault("CONTEXT", "/flowgate")
os.environ.setdefault("DB_TYPE", "sqlite")
_SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SERVER_DIR))

from modules.flow_gate.services.git.credentials import GitServiceError, git_error_envelope


def test_constructor_compatibility_and_round_trip():
    details = {"files": ["a.txt"]}
    params = {"n": 1, "flag": True, "labels": ["a"], "bad": {"nested": True}}
    exc = GitServiceError(409, "dirty_worktree", "developer text", details, params=params)
    details["files"].append("late.txt")
    params["n"] = 9

    assert exc.status == 409
    assert exc.code == "dirty_worktree"
    assert exc.details == {"files": ["a.txt"]}
    assert exc.params == {"n": 1, "flag": True, "labels": ["a"]}
    assert git_error_envelope(exc) == {
        "ok": False,
        "error": {
            "code": "dirty_worktree",
            "message": "developer text",
            "params": {"n": 1, "flag": True, "labels": ["a"]},
            "details": {"files": ["a.txt"]},
        },
    }


def test_diagnostic_is_never_serialized():
    sentinel = "SECRET STDERR SENTINEL"
    exc = GitServiceError(
        500, "git_error", "Git command failed",
        params={"bad": object()}, diagnostic=sentinel,
    )
    envelope = git_error_envelope(exc)
    assert exc.diagnostic == sentinel
    assert "diagnostic" not in envelope["error"]
    assert sentinel not in repr(envelope)
    assert "params" not in envelope["error"]


def test_legacy_positional_details_still_supported():
    exc = GitServiceError(409, "base_dirty", "developer text", {"files": ["x"]})
    assert exc.details["files"] == ["x"]


def test_global_handler_logs_diagnostic_without_raising(caplog):
    # routers.main's logger is `LogAssist.log`, not stdlib `logging` — its
    # warning() only accepts (tag, msg), never variadic %s substitutions like
    # git_service._log (a real logging.Logger). Calling the real handler
    # (rather than a hand-rolled replica) pins that the diagnostic actually
    # reaches the log instead of being swallowed by handleError/TypeError.
    from routers.main import git_service_exception_handler

    sentinel = "GIT DIAGNOSTIC SENTINEL 0578"
    exc = GitServiceError(500, "git_error", "developer text", diagnostic=sentinel)

    with caplog.at_level("WARNING", logger="LogAssist.log"):
        response = asyncio.run(git_service_exception_handler(None, exc))

    assert response.status_code == 500
    assert any(sentinel in record.getMessage() for record in caplog.records)
