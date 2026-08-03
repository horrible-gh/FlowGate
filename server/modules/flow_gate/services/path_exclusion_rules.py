"""도구가 남긴 흔적 판정 — 화면과 검사가 공유하는 하나의 규칙 (0382 NR0003 제안 3).

0382 B0001 의 사고는 규칙이 두 벌이어서 생겼다. 화면(파일 탐색기·변경 목록·최종승인)은
"경로의 어느 구간이든 점으로 시작하면 감춘다"를 썼고, 제출 검사(tr_scope_service)는
"맨 앞 구간만 점인지 본다"를 썼다. 그래서 ``server/.test-tmp-0313/...`` 261개가
**화면에는 한 줄도 안 뜨는데 제출은 막는** 상태가 됐고, 아무도 못 본 채 마무리 커밋에
실려 main 으로 들어갔다.

그래서 판정 코드를 이 한 곳으로 모은다. "tr_scope_service.is_excluded_path 를 정본으로
삼는다"는 NR 의 결론은 이름 그대로 유지된다 — 그 이름은 여기를 재수출할 뿐이고,
git_service 의 화면 필터도 같은 함수를 부른다.

판정은 네 부류다(0299 D0004 §3.3 을 이어받고 0382 에서 하나를 더한다).

1) 최상위에서 점(.)으로 시작하는 항목 — ``.git/``, ``.venv/``, ``.env`` 등.
2) **경로 중간의 점 디렉터리** (0382 추가) — ``server/.test-tmp-0313/...`` 이 여기 걸린다.
   마지막 구간(파일 이름)은 일부러 걸지 않는다. ``client/src/.eslintrc.json`` 처럼 정말
   고친 설정 파일까지 조용히 사라지면 안 되기 때문이다(0299 의 원래 판단을 유지).
3) 도구가 만드는 디렉터리 이름·접두어 — ``node_modules``, ``.pytest_cache``,
   ``.test-tmp-0313`` 등.
4) 실행하면 생기는 파일 확장자 — ``*.db``, ``*.pyc``, ``*.log`` 등.

프로젝트별 설정은 두지 않는다. 이 목록은 "도구가 남기는 흔적"의 목록이고, 이걸
프로젝트마다 다르게 만들면 검증의 기준선 자체가 프로젝트마다 달라져 판정을 서로
비교할 수 없게 된다. 늘려야 할 것이 생기면 여기에 추가한다.
"""
from __future__ import annotations

from typing import Iterable, Optional

# ── 사유 (화면에 "왜 뺐는지" 한 줄로 보여주기 위한 분류) ──────────────────────
REASON_DOT_TOPLEVEL = "dot_toplevel"
REASON_DOT_DIRECTORY = "dot_directory"
REASON_TOOL_DIRECTORY = "tool_directory"
REASON_GENERATED_FILE = "generated_file"
REASON_EMPTY = "empty_path"

EXCLUSION_REASONS = (
    REASON_DOT_TOPLEVEL,
    REASON_DOT_DIRECTORY,
    REASON_TOOL_DIRECTORY,
    REASON_GENERATED_FILE,
    REASON_EMPTY,
)

# 0382: 테스트가 저장소 안에 만들어 온 스크래치 디렉터리의 이름 접두어. 테스트 쪽
# 기본값은 저장소 밖으로 옮겼지만(제안 2-a), 이미 만들어진 것과 남의 체크아웃에서
# 흘러드는 것을 화면·검사·마무리가 똑같이 알아보게 여기에도 남긴다.
TEST_SCRATCH_PREFIX = ".test-tmp"

_EXCLUDED_SUFFIXES = (".db", ".sqlite", ".sqlite3", ".pyc", ".pyo", ".log")
_EXCLUDED_DIR_SEGMENTS = frozenset({
    "node_modules", "__pycache__", "dist", "build", ".venv", "venv",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", "htmlcov", ".tox", "site-packages",
})
_EXCLUDED_DIR_PREFIXES = ("pytest-cache-files-", TEST_SCRATCH_PREFIX)


def normalize_repo_path(path: str) -> str:
    """비교 전에 빈 구간과 현재-디렉터리(``.``) 구간을 없앤 저장소 상대 경로."""
    return "/".join(
        segment
        for segment in path.replace("\\", "/").split("/")
        if segment not in ("", ".")
    )


def exclusion_reason(path: str) -> Optional[str]:
    """이 경로가 작업 산출물이 아니라 도구·환경이 남긴 흔적이면 그 사유, 아니면 None."""
    normalized = normalize_repo_path(path)
    if not normalized:
        return REASON_EMPTY
    segments = normalized.split("/")
    if segments[0].startswith("."):
        return REASON_DOT_TOPLEVEL
    # 마지막 구간은 파일 이름일 수 있으므로 뺀다 — 디렉터리 구간만 본다.
    if any(seg.startswith(".") for seg in segments[:-1]):
        return REASON_DOT_DIRECTORY
    if any(seg in _EXCLUDED_DIR_SEGMENTS for seg in segments):
        return REASON_TOOL_DIRECTORY
    if any(seg.startswith(_EXCLUDED_DIR_PREFIXES) for seg in segments):
        return REASON_TOOL_DIRECTORY
    if segments[-1].lower().endswith(_EXCLUDED_SUFFIXES):
        return REASON_GENERATED_FILE
    return None


def is_excluded_path(path: str) -> bool:
    """작업의 산출물이 아니라 도구·환경이 남긴 흔적인가 (0299 D0004 §3.3 + 0382)."""
    return exclusion_reason(path) is not None


def partition_paths(paths: Iterable[str]) -> tuple[list[str], list[str]]:
    """``(작업 산출물, 도구가 남긴 흔적)`` — 입력 순서를 유지한다.

    마무리 커밋 게이트가 쓰는 모양이다. 흔적을 **버리지 않고 따로 돌려주는** 것이
    핵심이다. 0382 의 재발 방지 원칙은 "조용히 빼지 않는다"이므로, 호출부는 두 번째
    목록을 결과와 이벤트에 실어 화면에 보여준다.
    """
    kept: list[str] = []
    artifacts: list[str] = []
    for path in paths:
        (artifacts if is_excluded_path(path) else kept).append(path)
    return kept, artifacts
