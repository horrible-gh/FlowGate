"""flowgate.v02test.0023 -- a chat that changes source leaves a commit (0023-CH0002 rule).

The person is never asked for anything:

1. the chat AI names the commit message with its reply turn (``commit_message``);
2. when an edit-mode chat run ends, FlowGate commits the flow's worktree with it (the TR
   commit point, ``create_tr_commit``), or with a plain subject when the AI named none;
3. the outcome -- a commit, or why it failed and what is left -- is written into the
   conversation as a FlowGate turn (speaker ``system``). Nothing refuses a chat.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("DB_TYPE", "sqlite")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
os.environ.setdefault("ALLOWED_ORIGIN", "http://localhost")
os.environ.setdefault("CONTEXT", "/flowgate")

from modules.flow_gate.api.v1 import ai_invoke_routes, conversation_routes  # noqa: E402
from modules.flow_gate.db import tokens as db_tokens  # noqa: E402
from modules.flow_gate.services import chat_commit_service as svc  # noqa: E402
from modules.flow_gate.services import conversation_turn_service  # noqa: E402
from modules.flow_gate.services import git_service  # noqa: E402
from modules.flow_gate.services import invoke_mention_service as ims  # noqa: E402
from modules.flow_gate.services.ai_invoke import runtime  # noqa: E402

GROUP = "flowgate.v02test.0023"
CHAT = "flowgate.v02test.0023.0002-CH"


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True, text=True).stdout


@pytest.fixture()
def repo(tmp_path):
    root = tmp_path / "wt"
    root.mkdir()
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@example.invalid")
    _git(root, "config", "user.name", "t")
    (root / ".gitignore").write_text("ignored.log\n", encoding="utf-8")
    (root / "a.py").write_text("a = 1\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "init")
    return root


@pytest.fixture()
def announced(monkeypatch):
    """The FlowGate turns the run end writes (append_turn stood in for)."""
    turns: list[dict] = []
    monkeypatch.setattr(conversation_turn_service, "append_turn", lambda **kw: turns.append(kw) or {"ok": True})
    from modules.flow_gate.db import conversation_turns as turn_store
    monkeypatch.setattr(turn_store, "latest_user_locale", lambda doc_id: "ko")
    monkeypatch.setattr(turn_store, "current_head_seq", lambda doc_id: 7)
    return turns


@pytest.fixture()
def edit_chat(monkeypatch, repo, announced):
    """An edit-mode chat (its token holds source_access=read_write) of a group with Git."""
    monkeypatch.setattr(svc, "group_worktree", lambda group_id: repo)
    monkeypatch.setattr(db_tokens, "get_by_id", lambda token_id: {"token_id": token_id, "source_access": "read_write"})
    monkeypatch.setattr(git_service, "derive_commit_type", lambda group_id: "fix")
    return repo


def _run(message=None, **over):
    return {"action_scope": "chat", "run_id": "aiv2_x", "group_id": GROUP, "doc_ref": CHAT,
            "token_id": "tok_x", "chat_commit_message": message, **over}


def _commits(calls, paths=("a.py",)):
    def commit(group_id, subject):
        calls.append((group_id, subject))
        return {"committed": True, "commit": "abc1234", "commit_sha": "abc1234" + "0" * 33,
                "subject": subject, "skipped_reason": None, "committed_paths": list(paths)}
    return commit


# -- message -------------------------------------------------------------------

def test_message_is_one_line_cut_to_a_subject_and_blank_is_none():
    assert svc.normalize_message("  fix:   chat\n commit  ") == "fix: chat commit"
    assert len(svc.normalize_message("x" * 500)) == git_service.COMMIT_SUBJECT_MAX
    assert svc.normalize_message("   ") is None
    assert svc.normalize_message(None) is None
    assert svc.normalize_message(12) is None


def test_subject_keeps_a_conventional_message_and_types_a_plain_one(monkeypatch):
    monkeypatch.setattr(git_service, "derive_commit_type", lambda group_id: "fix")
    assert svc.commit_subject(GROUP, "feat(chat): commit point") == "feat(chat): commit point"
    assert svc.commit_subject(GROUP, "채팅 커밋 추가") == "fix: 채팅 커밋 추가"
    assert svc.fallback_subject(GROUP, 3) == "fix(chat): update 3 files"
    assert svc.fallback_subject(GROUP, 1) == "fix(chat): update 1 file"


# -- the reply records the message (rule 1) ------------------------------------

@pytest.fixture()
def live_run(monkeypatch):
    run = {"run_id": "aiv2_live", "status": "running", "chat_commit_message": None}
    monkeypatch.setattr(runtime, "get_run_record", lambda run_id: run if run_id == "aiv2_live" else None)
    return run


def _token(**over):
    return {"action_scope": "chat", "source_access": "read_write", "ai_run_id": "aiv2_live", **over}


def test_an_edit_chat_reply_records_its_message_on_the_live_run(live_run):
    assert svc.record_message(_token(), " feat(chat):  x ") == "feat(chat): x"
    assert live_run["chat_commit_message"] == "feat(chat): x"


@pytest.mark.parametrize("over", [{"source_access": "read"}, {"source_access": None},
                                  {"action_scope": "edit"}, {"ai_run_id": None}, {"ai_run_id": "aiv2_gone"}])
def test_a_message_outside_a_live_edit_chat_is_ignored(live_run, over):
    assert svc.record_message(_token(**over), "fix: x") is None
    assert live_run["chat_commit_message"] is None


def test_a_finished_run_records_nothing(live_run):
    live_run["status"] = "finished"
    assert svc.record_message(_token(), "fix: x") is None


def test_the_worker_turn_route_hands_the_message_on(monkeypatch):
    token = {"token_id": "tok_x", "action_scope": "chat", "doc_ref": CHAT, "consumed_at": None}
    monkeypatch.setattr(conversation_routes, "_authenticate", lambda doc_id, raw: (token, None))
    monkeypatch.setattr(conversation_turn_service, "append_turn",
                        lambda **kw: {"ok": True, "replayed": False, "turn": {"seq": 9}})
    seen = []
    monkeypatch.setattr(svc, "record_message", lambda tok, raw: seen.append((tok, raw)))
    body = conversation_routes.WorkerConversationTurnAppend(
        body="done", idempotency_key="tok_x", commit_message="fix(chat): x")
    assert conversation_routes._append_authenticated(CHAT, body, "raw").status_code == 201
    assert seen == [(token, "fix(chat): x")]
    seen.clear()
    plain = conversation_routes.WorkerConversationTurnAppend(body="done", idempotency_key="tok_x")
    conversation_routes._append_authenticated(CHAT, plain, "raw")
    assert seen == []


def test_a_chat_start_names_no_commit_message_any_more():
    assert "commit_message" not in ai_invoke_routes.AiInvokeStartRequest.model_fields


# -- the run end (rules 2 and 3) -----------------------------------------------

def test_run_end_commits_with_the_ai_message_and_says_so(edit_chat, announced, monkeypatch):
    calls = []
    monkeypatch.setattr(git_service, "create_tr_commit", _commits(calls))
    (edit_chat / "a.py").write_text("a = 2\n", encoding="utf-8")
    run = _run("feat(chat): commit point")
    result = svc.commit_on_finalize(run)
    assert calls == [(GROUP, "feat(chat): commit point")]
    assert result["committed"] and result["message_source"] == "ai" and result["files"] == ["a.py"]
    assert run["chat_commit"] is result
    [turn] = announced
    assert turn["actor"] == {"kind": "system", "display_name": "FlowGate", "run_id": "aiv2_x"}
    assert turn["doc_id"] == CHAT and turn["idempotency_key"] == "chat-commit:aiv2_x" and turn["based_on_seq"] == 7
    assert "`abc1234` feat(chat): commit point" in turn["body_raw"] and "`a.py`" in turn["body_raw"]
    assert "FlowGate가 만든" not in turn["body_raw"]


def test_run_end_without_an_ai_message_commits_with_a_plain_one(edit_chat, announced, monkeypatch):
    calls = []
    monkeypatch.setattr(git_service, "create_tr_commit", _commits(calls, ("a.py", "b.py")))
    (edit_chat / "a.py").write_text("a = 2\n", encoding="utf-8")
    (edit_chat / "b.py").write_text("b = 1\n", encoding="utf-8")
    result = svc.commit_on_finalize(_run())
    assert calls == [(GROUP, "fix(chat): update 2 files")]
    assert result["message_source"] == "fallback"
    assert "FlowGate가 만든 메시지로 커밋했습니다" in announced[0]["body_raw"]


def test_a_failed_commit_is_said_with_what_is_left(edit_chat, announced, monkeypatch):
    monkeypatch.setattr(git_service, "create_tr_commit", lambda g, s: {
        "committed": False, "skipped_reason": "git_busy", "committed_paths": []})
    (edit_chat / "a.py").write_text("a = 2\n", encoding="utf-8")
    result = svc.commit_on_finalize(_run("fix: x"))
    assert result["committed"] is False and result["skipped_reason"] == "git_busy"
    assert result["files"] == ["a.py"] and result["file_count"] == 1
    body = announced[0]["body_raw"]
    assert "자동 커밋에 실패했습니다 (`git_busy`)" in body and "`a.py`" in body
    assert "다음 편집 모드 채팅이 끝날 때 다시 커밋합니다" in body


def test_an_exception_is_said_and_never_raised(edit_chat, announced, monkeypatch):
    def boom(g, s):
        raise RuntimeError("synthetic")

    monkeypatch.setattr(git_service, "create_tr_commit", boom)
    (edit_chat / "a.py").write_text("a = 2\n", encoding="utf-8")
    assert svc.commit_on_finalize(_run("fix: x"))["skipped_reason"] == "commit_failed"
    assert "`commit_failed`" in announced[0]["body_raw"]


def test_nothing_to_commit_says_nothing(edit_chat, announced, monkeypatch):
    monkeypatch.setattr(git_service, "create_tr_commit", lambda g, s: pytest.fail("must not commit"))
    assert svc.commit_on_finalize(_run("fix: x")) is None   # clean worktree
    for reason in ("no_changes", "artifacts_only"):
        monkeypatch.setattr(git_service, "create_tr_commit", lambda g, s, r=reason: {
            "committed": False, "skipped_reason": r, "committed_paths": []})
        (edit_chat / "a.py").write_text(f"a = '{reason}'\n", encoding="utf-8")
        run = _run("fix: x")
        assert svc.commit_on_finalize(run) is None and "chat_commit" not in run
    assert announced == []


def test_a_read_chat_never_commits_even_on_a_dirty_worktree(edit_chat, announced, monkeypatch):
    monkeypatch.setattr(db_tokens, "get_by_id", lambda token_id: {"source_access": "read"})
    monkeypatch.setattr(git_service, "create_tr_commit", lambda g, s: pytest.fail("must not commit"))
    (edit_chat / "a.py").write_text("a = 2\n", encoding="utf-8")
    assert svc.commit_on_finalize(_run("fix: x")) is None
    assert announced == []


def test_no_git_or_another_scope_does_nothing(edit_chat, announced, monkeypatch):
    monkeypatch.setattr(git_service, "create_tr_commit", lambda g, s: pytest.fail("must not commit"))
    (edit_chat / "a.py").write_text("a = 2\n", encoding="utf-8")
    assert svc.commit_on_finalize(_run("fix: x", action_scope="work")) is None
    monkeypatch.setattr(svc, "group_worktree", lambda group_id: None)
    assert svc.commit_on_finalize(_run("fix: x")) is None
    assert announced == []


def test_announce_failure_never_raises(edit_chat, monkeypatch):
    monkeypatch.setattr(git_service, "create_tr_commit", _commits([]))
    monkeypatch.setattr(conversation_turn_service, "append_turn",
                        lambda **kw: (_ for _ in ()).throw(RuntimeError("closed")))
    (edit_chat / "a.py").write_text("a = 2\n", encoding="utf-8")
    assert svc.commit_on_finalize(_run("fix: x"))["committed"]


def test_real_commit_through_the_tr_commit_point(edit_chat, announced, monkeypatch):
    """create_tr_commit's own staging and commit, with only its DB gates stood in for."""
    from modules.flow_gate.services.git import commit as git_commit

    monkeypatch.setattr(git_service, "create_tr_commit",
                        lambda group_id, subject: git_commit._commit_worktree(edit_chat, subject, {}, group_id))
    (edit_chat / "a.py").write_text("a = 9\n", encoding="utf-8")
    (edit_chat / "c.py").write_text("c = 1\n", encoding="utf-8")
    (edit_chat / "ignored.log").write_text("x\n", encoding="utf-8")
    result = svc.commit_on_finalize(_run("feat(chat): real commit"))
    assert result["committed"], result
    assert _git(edit_chat, "log", "-1", "--format=%s").strip() == "feat(chat): real commit"
    assert svc.pending_paths(edit_chat) == []
    assert sorted(result["files"]) == ["a.py", "c.py"]
    assert len(announced) == 1


