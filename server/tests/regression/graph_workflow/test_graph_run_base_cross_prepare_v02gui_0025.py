"""v02gui.0025 (NR): the base window's start cards share one base whatever the order and timing of
their preparations (F1), and the window rules the fix must not move.

F1: the first ``run_base`` attempt of a window pins its base in its owner marker under the Git locks; its workspace
event is written only by ``record_ready`` once the locks are released. A start card prepared in that gap read the group
HEAD of its own moment, so one window had two bases (B completion criterion 2). Reuses the 0061 harness and the 0015
helpers; command starts are written here as run events (the harness ``emit_run_status`` is a Mock).
"""
import json

import pytest

from flowgate.graph_run import workspace
from modules.flow_gate.graph_workflow.store import run_store
from modules.flow_gate.services.git import merge_target
from test_graph_attempt_workspaces_0061 import PROJECT, harness, repo, prepare, _commit_in  # noqa: F401
from test_graph_run_base_window_v02gui_0015 import (
    _command, _advance, _input, _set_attempt, _end, _workspace_event, _materialize, _index)


def _directory(item):
    return workspace.location(workspace.attempt(item.attempt), PROJECT)[0]


def _marker(item):
    return merge_target.read_owner_marker(_directory(item))


def _marker_bytes(item):
    return (_directory(item) / merge_target.OWNER_MARKER).read_bytes()


def _ref(repo, item):
    return repo.git("rev-parse", workspace.ATTEMPT_REF_PREFIX + merge_target.workspace_key(item.attempt))


# -- 2': cross preparation (§7-A) -------------------------------------------------------------------------------------

def test_cross_prepared_start_cards_of_one_window_share_its_base(repo):
    s1 = _command("user_start", None)
    a, b = repo.new("a"), repo.new("b")
    assert workspace._base_window("run")[0] == s1
    # 1: A pinned c0 in its marker; not recorded yet (no attempt input, no workspace event)
    c0 = _materialize(a)
    assert c0 == repo.git("rev-parse", "HEAD")
    pinned = _marker(a)
    assert (pinned["input_commit"], pinned["input_source"], pinned["base_since"]) == (c0, "run_base", s1)
    with pytest.raises(AssertionError):
        _workspace_event(a)
    a_marker = _marker_bytes(a)
    # 2: the group HEAD moves on
    c1 = _advance(repo, "later.txt")
    assert c1 != c0
    # 3: B prepared in the gap
    root_b = prepare(b)
    # 4: A recorded afterwards from its marker
    root_a = prepare(a)
    assert _marker_bytes(a) == a_marker
    # 5: a later start card
    c = repo.new("c")
    root_c = prepare(c)

    # assertion 1
    assert _input(b) == _input(a) == _input(c) == c0 != c1
    # assertion 2
    marker_b = _marker(b)
    assert (marker_b["input_commit"], marker_b["input_source"], marker_b["base_since"]) == (c0, "run_base", s1)
    # assertion 3
    for item in (a, b, c):
        data = _workspace_event(item)[1]
        assert (data["input_source"], data["input_commit"], data["base_since"]) == ("run_base", c0, s1)
    # assertion 4: the interleaving happened (A recorded after B)
    assert _index(_workspace_event(b)[0]["event_seq"]) < _index(_workspace_event(a)[0]["event_seq"])
    # assertion 5
    for root in (root_a, root_b, root_c):
        assert not (root / "later.txt").exists()
    assert repo.git("rev-parse", "HEAD") == c1
    # assertion 6
    reasons = {e["reason"] for e in run_store.events("run") if e["cause"] == workspace.CAUSE}
    assert not reasons & {"join_input_conflict", workspace.UNCARRIED}


# -- 2'', 2''': what the fix must not take in ---------------------------------------------------------------------------

def test_cross_prepared_card_after_a_new_command_takes_the_group_head(repo):
    s1 = _command("user_start", None)
    a, b = repo.new("a"), repo.new("b")
    c0 = _materialize(a)
    c1 = _advance(repo, "later.txt")
    s2 = _command("user_command", "idle")
    prepare(b)
    prepare(a)
    assert _input(b) == c1 and _workspace_event(b)[1]["base_since"] == s2
    assert _input(a) == c0 and _workspace_event(a)[1]["base_since"] == s1
    c = repo.new("c")
    prepare(c)
    assert _input(c) == c1


