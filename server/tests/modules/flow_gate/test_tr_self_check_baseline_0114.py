"""flowgate.v02.0114 T#3: result block summary and baseline judgement on synthetic record pairs.

D 0006 9.2-5 / 9.2-6: the judge is fed two stored run rows (baseline, after) built here, the
after-run's pre-change anchor and an ancestry answer. Each case changes one thing and checks
that "reusable" is refused with the right reason, or that the failure split is right. The
real pytest side (the block the plugin prints) is pinned by
tests/architecture/test_selfcheck_evidence_0114.py; the anchor store by
test_tr_self_check_anchor_0114.py.
"""
from __future__ import annotations

import copy
import hashlib
import json

import pytest

from modules.flow_gate.services import tr_self_check_baseline as baseline
from modules.flow_gate.services import tr_self_check_result as result_block

EMPTY = hashlib.sha256(b"").hexdigest()
DIRTY = hashlib.sha256(b" M product.py\0").hexdigest()
A, B, C = "a" * 40, "b" * 40, "c" * 40
RUN_DEF = "1" * 64
ANCHOR = {"anchor_id": "anc_1", "state": "recorded", "base_commit": A, "source": "token_issue", "reason": None}
ENV = {
    "tools": {"python": "CPython 3.12.1", "pytest": "8.3.4", "xdist": "3.6.1", "git": "git version 2.45.1"},
    "host": {"os": "Windows", "platform": "win32",
             "capabilities": {"git": True, "symlinks": True, "api_source_tree": True, "run_bat": False}},
    "env": {"SYSTEMROOT": "C:\\Windows", "WINDIR": "C:\\Windows", "PATHEXT": ".COM;.EXE", "LANG": "ko_KR.UTF-8",
            "LC_ALL": None, "LC_CTYPE": None, "JAVA_HOME": None, "DOTNET_ROOT": None, "GOROOT": None},
    "dependencies": {"count": 120, "sha256": "d" * 64},
    "workers": {"mode": "serial", "applied": 0, "dist": "no", "cpu_limit": None},
}


def _fp(name: str) -> str:
    return hashlib.sha256(name.encode()).hexdigest()


def _block(failures, defs=None, *, run=RUN_DEF, collected=10, env=ENV, **over) -> dict:
    defs = {n: _fp(n) for n in failures} if defs is None else defs
    block = {
        "schema": result_block.SCHEMA, "exitstatus": 1 if failures else 0,
        "collected": collected, "completed": collected,
        "outcomes": {"passed": collected - len(failures), "failed": len(failures), "error": 0,
                     "skipped": 0, "xfailed": 0, "xpassed": 0},
        "failures": [{"nodeid": n, "outcome": "failed"} for n in sorted(failures)],
        "failures_total": len(failures), "truncated": False, "complete": True,
        "environment": env,
        "definitions": {"run": run, "run_status": "ok" if run else "missing", "scope": "all", "items": defs},
    }
    block.update(over)
    return block


def _tail(block: dict, *, prefix: str = "=== 3 failed, 7 passed in 1.0s ===\n") -> str:
    return f"{prefix}{result_block.BEGIN_MARKER}\n{json.dumps(block)}\n{result_block.END_MARKER}\n"


_AUTO = object()


