"""Chat commit point (flowgate.v02test.0023): a chat that changes source leaves a commit.

A card commits its work (``commit_result`` / the TR commit point). A chat used to change
the group worktree and stop there: nothing committed the files, the next card merge
refused the dirty worktree (``group_worktree_dirty``) and the work only reached Git
through the confirm card's absorb commit under a generic subject.

The rule (0023-CH0002), for a group whose project has Git. The person is never asked for
anything -- the chat stays a chat:

1. The AI names the commit message. A chat AI that changed source sends
   ``commit_message`` with its reply turn (the worker turn route, or the API worker's
   ``send_chat_reply`` tool); :func:`record_message` keeps it on the live run.
2. When an edit-mode chat run ends (its token held ``source_access=read_write``),
   FlowGate commits the group worktree (:func:`commit_on_finalize`) with that message.
   Without one, FlowGate writes a plain subject from the group type and the file count.
   Changes left uncommitted from before (an earlier failed commit, an edit outside the
   chat) go into the same commit -- a commit is of the worktree, as a card's is.
3. The outcome is written into the conversation as a FlowGate turn (speaker ``system``):
   the SHA, subject and files of a commit, or why the commit failed and which files are
   still uncommitted. A failed commit never blocks the next chat; the next edit-mode
   chat's end commits the leftover changes.

A read-mode chat cannot change source, so its end commits nothing and says nothing. A
run that left nothing to commit says nothing either. A group without Git (project Git
off, no managed worktree) is outside the rule. Nothing here ever raises into the run.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Paths named on the run and in the turn; the count is always exact.
PENDING_LIST_MAX = 20
TURN_FILES_MAX = 10
# Skips that mean "there was nothing to commit" -- no turn for them.
_NOTHING_TO_COMMIT = ("no_changes", "artifacts_only")

SYSTEM_DISPLAY_NAME = "FlowGate"

_STRINGS = {
    "ko": {
        "committed": "채팅 작업을 커밋했습니다: `{commit}` {subject} (파일 {n}개)",
        "fallback": "AI가 커밋 메시지를 보내지 않아 FlowGate가 만든 메시지로 커밋했습니다.",
        "failed": "자동 커밋에 실패했습니다 (`{reason}`). 커밋되지 않은 변경 {n}개가 남아 있습니다.",
        "failed_plain": "자동 커밋에 실패했습니다 (`{reason}`).",
        "retry": "다음 편집 모드 채팅이 끝날 때 다시 커밋합니다.",
        "more": "외 {n}개",
    },
    "en": {
        "committed": "Committed the chat's changes: `{commit}` {subject} ({n} file(s))",
        "fallback": "The AI sent no commit message, so FlowGate wrote one.",
        "failed": "The automatic commit failed (`{reason}`). {n} uncommitted change(s) remain.",
        "failed_plain": "The automatic commit failed (`{reason}`).",
        "retry": "The end of the next edit-mode chat commits them again.",
        "more": "{n} more",
    },
    "ja": {
        "committed": "チャットの変更をコミットしました: `{commit}` {subject}（{n} ファイル）",
        "fallback": "AI がコミットメッセージを送らなかったため、FlowGate が作成したメッセージでコミットしました。",
        "failed": "自動コミットに失敗しました（`{reason}`）。未コミットの変更が {n} 件残っています。",
        "failed_plain": "自動コミットに失敗しました（`{reason}`）。",
        "retry": "次の編集モードのチャットが終わるときに再度コミットします。",
        "more": "ほか {n} 件",
    },
}


def _gs():
    from modules.flow_gate.services import git_service
    return git_service


# -- the message ---------------------------------------------------------------

def normalize_message(raw: Optional[str]) -> Optional[str]:
    """One line, whitespace collapsed, cut to a commit subject; None when nothing is left.

    Cut rather than refused: a reply turn is never lost over its commit message."""
    if not isinstance(raw, str):
        return None
    gs = _gs()
    text = gs.normalize_subject(raw)
    return text[:gs.COMMIT_SUBJECT_MAX].rstrip() or None


def commit_subject(group_id: str, message: str) -> str:
    """The AI's words as the subject. A message that is not ``type(scope): summary`` yet
    gets the group's conventional type in front, the way a TR draft does."""
    gs = _gs()
    if gs.is_conventional_subject(message):
        return message
    return gs.conventional_subject(_commit_type(group_id), message)[:gs.COMMIT_SUBJECT_MAX]


def fallback_subject(group_id: str, file_count: int) -> str:
    """The subject when the AI named none, e.g. ``fix(chat): update 3 files``."""
    noun = "file" if file_count == 1 else "files"
    return f"{_commit_type(group_id)}(chat): update {file_count} {noun}"


def _commit_type(group_id: str) -> str:
    try:
        return _gs().derive_commit_type(group_id) or "chore"
    except Exception:
        return "chore"


def record_message(token: dict, raw: Optional[str]) -> Optional[str]:
    """Rule 1: keep the commit message a chat AI sent with its reply on its live run.

    Only a chat token that may change source (``source_access=read_write``) and is bound
    to a live run records one; anything else is ignored. Returns what was recorded."""
    message = normalize_message(raw)
    if message is None or not token or token.get("action_scope") != "chat":
        return None
    if token.get("source_access") != "read_write":
        return None
    run_id = token.get("ai_run_id")
    if not run_id:
        return None
    try:
        from modules.flow_gate.services.ai_invoke import runtime
        run = runtime.get_run_record(str(run_id))
    except Exception:
        logger.warning("chat commit: run lookup failed for %s", run_id, exc_info=True)
        return None
    if run is None or run.get("status") == "finished":
        return None
    run["chat_commit_message"] = message
    return message


# -- the worktree --------------------------------------------------------------

