"""flowgate.v02.0146 T#2 — the check card without a worktree test, moved verbatim out of ``test_fg_card_settings.py``.

Why a file of its own: this test builds a temporary bare origin and drives real Git for every setting it reads or
saves, so it alone took about 70-135 s of the 255 s the whole card settings file took on a one-processor host (measured in
flowgate.v02.0146 TR#2). ``--dist loadfile`` hands a whole file to one worker, so with both Git tests inside
``test_fg_card_settings.py`` that file was the long pole of any parallel run and no worker count could shorten it.
Nothing about the test changed — same body, same harness (``env`` / ``git_flow`` / helpers are the ones of
``test_fg_card_settings``) — it is only placed where ``loadfile`` can run it beside the other files.
"""
from __future__ import annotations

import pytest

from test_fg_card_settings import (  # noqa: F401 — fixtures used by name
    FA, FB, _gate_key, _get_refreshed, _git_cmd, db_path, decided, env, get_settings, git_flow, legacy_cards, node,
    put_draft, setting, stored_rows,
)

pytestmark = pytest.mark.usefixtures("legacy_cards")


def test_a_group_without_its_worktree_yet_lists_and_saves_real_branch_names(env, git_flow):
    """flowgate.v02.0098 T0012: a check card is set up before its group's work starts (no worktree yet). Its candidates
    are the real local branch names judged from where its branch will fork, each saved by the same judgement; an
    internal Graph attempt branch is never one. The confirm and the run still need the group's worktree."""
    from modules.flow_gate.db import git_integration as db_git
    from modules.flow_gate.db import groups as db_groups
    from modules.flow_gate.services.git import merge_target as mt
    from modules.flow_gate.services.git.credentials import GitServiceError

    fc = "flowgate.v02.0093"
    db_groups.create({"group_id": fc, "project_id": "flowgate", "module": "v02", "title": fc, "status": "OPEN"})
    assert db_git.get_state(fc) is None
    _git_cmd(["branch", "graph_attempt_att_x", "main"], git_flow["base"])
    g = put_draft(env, fc, 0, [node("tmp_g", None, "gate")])["key_map"]["tmp_g"]
    body = get_settings(env, fc, g)
    # flowgate.v02test.0020 rev2: ``old`` (lacking the work base history) is listed and refused when saved, below
    assert body["merge_target_options"] == ["feature/v0.2.0", "main", "old", "release/local"], body["merge_target_state"]
    assert body["default_target_branch"] == "main"
    state = body["merge_target_state"]
    assert (state["status"], state["code"]) == ("ok", None)
    assert (state["current"]["branch"], state["current"]["inherited"], state["current"]["error"]) == ("main", True, None)
    for name in [n for n in body["merge_target_options"] if n != "old"]:
        assert setting(env, fc, g, "merge_target_branch", name)["fields"]["merge_target_branch"]["value"] == name
    for name, code in (("old", "merge_target_incoming_work_base"), (git_flow["own"], "merge_target_internal_slot"),
                       ("graph_attempt_att_x", "merge_target_internal_slot")):
        assert setting(env, fc, g, "merge_target_branch", name, expect=409)["error"]["code"] == code, name
    with pytest.raises(GitServiceError) as strict:
        mt.judge_check_target("flowgate", fc, "main")
    assert (strict.value.code, strict.value.details["reason"]) == ("gate_git_unavailable", "group_slot_missing")
    assert db_git.get_state(fc) is None                                          # nothing provisioned or recorded