def _row(run_id, failures=(), *, defs=None, head=A, head_after=None, clean=True, status="completed",
         timed_out=False, host="node-1", block=None, exit_code=_AUTO, **over) -> dict:
    """A stored run row as the service writes it, with the summary made by the real parser."""
    block = _block(list(failures), defs) if block is None else block
    exit_code = (1 if failures else 0) if exit_code is _AUTO else exit_code
    summary, environment = result_block.summarize(_tail(block), exit_code)
    row = {
        "self_check_run_id": run_id, "project_id": "p", "group_id": "g", "tr_doc_id": "tr_1",
        "status": status, "timed_out": timed_out, "cancel_requested": False, "recovery_state": "none",
        "error_code": None, "exit_code": exit_code, "program": "pytest", "args": ["-q", "tests"],
        "args_json": '["-q", "tests"]', "cwd_relative": "server", "policy_version": "tr-selfcheck-v1",
        "source_head_before": head, "source_head_after": head_after or head,
        "source_status_hash_before": EMPTY if clean else DIRTY,
        "source_status_hash_after": EMPTY if clean else DIRTY,
        "source_index_hash_before": EMPTY, "source_index_hash_after": EMPTY,
        "source_changed_during_run": False, "worktree_state_changed": False,
        "result_summary": result_block.dumps(summary), "environment_evidence": result_block.dumps(environment),
        "host_node_key": host,
    }
    row.update(over)
    return row


def _judge(base, after, *, anchor=ANCHOR, ancestor=True):
    return baseline.judge(base, after, anchor=anchor, is_ancestor=lambda commit, head: ancestor)


def _codes(result) -> set:
    return {r["code"] for r in result["reasons"]}


def _categories(result, code) -> set:
    return {r.get("category") for r in result["reasons"] if r["code"] == code}


OLD, NEW = "tests/test_x.py::test_old", "tests/test_x.py::test_new"


# ── 9.2-5 result block summary ─────────────────────────────────────────────

def test_5a_a_normal_block_is_summarized_with_environment_and_definitions():
    summary, env = result_block.summarize(_tail(_block([OLD])), 1)
    assert summary["status"] == result_block.COMPLETE
    assert summary["failures"] == [{"nodeid": OLD, "outcome": "failed"}]
    assert summary["definitions"]["items"] == {OLD: _fp(OLD)} and summary["definitions"]["run"] == RUN_DEF
    assert set(env) == set(result_block.ENV_CATEGORIES) and env == ENV


def test_5b_a_block_after_a_very_long_failure_report_is_found_in_the_kept_tail():
    failures = [f"tests/test_many.py::test_{i:03d}" for i in range(116)]
    report = "".join(f"_____ test_{i:03d} _____\n" + ("E   assert 1 == 2\n" * 40) for i in range(116))
    stdout = _tail(_block(failures, collected=116), prefix=report)
    kept = stdout.encode("utf-8")[-64 * 1024:].decode("utf-8", errors="ignore")   # what Self-check stores
    assert len(stdout.encode()) > 64 * 1024
    summary, _env = result_block.summarize(kept, 1)
    assert summary["status"] == result_block.COMPLETE and summary["failures_total"] == 116


_REASON_IDS = {"no_block", "end_missing", "begin_missing", "multiple_blocks", "trailing_output", "unparsable",
               "malformed:schema", "malformed:failures", "malformed:collected", "truncated", "not_complete"}


@pytest.mark.parametrize("tail, reason", [
    ("=== 1 passed ===\n", "no_block"),
    (_tail(_block([OLD])).replace(result_block.END_MARKER, ""), "end_missing"),
    (_tail(_block([OLD]))[_tail(_block([OLD])).index("{"):], "begin_missing"),
    (_tail(_block([OLD])) + _tail(_block([])), "multiple_blocks"),
    (_tail(_block([OLD])) + "late output\n", "trailing_output"),
    (f"{result_block.BEGIN_MARKER}\n{{not json\n{result_block.END_MARKER}\n", "unparsable"),
    (_tail({**_block([OLD]), "schema": "other/1"}), "malformed:schema"),
    (_tail({**_block([OLD]), "failures": [{"nodeid": OLD, "outcome": "passed"}]}), "malformed:failures"),
    (_tail({**_block([OLD]), "collected": -1}), "malformed:collected"),
    (_tail(_block([OLD], truncated=True)), "truncated"),
    (_tail(_block([OLD], completed=9)), "not_complete"),
    (_tail(_block([OLD], complete=False)), "not_complete"),
], ids=lambda value: value if value in _REASON_IDS else "")
def test_5d_a_missing_broken_cut_or_repeated_block_is_no_or_incomplete_structured_result(tail, reason):
    summary, _env = result_block.summarize(tail, 1)
    assert summary["status"] == (result_block.NONE if reason == "no_block" else result_block.INCOMPLETE)
    assert summary["reason"] == reason


