"""0132 NR §4.1 / §5.3-4,5: push classification precedence and bounded, scrubbed evidence."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from flowgate.gate_decisions import git_adapter as git, redact  # noqa: E402

NON_FF = "! [rejected] abc -> main (non-fast-forward)\nhint: a pushed branch tip is behind its remote"


@pytest.fixture
def push_case(monkeypatch, tmp_path):
    state = {"rc": 1, "stderr": NON_FF, "head": "remote", "contains": False, "ancestor": False,
             "fetch": True, "exists": True, "reads": 0, "calls": []}
    slot = SimpleNamespace(base_root=tmp_path, group_id="isolated", project_id="p", secrets=("private-value",),
                           credentials=lambda: {})

    def run(args, **kwargs):
        state["calls"].append((args, kwargs))
        return subprocess.CompletedProcess(args, state["rc"], "", state["stderr"])

    def head(*args):
        state["reads"] += 1
        return state["head"]

    monkeypatch.setattr(git, "_gs", lambda: SimpleNamespace(_run_git=run, GIT_NET_TIMEOUT_SEC=30))
    monkeypatch.setattr(git, "_sync_origin", lambda slot: True)
    monkeypatch.setattr(git, "remote_head", head)
    monkeypatch.setattr(git, "commit_exists", lambda *args: state["exists"])
    monkeypatch.setattr(git, "fetch_branch", lambda *args: state["fetch"])
    monkeypatch.setattr(git, "is_ancestor", lambda root, a, b: state["contains"] if a == "sent" else state["ancestor"])
    return slot, state


@pytest.mark.parametrize("text,code", [
    *[(mark, "push_auth_failed") for mark in ("authentication failed", "permission denied", "access denied",
                                             "could not read username", "HTTP 401", "error: 403")],
    *[(mark, "push_protected") for mark in ("[remote rejected]", "protected branch", "GH006",
                                           "pre-receive hook declined", "hook declined", "not allowed to push",
                                           "push declined")],
    ("repository not found", "push_remote_missing"),
    ("does not appear to be a git repository", "push_remote_missing"),
    ("! [rejected] sent -> main (unspecified)", "push_rejected"),
    ("authorization policy refused", "push_rejected"),
    ("permission policy refused", "push_rejected"),
    ("! [rejected] abc401def403 -> main (unknown)", "push_rejected"),
])
@pytest.mark.parametrize("diverged", [False, True])
def test_explicit_rejection_precedes_graph(push_case, text, code, diverged):
    slot, state = push_case
    state.update(stderr=text + ("\n" + NON_FF if diverged and code != "push_rejected" else ""),
                 ancestor=not diverged)
    outcome, actual, evidence = git.push(slot, "sent", "main")
    assert (outcome, actual) == (git.PUSH_REJECTED, code)
    assert evidence["exit_code"] == 1 and state["reads"] == 1


@pytest.mark.parametrize("hint", ["fetch first", "non-fast-forward",
    "updates were rejected because the remote contains work", "tip of your current branch is behind",
    "a pushed branch tip is behind its remote"])
def test_every_approved_non_ff_hint_and_real_git_spacing(push_case, hint):
    slot, state = push_case
    state["stderr"] = " !\t[rejected] sent -> main\nhint: " + hint
    assert git.push(slot, "sent", "main")[:2] == (git.PUSH_REJECTED, "push_remote_ahead")
    assert state["reads"] == 1  # containment and non-ff use the same h1


@pytest.mark.parametrize("stderr,head,ancestor,code", [
    ("non-fast-forward", "remote", False, "push_rejected"),
    ("! [rejected] unknown", "remote", False, "push_rejected"),
    (NON_FF, "remote", True, "push_rejected"),
    (NON_FF, "remote", None, "push_rejected"),
    (NON_FF, "", False, "push_rejected"),
    ("! [remote rejected] non-fast-forward", "remote", False, "push_protected"),
    (NON_FF + "\nGH006 authentication failed repository not found", "remote", False, "push_auth_failed"),
    (NON_FF + "\nGH006 repository not found", "remote", False, "push_protected"),
    (NON_FF + "\nrepository not found", "remote", False, "push_remote_missing"),
])
def test_non_ff_requires_all_three_conditions_and_first_matching_reason(push_case, stderr, head, ancestor, code):
    slot, state = push_case
    state.update(stderr=stderr, head=head, ancestor=ancestor)
    assert git.push(slot, "sent", "main")[:2] == (git.PUSH_REJECTED, code)


@pytest.mark.parametrize("change,outcome,code", [
    ({"rc": 0}, git.PUSHED, "pushed"),
    ({"rc": -1, "contains": True}, git.PUSHED, "push_landed_after_error"),
    ({"head": "sent"}, git.PUSHED, "push_landed_after_error"),
    ({"head": git.READ_ERROR}, git.PUSH_UNKNOWN, "push_result_unknown"),
    ({"contains": None}, git.PUSH_UNKNOWN, "push_result_unknown"),
    ({"exists": False, "fetch": False}, git.PUSH_UNKNOWN, "push_result_unknown"),
    ({"rc": -1, "stderr": "authentication failed"}, git.PUSH_TRANSIENT, "push_network_error"),
    ({"stderr": "connection refused\nauthentication failed"}, git.PUSH_TRANSIENT, "push_network_error"),
])
def test_existing_landed_unknown_and_transient_precedence(push_case, change, outcome, code):
    slot, state = push_case
    state.update(change)
    actual, reason, evidence = git.push(slot, "sent", "main")
    assert (actual, reason) == (outcome, code)
    assert evidence["exit_code"] == state["rc"]


def test_tail_and_logs_scrub_secrets_before_truncation(push_case, caplog):
    slot, state = push_case
    raw = ("prefix\n" * 20 + "https://name:password123@example.test/repo\n"
           "Authorization: Bearer header-secret\ntoken=query-secret\nprivate-value\n"
           "ghp_ABCDEFGHIJKLMNOPQRSTUVWX\nlast diagnostic")
    state["stderr"] = raw
    evidence = git.push(slot, "sent", "main")[2]
    assert evidence["diagnostic_tail"].endswith("last diagnostic")
    assert "prefix" not in evidence["diagnostic_tail"]
    assert len(evidence["diagnostic_tail"]) <= redact.TEXT_MAX
    for secret in ("password123", "header-secret", "query-secret", "private-value", "ghp_ABCDEFGHIJKLMNOPQRSTUVWX"):
        assert secret not in str(evidence) + caplog.text


def test_origin_sync_failure_has_no_invented_command_evidence(push_case, monkeypatch):
    slot, state = push_case
    monkeypatch.setattr(git, "_sync_origin", lambda slot: False)
    assert git.push(slot, "sent", "main") == (git.PUSH_TRANSIENT, "origin_sync_failed", {})
    assert not state["calls"]


def test_fixed_locale_reaches_git_child_without_changing_parent(push_case, monkeypatch):
    from modules.flow_gate.services.git import command
    slot, state = push_case
    monkeypatch.setenv("LC_ALL", "ko_KR.UTF-8")
    monkeypatch.setenv("LANGUAGE", "ja:ko")
    seen = []

    def child(args, **kwargs):
        seen.append(kwargs["env"])
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(command.subprocess, "run", child)
    monkeypatch.setattr(git, "_gs", lambda: SimpleNamespace(_run_git=command._run_git, GIT_NET_TIMEOUT_SEC=30))
    assert git.push(slot, "sent", "main")[:2] == (git.PUSHED, "pushed")
    assert seen[0]["LC_ALL"] == seen[0]["LANGUAGE"] == "C"
    assert os.environ["LC_ALL"] == "ko_KR.UTF-8" and os.environ["LANGUAGE"] == "ja:ko"
