"""v02gui.0015 (0001-B, NR0003 §6): a start card's base (``run_base``) is pinned per base window, not per run.

The window follows the run's last command start (a person's ``user_start`` / ``user_command`` / ``user_resume`` to
``running``, not from ``paused``); its first ``run_base`` card starts from the group HEAD of that moment and the later
start cards of the same window share that base. An attempt belongs to the window its owner marker pinned the base in
(``base_since``); a workspace event without the tag (a marker made before it) only when its attempt was created in
the window. Reuses the 0061 attempt-workspace harness, whose ``emit_run_status`` is a Mock: the command starts are
written here as run events.
"""
import json

import pytest

from flowgate.graph_run import command_result, workspace
from modules.flow_gate.graph_workflow.services import run_events
from modules.flow_gate.graph_workflow.store import run_store
from modules.flow_gate.services.git import merge_target
from test_graph_attempt_workspaces_0061 import PROJECT, harness, repo, prepare, _commit_in  # noqa: F401

OLD = "2000-01-01T00:00:00.000+00:00"


def _command(cause, from_status, to_status="running"):
    run = run_store.run_by_id("run")
    seq = int(run["last_event_seq"]) + 1
    run_store.append_event("run", seq, run_events.RUN_STATUS, {"mode": "full"}, node_key=None,
                           from_status=from_status, to_status=to_status, cause=cause, result_doc_id=None,
                           canvas_state=None, actor_user_id=None, reason=None)
    return seq


def _advance(repo, name):
    """A commit added to the group worktree (a merge / a direct edit) after the window's base was pinned."""
    (repo.group / name).write_text(name, encoding="utf-8")
    repo.git("add", name)
    _commit_in(repo.group, "commit", "-qm", name)
    return repo.git("rev-parse", "HEAD")


def _input(item):
    return workspace.attempt(item.attempt)["input_commit"]


def _set_attempt(item, **values):
    sets = ", ".join(f"{key} = ?" for key in values)
    run_store._affected(f"UPDATE graph_node_attempts SET {sets} WHERE attempt_id = ?", [*values.values(), item.attempt])


def _end(item):
    _set_attempt(item, status="ended", active_node_key=None)


def _workspace_event(item):
    for event in run_store.events("run"):
        data = json.loads(event["payload_json"])
        if event["cause"] == workspace.CAUSE and data.get("attempt_id") == item.attempt:
            return event, data
    raise AssertionError("no workspace event for " + item.attempt)


def _untag_event(item):
    """The attempt's workspace event as a marker made before ``base_since`` recorded it."""
    event, data = _workspace_event(item)
    data.pop("base_since", None)
    run_store._affected("UPDATE graph_run_events SET payload_json = ? WHERE run_id = 'run' AND event_seq = ?",
                        [json.dumps(data, sort_keys=True), event["event_seq"]])


def _untag_marker(item):
    directory = workspace.location(workspace.attempt(item.attempt), PROJECT)[0]
    marker = merge_target.read_owner_marker(directory)
    marker.pop("base_since")
    (directory / merge_target.OWNER_MARKER).write_text(json.dumps(marker), encoding="utf-8")


def _materialize(item):
    """Git done, journal not (as ``test_recover_after_git_before_journal_pins_original_group_head``)."""
    sha = workspace._materialize(workspace.attempt(item.attempt), PROJECT)
    assert _input(item) is None
    return sha


def _index(seq):
    return next(i for i, e in enumerate(run_store.events("run")) if e["event_seq"] == seq)


# -- 1, 8: a new command on an idle run starts from the group HEAD of that moment -----------------------------------

def test_new_command_on_idle_run_starts_from_group_head(repo):
    _command("user_start", None)
    first = repo.new("a")
    prepare(first)
    c0 = _input(first)
    _end(first)
    c1 = _advance(repo, "later.txt")
    _command("user_command", "idle")
    card = repo.new("b")
    root = prepare(card)
    assert _input(card) == c1 == repo.git("rev-parse", "HEAD") != c0
    assert (root / "later.txt").exists()


def test_new_command_on_idle_run_by_resume_starts_from_group_head(repo):
    _command("user_start", None)
    first = repo.new("a")
    prepare(first)
    _end(first)
    c1 = _advance(repo, "later.txt")
    _command("user_resume", "idle")
    card = repo.new("b")
    prepare(card)
    assert _input(card) == c1


def test_first_card_of_a_window_starts_from_group_head(repo):
    _command("user_start", None)
    c1 = _advance(repo, "later.txt")
    card = repo.new("b")
    prepare(card)
    assert _input(card) == c1 == repo.git("rev-parse", "HEAD")
    assert _workspace_event(card)[1]["base_since"] == 1


# -- 2, 4, 5, 6: the same window keeps one base ------------------------------------------------------------------------