def test_5d_an_oversize_block_is_incomplete():
    huge = _block([OLD], definitions={"run": RUN_DEF, "run_status": "ok", "scope": "all",
                                      "items": {f"t::{i}": _fp(str(i)) for i in range(600)}})
    summary, env = result_block.summarize(_tail(huge), 1)
    assert (summary["status"], summary["reason"], env) == (result_block.INCOMPLETE, "oversize", None)


@pytest.mark.parametrize("failures, exit_code", [([OLD], 0), ([], 1), ([], None)])
def test_5e_exit_code_and_block_disagreeing_is_a_mismatch_and_nothing_is_promoted(failures, exit_code):
    summary, _env = result_block.summarize(_tail(_block(failures)), exit_code)
    assert summary["status"] == result_block.MISMATCH and summary["mismatch"] is True
    # A mismatched run can be neither side of a comparison.
    base = _row("b", failures, exit_code=exit_code)
    after = _row("a", [OLD], clean=False)
    assert "baseline_summary_mismatch" in _codes(_judge(base, after))
    assert "after_run_summary_mismatch" in _codes(_judge(_row("b", [OLD]), _row("a", failures, exit_code=exit_code)))


def _counts(**over) -> dict:
    return {**{"passed": 9, "failed": 1, "error": 0, "skipped": 0, "xfailed": 0, "xpassed": 0}, **over}


@pytest.mark.parametrize("over, exit_code, reason, mismatch", [
    # rej_01M67VSC9EK69SR8 P2 (1): a valid failure block whose exitstatus says "internal error"
    ({"exitstatus": 3}, 1, "abnormal_exitstatus", True),
    ({"exitstatus": 2}, 2, "abnormal_exitstatus", False),            # interrupted, even if the codes agree
    ({"exitstatus": 5, "failures": [], "failures_total": 0, "outcomes": _counts(passed=10, failed=0)},
     5, "abnormal_exitstatus", True),
    # rej_01M67VSC9EK69SR8 P2 (2): one failed outcome but no failure listed, exitstatus 0, exit code 0
    ({"failures": [], "failures_total": 0, "exitstatus": 0}, 0, "inconsistent:failures_vs_outcomes", False),
    ({"outcomes": _counts(passed=10, failed=0)}, 1, "inconsistent:failures_vs_outcomes", False),
    ({"failures": [{"nodeid": OLD, "outcome": "error"}]}, 1, "inconsistent:failure_entries_vs_outcomes", False),
    ({"outcomes": _counts(passed=20)}, 1, "inconsistent:outcomes_vs_completed", False),
    ({"outcomes": _counts(passed=2)}, 1, "inconsistent:outcomes_vs_completed", False),
])
def test_5d_5e_an_exitstatus_or_count_contradiction_is_never_complete(over, exit_code, reason, mismatch):
    block = {**_block([OLD]), **over}
    summary, _env = result_block.summarize(_tail(block), exit_code)
    assert (summary["status"], summary["reason"], summary["mismatch"]) == (result_block.INCOMPLETE, reason, mismatch)
    # neither side of a comparison
    bad = _row("bad", [OLD], block=block, exit_code=exit_code)
    assert "baseline_summary_incomplete" in _codes(_judge(bad, _row("final", [OLD], clean=False)))
    after = _judge(_row("base", [OLD]), {**bad, "self_check_run_id": "final"})
    assert after["reusable"] is False and "after_run_summary_incomplete" in _codes(after)