# -- the turn ------------------------------------------------------------------

def test_turn_body_in_english_lists_at_most_ten_files():
    files = [f"f{i}.py" for i in range(12)]
    body = svc.turn_body({"committed": True, "commit": "abc1234", "subject": "fix: x",
                          "message_source": "ai", "file_count": 12, "files": files}, "en")
    assert body.startswith("Committed the chat's changes: `abc1234` fix: x (12 file(s))")
    assert "`f9.py`" in body and "`f10.py`" not in body and "- 2 more" in body


def test_turn_body_unknown_locale_falls_back_to_korean_and_paths_stay_one_line():
    body = svc.turn_body({"committed": False, "skipped_reason": "git_busy", "file_count": 1,
                          "files": ["a`b\nc.py"]}, None)
    assert body.startswith("자동 커밋에 실패했습니다")
    assert "- `a'b c.py`" in body


def test_system_actor_speaks_as_flowgate_not_as_the_ai():
    resolved = conversation_turn_service.resolve_actor({"kind": "system", "run_id": "aiv2_x"})
    assert resolved["speaker"] == "system"
    assert resolved["participant_key"] == "system:flowgate"
    assert resolved["display_name"] == "FlowGate" and resolved["source_run_id"] == "aiv2_x"


# -- the AI is told ------------------------------------------------------------

def _mention(monkeypatch, source_access):
    monkeypatch.setattr(ims.conversation_turns, "current_head_seq", lambda doc_id: 0)
    return ims.build_conversation_mention(
        doc_id="p.default.0001.0008-CH", project="p", module="default", group_name="p.default.0001",
        raw_token="RAW", token_id="tok_20261112_000001", api_base_url="http://h:1/api/v1",
        source_access=source_access)


def test_an_edit_chat_mention_asks_for_the_commit_message(monkeypatch):
    text = _mention(monkeypatch, "read_write")
    submit = text[text.index("Submit: POST"):]
    assert '"commit_message": "<only if you changed source files: see below>"' in submit
    assert '"force_encoding_reason": "<optional: only if a genuinely-flagged body must go through anyway>",' in submit
    assert "Do not commit yourself" in text


@pytest.mark.parametrize("access", ["read", None])
def test_a_read_chat_mention_does_not(monkeypatch, access):
    text = _mention(monkeypatch, access)
    assert "commit_message" not in text


def test_the_api_reply_tool_takes_an_optional_commit_message():
    schema = runtime._CHAT_TOOL_SCHEMA
    assert "commit_message" in schema["properties"] and schema["required"] == ["body"]
