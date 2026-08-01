"""TR 작업범위 검증 (flowgate.default.0299 — R0001 → D0004 → NR0006 → T0007).

NR0006 §5 의 검증 시나리오 중 순수 함수로 확정 가능한 것을 덮는다: 섹션 파싱,
경로 정규화/거부, 제외 규칙, 판정 매트릭스, 실제 변경 수집(진짜 git 저장소를 만들어
committed/staged/unstaged/untracked/delete/rename 을 모두 섞는다), 그리고 반려
안내문의 필수 구성.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from modules.flow_gate.services import tr_scope_service as trs


# ── 신고목록 파서 (D0004 §3.2) ────────────────────────────────────────────────

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


def test_none_marker_is_an_empty_but_present_report():
    parsed = trs.parse_reported_files("## 변경 파일\n\n없음\n")
    assert parsed.found is True
    assert parsed.declared_none is True
    assert parsed.paths == []


@pytest.mark.parametrize("item", ["/etc/passwd", "C:/repo/server/a.py", "../outside.py"])
def test_absolute_and_escaping_paths_are_out_of_scope(item):
    """표기 자체가 범위 밖임을 자백하는 형식만 TRV-002 (N0005 Q1 = o3)."""
    parsed = trs.parse_reported_files(f"## 변경 파일\n\n- {item}\n")
    assert parsed.out_of_scope == [item]
    assert parsed.paths == []


def test_tolerated_variants_are_normalized_not_rejected():
    """정직하게 신고한 작업자가 표기 변형 때문에 반려되면 안 된다."""
    parsed = trs.parse_reported_files(
        "## 변경 파일\n\n"
        "- `client/b.vue` — 표시 추가\n"
        '- "server/a.py"\n'
        "- ./x/y.py\n"
        r"- server\win\path.py" "\n"
        "- server//double.py\n"
        "- server/a.py\n"  # 중복
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


# ── 제외 규칙 (D0004 §3.3 / N0005 Q2 확정) ───────────────────────────────────

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
    # 최상위가 아닌 점 항목은 살린다 — 정말 고친 설정 파일이 조용히 사라지면 안 된다.
    "client/src/.eslintrc.json",
])
def test_kept_paths(path):
    assert trs.is_excluded_path(path) is False


# ── 판정 매트릭스 (D0004 §3.7) ───────────────────────────────────────────────

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
    """TRV-002 는 추리가 아니라 자백이므로 단계와 무관하게 거부한다."""
    assert trs._verdict_for([trs.TRV_OUT_OF_SCOPE], stage) == trs.VERDICT_REJECT


@pytest.mark.parametrize("stage", [trs.STAGE_OBSERVE, trs.STAGE_WARN, trs.STAGE_ENFORCE])
def test_no_scope_alone_never_rejects(stage):
    """TRV-006 은 서버 사정이므로 작업자 책임으로 돌리지 않는다."""
    assert trs._verdict_for([trs.TRV_NO_SCOPE], stage) != trs.VERDICT_REJECT


def test_heaviest_disposition_wins():
    codes = [trs.TRV_NO_SCOPE, trs.TRV_FORMAT, trs.TRV_OUT_OF_SCOPE]
    assert trs._verdict_for(codes, trs.STAGE_OBSERVE) == trs.VERDICT_REJECT


def test_clean_report_passes_in_every_stage():
    for stage in (trs.STAGE_OBSERVE, trs.STAGE_WARN, trs.STAGE_ENFORCE):
        assert trs._verdict_for([], stage) == trs.VERDICT_PASS


# ── 반려 안내문 (D0004 §3.8) ─────────────────────────────────────────────────

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
    # 자기 위치를 몰라서 생기는 사고가 대부분이므로 배정 위치 두 줄이 핵심이다.
    assert "flowgate_default_0299" in notice
    assert "[3]" in notice and "server/x.py" in notice
    assert trs.SECTION_HEADING in notice          # 재제출 형식
    assert "[5]" in notice                        # 되돌리는 법
    # 어조는 단정이 아니라 추정 (D0004 §3.8).
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


# ── 언어 전달 (0355 T2/TR2, NR0003 §1-4) ─────────────────────────────────────

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
    """locale 인자를 안 주면 기존 호출부(로그 등)와 완전히 같아야 한다."""
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
    assert "flowgate_default_0355" in notice  # 자리표시자 값은 번역하지 않는다
    assert "server/a.py" in notice and "server/y.py" in notice
    assert trs.SECTION_HEADING_EN in notice  # 재제출 안내의 구역 제목도 영어


def test_notice_ja_has_japanese_no_korean_and_english_grammar_heading():
    notice = trs.build_notice(_SAMPLE_RESULT, "ja")
    assert not _HANGUL.search(notice), "ja notice must not contain Korean"
    assert _KANA.search(notice), "ja notice must contain Japanese"
    assert "flowgate_default_0355" in notice
    # 일본어 별칭은 만들지 않았으므로(0355 T0009) 구역 제목은 영어 정식 표기로 안내한다.
    assert trs.SECTION_HEADING_EN in notice
    assert trs.SECTION_HEADING not in notice


def test_notice_unknown_locale_falls_back_to_korean():
    notice = trs.build_notice(_SAMPLE_RESULT, "fr")
    assert notice == trs.build_notice(_SAMPLE_RESULT, "ko")


def test_tr_section_guide_localized_no_leak_and_keeps_placeholder_shape():
    ko = trs.tr_section_guide("ko")
    en = trs.tr_section_guide("en")
    ja = trs.tr_section_guide("ja")

    assert ko == trs.TR_SECTION_GUIDE  # 하위호환: 모듈 상수는 ko 렌더링과 같다
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
    """evaluate() 의 locale 인자가 반려 안내문까지 전달되는지 (T2/TR2)."""
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


# ── 실제 변경 수집 (D0004 §3.3) ──────────────────────────────────────────────

def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, timeout=60
    )
    assert proc.returncode == 0, f"git {' '.join(args)} failed: {proc.stderr}"
    return proc.stdout


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
    """committed + staged + unstaged + untracked + delete + rename 을 한 번에."""
    from modules.flow_gate.services import git_service

    monkeypatch.setattr(
        git_service, "effective_src_root_ex",
        lambda project_id, group_id: (work_repo, git_service.SRC_ROOT_WORKTREE),
    )
    monkeypatch.setattr(git_service.db_git, "get_state", lambda gid: {"branch": "work"})
    monkeypatch.setattr(git_service.db_git, "get_config", lambda pid: {"base_branch": "main"})

    result = git_service.collect_scope_changes("p", "g")
    assert result["available"] is True
    paths = set(result["paths"])
    assert {"committed.py", "staged.py", "edited.py", "untracked.py", "deleted.py"} <= paths
    # 이름 변경은 바뀐 뒤 경로만 (D0004 §3.2). --no-renames 를 쓰면 여기서 깨진다.
    assert "renamed_to.py" in paths
    assert "renamed_from.py" not in paths
    assert "kept.py" not in paths


def test_collect_scope_changes_never_falls_back_to_the_base_tree(monkeypatch):
    """워크트리를 못 찾으면 판정 불가지, main 을 대신 재는 것이 아니다."""
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


# ── evaluate() 통합 (D0004 §3.4) ─────────────────────────────────────────────

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
    """한 번에 하나씩 알려주면 왕복만 늘어난다 (D0004 §3.4)."""
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
    """워크트리를 못 보는 상태로 대조하면 신고 전부가 TRV-003 으로 찍혀 오도한다."""
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
    """이미 발급되어 돌고 있는 작업들에는 섹션이 없다 (D0004 §3.6 단계 도입 근거)."""
    _stub_scope(monkeypatch, trs.STAGE_OBSERVE, ["server/a.py"])
    result = trs.evaluate("p", "g", "# TR\n\n섹션 없는 옛 서식\n")
    assert trs.TRV_MISSING_SECTION in result["codes"]
    assert result["verdict"] == trs.VERDICT_PASS


# ── 문서 meta 왕복 ───────────────────────────────────────────────────────────

def test_meta_slice_keeps_the_true_count_after_trimming():
    """화면은 "n건"을 정직하게 써야 하므로 잘린 뒤에도 진짜 개수가 남아야 한다."""
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
    # notice 는 거부일 때만 존재하고 거부에는 문서가 없다 — meta 에 실릴 일이 없다.
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