@pytest.mark.parametrize("exitstatus", ["1", True, -1, 1.0, None, 256])
def test_5d_a_malformed_exitstatus_is_incomplete(exitstatus):
    block = {**_block([OLD]), "exitstatus": exitstatus}
    if exitstatus is None:
        del block["exitstatus"]
    summary, _env = result_block.summarize(_tail(block), 1)
    assert (summary["status"], summary["reason"]) == (result_block.INCOMPLETE, "malformed:exitstatus")


def test_5e_a_block_exitstatus_differing_from_the_process_exit_code_is_a_mismatch():
    summary, _env = result_block.summarize(_tail(_block([OLD])), 2)     # block 1, process 2
    assert (summary["status"], summary["mismatch"]) == (result_block.MISMATCH, True)
    assert result_block.summarize(_tail(_block([OLD])), 1)[0]["status"] == result_block.COMPLETE


def test_5f_the_summary_never_changes_the_run_verdict_fields():
    row = _row("r", [OLD])
    before = {k: row[k] for k in ("status", "exit_code", "timed_out")}
    result_block.summarize(_tail(_block([])), row["exit_code"])     # a block claiming "no failures"
    assert {k: row[k] for k in ("status", "exit_code", "timed_out")} == before


# ── 9.2-6 baseline safety ─────────────────────────────────────────────────

def test_6a_before_and_after_commit_both_final_runs_compare_with_the_first_baseline():
    base = _row("base", [OLD])                                     # HEAD A, clean
    before_commit = _row("final1", [OLD, NEW], clean=False)        # HEAD A, uncommitted change
    after_commit = _row("final2", [OLD, NEW], head=B)              # HEAD B (A's descendant)
    for after in (before_commit, after_commit):
        result = _judge(base, after)
        assert result["reusable"] is True, result["reasons"]
        assert result["comparison"]["new_failures"] == [NEW]
        assert result["comparison"]["existing_failures"] == [OLD]
        assert result["anchor"]["base_commit"] == A


def test_6b_an_after_change_run_named_as_baseline_is_refused_and_its_failures_are_not_existing():
    after_change = _row("final1", [OLD, NEW], head=B)             # ran on post-change content
    later = _row("final2", [OLD, NEW], head=B)
    result = _judge(after_change, later)
    assert result["reusable"] is False and "head_mismatch" in _codes(result)
    assert result["comparison"] is None
    dirty_base = _row("dirty", [OLD, NEW], clean=False)            # same HEAD, changes uncommitted
    assert "dirty_worktree" in _codes(_judge(dirty_base, later))


def test_6c_rework_keeps_comparing_with_the_first_baseline_through_the_inherited_anchor():
    base = _row("base", [OLD])
    rework = _row("rework", [OLD, NEW], head=C)                     # HEAD already holds earlier changes
    result = _judge(base, rework, anchor=ANCHOR)                    # the rework token's inherited anchor
    assert result["reusable"] is True and result["anchor"]["anchor_id"] == "anc_1"


@pytest.mark.parametrize("anchor", [None, {**ANCHOR, "state": "none", "base_commit": None, "reason": "dirty_worktree"},
                                    {**ANCHOR, "state": "none", "base_commit": None, "reason": "probe_failed"}])
def test_6e_no_anchor_means_no_reuse_even_for_a_clean_first_run_after_commit(anchor):
    first_clean_run = _row("first", [OLD, NEW], head=B)             # committed change, clean tree, first run
    after = _row("final", [OLD, NEW], head=B)
    result = _judge(first_clean_run, after, anchor=anchor)
    assert result["reusable"] is False and "anchor_missing" in _codes(result)
    assert result["comparison"] is None


def test_6h_an_anchor_that_is_not_an_ancestor_of_head_is_refused():
    result = _judge(_row("base", [OLD]), _row("final", [OLD], head=C), ancestor=False)
    assert result["reusable"] is False and "anchor_not_ancestor" in _codes(result)
    unknown = _judge(_row("base", [OLD]), _row("final", [OLD], head=C), ancestor=None)
    assert "ancestry_unverified" in _codes(unknown)


