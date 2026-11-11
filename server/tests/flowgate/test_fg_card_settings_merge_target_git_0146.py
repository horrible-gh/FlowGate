"""flowgate.v02.0146 T#2 — the merge-target judgement test, moved verbatim out of ``test_fg_card_settings.py``.

Why a file of its own: this test builds a temporary bare origin and drives real Git for every setting it reads or
saves, so it alone took about 120-190 s of the 255 s the whole card settings file took on a one-processor host (measured in
flowgate.v02.0146 TR#2). ``--dist loadfile`` hands a whole file to one worker, so with both Git tests inside
``test_fg_card_settings.py`` that file was the long pole of any parallel run and no worker count could shorten it.
Nothing about the test changed — same body, same harness (``env`` / ``git_flow`` / helpers are the ones of
``test_fg_card_settings``) — it is only placed where ``loadfile`` can run it beside the other files.
"""
from __future__ import annotations

import json

import pytest

from test_fg_card_settings import (  # noqa: F401 — fixtures used by name
    FA, FB, _gate_key, _get_refreshed, _git_cmd, db_path, decided, env, get_settings, git_flow, legacy_cards, node,
    put_draft, setting, stored_rows,
)

pytestmark = pytest.mark.usefixtures("legacy_cards")


def test_the_merge_target_branch_is_saved_only_for_a_target_the_judgement_allows(env, git_flow):
    from modules.flow_gate.db import groups as db_groups

    work_base_before = db_groups.get_by_id(FB).get("work_base_ref")
    g = _gate_key(env)
    body = get_settings(env, FB, g)
    # not the groups' own slots, not a remote. flowgate.v02test.0020 rev2: a branch lacking the work base history
    # (``old``) is listed -- the list no longer reads the scope floor -- and its save is refused below
    assert body["merge_target_options"] == ["feature/v0.2.0", "main", "old", "release/local"], body["merge_target_state"]
    assert body["default_target_branch"] == "main"
    state = body["merge_target_state"]
    assert (state["status"], state["code"], state["reason"]) == ("ok", None, None)
    assert state["current"] == {"branch": "main", "inherited": True, "checked": True, "error": None,
                                "unmerge_supported": True, "warnings": []}
    assert state["default"]["branch"] == "main" and state["default"]["error"] is None
    for name, status, code in (("nope", 404, "merge_target_not_found"),
                               ("remote-only", 409, "merge_target_remote_only"),
                               (git_flow["other"], 409, "merge_target_internal_slot"),
                               (git_flow["own"], 409, "merge_target_same_branch"),
                               ("a..b", 422, "merge_target_invalid"), ("-x", 422, "merge_target_invalid"),
                               ("HEAD", 422, "merge_target_invalid"),
                               ("old", 409, "merge_target_incoming_work_base")):
        refused = setting(env, FB, g, "merge_target_branch", name, expect=status)
        assert refused["error"]["code"] == code, (name, refused["error"])
        assert "\\" not in json.dumps(refused["error"]["params"])                # no worktree path
    assert stored_rows(env, FB, g) == {} and get_settings(env, FB, g)["settings_version"] == 0
    # every other candidate is a target the save allows (one judgement), whatever the merge mode
    setting(env, FB, g, "merge_mode", "commit")
    for name in [n for n in reversed(body["merge_target_options"]) if n != "old"]:
        saved = setting(env, FB, g, "merge_target_branch", name)
        assert saved["fields"]["merge_target_branch"] == {"value": name, "stored": True, "default": None,
                                                          "choices": None}
        assert (saved["merge_target_state"]["current"]["branch"], saved["merge_target_state"]["current"]["inherited"]) \
            == (name, False)
    picked = get_settings(env, FB, g)["merge_target_state"]["current"]
    assert (picked["error"], picked["unmerge_supported"]) == (None, False)          # a non-base target: no unmerge
    assert picked["warnings"] == ["target_differs_from_work_base", "unmerge_unsupported_for_non_base_target"]
    # saving the target touched neither push nor the remote; the group's work base never moves
    assert {field for (_key, field) in stored_rows(env, FB, g)} == {"merge_mode", "merge_target_branch"}
    assert db_groups.get_by_id(FB).get("work_base_ref") == work_base_before
    # the stored target disappears: its name stays, it is no candidate and the state says why (the refresh read
    # option skips the cache); the save judges the name again, never from the cache
    for name, status, code in (("release/local", 404, "merge_target_not_found"),
                               ("feature/v0.2.0", 409, "merge_target_remote_only")):   # its origin copy is left
        setting(env, FB, g, "merge_target_branch", name)
        _git_cmd(["branch", "-D", name], git_flow["base"])
        gone = _get_refreshed(env, FB, g)
        assert gone["fields"]["merge_target_branch"]["value"] == name
        assert name not in gone["merge_target_options"] and "main" in gone["merge_target_options"]
        assert gone["merge_target_state"]["current"]["error"] == code
        assert setting(env, FB, g, "merge_target_branch", name, expect=status)["error"]["code"] == code
    cleared = setting(env, FB, g, "merge_target_branch", None)                   # null: back to the work base
    assert cleared["merge_target_state"]["current"]["inherited"] is True
    assert cleared["merge_target_state"]["current"]["branch"] == "main"
