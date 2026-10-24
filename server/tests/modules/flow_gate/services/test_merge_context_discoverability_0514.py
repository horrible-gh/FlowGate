"""Merge context discoverability regressions -- group 0514 T0004.

0514.0003-NR found that a separate Merge Context Tool is unnecessary: `merge_preview`
already returns merge_base/head/target_sha/merge_tree, and `read/grep/glob/stat` already
accept `ref` to read either the current worktree (ref omitted) or a committed Git tree
(ref supplied). What was missing was discoverability -- the help text and API provider
tool descriptions did not spell out that the two combine into a 5-way merge-context
recipe. 0514.0004-T asks only for help/description text to close that gap, with no new
remote operation and no change to existing backend contracts. These tests pin the
resulting text so a future edit cannot silently drop the recipe or blur the ref
semantics, without touching `merge_preview`/`read`/`grep`/`glob`/`stat` behavior itself
(already covered by test_remote_merge_preview_0524.py,
test_remote_conflict_session_root_0478.py and test_remote_tool_read_lines_0507.py).
"""
from __future__ import annotations

from modules.flow_gate.services import api_server_tools, tool_registry

LOCALES = ("ko", "ja", "en")


# ── TC-1: merge_preview help carries the 5-way recipe in one block ─────────────────

def test_merge_preview_caution_names_all_five_merge_context_angles():
    for locale in LOCALES:
        blob = " ".join(tool_registry.CAUTIONS["merge_preview"][locale])
        for token in ("head", "target_sha", "merge_base", "merge_tree"):
            assert token in blob, f"{locale} missing {token}"
        # worktree angle: read() without a ref, spelled out as such in each locale.
        assert "read(path)" in blob


def test_merge_preview_caution_shows_the_read_ref_call_shape_for_each_angle():
    for locale in LOCALES:
        blob = " ".join(tool_registry.CAUTIONS["merge_preview"][locale])
        assert "ref=head" in blob
        assert "ref=target_sha" in blob
        assert "ref=merge_base" in blob
        assert "ref=merge_tree" in blob


def test_merge_preview_caution_says_grep_glob_stat_take_the_same_ref():
    for locale in LOCALES:
        blob = " ".join(tool_registry.CAUTIONS["merge_preview"][locale])
        assert "grep" in blob and "glob" in blob and "stat" in blob


# ── TC-2: ref semantics stay unambiguous (working tree vs committed tree) ──────────

def test_ref_field_description_keeps_working_tree_vs_committed_tree_distinction():
    for name in ("read", "grep", "glob", "stat"):
        for locale in LOCALES:
            fields = tool_registry._request_fields(name, locale)
            ref_field = next(f for f in fields if f["name"] == "ref")
            assert ref_field["required"] is False
            assert ref_field["default"] is None
            description = ref_field["description"]
            # "omitted" side must still mean the live working tree with uncommitted changes...
            assert "working tree" in description
            assert ("uncommitted" in description or "미커밋" in description or "未commit" in description)
            # ...and it must connect the field to merge_preview's own return values.
            assert "merge_preview" in description
            for key in ("head", "target_sha", "merge_base", "merge_tree"):
                assert key in description


# ── TC-3: API provider descriptions go beyond name.replace("_", " ") ───────────────

def test_read_source_file_and_merge_preview_source_descriptions_are_not_bare_name_replacement():
    for name in ("read_source_file", "merge_preview_source"):
        description = api_server_tools.DESCRIPTIONS[name]
        assert description != name.replace("_", " ")
        assert "ref" in description
        assert "merge_preview" in description or name == "merge_preview_source"


def test_ref_capable_source_tool_descriptions_all_mention_merge_preview_link():
    for name in ("read_source_file", "search_source", "glob_source", "stat_source"):
        description = api_server_tools.DESCRIPTIONS[name]
        assert description != name.replace("_", " ")
        assert "merge_preview_source" in description
        assert "ref" in description


def test_merge_preview_source_description_lists_all_four_returned_values():
    description = api_server_tools.DESCRIPTIONS["merge_preview_source"]
    for key in ("merge_base", "head", "target_sha", "merge_tree"):
        assert key in description


def test_merge_preview_source_description_does_not_claim_ref_reaches_the_worktree():
    # T0004 §3.3/TC-3: the four returned values are committed-tree refs only. The
    # worktree (with uncommitted changes) is reached by omitting ref, never by passing
    # one of merge_preview_source's return values -- the description must not blur that.
    description = api_server_tools.DESCRIPTIONS["merge_preview_source"]
    assert "omit ref" in description.lower()
    assert "worktree" in description.lower()
    # The old phrasing listed "the worktree" among things reachable by passing the
    # returned values as ref; guard against that specific regression.
    assert "inspect the worktree, head" not in description.lower()


# ── TC-4: alias -> operation mapping is unchanged by the description work ─────────

def test_source_alias_to_operation_mapping_is_unchanged():
    assert api_server_tools.SOURCE_OPS == {
        "read_source_file": "read",
        "search_source": "grep",
        "glob_source": "glob",
        "stat_source": "stat",
        "diff_source": "diff",
        "log_source": "log",
        "show_commit_source": "show",
        "merge_preview_source": "merge_preview",
        "patch_source_file": "patch",
        "write_source_file": "write",
        "remove_source_file": "remove",
    }


def test_no_new_merge_context_operation_was_added():
    from modules.flow_gate.services import remote_tool_service

    forbidden = {"get_merge_context", "list_merge_context", "merge_context"}
    assert forbidden.isdisjoint(remote_tool_service.OPS)
    assert forbidden.isdisjoint(tool_registry.DISPLAY_ORDER)
    assert forbidden.isdisjoint(api_server_tools.SOURCE_NAMES)