def test_6h_a_graph_attempt_run_is_judged_on_its_input_commit():
    from modules.flow_gate.services import tr_self_check_anchor as anchors
    attempt_run = {"attempt_id": "att_1", "input_commit": A, "tr_doc_id": "tr_1"}
    anchor = anchors.resolve_for_run(attempt_run)
    assert (anchor["source"], anchor["state"], anchor["base_commit"]) == ("graph_input_commit", "recorded", A)
    assert _judge(_row("base", [OLD]), _row("final", [OLD], clean=False), anchor=anchor)["reusable"] is True
    missing = anchors.resolve_for_run({"attempt_id": "att_1", "input_commit": None})
    assert _judge(_row("base", [OLD]), _row("final", [OLD]), anchor=missing)["reusable"] is False


def _env_with(category, value):
    env = copy.deepcopy(ENV)
    env[category] = value
    return env


_ONE_CATEGORY_DIFFERS = [
    ("host", _env_with("host", {**ENV["host"], "capabilities": {**ENV["host"]["capabilities"], "git": False}})),
    ("host", _env_with("host", {**ENV["host"], "capabilities": {**ENV["host"]["capabilities"], "symlinks": False}})),
    ("env", _env_with("env", {**ENV["env"], "LANG": "C.UTF-8"})),
    ("dependencies", _env_with("dependencies", {"count": 120, "sha256": "e" * 64})),
    ("workers", _env_with("workers", {"mode": "xdist", "applied": 2, "dist": "loadfile", "cpu_limit": None})),
]


@pytest.mark.parametrize("category, env", _ONE_CATEGORY_DIFFERS)
def test_6i_one_differing_environment_category_with_equal_tool_versions_refuses_reuse(category, env):
    assert env["tools"] == ENV["tools"]
    after = _row("final", [OLD], block=_block([OLD], env=env))
    result = _judge(_row("base", [OLD]), after)
    assert result["reusable"] is False
    assert _categories(result, "environment_mismatch") == {category}


def test_6i_same_auto_request_but_a_different_applied_worker_count_refuses_reuse():
    two = _env_with("workers", {"mode": "xdist", "applied": 2, "dist": "loadfile", "cpu_limit": None})
    four = _env_with("workers", {"mode": "xdist", "applied": 4, "dist": "loadfile", "cpu_limit": None})
    base = _row("base", [OLD], block=_block([OLD], env=two), args=["-n", "auto", "--dist", "loadfile"])
    after = _row("final", [OLD], block=_block([OLD], env=four), args=["-n", "auto", "--dist", "loadfile"])
    assert _categories(_judge(base, after), "environment_mismatch") == {"workers"}


def test_6i_a_different_host_identifier_refuses_reuse():
    result = _judge(_row("base", [OLD], host="node-1"), _row("final", [OLD], host="node-2"))
    assert _categories(result, "environment_mismatch") == {baseline.HOST_CATEGORY}


@pytest.mark.parametrize("category", result_block.ENV_CATEGORIES)
def test_6j_a_block_missing_one_environment_category_is_missing_evidence_not_same(category):
    env = {k: v for k, v in ENV.items() if k != category}
    for base, after in ((_row("base", [OLD], block=_block([OLD], env=env)), _row("final", [OLD])),
                        (_row("base", [OLD]), _row("final", [OLD], block=_block([OLD], env=env)))):
        result = _judge(base, after)
        assert result["reusable"] is False and _categories(result, "environment_missing") == {category}


def _without(category, key):
    env = copy.deepcopy(ENV)
    del env[category][key]
    return env