def _window_with_one_base(repo):
    s1 = _command("user_start", None)
    first = repo.new("a")
    prepare(first)
    c0 = _input(first)
    _end(first)
    _advance(repo, "later.txt")
    return s1, c0


def test_parallel_start_cards_of_one_window_share_its_base(repo):
    s1, c0 = _window_with_one_base(repo)
    card = repo.new("b")
    root = prepare(card)
    assert _input(card) == c0
    assert not (root / "later.txt").exists()
    assert _workspace_event(card)[1]["base_since"] == s1


def test_an_automatic_wake_is_not_a_new_window(repo):
    _s1, c0 = _window_with_one_base(repo)
    _command("work_resumed", "idle")
    card = repo.new("b")
    prepare(card)
    assert _input(card) == c0


def test_paused_recommand_keeps_the_base_but_cuts_the_command_window(repo):
    """5a / 5c: the base window is not the command window; merging them into ``_is_command_start`` fails here."""
    s1, c0 = _window_with_one_base(repo)
    s2 = _command("user_command", "paused")
    card = repo.new("b")
    prepare(card)
    assert _input(card) == c0
    events = run_store.events("run")
    assert command_result._is_command_start(events[_index(s2)])
    window = command_result.command_window(events, _index(s1))
    assert window.cut and window.cut_index == _index(s2) and window.ended_by == "superseded"


def test_paused_resume_keeps_the_base_and_the_command_window(repo):
    s1, c0 = _window_with_one_base(repo)
    s2 = _command("user_resume", "paused")
    card = repo.new("b")
    prepare(card)
    assert _input(card) == c0
    events = run_store.events("run")
    assert not command_result._is_command_start(events[_index(s2)])
    window = command_result.command_window(events, _index(s1))
    assert not window.cut and window.ended_by is None


def test_legacy_stopped_resume_keeps_the_base(repo):
    _s1, c0 = _window_with_one_base(repo)
    _command("user_command", "stopped")
    card = repo.new("b")
    prepare(card)
    assert _input(card) == c0


def test_idle_acknowledge_keeps_the_base(repo):
    _s1, c0 = _window_with_one_base(repo)
    s2 = _command("user_command", "idle", to_status="idle")
    card = repo.new("b")
    prepare(card)
    assert _input(card) == c0
    assert command_result._is_command_start(run_store.events("run")[_index(s2)])


# -- 3: an invalidated base is not a base ----------------------------------------------------------------------------

@pytest.mark.parametrize("reason", ["time_machine", "user_stop", "worktree_failed"])
def test_only_invalidated_bases_left_start_from_group_head(repo, reason):
    _command("user_start", None)
    first = repo.new("a")
    prepare(first)
    _set_attempt(first, status="invalidated", active_node_key=None, invalidated_reason=reason)
    c1 = _advance(repo, "later.txt")
    card = repo.new("b")
    prepare(card)
    assert _input(card) == c1


# -- 7, 9e: a run with no command start keeps the run-wide base (window 0) ---------------------------------------------

def test_run_without_command_start_keeps_its_first_base_tagged_zero(repo):
    first = repo.new("a")
    prepare(first)
    c0 = _input(first)
    assert _workspace_event(first)[1]["base_since"] == 0
    _advance(repo, "later.txt")
    card = repo.new("b")
    prepare(card)
    assert _input(card) == c0


# -- 9, 9a-9c: the marker pins the window with the base -----------------------------------------------------------------

def test_recovery_after_a_new_command_keeps_its_base_out_of_the_new_window(repo):
    s1 = _command("user_start", None)
    old = repo.new("a")
    c0 = _materialize(old)
    c1 = _advance(repo, "later.txt")
    s2 = _command("user_command", "idle")
    prepare(old)
    assert _input(old) == c0
    event, data = _workspace_event(old)
    assert event["event_seq"] > s2 and data["base_since"] == s1
    card = repo.new("b")
    prepare(card)
    assert _input(card) == c1 == repo.git("rev-parse", "HEAD") != c0


def test_late_recovery_in_the_same_window_stays_its_base(repo):
    s1 = _command("user_start", None)
    first = repo.new("a")
    c0 = _materialize(first)
    _advance(repo, "later.txt")
    prepare(first)
    assert _workspace_event(first)[1]["base_since"] == s1
    card = repo.new("b")
    prepare(card)
    assert _input(card) == c0


def test_new_window_base_recorded_before_an_old_recovery_stays_the_base(repo):
    _command("user_start", None)
    old = repo.new("a")
    c0 = _materialize(old)
    c1 = _advance(repo, "later.txt")
    s2 = _command("user_command", "idle")
    card = repo.new("b")
    prepare(card)
    assert _input(card) == c1 and _workspace_event(card)[1]["base_since"] == s2
    prepare(old)
    assert _input(old) == c0
    _advance(repo, "even_later.txt")
    third = repo.new("c")
    prepare(third)
    assert _input(third) == c1


