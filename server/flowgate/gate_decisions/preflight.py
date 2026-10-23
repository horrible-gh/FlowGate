"""The checks of a confirm before anything is accepted (flowgate.v02.0085 T#2, D0007 §7.1 / §7.3.1).

A confirm fixes the gate card's settings as they are (the settings snapshot and version) and what they ask for:

- ``merge_mode`` merge / commit is a Git apply, hold or unset is none; ``archive`` keeps the group's result as a
  checkpoint. Neither: the confirm needs no Git and is done at once, also in a group without a Git repository.
- In hold / unset the stored remote, push and keep branch are not used (they are no part of the effective settings).
- ``push`` needs the remote ``origin`` chosen (``merge_target``) and an ``origin`` remote in the checkout; a push with
  no remote chosen is refused with an explicit error, never pushed silently (an old ``push=true`` with no remote).
- A Git apply or an archive needs the group's Git slot (an enabled project Git integration, the group's registered
  worktree and its branch): otherwise ``gate_git_unavailable`` before anything is accepted.
- merge goes into the group's work base branch (stored at the group's creation, else the project's base branch),
  never a branch guessed from ``origin``; a work base that is missing or is the group's own branch is refused.
  The target branch's name and its SHA of the moment are recorded; commit records the group branch and its HEAD.

The group result itself (the frozen SHA) is taken later by the job under the group's Git lock (D0007 §7.3), not here:
these checks only read local refs and the remote's presence - no lock, no network.
"""
from __future__ import annotations

from typing import Any, Optional

from flowgate.gate_decisions import legacy_adapter as legacy

GIT_MODES = ("merge", "commit")
ORIGIN = "origin"
SETTINGS_INVALID, GIT_UNAVAILABLE = "gate_settings_invalid", "gate_git_unavailable"
(PUSH_REQUIRES_ORIGIN, ORIGIN_NOT_CONFIGURED, TARGET_BRANCH_INVALID) = (
    "push_requires_origin", "origin_not_configured", "target_branch_invalid")


def settings_of(flow_id: str, node_key: str) -> tuple[dict, int]:
    """The gate card's settings as shown (a null switch reads false, an old branch-name merge target reads unset) and
    its settings version."""
    from flowgate.card_settings import fields, repository

    stored = repository.values(flow_id, node_key)
    view = fields.view(fields.CHECK, stored, ())
    shown = {name: view[name]["value"] for name in ("merge_mode", "merge_target", "push", "keep_branch", "archive")
             if name in view}
    return shown, repository.settings_version(flow_id, node_key)


def effective(shown: dict) -> dict:
    """What the settings make a confirm do (hold / unset: no remote, push or keep branch)."""
    mode = shown.get("merge_mode")
    git = mode in GIT_MODES
    return {"merge_mode": mode if git else (mode or None), "git_apply": git, "archive": bool(shown.get("archive")),
            "merge_target": shown.get("merge_target") if git else None,
            "push": bool(shown.get("push")) if git else False,
            "keep_branch": bool(shown.get("keep_branch")) if git else False}


def _refuse(code: str, flow_id: str, node_key: str, reason: str, **extra: Any) -> legacy.GraphApiError:
    return legacy.GraphApiError(409, code, {"flow_id": flow_id, "node_key": node_key, "reason": reason, **extra})


def plan(project_id: Optional[str], flow_id: str, node_key: str) -> dict:
    """The confirm's plan, or a 409 (``gate_settings_invalid`` / ``gate_git_unavailable``) with nothing written:
    ``{"settings": shown, "settings_version", "effective", "needs_work", "target_remote", "target_branch",
    "target_sha", "group_branch", "group_head"}``. ``needs_work`` is a Git apply or an archive (a job is accepted)."""
    shown, version = settings_of(flow_id, node_key)
    eff = effective(shown)
    out = {"settings": shown, "settings_version": version, "effective": eff,
           "needs_work": eff["git_apply"] or eff["archive"], "target_remote": None, "target_branch": None,
           "target_sha": None, "group_branch": None, "group_head": None}
    if eff["push"] and eff["merge_target"] != ORIGIN:
        raise _refuse(SETTINGS_INVALID, flow_id, node_key, PUSH_REQUIRES_ORIGIN)
    if not out["needs_work"]:
        return out
    try:
        slot = legacy.git_slot(project_id, flow_id)
    except Exception:  # noqa: BLE001 — a slot that cannot be read is no slot (fail closed)
        slot = {"ok": False, "reason": "probe_failed"}
    if not slot.get("ok"):
        raise _refuse(GIT_UNAVAILABLE, flow_id, node_key, str(slot.get("reason") or "unavailable"))
    out.update(group_branch=slot["group_branch"], group_head=slot["group_head"])
    if eff["push"] and not slot.get("origin"):
        raise _refuse(SETTINGS_INVALID, flow_id, node_key, ORIGIN_NOT_CONFIGURED)
    if eff["merge_mode"] == "merge":
        base = slot.get("work_base")
        if not base or base == slot["group_branch"] or not slot.get("work_base_sha"):
            raise _refuse(SETTINGS_INVALID, flow_id, node_key, TARGET_BRANCH_INVALID, target_branch=base)
        out.update(target_branch=base, target_sha=slot["work_base_sha"])
    elif eff["merge_mode"] == "commit":
        out.update(target_branch=slot["group_branch"], target_sha=slot["group_head"])
    out["target_remote"] = ORIGIN if eff["git_apply"] and eff["merge_target"] == ORIGIN else None
    return out