_SUB_ITEM_LOST = [
    # rej_01M67VSC9EK69SR8 P2: env reduced to {"LANG": None} on both sides
    ("env", _env_with("env", {"LANG": None})),
    ("env", _without("env", "SYSTEMROOT")),
    ("env", _env_with("env", {**ENV["env"], "EXTRA_KEY": "x"})),
    ("env", _env_with("env", {**ENV["env"], "LANG": 1})),
    ("tools", _without("tools", "git")),
    ("tools", _env_with("tools", {**ENV["tools"], "pytest": ""})),
    ("host", _without("host", "platform")),
    ("host", _env_with("host", {**ENV["host"], "capabilities": {}})),
    ("dependencies", _without("dependencies", "count")),
    ("dependencies", _env_with("dependencies", {"count": 0, "sha256": "d" * 64})),
    ("workers", _without("workers", "dist")),
    ("workers", _env_with("workers", {"mode": "serial", "applied": 2, "dist": "no", "cpu_limit": None})),
    ("workers", _env_with("workers", {"mode": "xdist", "applied": 0, "dist": "loadfile", "cpu_limit": None})),
    ("workers", _env_with("workers", {"mode": "xdist", "applied": 2, "dist": "no", "cpu_limit": None})),
    ("workers", _env_with("workers", {"mode": "serial", "applied": 0, "dist": "no", "cpu_limit": 0})),
]


@pytest.mark.parametrize("category, env", _SUB_ITEM_LOST)
def test_6j_the_same_sub_item_lost_on_both_sides_is_unreadable_never_same(category, env):
    summary, parsed = result_block.summarize(_tail(_block([OLD], env=env)), 1)
    assert summary["status"] == result_block.COMPLETE and parsed[category] == result_block.UNREADABLE
    result = _judge(_row("base", [OLD], block=_block([OLD], env=env)), _row("final", [OLD], block=_block([OLD], env=env)))
    assert result["reusable"] is False and result["comparison"] is None
    assert _categories(result, "environment_unreadable") == {category}


@pytest.mark.parametrize("category, env", _SUB_ITEM_LOST)
def test_6j_stored_evidence_is_rechecked_by_the_judge(category, env):
    """Rows whose stored evidence a laxer summary let through are still refused."""
    stored = result_block.dumps(env)
    result = _judge(_row("base", [OLD], environment_evidence=stored), _row("final", [OLD], environment_evidence=stored))
    assert result["reusable"] is False and _categories(result, "environment_unreadable") == {category}


def test_6j_every_host_key_is_required_with_its_value_or_none():
    from modules.flow_gate.services.tr_self_check_policy import HOST_KEYS
    assert set(ENV["env"]) == set(HOST_KEYS)
    assert result_block.check_category("env", {key: None for key in HOST_KEYS}) == {key: None for key in HOST_KEYS}
    assert result_block.check_category("env", {key: "" for key in HOST_KEYS}) == {key: "" for key in HOST_KEYS}
    for key in HOST_KEYS:
        lost = {k: None for k in HOST_KEYS if k != key}
        assert result_block.check_category("env", lost) == result_block.UNREADABLE, key


def test_6j_a_malformed_category_is_unreadable_and_refused():
    bad = _env_with("dependencies", {"count": "many", "sha256": "x"})
    result = _judge(_row("base", [OLD], block=_block([OLD], env=bad)), _row("final", [OLD]))
    assert _categories(result, "environment_unreadable") == {"dependencies"}


def test_6j_a_run_from_before_the_evidence_existed_is_missing_evidence():
    legacy = _row("legacy", [OLD], host=None, environment_evidence=None)
    result = _judge(legacy, _row("final", [OLD]))
    assert result["reusable"] is False
    assert _categories(result, "environment_missing") == set(result_block.ENV_CATEGORIES) | {baseline.HOST_CATEGORY}
    no_summary = _row("legacy2", [OLD], result_summary=None, environment_evidence=None, host_node_key=None)
    assert "baseline_summary_none" in _codes(_judge(no_summary, _row("final", [OLD])))


def test_6k_all_categories_equal_is_reusable_and_differs_from_a_one_category_pair():
    same = _judge(_row("base", [OLD]), _row("final", [OLD]))
    other = _judge(_row("base", [OLD]), _row("final", [OLD], block=_block([OLD], env=_ONE_CATEGORY_DIFFERS[2][1])))
    assert same["reusable"] is True and not same["reasons"]
    assert other["reusable"] is False