def test_old_attempt_first_prepared_in_the_new_window_is_its_base(repo):
    _command("user_start", None)
    old = repo.new("a")
    c1 = _advance(repo, "later.txt")
    s2 = _command("user_command", "idle")
    prepare(old)
    assert _input(old) == c1 and _workspace_event(old)[1]["base_since"] == s2
    _advance(repo, "even_later.txt")
    card = repo.new("b")
    prepare(card)
    assert _input(card) == c1


# -- 9d, 9d-x, 9d-y, 9d-z: events without the tag (markers made before it) ----------------------------------------------

def test_untagged_event_of_an_attempt_created_in_the_window_stays_its_base(repo):
    _command("user_start", None)  # before the attempt: its created_at is at or after the window start
    first = repo.new("a")
    c0 = _materialize(first)
    _advance(repo, "later.txt")
    prepare(first)
    _untag_event(first)
    card = repo.new("b")
    prepare(card)
    assert _input(card) == c0


def test_untagged_marker_recovered_after_a_new_command_is_not_the_new_base(repo):
    """9d-x: the acceptance scenario of a marker made before the fix (no ``base_since``)."""
    _command("user_start", None)
    old = repo.new("a")
    _set_attempt(old, created_at=OLD)
    c0 = _materialize(old)
    _untag_marker(old)
    c1 = _advance(repo, "later.txt")
    _command("user_command", "idle")
    prepare(old)
    assert _input(old) == c0 and "base_since" not in _workspace_event(old)[1]
    card = repo.new("b")
    prepare(card)
    assert _input(card) == c1 == repo.git("rev-parse", "HEAD") != c0


def test_untagged_old_attempt_first_prepared_in_the_new_window_is_not_a_base(repo):
    """9d-y: the legacy cost, fixed as such -- a later start card takes the group HEAD of its own moment."""
    _command("user_start", None)
    old = repo.new("a")
    _set_attempt(old, created_at=OLD)
    c1 = _advance(repo, "later.txt")
    _command("user_command", "idle")
    prepare(old)
    _untag_event(old)
    assert _input(old) == c1
    c2 = _advance(repo, "even_later.txt")
    card = repo.new("b")
    prepare(card)
    assert _input(card) == c2


def test_untagged_event_whose_attempt_row_is_missing_is_not_a_base(repo):
    _command("user_start", None)
    first = repo.new("a")
    prepare(first)
    event, data = _workspace_event(first)
    data.pop("base_since")
    data["attempt_id"] = "att_missing"
    run_store._affected("UPDATE graph_run_events SET payload_json = ? WHERE run_id = 'run' AND event_seq = ?",
                        [json.dumps(data, sort_keys=True), event["event_seq"]])
    c1 = _advance(repo, "later.txt")
    card = repo.new("b")
    prepare(card)
    assert _input(card) == c1


@pytest.mark.parametrize("created, since_at, expected", [
    # (i) instants, not text: '+' sorts before 'Z', so a text comparison would call the first one earlier
    ("2026-11-14T06:00:00.000+00:00", "2026-11-14T06:00:00.000Z", True),
    ("2026-11-14T05:59:59.999Z", "2026-11-14T06:00:00.000+00:00", False),
    ("2026-11-14T15:00:00.000+09:00", "2026-11-14T06:00:00.000+00:00", True),
    # (ii) the same millisecond is in the window
    ("2026-11-14T06:00:00.000+00:00", "2026-11-14T06:00:00.000+00:00", True),
    # (iii) unreadable -> not in it (the card takes the group HEAD)
    ("not a time", "2026-11-14T06:00:00.000+00:00", False),
    ("2026-11-14T06:00:00.000", "2026-11-14T06:00:00.000+00:00", False),
    ("2026-11-14T06:00:00.000+00:00", None, False),
    # (iv) no attempt row value
    (None, "2026-11-14T06:00:00.000+00:00", False),
])
def test_untagged_window_check_compares_utc_instants(created, since_at, expected):
    assert workspace._in_window({}, {"created_at": created}, 3, since_at) is expected


def test_window_tag_is_compared_as_a_number_and_zero_is_a_window():
    assert workspace._in_window({"base_since": 0}, {}, 0, None) is True
    assert workspace._in_window({"base_since": "3"}, {}, 3, None) is True
    assert workspace._in_window({"base_since": 2}, {"created_at": "2999-01-01T00:00:00.000+00:00"}, 3,
                                "2026-11-14T06:00:00.000+00:00") is False
    assert workspace._in_window({"base_since": "x"}, {}, 3, None) is False
    assert workspace._in_window({}, {}, 0, None) is True