def test_cross_prepared_card_ignores_an_invalidated_pinned_marker(repo):
    _command("user_start", None)
    a, b = repo.new("a"), repo.new("b")
    _materialize(a)
    _set_attempt(a, status="invalidated", active_node_key=None, invalidated_reason="user_stop")
    c1 = _advance(repo, "later.txt")
    prepare(b)
    assert _input(b) == c1


# -- 4: valid and invalidated attempts mixed ----------------------------------------------------------------------------

@pytest.mark.parametrize("invalid_first", [True, False])
def test_mixed_valid_and_invalidated_bases_keep_the_valid_one(repo, invalid_first):
    _command("user_start", None)
    first, second = repo.new("a"), repo.new("b")
    prepare(first)
    c0 = _input(first)
    prepare(second)
    assert _input(second) == c0
    invalid, valid = (first, second) if invalid_first else (second, first)
    _set_attempt(invalid, status="invalidated", active_node_key=None, invalidated_reason="time_machine")
    _end(valid)
    _advance(repo, "later.txt")
    card = repo.new("c")
    prepare(card)
    assert _input(card) == c0


@pytest.mark.parametrize("invalid_first", [True, False])
def test_mixed_pinned_markers_keep_the_valid_one(repo, invalid_first):
    """F1 with markers only (nothing recorded yet): an invalidated marker is no candidate, a valid one is."""
    _command("user_start", None)
    first, second = repo.new("a"), repo.new("b")
    c0 = _materialize(first)
    assert _materialize(second) == c0
    invalid = first if invalid_first else second
    _set_attempt(invalid, status="invalidated", active_node_key=None, invalidated_reason="time_machine")
    _advance(repo, "later.txt")
    card = repo.new("c")
    prepare(card)
    assert _input(card) == c0


# -- 5a: a retry after a worktree failure --------------------------------------------------------------------------------

def test_retry_after_a_failed_worktree_starts_from_the_next_command_head(repo):
    _command("user_start", None)
    failed = repo.new("a")
    _materialize(failed)
    _set_attempt(failed, status="invalidated", active_node_key=None, invalidated_reason="worktree_failed")
    c1 = _advance(repo, "later.txt")
    s2 = _command("user_command", "idle")
    retry = repo.new("a2")
    prepare(retry)
    assert _input(retry) == c1 and _workspace_event(retry)[1]["base_since"] == s2


# -- 5b': an automatic wake or settle keeps the window (Q #622, intended) ----------------------------------------------

@pytest.mark.parametrize("cause, reason", [
    ("work_resumed", "approval"), ("work_resumed", "gate_publish"), ("work_resumed", "question_answered"),
    ("run_idle", None)])
def test_an_automatic_wake_keeps_the_base_until_a_person_commands(repo, cause, reason):
    s1 = _command("user_start", None)
    first = repo.new("a")
    prepare(first)
    c0 = _input(first)
    _end(first)
    c1 = _advance(repo, "later.txt")
    before = workspace._base_window("run")
    seq = _command(cause, "idle")
    if reason:
        run_store._affected("UPDATE graph_run_events SET reason = ? WHERE run_id = 'run' AND event_seq = ?",
                            [reason, seq])
    assert workspace._base_window("run") == before and before[0] == s1
    woken = repo.new("b")
    prepare(woken)
    assert _input(woken) == c0 and _workspace_event(woken)[1]["base_since"] == s1
    s2 = _command("user_command", "idle")
    card = repo.new("c")
    prepare(card)
    assert _input(card) == c1 and _workspace_event(card)[1]["base_since"] == s2


# -- 5e: a new window leaves the attempts prepared before as they are ----------------------------------------------------

def test_a_new_window_keeps_earlier_attempt_refs_and_markers(repo):
    _command("user_start", None)
    old, pinned = repo.new("a"), repo.new("b")
    prepare(old)
    _materialize(pinned)
    kept = {item.attempt: (_ref(repo, item), _marker_bytes(item)) for item in (old, pinned)}
    _advance(repo, "later.txt")
    _command("user_command", "idle")
    card = repo.new("c")
    prepare(card)
    prepare(pinned)
    assert {item.attempt: (_ref(repo, item), _marker_bytes(item)) for item in (old, pinned)} == kept