OTHER = "tests/test_y.py::test_other_old"


def test_6l_only_the_test_body_changed_is_a_definition_changed_failure():
    base = _row("base", [OLD, OTHER])
    after = _row("final", [OLD, OTHER], clean=False, defs={OLD: _fp("body edited"), OTHER: _fp(OTHER)})
    result = _judge(base, after)
    assert result["reusable"] is True
    assert result["comparison"]["existing_failures"] == [OTHER]
    assert result["comparison"]["definition_changed_failures"] == [{"nodeid": OLD, "definition": "changed"}]


def test_6m_fixture_change_hits_its_users_only_and_conftest_or_settings_change_hits_the_whole_baseline():
    uses = "tests/test_x.py::test_uses_fixture"
    base = _row("base", [uses, OTHER])
    after = _row("final", [uses, OTHER], clean=False, defs={uses: _fp("fixture edited"), OTHER: _fp(OTHER)})
    comparison = _judge(base, after)["comparison"]
    assert comparison["definition_changed_failures"] == [{"nodeid": uses, "definition": "changed"}]
    assert comparison["existing_failures"] == [OTHER]
    whole = _judge(base, _row("final", [uses, OTHER], block=_block([uses, OTHER], run="2" * 64)))
    assert whole["reusable"] is False and "definition_run_mismatch" in _codes(whole)


@pytest.mark.parametrize("after_defs, state", [
    ({}, "missing"),
    ({OLD: result_block.UNTRACEABLE}, "untraceable"),
])
def test_6n_missing_or_untraceable_item_evidence_is_never_existing(after_defs, state):
    result = _judge(_row("base", [OLD]), _row("final", [OLD], defs=after_defs))
    assert result["comparison"]["existing_failures"] == []
    assert result["comparison"]["definition_changed_failures"] == [{"nodeid": OLD, "definition": state}]


def test_6n_malformed_or_absent_definition_evidence_refuses_the_whole_baseline():
    broken = _block([OLD], definitions={"run": RUN_DEF, "run_status": "ok", "scope": "all", "items": {OLD: "zz"}})
    for base in (_row("base", [OLD], block=broken),
                 _row("base", [OLD], block={k: v for k, v in _block([OLD]).items() if k != "definitions"}),
                 _row("base", [OLD], block=_block([OLD], run=None))):
        result = _judge(base, _row("final", [OLD]))
        assert result["reusable"] is False
        assert _codes(result) & {"definition_run_missing", "definition_run_unverifiable"}


def test_6n_a_failed_only_scope_still_compares_failures_but_not_vanished_items():
    block = _block([OLD], definitions={"run": RUN_DEF, "run_status": "ok", "scope": "failed_only",
                                       "items": {OLD: _fp(OLD)}})
    gone = "tests/test_x.py::test_fixed"
    result = _judge(_row("base", [OLD, gone]), _row("final", [OLD], block=block))
    assert result["comparison"]["existing_failures"] == [OLD]
    assert result["comparison"]["resolved_or_gone"] == [{"nodeid": gone, "definition": "missing"}]


def test_6o_new_and_renamed_tests_are_new_failures_and_unchanged_items_stay_existing():
    renamed_old, renamed_new = "tests/test_x.py::test_before", "tests/test_x.py::test_after"
    added = "tests/test_added.py::test_brand_new"
    base = _row("base", [OLD, renamed_old])
    after = _row("final", [OLD, renamed_new, added], clean=False)
    comparison = _judge(base, after)["comparison"]
    assert comparison["existing_failures"] == [OLD]
    assert comparison["new_failures"] == sorted([renamed_new, added])
    assert comparison["resolved_or_gone"] == [{"nodeid": renamed_old, "definition": "missing"}]


