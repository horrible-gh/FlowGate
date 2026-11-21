"""TR work-scope validation.

Cover the review scenarios verifiable through pure functions: section parsing,
path normalization/rejection, exclusions, verdict matrix, actual change collection
using a real Git repository mixing committed/staged/unstaged/untracked/delete/rename,
and the required rejection-notice structure.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from modules.flow_gate.services import tr_scope_service as trs


# ── Reported-file list parser ──

def test_section_absent_is_not_found():
    parsed = trs.parse_reported_files("# TR\n\n본문만 있고 섹션이 없다.\n")
    assert parsed.found is False
    assert parsed.paths == []


def test_section_stops_at_next_heading():
    parsed = trs.parse_reported_files(
        "## 변경 파일\n\n- server/a.py\n\n## 다음 절\n\n- 이건 목록이지만 다른 섹션이다\n"
    )
    assert parsed.paths == ["server/a.py"]
    assert parsed.format_errors == []


# ── the earlier implementation: Markdown horizontal rule as a Changed Files section boundary ─────

def test_horizontal_rule_ends_section_before_verification_footer():
    """R1 -- the real-world symptom: a '---' separated verification footer must not be
    read as part of the changed-files list."""
    parsed = trs.parse_reported_files(
        "## Changed Files\n- server/a.py\n\n---\n"
        "**Verification Status:** \u2713 Complete\n"
        "**Verification Date:** 2026-09-04\n"
        "**System:** Automated Update Verification\n"
        "**Result:** All verification steps passed successfully\n"
    )
    assert parsed.paths == ["server/a.py"]
    assert parsed.format_errors == []


def test_horizontal_rule_ends_section_with_korean_heading():
    """R2"""
    parsed = trs.parse_reported_files("## 변경 파일\n- server/a.py\n\n---\nfooter\n")
    assert parsed.paths == ["server/a.py"]
    assert parsed.format_errors == []


def test_horizontal_rule_variant_asterisks():
    """R3"""
    parsed = trs.parse_reported_files("## Changed Files\n- server/a.py\n\n***\nfooter\n")
    assert parsed.paths == ["server/a.py"]
    assert parsed.format_errors == []


def test_horizontal_rule_variant_underscores():
    """R4"""
    parsed = trs.parse_reported_files("## Changed Files\n- server/a.py\n\n___\nfooter\n")
    assert parsed.paths == ["server/a.py"]
    assert parsed.format_errors == []


def test_next_heading_still_ends_section_as_before():
    """R5 -- unchanged behavior."""
    parsed = trs.parse_reported_files(
        "## Changed Files\n- server/a.py\n\n## Step Verification\n- not a changed file\n"
    )
    assert parsed.paths == ["server/a.py"]
    assert parsed.format_errors == []


def test_prose_without_separator_still_a_format_error():
    """R6 -- strict list validation is not weakened."""
    parsed = trs.parse_reported_files("## Changed Files\n- server/a.py\n작업 완료\n")
    assert any("작업 완료" in e for e in parsed.format_errors)


def test_files_hidden_after_separator_are_not_declared_but_still_reach_trv004(monkeypatch):
    """R7 -- a path written after the separator is outside the declared list, so it must
    still surface as TRV-004 (unreported) when the worktree comparison finds it."""
    monkeypatch.setattr(trs, "resolve_stage", lambda project_id: trs.STAGE_ENFORCE)
    monkeypatch.setattr(
        trs.git_service, "collect_scope_changes",
        lambda project_id, group_id: {
            "available": True, "reason": "worktree", "worktree": "C:/wt", "branch": "work",
            "paths": ["server/a.py", "server/b.py"],
        },
    )
    body = "## Changed Files\n- server/a.py\n---\n- server/b.py\n"
    parsed = trs.parse_reported_files(body)
    assert parsed.paths == ["server/a.py"]

    result = trs.evaluate("p", "g", body)
    assert result["unreported"] == ["server/b.py"]
    assert trs.TRV_UNREPORTED in result["codes"]


def test_none_marker_before_horizontal_rule_footer():
    """R8 -- an existing declared-none variant must keep working when followed by a
    horizontal-rule-separated footer."""
    parsed = trs.parse_reported_files("## Changed Files\nNone\n---\nfooter\n")
    assert parsed.found is True
    assert parsed.declared_none is True
    assert parsed.paths == []
    assert parsed.format_errors == []


def test_none_marker_is_an_empty_but_present_report():
    parsed = trs.parse_reported_files("## 변경 파일\n\n없음\n")
    assert parsed.found is True
    assert parsed.declared_none is True
    assert parsed.paths == []


@pytest.mark.parametrize("item", ["/etc/passwd", "C:/repo/server/a.py", "../outside.py"])
def test_absolute_and_escaping_paths_are_out_of_scope(item):
    """Only explicitly out-of-scope path forms trigger TRV-002 (N0005 Q1 = o3)."""
    parsed = trs.parse_reported_files(f"## 변경 파일\n\n- {item}\n")
    assert parsed.out_of_scope == [item]
    assert parsed.paths == []


def test_tolerated_variants_are_normalized_not_rejected():
    """Do not reject an honest report merely for harmless notation variants."""
    parsed = trs.parse_reported_files(
        "## 변경 파일\n\n"
        "- `client/b.vue` — 표시 추가\n"
        '- "server/a.py"\n'
        "- ./x/y.py\n"
        r"- server\win\path.py" "\n"
        "- server//double.py\n"
        "- server/a.py\n"  # Duplicate
    )
    assert parsed.paths == [
        "client/b.vue", "server/a.py", "x/y.py", "server/win/path.py", "server/double.py",
    ]
    assert parsed.out_of_scope == []
    assert parsed.format_errors == []


def test_prose_and_non_list_lines_are_format_errors():
    parsed = trs.parse_reported_files(
        "## 변경 파일\n\ninbox_routes 를 고쳤습니다\n- 그리고 이것도 손봤습니다\n"
    )
    assert len(parsed.format_errors) == 2


def test_item_count_cap():
    items = "\n".join(f"- server/f{i}.py" for i in range(trs.MAX_ITEMS + 1))
    parsed = trs.parse_reported_files(f"## 변경 파일\n\n{items}\n")
    assert any(str(trs.MAX_ITEMS) in e for e in parsed.format_errors)


# ── Exclusion rules (N0005 Q2 decision) ──

@pytest.mark.parametrize("path", [
    ".git/config", ".venv/lib/x.py", "server/app.db", "server/app.sqlite3",
    "client/node_modules/pkg/index.js", "server/modules/__pycache__/a.pyc", "client/dist/app.js",
    "pytest-cache-files-abcd1234/README.md",
    "server/pytest-cache-files-incomplete/CACHEDIR.TAG",
])
def test_excluded_paths(path):
    assert trs.is_excluded_path(path) is True


@pytest.mark.parametrize("path", [
    "server/modules/flow_gate/api/inbox_routes.py",
    "client/src/main/components/MainPanel.vue",
    # Keep non-root dotfiles: genuinely changed configuration files must not disappear.
    "client/src/.eslintrc.json",
])
def test_kept_paths(path):
    assert trs.is_excluded_path(path) is False


# ── Verdict matrix ──

@pytest.mark.parametrize("stage,expected", [
    (trs.STAGE_OBSERVE, trs.VERDICT_PASS),
    (trs.STAGE_WARN, trs.VERDICT_WARN),
    (trs.STAGE_ENFORCE, trs.VERDICT_REJECT),
])
@pytest.mark.parametrize("code", [
    trs.TRV_MISSING_SECTION, trs.TRV_UNCONFIRMED, trs.TRV_UNREPORTED, trs.TRV_FORMAT,
])
def test_staged_codes_follow_the_stage(code, stage, expected):
    assert trs._verdict_for([code], stage) == expected


@pytest.mark.parametrize("stage", [trs.STAGE_OBSERVE, trs.STAGE_WARN, trs.STAGE_ENFORCE])
def test_out_of_scope_always_rejects(stage):
    """TRV-002 is an explicit declaration, not an inference; reject at every stage."""
    assert trs._verdict_for([trs.TRV_OUT_OF_SCOPE], stage) == trs.VERDICT_REJECT


@pytest.mark.parametrize("stage", [trs.STAGE_OBSERVE, trs.STAGE_WARN, trs.STAGE_ENFORCE])
def test_no_scope_alone_never_rejects(stage):
    """TRV-006 reflects server conditions, not worker responsibility."""
    assert trs._verdict_for([trs.TRV_NO_SCOPE], stage) != trs.VERDICT_REJECT


def test_heaviest_disposition_wins():
    codes = [trs.TRV_NO_SCOPE, trs.TRV_FORMAT, trs.TRV_OUT_OF_SCOPE]
    assert trs._verdict_for(codes, trs.STAGE_OBSERVE) == trs.VERDICT_REJECT


def test_clean_report_passes_in_every_stage():
    for stage in (trs.STAGE_OBSERVE, trs.STAGE_WARN, trs.STAGE_ENFORCE):
        assert trs._verdict_for([], stage) == trs.VERDICT_PASS


# ── Rejection notice ──

def test_notice_carries_all_five_blocks():
    notice = trs.build_notice({
        "codes": [trs.TRV_UNCONFIRMED],
        "branch": "flowgate_default_0299",
        "worktree": "C:/storage/flowgate/src/FlowGate/flowgate_default_0299",
        "detected": ["server/x.py"],
        "unconfirmed": ["server/a.py"],
        "unreported": [],
        "out_of_scope": [],
        "format_errors": [],
    })
    assert "[1] 반려 사유" in notice
    # Most incidents stem from an unknown location; the two assigned-location lines are essential.
    assert "flowgate_default_0299" in notice
    assert "[3]" in notice and "server/x.py" in notice
    assert trs.SECTION_HEADING in notice          # Resubmission format
    assert "[5]" in notice                        # How to revert
    # Phrase this as an inference, not a certainty.
    assert "대개" in notice


def test_tr_instructions_use_a_path_placeholder_instead_of_project_files():
    notice = trs.build_notice({
        "codes": [trs.TRV_FORMAT],
        "branch": "b",
        "worktree": "w",
        "detected": [],
        "unconfirmed": [],
        "unreported": [],
        "out_of_scope": [],
        "format_errors": ["목록 형식이 아닌 줄: x"],
    })
    placeholder = "<저장소 루트 기준 상대경로. 바뀐 파일마다 한 줄씩 추가>"
    old_examples = (
        "server/modules/flow_gate/api/inbox_routes.py",
        "client/src/main/components/MainPanel.vue",
    )

    assert trs.TR_SECTION_GUIDE.count(placeholder) == 1
    assert notice.count(placeholder) == 1
    for old_example in old_examples:
        assert old_example not in trs.TR_SECTION_GUIDE
        assert old_example not in notice


def test_notice_truncates_long_lists_but_keeps_the_true_count():
    detected = [f"server/f{i}.py" for i in range(120)]
    notice = trs.build_notice({
        "codes": [trs.TRV_UNREPORTED], "branch": "b", "worktree": "w",
        "detected": detected, "unconfirmed": [], "unreported": [],
        "out_of_scope": [], "format_errors": [],
    })
    assert "전체 120건" in notice
    assert notice.count("server/f") <= trs._MAX_LISTED + 5


def test_revert_block_is_omitted_when_nothing_was_written_elsewhere():
    notice = trs.build_notice({
        "codes": [trs.TRV_FORMAT], "branch": "b", "worktree": "w",
        "detected": [], "unconfirmed": [], "unreported": [],
        "out_of_scope": [], "format_errors": ["목록 형식이 아닌 줄: x"],
    })
    assert "[5]" not in notice


# ── Locale propagation ──

import re as _re

_HANGUL = _re.compile(r"[가-힣]")
_KANA = _re.compile(r"[ぁ-んァ-ヶ]")

_SAMPLE_RESULT = {
    "codes": [trs.TRV_UNCONFIRMED, trs.TRV_UNREPORTED],
    "branch": "flowgate_default_0355",
    "worktree": "C:/storage/flowgate/src/FlowGate/flowgate_default_0355",
    "detected": ["server/x.py"],
    "unconfirmed": ["server/a.py"],
    "unreported": ["server/y.py"],
    "out_of_scope": [],
    "format_errors": [],
}


def test_notice_default_locale_is_unchanged_korean():
    """Omitting locale must preserve existing callers, including logs, exactly."""
    notice = trs.build_notice(_SAMPLE_RESULT)
    assert notice == trs.build_notice(_SAMPLE_RESULT, "ko")
    assert _HANGUL.search(notice)


def test_notice_en_has_no_korean_but_keeps_structure_and_codes():
    notice = trs.build_notice(_SAMPLE_RESULT, "en")
    assert not _HANGUL.search(notice), "en notice must not contain Korean"
    assert not _KANA.search(notice), "en notice must not contain Japanese"
    for marker in ("[1]", "[2]", "[3]", "[4]"):
        assert marker in notice
    assert trs.TRV_UNCONFIRMED in notice and trs.TRV_UNREPORTED in notice
    assert "flowgate_default_0355" in notice  # Placeholder values are not translated
    assert "server/a.py" in notice and "server/y.py" in notice
    assert trs.SECTION_HEADING_EN in notice  # The resubmission section heading is English too


def test_notice_ja_has_japanese_no_korean_and_english_grammar_heading():
    notice = trs.build_notice(_SAMPLE_RESULT, "ja")
    assert not _HANGUL.search(notice), "ja notice must not contain Korean"
    assert _KANA.search(notice), "ja notice must contain Japanese"
    assert "flowgate_default_0355" in notice
    # No Japanese alias exists; use the canonical English heading.
    assert trs.SECTION_HEADING_EN in notice
    assert trs.SECTION_HEADING not in notice


def test_notice_unknown_locale_falls_back_to_korean():
    notice = trs.build_notice(_SAMPLE_RESULT, "fr")
    assert notice == trs.build_notice(_SAMPLE_RESULT, "ko")


def test_tr_section_guide_localized_no_leak_and_keeps_placeholder_shape():
    ko = trs.tr_section_guide("ko")
    en = trs.tr_section_guide("en")
    ja = trs.tr_section_guide("ja")

    assert ko == trs.TR_SECTION_GUIDE  # Compatibility: the module constant equals the ko rendering
    assert _HANGUL.search(ko)

    assert not _HANGUL.search(en) and not _KANA.search(en)
    assert trs.SECTION_HEADING_EN in en

    assert not _HANGUL.search(ja) and _KANA.search(ja)
    assert trs.SECTION_HEADING_EN in ja and trs.SECTION_HEADING not in ja


def test_tr_section_placeholder_localized():
    ko = trs.tr_section_placeholder("ko")
    en = trs.tr_section_placeholder("en")

    assert ko == trs.TR_SECTION_PLACEHOLDER
    assert trs.SECTION_HEADING in ko and "없음" in ko

    assert trs.SECTION_HEADING_EN in en and "None" in en
    assert not _HANGUL.search(en)


def test_evaluate_threads_locale_into_rejected_notice(monkeypatch):
    """Propagate evaluate() locale to the rejection notice (T2/TR2)."""
    monkeypatch.setattr(trs, "resolve_stage", lambda project_id: trs.STAGE_ENFORCE)
    monkeypatch.setattr(
        trs.git_service, "collect_scope_changes",
        lambda project_id, group_id: {
            "available": True, "reason": "worktree",
            "worktree": "C:/wt", "branch": "work", "paths": [],
        },
    )
    result = trs.evaluate("p", "g", "## 변경 파일\n\n- /etc/passwd\n", locale="en")
    assert result["verdict"] == trs.VERDICT_REJECT
    assert not _HANGUL.search(result["notice"])
    assert "TRV-002" in result["notice"]


# ── Collect actual changes ──

def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, timeout=60
    )
    assert proc.returncode == 0, f"git {' '.join(args)} failed: {proc.stderr}"
    return proc.stdout


def _pin_recorded_floor(monkeypatch, repo: Path, floor_ref: str = "main") -> None:
    """collect_scope_changes measures from the group's RECORDED floor."""
    from modules.flow_gate.services import git_service
    from modules.flow_gate.services.git import scope_base

    floor = _git(repo, "rev-parse", floor_ref).strip()
    monkeypatch.setattr(git_service.db_git, "get_state", lambda gid: {
        "branch": "work", "work_base_sha": floor, "work_base_state": "verified",
    })
    monkeypatch.setattr(git_service.db_git, "get_config", lambda pid: {"enabled": 1, "base_branch": "main"})
    monkeypatch.setattr(git_service, "resolve_group_work_base_ref", lambda *a, **k: "main")
    monkeypatch.setattr(scope_base.db_tr_ledger, "group_commit_evidence", lambda gid: [])


