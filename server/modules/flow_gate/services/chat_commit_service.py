"""Chat commit point (flowgate.v02test.0023): a chat that changes source leaves a commit.

A card commits its work (``commit_result`` / the TR commit point). A chat used to change
the group worktree and stop there: nothing committed the files, the next card merge
refused the dirty worktree (``group_worktree_dirty``) and the work only reached Git
through the confirm card's absorb commit under a generic subject. The rule fixed in
0023-CH0002 for a group whose project has Git:

1. A chat is started with a commit message (``commit_message`` on /ai-invoke/start).
2. When that chat run ends, FlowGate commits the group worktree with that message
   (:func:`commit_on_finalize`). A run that changed nothing makes no commit.
3. A chat may not start while the worktree holds uncommitted changes and no commit
   message was given: 409 ``chat_commit_message_required`` (:func:`refusal`). Given the
   message, the run starts and its end commits those changes together with its own.

A run without a message that changed files leaves them uncommitted on purpose; rule 3
then stops the next chat until someone names the commit. The commit itself is the TR
commit point's (``create_tr_commit``): the same staging, tool-debris exclusions, author
and lock -- it never raises and never waits for the Group lock, so a busy Git is a
``git_busy`` skip that rule 3 picks up on the next chat.

A group without Git (project Git off, no managed worktree) is outside the rule: nothing
is refused and nothing is committed. A failed status read never refuses a chat either;
the commit at the run end still happens when a message was given.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

ERR_COMMIT_MESSAGE_REQUIRED = "chat_commit_message_required"
# Paths named in the refusal and on the run; the count is always exact.
PENDING_LIST_MAX = 20
SKIP_MESSAGE_MISSING = "message_missing"


def _gs():
    from modules.flow_gate.services import git_service
    return git_service


def normalize_message(raw: Optional[str]) -> Optional[str]:
    """One line, whitespace collapsed; None when nothing is left."""
    if raw is None:
        return None
    text = _gs().normalize_subject(raw)
    return text or None


def validate_message(raw: Optional[str]) -> Optional[str]:
    """The normalized message, or ValueError when it is longer than a commit subject may be."""
    message = normalize_message(raw)
    if message is not None and len(message) > _gs().COMMIT_SUBJECT_MAX:
        raise ValueError(f"must be at most {_gs().COMMIT_SUBJECT_MAX} characters")
    return message


def commit_subject(group_id: str, message: str) -> str:
    """The person's words as the subject. A message that is not ``type(scope): summary``
    yet gets the group's conventional type in front, the way a TR draft does."""
    gs = _gs()
    if gs.is_conventional_subject(message):
        return message
    try:
        commit_type = gs.derive_commit_type(group_id) or "chore"
    except Exception:
        commit_type = "chore"
    return gs.conventional_subject(commit_type, message)[:gs.COMMIT_SUBJECT_MAX]


def group_worktree(group_id: str) -> Optional[Path]:
    """The group's managed worktree when its project has Git, else None.

    The same gates ``create_tr_commit`` passes before it commits, so "this group has Git"
    means one thing for the refusal and for the commit."""
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


def pending_changes(group_id: str) -> Optional[list[str]]:
    """Uncommitted paths of the group worktree; None when the rule does not apply (no Git)
    or the worktree could not be read."""
    try:
        root = group_worktree(group_id)
        if root is None:
            return None
        return pending_paths(root)
    except Exception:
        logger.warning("chat commit: pending change check failed for %s", group_id, exc_info=True)
        return None


def refusal(group_id: str, message: Optional[str]) -> Optional[dict]:
    """Rule 3: the 409 body when this chat may not start, else None."""
    if message:
        return None
    paths = pending_changes(group_id)
    if not paths:
        return None
    shown = ", ".join(paths[:5]) + (f" (+{len(paths) - 5})" if len(paths) > 5 else "")
    return {
        "code": ERR_COMMIT_MESSAGE_REQUIRED,
        "message": f"{len(paths)} uncommitted change(s) in the flow's worktree: {shown}. "
                   "Enter a commit message and call the AI again.",
        "group_id": group_id,
        "file_count": len(paths),
        "files": paths[:PENDING_LIST_MAX],
    }


def commit_on_finalize(run: dict) -> Optional[dict]:
    """Rules 2 and 1: commit the worktree with the run's message when the run ends.

    Records ``run["chat_commit"]`` and returns it; None when the group has no Git or the
    run neither had a message nor left changes. Never raises."""
    if run.get("action_scope") != "chat" or not run.get("group_id"):
        return None
    group_id = run["group_id"]
    message = run.get("chat_commit_message")
    try:
        if not message:
            paths = pending_changes(group_id)
            if not paths:
                return None
            result = {"committed": False, "commit": None, "commit_sha": None, "subject": None,
                      "skipped_reason": SKIP_MESSAGE_MISSING, "file_count": len(paths),
                      "files": paths[:PENDING_LIST_MAX]}
        else:
            if group_worktree(group_id) is None:
                return None
            outcome = _gs().create_tr_commit(group_id, commit_subject(group_id, message))
            committed = list(outcome.get("committed_paths") or [])
            result = {"committed": bool(outcome.get("committed")), "commit": outcome.get("commit"),
                      "commit_sha": outcome.get("commit_sha"), "subject": outcome.get("subject"),
                      "skipped_reason": outcome.get("skipped_reason"),
                      "file_count": len(committed), "files": committed[:PENDING_LIST_MAX]}
            if not result["committed"] and result["skipped_reason"] in ("no_changes", "artifacts_only"):
                return None
    except Exception:
        logger.warning("chat commit failed for run %s", run.get("run_id"), exc_info=True)
        result = {"committed": False, "commit": None, "commit_sha": None, "subject": None,
                  "skipped_reason": "commit_failed", "file_count": 0, "files": []}
    run["chat_commit"] = result
    if result["committed"]:
        logger.info("chat run %s committed %s in %s", run.get("run_id"), result["commit"], group_id)
    return result