def group_worktree(group_id: str) -> Optional[Path]:
    """The group's managed worktree when its project has Git, else None.

    The same gates ``create_tr_commit`` passes before it commits."""
    gs = _gs()
    project_id = gs._project_of_group(group_id)
    if not project_id:
        return None
    cfg = gs.db_git.get_config(project_id)
    if cfg is None or not cfg.get("enabled") or not gs.git_available():
        return None
    state = gs.db_git.get_state(group_id)
    if state is None or not state.get("worktree_registered") or not state.get("branch"):
        return None
    project_name = gs._project_name(project_id)
    if not project_name:
        return None
    root = gs.src_root(project_name, state["branch"])
    return root if root.is_dir() else None


def pending_paths(root: Path) -> list[str]:
    """Uncommitted paths a commit of ``root`` would take: tracked changes (deletions too)
    and new files outside the tool-debris rule. Ignored files never count."""
    from modules.flow_gate.services import path_exclusion_rules
    gs = _gs()
    tracked = gs._dirty_files(root, include_untracked=False)
    kept, _artifacts = path_exclusion_rules.partition_paths(gs._worktree_untracked_paths(root))
    return sorted(set(tracked) | set(kept))


def run_may_write(run: dict) -> bool:
    """Was this chat run an edit-mode one? Read from its token row, not a stashed value."""
    token_id = run.get("token_id")
    if not token_id:
        return False
    from modules.flow_gate.db import tokens as db_tokens
    token = db_tokens.get_by_id(token_id)
    return bool(token) and token.get("source_access") == "read_write"


# -- the run end ---------------------------------------------------------------

def commit_on_finalize(run: dict) -> Optional[dict]:
    """Rules 2 and 3: commit the worktree when an edit-mode chat run ends, then say so.

    Records ``run["chat_commit"]`` and returns it; None when the rule does not apply or
    there was nothing to commit. Never raises."""
    if run.get("action_scope") != "chat" or not run.get("group_id"):
        return None
    group_id = run["group_id"]
    try:
        if not run_may_write(run):
            return None
        root = group_worktree(group_id)
        if root is None:
            return None
        pending = pending_paths(root)
        if not pending:
            return None
        message = run.get("chat_commit_message")
        source = "ai" if message else "fallback"
        subject = commit_subject(group_id, message) if message else fallback_subject(group_id, len(pending))
        outcome = _gs().create_tr_commit(group_id, subject)
        if not outcome.get("committed") and outcome.get("skipped_reason") in _NOTHING_TO_COMMIT:
            return None
        committed = list(outcome.get("committed_paths") or [])
        result = {
            "committed": bool(outcome.get("committed")),
            "commit": outcome.get("commit"), "commit_sha": outcome.get("commit_sha"),
            "subject": outcome.get("subject") or subject, "message_source": source,
            "skipped_reason": outcome.get("skipped_reason"),
        }
        if result["committed"]:
            files = committed
        else:
            try:
                files = pending_paths(root)
            except Exception:
                files = pending
        result["file_count"] = len(files)
        result["files"] = files[:PENDING_LIST_MAX]
    except Exception:
        logger.warning("chat commit failed for run %s", run.get("run_id"), exc_info=True)
        result = {"committed": False, "commit": None, "commit_sha": None, "subject": None,
                  "message_source": None, "skipped_reason": "commit_failed",
                  "file_count": 0, "files": []}
    run["chat_commit"] = result
    if result["committed"]:
        logger.info("chat run %s committed %s in %s", run.get("run_id"), result["commit"], group_id)
    _announce(run, result)
    return result


def _announce(run: dict, result: dict) -> None:
    """Rule 3: the outcome as a FlowGate turn of the chat. Never raises."""
    doc_id = run.get("doc_ref")
    if not doc_id:
        return
    try:
        from modules.flow_gate.db import conversation_turns as turn_store
        from modules.flow_gate.services import conversation_turn_service
        body = turn_body(result, turn_store.latest_user_locale(doc_id))
        conversation_turn_service.append_turn(
            doc_id=doc_id,
            actor={"kind": "system", "display_name": SYSTEM_DISPLAY_NAME, "run_id": run.get("run_id")},
            body_raw=body,
            idempotency_key=f"chat-commit:{run.get('run_id')}",
            based_on_seq=turn_store.current_head_seq(doc_id),
        )
    except Exception:
        logger.warning("chat commit: announcing failed for run %s", run.get("run_id"), exc_info=True)


def turn_body(result: dict, locale: Optional[str]) -> str:
    """The FlowGate turn: what was committed, or why not and what is left."""
    s = _STRINGS.get(locale or "") or _STRINGS["ko"]
    files = list(result.get("files") or [])
    count = int(result.get("file_count") or len(files))
    lines: list[str] = []
    if result.get("committed"):
        lines.append(s["committed"].format(commit=result.get("commit") or "?",
                                           subject=result.get("subject") or "", n=count))
        if result.get("message_source") == "fallback":
            lines.append(s["fallback"])
    else:
        reason = result.get("skipped_reason") or "commit_failed"
        lines.append(s["failed"].format(reason=reason, n=count) if count else s["failed_plain"].format(reason=reason))
    if files:
        lines.append("")
        lines += [f"- `{_inline(path)}`" for path in files[:TURN_FILES_MAX]]
        if count > TURN_FILES_MAX:
            lines.append(f"- {s['more'].format(n=count - TURN_FILES_MAX)}")
    if not result.get("committed"):
        lines += ["", s["retry"]]
    return "\n".join(lines)


def _inline(path: str) -> str:
    """A path as one safe line inside a code span."""
    text = str(path).replace("\r", " ").replace("\n", " ").replace("`", "'")
    return " ".join(text.split())