@pytest.fixture()
def work_repo(tmp_path: Path) -> Path:
    if shutil.which("git") is None:
        pytest.skip("git is not available")
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "T")
    for name in ("kept.py", "edited.py", "deleted.py", "renamed_from.py"):
        (repo / name).write_text(f"# {name}\n" * 20, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    _git(repo, "checkout", "-q", "-b", "work")

    # committed on the branch
    (repo / "committed.py").write_text("x = 1\n", encoding="utf-8")
    _git(repo, "add", "committed.py")
    _git(repo, "commit", "-q", "-m", "work commit")
    # staged / unstaged / untracked / delete / rename
    (repo / "staged.py").write_text("y = 2\n", encoding="utf-8")
    _git(repo, "add", "staged.py")
    (repo / "edited.py").write_text("changed\n", encoding="utf-8")
    (repo / "untracked.py").write_text("z = 3\n", encoding="utf-8")
    _git(repo, "rm", "-q", "deleted.py")
    _git(repo, "mv", "renamed_from.py", "renamed_to.py")
    return repo


def test_collect_scope_changes_unions_every_source(work_repo, monkeypatch):
    """Combine committed, staged, unstaged, untracked, deleted, and renamed paths."""
    from modules.flow_gate.services import git_service

    monkeypatch.setattr(
        git_service, "effective_src_root_ex",
        lambda project_id, group_id: (work_repo, git_service.SRC_ROOT_WORKTREE),
    )
    _pin_recorded_floor(monkeypatch, work_repo)

    result = git_service.collect_scope_changes("p", "g")
    assert result["available"] is True
    paths = set(result["paths"])
    assert {"committed.py", "staged.py", "edited.py", "untracked.py", "deleted.py"} <= paths
    # Renames include only the new path; --no-renames breaks this.
    assert "renamed_to.py" in paths
    assert "renamed_from.py" not in paths
    assert "kept.py" not in paths


def test_collect_scope_changes_never_falls_back_to_the_base_tree(monkeypatch):
    """A missing worktree means unavailable validation, never measuring main instead."""
    from modules.flow_gate.services import git_service

    monkeypatch.setattr(
        git_service, "effective_src_root_ex",
        lambda project_id, group_id: (None, git_service.SRC_ROOT_DIR_MISSING),
    )
    result = git_service.collect_scope_changes("p", "g")
    assert result["available"] is False
    assert result["reason"] == git_service.SRC_ROOT_DIR_MISSING
    assert result["paths"] == []


def test_parse_name_status_z_stays_in_sync_across_renames():
    from modules.flow_gate.services import git_service

    stdout = "M\0a.py\0R100\0old.py\0new.py\0A\0b.py\0"
    assert git_service._parse_name_status_z(stdout) == ["a.py", "new.py", "b.py"]


# ── evaluate integration ──

def _stub_scope(monkeypatch, stage, actual_paths, available=True):
    monkeypatch.setattr(trs, "resolve_stage", lambda project_id: stage)
    monkeypatch.setattr(
        trs.git_service, "collect_scope_changes",
        lambda project_id, group_id: {
            "available": available, "reason": "worktree" if available else "worktree_dir_missing",
            "worktree": "C:/wt", "branch": "work", "paths": list(actual_paths),
        },
    )


def test_evaluate_reports_every_applicable_code_at_once(monkeypatch):
    """Reporting only one problem at a time adds round trips."""
    _stub_scope(monkeypatch, trs.STAGE_ENFORCE, ["server/real.py"])
    result = trs.evaluate("p", "g", "## 변경 파일\n\n- server/ghost.py\n- /abs/x.py\n- 산문 한 줄\n")
    assert set(result["codes"]) >= {trs.TRV_FORMAT, trs.TRV_OUT_OF_SCOPE, trs.TRV_UNCONFIRMED, trs.TRV_UNREPORTED}
    assert result["verdict"] == trs.VERDICT_REJECT
    assert result["unconfirmed"] == ["server/ghost.py"]
    assert result["unreported"] == ["server/real.py"]
    assert result["notice"]


def test_evaluate_passes_on_an_exact_match(monkeypatch):
    _stub_scope(monkeypatch, trs.STAGE_ENFORCE, ["server/a.py", "client/b.vue", ".git/index"])
    result = trs.evaluate("p", "g", "## 변경 파일\n\n- server/a.py\n- client/b.vue\n")
    assert result["codes"] == []
    assert result["verdict"] == trs.VERDICT_PASS


def test_evaluate_prior_declared_suppresses_only_old_reported_paths(monkeypatch):
    _stub_scope(
        monkeypatch,
        trs.STAGE_ENFORCE,
        ["server/prior.py", "server/current.py", "server/new_unreported.py"],
    )
    result = trs.evaluate(
        "p",
        "g",
        "## 변경 파일\n\n- server/current.py\n",
        prior_declared=["server/prior.py"],
    )

    assert result["unconfirmed"] == []
    assert result["unreported"] == ["server/new_unreported.py"]
    assert result["codes"] == [trs.TRV_UNREPORTED]


def test_evaluate_prior_declared_never_masks_current_unconfirmed(monkeypatch):
    _stub_scope(monkeypatch, trs.STAGE_ENFORCE, ["server/prior.py"])
    result = trs.evaluate(
        "p",
        "g",
        "## 변경 파일\n\n- server/ghost.py\n",
        prior_declared=["server/prior.py", "server/ghost.py"],
    )

    assert result["unconfirmed"] == ["server/ghost.py"]
    assert result["unreported"] == []
    assert result["codes"] == [trs.TRV_UNCONFIRMED]


def test_evaluate_without_prior_declared_keeps_the_original_behavior(monkeypatch):
    _stub_scope(monkeypatch, trs.STAGE_ENFORCE, ["server/prior.py", "server/current.py"])
    body = "## 변경 파일\n\n- server/current.py\n"

    omitted = trs.evaluate("p", "g", body)
    empty = trs.evaluate("p", "g", body, prior_declared=[])

    assert omitted == empty
    assert omitted["unreported"] == ["server/prior.py"]
    assert omitted["codes"] == [trs.TRV_UNREPORTED]


def test_evaluate_accepts_none_when_nothing_changed(monkeypatch):
    _stub_scope(monkeypatch, trs.STAGE_ENFORCE, [])
    result = trs.evaluate("p", "g", "## 변경 파일\n\n없음\n")
    assert result["verdict"] == trs.VERDICT_PASS


def test_evaluate_does_not_compare_when_the_worktree_is_unreadable(monkeypatch):
    """Comparing an unreadable worktree misleadingly marks every declaration TRV-003."""
    _stub_scope(monkeypatch, trs.STAGE_ENFORCE, [], available=False)
    result = trs.evaluate("p", "g", "## 변경 파일\n\n- server/a.py\n")
    assert result["codes"] == [trs.TRV_NO_SCOPE]
    assert result["unconfirmed"] == []
    assert result["verdict"] == trs.VERDICT_WARN


def test_evaluate_is_skipped_when_git_integration_is_off(monkeypatch):
    monkeypatch.setattr(trs, "resolve_stage", lambda project_id: None)
    result = trs.evaluate("p", "g", "본문에 섹션이 없다")
    assert result["verdict"] == trs.VERDICT_SKIPPED
    assert result["codes"] == []


def test_missing_section_is_only_a_warning_below_enforce(monkeypatch):
    """Already-issued work lacks this section (staged rollout rationale)."""
    _stub_scope(monkeypatch, trs.STAGE_OBSERVE, ["server/a.py"])
    result = trs.evaluate("p", "g", "# TR\n\n섹션 없는 옛 서식\n")
    assert trs.TRV_MISSING_SECTION in result["codes"]
    assert result["verdict"] == trs.VERDICT_PASS


# ── Document metadata round trip ──

def test_meta_slice_keeps_the_true_count_after_trimming():
    """Retain the true count after trimming so the UI can report it accurately."""
    from modules.flow_gate.api.inbox_routes import _TR_SCOPE_META_MAX_PATHS, _tr_scope_meta

    detected = [f"server/f{i}.py" for i in range(_TR_SCOPE_META_MAX_PATHS + 30)]
    meta = _tr_scope_meta({
        "verdict": trs.VERDICT_WARN, "stage": trs.STAGE_WARN, "codes": [trs.TRV_UNREPORTED],
        "branch": "work", "detected": detected, "reported": [],
        "notice": "이건 저장되면 안 된다",
    })
    assert meta["detected"]["count"] == len(detected)
    assert len(meta["detected"]["items"]) == _TR_SCOPE_META_MAX_PATHS
    assert meta["reported"] == {"count": 0, "items": []}
    # Notices exist only for rejected submissions, which create no document or metadata.
    assert "notice" not in meta


def test_reported_meta_keeps_more_than_display_limit_and_round_trips():
    import json as _json

    from modules.flow_gate.api.inbox_routes import (
        _TR_SCOPE_META_MAX_PATHS,
        _tr_scope_meta,
    )

    reported = [f"server/reported_{i}.py" for i in range(_TR_SCOPE_META_MAX_PATHS + 30)]
    meta = _tr_scope_meta({
        "verdict": trs.VERDICT_PASS,
        "stage": trs.STAGE_ENFORCE,
        "codes": [],
        "reported": reported,
        "detected": reported,
    })
    restored = trs.verdict_from_meta(_json.dumps({"tr_scope": meta}))

    assert restored["reported"]["count"] == len(reported)
    assert restored["reported"]["items"] == reported
    assert len(restored["detected"]["items"]) == _TR_SCOPE_META_MAX_PATHS


def test_prior_tr_declared_unions_group_meta_and_excludes_current_document(monkeypatch):
    from modules.flow_gate.api import inbox_routes

    documents = [
        {
            "doc_id": "g.0001-TR",
            "type_code": "TR",
            "meta": {"tr_scope": {"reported": {"count": 2, "items": ["a.py", "b.py"]}}},
        },
        {
            "doc_id": "g.0002-TR",
            "type_code": "TR",
            "meta": '{"tr_scope":{"reported":{"count":2,"items":["b.py","c.py"]}}}',
        },
        {
            "doc_id": "g.0003-TR",
            "type_code": "TR",
            "meta": {"tr_scope": {"reported": {"count": 1, "items": ["self.py"]}}},
        },
        {
            "doc_id": "g.0004-NR",
            "type_code": "NR",
            "meta": {"tr_scope": {"reported": {"count": 1, "items": ["ignore.py"]}}},
        },
    ]
    monkeypatch.setattr(
        inbox_routes.db_docs,
        "get_documents_by_group_id",
        lambda group_id: documents,
    )

    assert inbox_routes._prior_tr_declared("g", exclude_doc_id="g.0003-TR") == [
        "a.py",
        "b.py",
        "c.py",
    ]


def test_prior_tr_declared_includes_other_mutating_types_like_ts(monkeypatch):
    """a TS document's declared changed-files must also feed the
    prior-declared pool -- before this fix the function hard-compared type_code ==
    "TR", so a sibling TS's report was invisible and could make a later TR/TS
    submission touching the same path come back flagged "unconfirmed" even though a
    TS in the same group had already reported it. NR (non-mutating) must stay excluded.
    """
    from modules.flow_gate.api import inbox_routes

    documents = [
        {
            "doc_id": "g.0001-TR",
            "type_code": "TR",
            "meta": {"tr_scope": {"reported": {"count": 1, "items": ["a.py"]}}},
        },
        {
            "doc_id": "g.0002-TS",
            "type_code": "TS",
            "meta": {"tr_scope": {"reported": {"count": 1, "items": ["b.py"]}}},
        },
        {
            "doc_id": "g.0003-NR",
            "type_code": "NR",
            "meta": {"tr_scope": {"reported": {"count": 1, "items": ["ignore.py"]}}},
        },
    ]
    monkeypatch.setattr(
        inbox_routes.db_docs,
        "get_documents_by_group_id",
        lambda group_id: documents,
    )

    assert inbox_routes._prior_tr_declared("g") == ["a.py", "b.py"]


def test_meta_round_trips_through_verdict_from_meta():
    import json as _json

    from modules.flow_gate.api.inbox_routes import _tr_scope_meta

    meta = _tr_scope_meta({"verdict": trs.VERDICT_PASS, "stage": trs.STAGE_ENFORCE, "codes": []})
    restored = trs.verdict_from_meta(_json.dumps({"tr_scope": meta}))
    assert restored == meta


def test_verdict_from_meta_accepts_both_text_and_dict():
    assert trs.verdict_from_meta('{"tr_scope": {"verdict": "pass"}}') == {"verdict": "pass"}
    assert trs.verdict_from_meta({"tr_scope": {"verdict": "warn"}}) == {"verdict": "warn"}
    assert trs.verdict_from_meta('{"content_sha256": "x"}') is None
    assert trs.verdict_from_meta(None) is None
    assert trs.verdict_from_meta("not json") is None


# ── Sidebar for documents without stored verdicts ──
#
# meta['tr_scope'] is calculated once on submission. Earlier documents lack the key.
# If document detail omits tr_scope entirely, the sidebar hides its
# [작업범위 검증] section, making unevaluated documents indistinguishable
# from documents outside the validation scope. These tests cover that gap.

_TS_BODY_WITH_SECTION = (
    "## Summary\n\n회귀 스위트.\n\n## 변경 파일\n\n- server/tests/test_inbox.py\n"
)

# rev3: the TS body actually opened by the reviewer in the 8080 preview
# (test.test.0002.0002-TS). It predates validation and has no '## 변경 파일' section.
# rev2 produced no verdict for this input, causing the card to disappear.
_TS_BODY_NO_SECTION = (
    "---\n"
    "next_type: TS\n"
    "title: Write Feature Test Scenarios\n"
    "---\n"
    "\n"
    "## 테스트 준비\n"
    "\n"
    "- cmd: cd test && python -m pytest --version\n"
    "\n"
    "## 테스트 케이스\n"
    "\n"
    "### TC-1: Write feature produces expected message/mention\n"
    "- cmd: cd test && python -c \"print('ok')\"\n"
    "- 기대: Write feature returns success message\n"
)


def test_unevaluated_verdict_reads_the_reported_section_for_a_ts_document():
    verdict = trs.unevaluated_verdict("TS", _TS_BODY_WITH_SECTION)

    assert verdict is not None
    assert verdict["evaluated"] is False
    assert verdict["verdict"] == trs.VERDICT_SKIPPED
    assert verdict["scope_reason"] == "not_evaluated"
    assert verdict["reported"] == {"count": 1, "items": ["server/tests/test_inbox.py"]}
    # No comparison occurred: do not even return an empty detected list.
    assert "detected" not in verdict


def test_unevaluated_verdict_is_only_for_mutating_types():
    # Non-target types still hide the section because they are never validated.
    assert trs.unevaluated_verdict("NR", _TS_BODY_WITH_SECTION) is None
    assert trs.unevaluated_verdict("R", _TS_BODY_WITH_SECTION) is None
    # T lost write/patch/remove privileges and is no longer a target type.
    assert trs.unevaluated_verdict("T", "작업지시 가 승인되었습니다.") is None
    # A single '없음' line counts as a present section and produces a zero-item verdict.
    none_verdict = trs.unevaluated_verdict("TR", "## 변경 파일\n\n없음\n")
    assert none_verdict is not None and none_verdict["reported"]["count"] == 0
    assert none_verdict["scope_reason"] == "not_evaluated"


def test_unevaluated_verdict_still_shows_a_card_when_the_body_has_no_section():
    """rev3: show the card even without a section.

    rev2 returned None and hid the card, prompting the same rejection again.
    TS documents submitted before validation never received the instruction to add
    '## 변경 파일', so its absence is normal. Hiding the area in that normal state
    violates the requirement's requirement to provide scope validation in the sidebar.
    """
    for type_code, body in (
        ("TS", _TS_BODY_NO_SECTION),          # Body shape of the TS document actually opened by the reviewer
        ("TS", None),                          # The body file cannot be read
        ("TSR", ""),
    ):
        verdict = trs.unevaluated_verdict(type_code, body)
        assert verdict is not None, f"{type_code} 카드가 감춰졌다: {body!r}"
        assert verdict["evaluated"] is False
        assert verdict["verdict"] == trs.VERDICT_SKIPPED
        # The guidance must distinguish this case; referring to a reported list would mislead.
        assert verdict["scope_reason"] == "not_evaluated_no_section"
        assert verdict["reported"] == {"count": 0, "items": []}


def test_unevaluated_verdict_drops_excluded_paths_like_evaluate_does():
    verdict = trs.unevaluated_verdict(
        "TS", "## 변경 파일\n\n- server/app.py\n- .git/config\n"
    )

    assert verdict is not None
    assert verdict["reported"]["items"] == ["server/app.py"]


def _call_get_document(documents_router, doc_id: str) -> dict:
    """Call the actual route handler.

    The RBAC decorator is a no-op when rbac.decorators is unavailable (the stub
    at the top of documents.py). Use __wrapped__ when present, otherwise the
    function itself, to reach the real handler in either environment.
    """
    handler = getattr(documents_router.get_document, "__wrapped__", documents_router.get_document)
    return handler(doc_id, current_user={"user_id": "u"})


def _stub_document_detail(monkeypatch, documents_router, row: dict, body: str, tmp_path):
    """Stub surrounding dependencies while retaining the actual document-detail path.

    Bodies live in files, not a documents row or doc['content'] column.
    Exercise real path resolution with a file in tmp_path. The rev2 draft
    silently did nothing because it tried to read the body from the row.
    """
    doc_file = tmp_path / "doc.md"
    doc_file.write_text(body, encoding="utf-8")
    monkeypatch.setattr(
        documents_router.document_service, "get_document", lambda doc_id: dict(row)
    )
    monkeypatch.setattr(documents_router, "_document_file_path", lambda doc: doc_file)
    monkeypatch.setattr(documents_router, "_load_ai_reviews", lambda doc_id: (None, []))
    monkeypatch.setattr(documents_router, "_load_test_runs", lambda doc_id: (None, []))

def test_document_detail_serves_the_unevaluated_verdict_when_meta_has_none(monkeypatch, tmp_path):
    """Verify at the route that document-detail GET sends the sidebar verdict."""
    from modules.flow_gate.documents.routers import documents as documents_router

    row = {
        "doc_id": "flowgate.default.0390.0006-TS",
        "type_code": "TS",
        "meta": {"content_sha256": "x"},   # No tr_scope: submitted before validation applied
        "workflow_steps": None,
    }
    _stub_document_detail(monkeypatch, documents_router, row, _TS_BODY_WITH_SECTION, tmp_path)

    out = _call_get_document(documents_router, row["doc_id"])

    assert out["tr_scope"]["evaluated"] is False
    assert out["tr_scope"]["reported"]["items"] == ["server/tests/test_inbox.py"]


def test_document_detail_serves_a_card_for_a_ts_document_with_no_section(monkeypatch, tmp_path):
    """rev3 regression: the route supplies tr_scope for the body the reviewer opened.

    rev2 omitted tr_scope from this response, so v-if="trScope" hid the entire
    [작업범위 검증] area: the exact reason for rejection.
    """
    from modules.flow_gate.documents.routers import documents as documents_router

    row = {
        "doc_id": "test.test.0002.0002-TS",
        "type_code": "TS",
        "meta": {"content_sha256": "x"},   # No tr_scope
        "workflow_steps": None,
    }
    _stub_document_detail(monkeypatch, documents_router, row, _TS_BODY_NO_SECTION, tmp_path)

    out = _call_get_document(documents_router, row["doc_id"])

    assert "tr_scope" in out, "사이드바가 카드를 그릴 수 있는 값이 응답에 없다"
    assert out["tr_scope"]["evaluated"] is False
    assert out["tr_scope"]["scope_reason"] == "not_evaluated_no_section"
    assert out["tr_scope"]["reported"] == {"count": 0, "items": []}


def test_document_detail_still_prefers_the_stored_verdict(monkeypatch, tmp_path):
    """Prefer the verdict calculated on submission; body parsing must not replace it."""
    from modules.flow_gate.documents.routers import documents as documents_router

    stored = {"verdict": "pass", "stage": "observe", "reported": {"count": 0, "items": []}}
    row = {
        "doc_id": "flowgate.default.0390.0005-TR",
        "type_code": "TR",
        "meta": {"tr_scope": stored},
        "workflow_steps": None,
    }
    _stub_document_detail(monkeypatch, documents_router, row, _TS_BODY_WITH_SECTION, tmp_path)

    out = _call_get_document(documents_router, row["doc_id"])

    assert out["tr_scope"] == stored
    assert "evaluated" not in out["tr_scope"]


# ── the earlier implementation: per-file actual status manifest ──────────────────────────────

def test_collect_scope_changes_entries_carry_status_and_rename_old_path(work_repo, monkeypatch):
    """Reviewers need per-file status, not just a bare path list."""
    from modules.flow_gate.services import git_service

    monkeypatch.setattr(
        git_service, "effective_src_root_ex",
        lambda project_id, group_id: (work_repo, git_service.SRC_ROOT_WORKTREE),
    )
    _pin_recorded_floor(monkeypatch, work_repo)

    result = git_service.collect_scope_changes("p", "g")
    by_path = {entry["path"]: entry for entry in result["entries"]}

    # entries and paths never drift apart -- a path is never dropped for lacking status.
    assert set(by_path) == set(result["paths"])
    assert by_path["committed.py"]["status"] == "A"
    assert by_path["staged.py"]["status"] == "A"
    assert by_path["untracked.py"]["status"] == "A"
    assert by_path["edited.py"]["status"] == "M"
    assert by_path["edited.py"]["old_path"] is None
    assert by_path["deleted.py"]["status"] == "D"
    assert by_path["renamed_to.py"]["status"] == "R"
    assert by_path["renamed_to.py"]["old_path"] == "renamed_from.py"


def test_evaluate_file_manifest_marks_reported_prior_reported_and_unreported_exclusively(monkeypatch):
    """reporting_state is mutually exclusive; a current declaration always wins over a
    prior one, and prior-reported must never explain away this body's own unconfirmed
    report (acceptance criteria)."""
    monkeypatch.setattr(trs, "resolve_stage", lambda project_id: trs.STAGE_ENFORCE)
    monkeypatch.setattr(
        trs.git_service, "collect_scope_changes",
        lambda project_id, group_id: {
            "available": True, "reason": "worktree", "worktree": "C:/wt", "branch": "work",
            "paths": ["server/a.py", "server/b.py", "server/c.py"],
            "entries": [
                {"path": "server/a.py", "status": "M", "old_path": None},
                {"path": "server/b.py", "status": "A", "old_path": None},
                {"path": "server/c.py", "status": "D", "old_path": None},
            ],
        },
    )
    # a.py: reported now AND previously declared -- current wins ("reported").
    # b.py: only previously declared -- "prior-reported".
    # c.py: neither -- "unreported" (TRV-004).
    body = "## 변경 파일\n\n- server/a.py\n- server/ghost.py\n"
    result = trs.evaluate("p", "g", body, prior_declared=["server/a.py", "server/b.py"])

    manifest = {row["path"]: row for row in result["file_manifest"]}
    assert manifest["server/a.py"]["reporting_state"] == "reported"
    assert manifest["server/a.py"]["reported_in_current_tr"] is True
    assert manifest["server/a.py"]["reported_in_prior_tr"] is True
    assert manifest["server/b.py"]["reporting_state"] == "prior-reported"
    assert manifest["server/b.py"]["reported_in_current_tr"] is False
    assert manifest["server/c.py"]["reporting_state"] == "unreported"
    assert manifest["server/c.py"]["actual_status"] == "D"

    states = {row["reporting_state"] for row in result["file_manifest"]}
    assert states == {"reported", "prior-reported", "unreported"}

    # server/ghost.py was declared but never actually changed -- unconfirmed, and the
    # prior-declared pool (which does not name it) must not hide that.
    assert result["unconfirmed"] == ["server/ghost.py"]
    assert "server/ghost.py" not in manifest
    assert trs.TRV_UNREPORTED in result["codes"]
    assert trs.TRV_UNCONFIRMED in result["codes"]


# ── the earlier implementation: Graph attempt basis ────────────────────────────

def _forbid_group_measurement(monkeypatch):
    """An attempt basis must never read the group worktree or its recorded floor."""
    from modules.flow_gate.services import git_service

    def _refuse(*_a, **_k):
        raise AssertionError("group worktree must not be measured for a Graph attempt")

    monkeypatch.setattr(git_service, "collect_scope_changes", _refuse)
    monkeypatch.setattr(git_service, "effective_src_root_ex", _refuse)
    monkeypatch.setattr(git_service, "resolve_scope_floor", _refuse)


def _raise(exc):
    def _call(*_a, **_k):
        raise exc
    return _call


def test_collect_scope_changes_at_measures_explicit_root_from_explicit_floor(work_repo, monkeypatch):
    """Modified / new / rename on an explicit tree from an explicit floor, no group lookup."""
    from modules.flow_gate.services import git_service

    _forbid_group_measurement(monkeypatch)
    floor = _git(work_repo, "rev-parse", "main").strip()

    result = git_service.collect_scope_changes_at(work_repo, floor)
    by_path = {entry["path"]: entry for entry in result["entries"]}
    assert result["available"] is True
    assert result["reason"] == git_service.SRC_ROOT_WORKTREE
    assert result["worktree"] == str(work_repo)
    assert result["branch"] == "work"
    assert result["scope_base_sha"] == floor
    assert result["work_base_ref"] is None
    assert set(by_path) == set(result["paths"])
    assert by_path["edited.py"]["status"] == "M"
    assert by_path["untracked.py"]["status"] == "A"
    assert by_path["committed.py"]["status"] == "A"
    assert by_path["renamed_to.py"]["status"] == "R"
    assert by_path["renamed_to.py"]["old_path"] == "renamed_from.py"
    assert "renamed_from.py" not in by_path and "kept.py" not in by_path


def test_collect_scope_changes_at_floor_is_the_given_commit_not_the_group_floor(work_repo, monkeypatch):
    """A later floor (the attempt's input_commit) leaves the earlier commit out."""
    from modules.flow_gate.services import git_service

    _forbid_group_measurement(monkeypatch)
    head = _git(work_repo, "rev-parse", "HEAD").strip()
    result = git_service.collect_scope_changes_at(work_repo, head)
    assert result["available"] is True
    assert "committed.py" not in result["paths"]
    assert {"staged.py", "edited.py", "untracked.py", "deleted.py", "renamed_to.py"} <= set(result["paths"])


@pytest.mark.parametrize("case", ["no_root", "no_floor", "missing_dir", "unknown_floor"])
def test_collect_scope_changes_at_is_unavailable_never_a_partial_answer(work_repo, tmp_path, monkeypatch, case):
    from modules.flow_gate.services import git_service

    _forbid_group_measurement(monkeypatch)
    root, floor = work_repo, _git(work_repo, "rev-parse", "main").strip()
    if case == "no_root":
        root = None
    elif case == "no_floor":
        floor = None
    elif case == "missing_dir":
        root = tmp_path / "gone"
    else:
        floor = "0" * 40
    result = git_service.collect_scope_changes_at(root, floor)
    assert result["available"] is False
    assert result["paths"] == [] and result["entries"] == []


def test_evaluate_attempt_scope_detects_attempt_changes_without_trv003(work_repo, monkeypatch):
    """The 0109 deadlock: honest declarations of attempt-tree changes are confirmed, not TRV-003."""
    _forbid_group_measurement(monkeypatch)
    monkeypatch.setattr(trs, "resolve_stage", lambda project_id: trs.STAGE_ENFORCE)
    floor = _git(work_repo, "rev-parse", "main").strip()
    body = (
        "## 변경 파일\n\n- committed.py\n- staged.py\n- edited.py\n- untracked.py\n"
        "- deleted.py\n- renamed_to.py\n"
    )
    scope = {"attempt_id": "att_x", "root": work_repo, "input_commit": floor, "input_source": "run_base"}

    result = trs.evaluate("p", "g", body, attempt_scope=scope)
    assert result["codes"] == []
    assert result["verdict"] == trs.VERDICT_PASS
    assert result["unconfirmed"] == [] and result["unreported"] == []
    assert set(result["detected"]) == {
        "committed.py", "staged.py", "edited.py", "untracked.py", "deleted.py", "renamed_to.py"}
    assert result["scope_source"] == trs.SCOPE_SOURCE_GRAPH_ATTEMPT
    assert result["attempt_id"] == "att_x"
    assert result["input_commit"] == floor
    assert result["scope_base_sha"] == floor
    assert result["branch"] == "work"
    manifest = {row["path"]: row for row in result["file_manifest"]}
    assert manifest["renamed_to.py"]["actual_status"] == "R"
    assert manifest["renamed_to.py"]["old_path"] == "renamed_from.py"


@pytest.mark.parametrize("scope", [
    {"attempt_id": "att_x", "root": None, "input_commit": None,
     "error": {"code": "graph_worktree_unavailable", "reason": "attempt_not_active"}},
    {"attempt_id": "att_x", "root": None, "input_commit": "a" * 40},
    {"attempt_id": "att_x", "root": "C:/attempt/tree", "input_commit": None},
])
def test_evaluate_unresolved_attempt_is_trv006_never_group_fallback(monkeypatch, scope):
    _forbid_group_measurement(monkeypatch)
    monkeypatch.setattr(trs.git_service, "collect_scope_changes_at",
                        _raise(AssertionError("an unresolved attempt must not be measured at all")))
    monkeypatch.setattr(trs, "resolve_stage", lambda project_id: trs.STAGE_ENFORCE)

    result = trs.evaluate("p", "g", "## 변경 파일\n\n- server/a.py\n", attempt_scope=scope)
    assert result["codes"] == [trs.TRV_NO_SCOPE]
    assert result["unconfirmed"] == [] and result["detected"] == []
    assert result["verdict"] == trs.VERDICT_WARN
    assert result["scope_source"] == trs.SCOPE_SOURCE_GRAPH_ATTEMPT
    assert result["attempt_id"] == "att_x"
    assert result["attempt_error"]["code"]
    assert result["scope_reason"] == result["attempt_error"]["code"]


def test_evaluate_without_attempt_scope_keeps_group_basis(monkeypatch):
    _stub_scope(monkeypatch, trs.STAGE_ENFORCE, ["server/a.py"])
    result = trs.evaluate("p", "g", "## 변경 파일\n\n- server/a.py\n")
    assert result["verdict"] == trs.VERDICT_PASS
    assert result["scope_source"] == trs.SCOPE_SOURCE_GROUP
    assert result["attempt_id"] is None and result["input_commit"] is None
    assert "attempt_error" not in result


def test_resolve_attempt_scope_unmapped_mapped_and_failures(monkeypatch):
    from modules.flow_gate.services import graph_source_hooks as hooks

    token = {"token_id": "t", "project": "p", "group_id": "g"}
    monkeypatch.setattr(hooks, "binding", lambda tok: None)
    monkeypatch.setattr(hooks, "attempt_scope",
                        _raise(AssertionError("unmapped token must not resolve an attempt")))
    assert trs.resolve_attempt_scope(token, "p", "g") is None

    monkeypatch.setattr(hooks, "binding", lambda tok: "att_x")
    resolved = {"attempt_id": "att_x", "root": "C:/tree", "input_commit": "c" * 40, "input_source": "predecessors"}
    seen = {}

    def _scope(tok, *, project_id=None, group_id=None):
        seen.update(project_id=project_id, group_id=group_id)
        return resolved

    monkeypatch.setattr(hooks, "attempt_scope", _scope)
    assert trs.resolve_attempt_scope(token, "p", "g") is resolved
    assert seen == {"project_id": "p", "group_id": "g"}

    monkeypatch.setattr(hooks, "attempt_scope", _raise(
        hooks.GraphSourceError("graph_worktree_unavailable", reason="input_mismatch")))
    failed = trs.resolve_attempt_scope(token, "p", "g")
    assert failed["attempt_id"] == "att_x" and failed["root"] is None
    assert failed["error"] == {"code": "graph_worktree_unavailable", "reason": "input_mismatch"}

    monkeypatch.setattr(hooks, "attempt_scope", _raise(RuntimeError("db")))
    assert trs.resolve_attempt_scope(token, "p", "g")["error"]["code"] == trs.ATTEMPT_RESOLUTION_ERROR

    monkeypatch.setattr(hooks, "binding", _raise(RuntimeError("db down")))
    unknown = trs.resolve_attempt_scope(token, "p", "g")
    assert unknown["error"] == {"code": trs.ATTEMPT_RESOLUTION_ERROR, "reason": "binding_lookup_failed"}


def test_meta_carries_scope_source_attempt_and_input_commit():
    from modules.flow_gate.api.inbox_routes import _tr_scope_meta

    meta = _tr_scope_meta({
        "verdict": trs.VERDICT_PASS, "stage": trs.STAGE_OBSERVE, "codes": [],
        "scope_source": trs.SCOPE_SOURCE_GRAPH_ATTEMPT, "attempt_id": "att_x", "input_commit": "d" * 40,
    })
    assert meta["scope_source"] == trs.SCOPE_SOURCE_GRAPH_ATTEMPT
    assert meta["attempt_id"] == "att_x"
    assert meta["input_commit"] == "d" * 40
    assert trs.verdict_from_meta({"tr_scope": meta})["scope_source"] == trs.SCOPE_SOURCE_GRAPH_ATTEMPT