def test_6o_a_product_only_change_keeps_definitions_equal_and_failures_existing():
    result = _judge(_row("base", [OLD]), _row("final", [OLD], clean=False))   # same definition evidence
    assert result["comparison"]["existing_failures"] == [OLD]


@pytest.mark.parametrize("what", ["module_global_RUN_FIELDS", "helper_get_settings", "data_file"])
def test_6p_only_a_directly_referenced_input_changed_is_a_definition_changed_failure(what):
    used = "tests/flowgate/test_fg_card_settings.py::test_card_kinds_and_their_settings_follow_the_v7_blocks"
    elsewhere = "tests/other/test_unrelated.py::test_known_failure"
    base = _row("base", [used, elsewhere], defs={used: _fp(used), elsewhere: _fp(elsewhere)})
    after = _row("final", [used, elsewhere], clean=False, defs={used: _fp(used + what), elsewhere: _fp(elsewhere)})
    comparison = _judge(base, after)["comparison"]
    assert comparison["definition_changed_failures"] == [{"nodeid": used, "definition": "changed"}]
    assert comparison["existing_failures"] == [elsewhere]


def test_6q_untraceable_module_items_are_never_existing_and_another_tests_body_is_no_input():
    a1, a2 = "tests/test_dyn.py::test_one", "tests/test_dyn.py::test_two"
    base = _row("base", [a1, a2], defs={a1: result_block.UNTRACEABLE, a2: result_block.UNTRACEABLE})
    after = _row("final", [a1, a2], defs={a1: result_block.UNTRACEABLE, a2: result_block.UNTRACEABLE})
    comparison = _judge(base, after)["comparison"]
    assert comparison["existing_failures"] == []
    assert {c["definition"] for c in comparison["definition_changed_failures"]} == {"untraceable"}
    # another test's body change leaves this item's evidence equal (pinned on real runs in
    # test_selfcheck_evidence_0114.py) -> still existing
    assert _judge(_row("base", [a1], defs={a1: _fp(a1)}),
                  _row("final", [a1], defs={a1: _fp(a1)}))["comparison"]["existing_failures"] == [a1]


def test_6r_cut_or_pre_evidence_definition_evidence_is_missing_never_same():
    cut = _block([OLD, OTHER], truncated=True)
    assert "baseline_summary_incomplete" in _codes(_judge(_row("base", [OLD, OTHER], block=cut), _row("final", [OLD])))
    legacy = {k: v for k, v in _block([OLD]).items() if k != "definitions"}
    result = _judge(_row("base", [OLD], block=legacy), _row("final", [OLD]))
    assert result["reusable"] is False and "definition_run_missing" in _codes(result)


@pytest.mark.parametrize("change, code", [
    ({"status": "failed", "error_code": "selfcheck_internal_error"}, "baseline_not_completed"),
    ({"status": "cancelled"}, "baseline_not_completed"),
    ({"timed_out": True}, "baseline_timed_out"),
    ({"recovery_state": "recovered"}, "baseline_recovery_state"),
    ({"source_changed_during_run": True}, "baseline_source_changed"),
    ({"worktree_state_changed": True}, "baseline_source_changed"),
    ({"args": ["-q", "tests", "-k", "x"]}, "command_mismatch"),
    ({"cwd_relative": "."}, "command_mismatch"),
    ({"policy_version": "tr-selfcheck-v2"}, "command_mismatch"),
    ({"group_id": "other"}, "different_group"),
])
def test_6_each_broken_reuse_condition_is_refused_and_existing_failures_are_never_counted_as_passes(change, code):
    base = _row("base", [OLD], **change)
    result = _judge(base, _row("final", [OLD]))
    assert result["reusable"] is False and code in _codes(result)
    assert result["comparison"] is None
    assert result["advisory"] is True


def test_6_the_judgement_is_read_only():
    base, after = _row("base", [OLD]), _row("final", [OLD, NEW], clean=False)
    frozen = copy.deepcopy((base, after))
    _judge(base, after)
    assert (base, after) == frozen
