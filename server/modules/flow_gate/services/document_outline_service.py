"""문서 조회 도구 — 정본 텍스트·목차·구간 계산 (group 0370, P0002 / L0003).

P0002 가 형식(로케이터·응답 모양)을 고정했고, L0003 이 그 형식을 채우는 계산 절차를
확정했다. 이 모듈은 L0003 §1(파라미터)과 §2-1~§2-6 을 그대로 구현한 **단일 진실
공급원**이다. `/outline`·`/section`·`/meta` 라우트와 검색의 매치 위치 계산(2-7),
저장 응답의 변경 요약(2-9, change_summary_service)이 전부 여기 있는 함수만 쓴다.
같은 이름의 숫자가 화면마다 다르면 안 되기 때문이다(L0003 목적).

핵심 규약 세 가지만 먼저 적는다.

* 모든 좌표는 **저장된 파일 원문 기준**이다. 줄 번호는 1부터 양끝 포함, 문자 위치는
  0부터 끝 제외, 글자 수는 바이트가 아니라 유니코드 코드포인트 수다(P0002 §1-2).
* 머리말 판정은 검색이 쓰는 정규식(`content_search_service._FRONTMATTER`)을 그대로
  불러 쓴다. 새로 만들면 검색이 말하는 자리와 목차가 말하는 자리가 어긋난다(L0003 §2-1).
* 별칭 표(`변경 파일` ↔ `Changed Files` 등)는 기존 파서의 상수에서 **불러** 만든다.
  값을 옮겨 적으면 한쪽만 고쳐져 어긋난다(L0003 §2-4).
"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from bisect import bisect_right
from pathlib import Path
from typing import Any, Optional

# L0003 §2-1: 머리말 경계 판정은 기존 검색과 글자 하나까지 같아야 한다. 사문서(private)
# 이름을 일부러 가져온다 — 복사해 두 벌을 만드는 쪽이 훨씬 위험하기 때문이다.
from modules.flow_gate.services.content_search_service import (  # noqa: F401
    SEARCH_SNIPPET_CHARS as _SEARCH_SNIPPET_CHARS,
    _FRONTMATTER,
)

# ── 1. 파라미터 (L0003 §1 — 코드 안에 흩어 적지 않는다) ────────────────────────────
SECTION_MAX_CHARS_DEFAULT = 20000     # 구간 읽기 기본 상한 (글자)
SECTION_MAX_CHARS_LIMIT = 200000      # 요청으로 올릴 수 있는 상한 (글자)
OUTLINE_MAX_ITEMS = 500               # 목차 응답 최대 항목 (개)
MAX_HEADING_LEVEL = 6                 # max_level 기본값이자 최대 깊이
CONTEXT_LINES_DEFAULT = 2             # 검색 앞뒤 줄 기본값
CONTEXT_LINES_MAX = 10                # 검색 앞뒤 줄 상한
HITS_PER_DOC_DEFAULT = 3              # 문서당 돌려줄 매치 수 기본값
HITS_PER_DOC_MAX = 10                 # 문서당 매치 수 상한
MATCH_SCAN_MAX = 1000                 # 한 문서에서 매치를 세는 상한
CONTEXT_LINE_MAX_CHARS = 400          # before/after 한 줄의 표시 상한
TURN_CONTEXT_TURNS_MAX = 3            # 대화 앞뒤로 붙일 최대 턴 수
TURN_CONTEXT_CHARS = _SEARCH_SNIPPET_CHARS   # 앞뒤 턴 한 줄의 표시 상한 (기존 값 재사용)
CANDIDATE_MAX_NOT_FOUND = 5           # 404 응답에 붙이는 비슷한 제목 수
CANDIDATE_MAX_AMBIGUOUS = 10          # 같은 이름이 여럿일 때 알려 줄 후보 수
CANDIDATE_MIN_SCORE = 0.20            # 이 값 미만이면 비슷한 제목으로 치지 않음
SECTION_DIFF_MAX = 50                 # sections_added/removed/changed 각각의 상한
SUMMARY_MAX_LINES = 20000             # 이 줄 수를 넘는 문서는 변경 요약을 생략
SUMMARY_TIME_BUDGET_MS = 2000         # 변경 요약 계산 시간 예산
HEADING_TITLE_MAX = 300               # 제목 글자를 이 길이에서 자른다


# ── 2-1. 정본 텍스트와 좌표 ───────────────────────────────────────────────────────

def canonical_text(raw: str) -> str:
    """BOM 을 떼고 개행을 ``\\n`` 하나로 통일한 정본 텍스트.

    같은 문서를 어떤 운영체제에서 저장했느냐로 줄 번호가 달라지면 안 된다. 원본이
    CRLF 였다면 정규화 뒤 글자 수가 원본 바이트 수보다 줄어드는데, 그것이 옳다.
    """
    text = raw or ""
    if text.startswith("﻿"):
        text = text[1:]
    return text.replace("\r\n", "\n").replace("\r", "\n")


def read_canonical(path: Any) -> Optional[str]:
    """파일 하나를 정본 텍스트로 읽는다. 읽지 못하면 None.

    UTF-8 로 못 읽는 바이트는 대체 문자로 바꿔 읽고 계속한다 — 인코딩이 깨진 파일
    하나 때문에 조회가 죽지 않게 한다(L0003 §5).
    """
    try:
        raw = Path(path).read_bytes()
    except (OSError, TypeError, ValueError):
        return None
    return canonical_text(raw.decode("utf-8", errors="replace"))


def split_lines(text: str) -> list[str]:
    """정본 텍스트를 줄로 나눈다.

    **파일이 개행으로 끝나면 그 뒤에 줄이 하나 더 있다고 세지 않는다.** 이 처리를
    정하지 않으면 같은 파일이 1217줄이 되었다 1218줄이 되었다 한다(L0003 §2-1).
    """
    parts = (text or "").split("\n")
    if parts and parts[-1] == "":
        parts = parts[:-1]
    return parts


def line_offsets(lines: list[str]) -> list[int]:
    """``offsets[i]`` = (i+1)번째 줄의 첫 글자 위치(0부터)."""
    offsets: list[int] = []
    cursor = 0
    for line in lines:
        offsets.append(cursor)
        cursor += len(line) + 1  # +1 은 개행
    return offsets


def frontmatter_span(text: str) -> tuple[int, int]:
    """``(body_line_start, frontmatter_chars)``.

    머리말이 없으면 ``(1, 0)``. 닫는 줄이 끝까지 나오지 않으면 머리말이 없는 것으로
    본다 — 문서 전체를 머리말로 삼아 통째로 감추는 것보다 낫다(L0003 §2-1).
    """
    m = _FRONTMATTER.match(text or "")
    if m is None:
        return 1, 0
    consumed = text[: m.end()]
    newlines = consumed.count("\n")
    # 닫는 줄의 개행까지 머리말에 포함하므로 body_line_start 는 닫는 줄의 *다음* 줄.
    # 파일이 개행 없이 끝나 `\n?` 가 아무것도 먹지 못한 경우에도 본문 시작은 그 다음
    # 줄이므로 한 칸 더 민다.
    if consumed.endswith("\n"):
        return newlines + 1, m.end()
    return newlines + 2, m.end()


def content_sha256(text: str) -> str:
    """정본 텍스트 **전체**(머리말 포함)의 지문.

    ``documents._normalise_markdown_for_fingerprint`` 를 쓰지 않는다. 그것은 중복
    제출 탐지용이라 머리말의 일부 키와 끝 공백을 지운다. 여기서 필요한 것은 "지금 읽고
    있는 파일이 아까 그 파일과 같은가" 이므로 끝 공백 한 칸이 달라졌으면 다른 지문이어야
    한다(L0003 §2-1).
    """
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


# ── 2-2. 제목 훑기 ───────────────────────────────────────────────────────────────
#
# 코드 울타리를 지키는 이유: 이 프로젝트 문서는 마크다운을 인용하는 일이 잦다. TR 문서가
# "이렇게 적으세요" 하고 울타리 안에 `## 변경 파일` 을 보여 주면, 울타리를 무시한 파서는
# 그 줄을 진짜 제목으로 세어 그 뒤 구간 번호가 통째로 한 칸씩 밀린다.
_FENCE_RE = re.compile(r"^ {0,3}(?P<fence>`{3,}|~{3,})(?P<info>.*)$")
# ATX 제목: 줄 앞 공백 0~3칸 뒤 `#` 1~6개. `#` 뒤에는 공백·탭이 하나 이상 오거나 줄이
# 거기서 끝나야 한다(`#태그` 는 제목이 아니다). 4칸 이상 들여쓰면 코드 블록이다.
_ATX_RE = re.compile(r"^ {0,3}(?P<hashes>#{1,6})(?:[ \t]+(?P<title>.*))?$")
# 줄 끝의 닫는 `#` 무리(` ###`).
_CLOSING_HASH_RE = re.compile(r"(?:^|[ \t])#+[ \t]*$")


def _heading_title(raw: str) -> str:
    title = (raw or "").strip()
    title = _CLOSING_HASH_RE.sub("", title).strip()
    if len(title) > HEADING_TITLE_MAX:
        title = title[:HEADING_TITLE_MAX]
    return title


def scan_headings(
    text: str, body_line_start: int, lines: Optional[list[str]] = None
) -> list[dict]:
    """머리말 뒤부터 ATX 제목 줄을 훑는다. 코드 울타리 안은 건너뛴다.

    - 여는 울타리: 공백 0~3칸 뒤 ````` 또는 ``~`` 가 3개 이상 연속.
    - 닫는 울타리: **같은 문자**로, 여는 울타리보다 **짧지 않게**, 뒤에 정보 문자열이
      없어야 한다.
    - 울타리가 끝까지 닫히지 않으면 문서 끝까지 울타리 안이다(CommonMark 와 같다).
    - 밑줄식(setext) 제목은 세지 않는다. ``---`` 은 머리말 울타리·가로줄과 글자가 같고,
      기존 파서(``tr_scope_service``·``test_run_service``)가 전부 ATX 만 보기 때문이다.
    - 제목 글자가 비어 있어도(``##`` 만 있는 줄) 제목으로 센다. 빼면 뒤 구간의
      ``section_id`` 가 밀린다.
    """
    if lines is None:
        lines = split_lines(text)
    fence_char: Optional[str] = None
    fence_len = 0
    headings: list[dict] = []
    for i in range(max(1, body_line_start), len(lines) + 1):
        line = lines[i - 1]
        fence = _FENCE_RE.match(line)
        if fence_char is None:
            if fence is not None:
                fence_char = fence.group("fence")[0]
                fence_len = len(fence.group("fence"))
                continue
        else:
            if (
                fence is not None
                and fence.group("fence")[0] == fence_char
                and len(fence.group("fence")) >= fence_len
                and not fence.group("info").strip()
            ):
                fence_char = None
            continue
        atx = _ATX_RE.match(line)
        if atx is not None:
            headings.append({
                "line": i,
                "level": len(atx.group("hashes")),
                "title": _heading_title(atx.group("title") or ""),
            })
    return headings


# ── 2-3. 구간 경계 ───────────────────────────────────────────────────────────────

def build_tree(headings: list[dict]) -> list[dict]:
    """제목 목록에 ``section_id``·``parent_id``·``heading_path`` 를 붙인다.

    ``##`` 다음에 바로 ``####`` 가 오면 ``####`` 의 부모는 그 ``##`` 이다. 없는 ``###``
    을 지어내지 않고 ``level`` 은 글에 적힌 대로 싣는다. 문서가 ``###`` 으로 시작해도
    ``parent_id`` 가 null 일 뿐 오류가 아니다(L0003 §2-3).
    """
    items: list[dict] = []
    stack: list[dict] = []
    for n, h in enumerate(headings, start=1):
        while stack and stack[-1]["level"] >= h["level"]:
            stack.pop()
        parent = stack[-1] if stack else None
        heading_path = (parent["heading_path"] + [h["title"]]) if parent else [h["title"]]
        item = {
            "section_id": f"s{n}",
            "parent_id": parent["section_id"] if parent else None,
            "level": h["level"],
            "title": h["title"],
            "heading_path": heading_path,
            "line_start": h["line"],
        }
        items.append(item)
        stack.append(item)
    return items


def section_end_line(
    items: list[dict], idx: int, include_children: bool, document_lines: int
) -> int:
    """구간의 끝 줄(양끝 포함).

    ``include_children`` 이 참이면 **나와 같거나 얕은 다음 제목의 앞줄**까지, 거짓이면
    **다음 제목의 앞줄**까지(제 몸통 글자만). 어느 쪽이든 끝 줄이 시작 줄보다 작아지지
    않는다 — 제목이 연달아 붙어 있으면 구간은 제목 줄 하나짜리다.
    """
    me = items[idx]
    end = document_lines
    for j in range(idx + 1, len(items)):
        if include_children:
            if items[j]["level"] <= me["level"]:
                end = items[j]["line_start"] - 1
                break
        else:
            end = items[j]["line_start"] - 1
            break
    return max(end, me["line_start"])


def enclosing_section(items: list[dict], line: int) -> Optional[dict]:
    """시작 줄을 넘지 않는 마지막 제목 = 가장 깊은 조상.

    찾은 것이 없으면(머리말 안이거나 첫 제목보다 앞) None 이며 오류가 아니다. 제목이
    없는 문서(요건 R)가 전부 이 경우다.
    """
    best: Optional[dict] = None
    for it in items:
        if it["line_start"] <= line:
            best = it
        else:
            break
    return best


class DocumentText:
    """정본 텍스트 하나와 그 위에서 계산되는 모든 좌표.

    **모든 구간·목차·요약 계산은 이 객체가 들고 있는 하나의 텍스트 위에서만 한다.**
    같은 파일을 읽어도 읽는 방식이 다르면 숫자가 달라지기 때문이다(L0003 §2-1).
    """

    def __init__(self, text: str):
        self.text = text or ""
        self.lines = split_lines(self.text)
        self.offsets = line_offsets(self.lines)
        self.document_lines = len(self.lines)
        self.document_chars = len(self.text)
        self.body_line_start, self.frontmatter_chars = frontmatter_span(self.text)
        self._sha: Optional[str] = None
        self._items: Optional[list[dict]] = None

    # -- 만들기 -------------------------------------------------------------
    @classmethod
    def from_path(cls, path: Any) -> Optional["DocumentText"]:
        text = read_canonical(path)
        return None if text is None else cls(text)

    @classmethod
    def from_raw(cls, raw: str) -> "DocumentText":
        return cls(canonical_text(raw))

    # -- 파생값 -------------------------------------------------------------
    @property
    def content_sha256(self) -> str:
        if self._sha is None:
            self._sha = content_sha256(self.text)
        return self._sha

    @property
    def items(self) -> list[dict]:
        if self._items is None:
            self._items = build_tree(
                scan_headings(self.text, self.body_line_start, self.lines)
            )
        return self._items

    @property
    def section_total(self) -> int:
        return len(self.items)

    # -- 좌표 ---------------------------------------------------------------
    def char_start_of(self, line: int) -> int:
        if self.document_lines == 0:
            return 0
        if line <= 1:
            return 0
        if line > self.document_lines:
            return self.document_chars
        return self.offsets[line - 1]

    def char_end_of(self, line: int) -> int:
        """``line`` 의 끝(끝 제외). 앞 구간의 끝과 뒤 구간의 시작이 같은 값이 된다."""
        if line >= self.document_lines:
            return self.document_chars
        return self.offsets[line]

    def line_containing(self, char_pos: int) -> int:
        if self.document_lines == 0:
            return 1
        pos = max(0, min(int(char_pos), max(0, self.document_chars - 1)))
        return max(1, bisect_right(self.offsets, pos))

    def slice_lines(self, line_start: int, line_end: int) -> str:
        return self.text[self.char_start_of(line_start): self.char_end_of(line_end)]

    def heading_line_text(self, item: Optional[dict]) -> Optional[str]:
        if not item:
            return None
        line = item["line_start"]
        if 1 <= line <= self.document_lines:
            return self.lines[line - 1]
        return None

    def index_of(self, item: dict) -> int:
        return self.items.index(item)

    def find_by_section_id(self, section_id: str) -> Optional[dict]:
        for it in self.items:
            if it["section_id"] == section_id:
                return it
        return None

    # -- 본문 좌표 → 파일 좌표 (L0003 §2-1 / §2-7) ---------------------------
    def body_char_to_file(self, body_char: int) -> int:
        return int(body_char) + self.frontmatter_chars

    def body_line_to_file(self, body_line: int) -> int:
        return int(body_line) + self.body_line_start - 1


# ── 2-4. 제목 이름 맞추기 ─────────────────────────────────────────────────────────

_WS_COLLAPSE = re.compile(r"\s+")
_TRAILING_COLON = re.compile(r"[:：]$")
_NUM_PREFIX = re.compile(r"^\d+([.\-]\d+)*[.)]?\s+")
_KO_PREFIX = re.compile(r"^[가-힣]\.\s+")
_LEADING_HASHES = re.compile(r"^\s{0,3}#{1,6}\s*")


def normalize_heading(s: str) -> str:
    """n1 NFKC → n2 공백 접기 → n3 백틱 제거 → n4 casefold → n5 끝 ':' 하나 제거.

    **강조 표기(``*``·``_``)는 지우지 않는다.** 파일 이름과 식별자에 ``_`` 가 흔해서
    지우면 ``client_src`` 와 ``clientsrc`` 가 같은 제목이 된다. 백틱만 지우는 이유는
    백틱이 제목 글자에 쓰일 일이 없기 때문이다(L0003 §2-4).
    """
    t = unicodedata.normalize("NFKC", s or "")
    t = _WS_COLLAPSE.sub(" ", t).strip()
    t = t.replace("`", "")
    t = t.casefold()
    t = _TRAILING_COLON.sub("", t)
    return t.strip()


def strip_number_prefix(t: str) -> str:
    """번호 접두사를 뗀다: ``2.1 ``, ``5. ``, ``3) ``, ``가. ``."""
    t = _NUM_PREFIX.sub("", t or "")
    t = _KO_PREFIX.sub("", t)
    return t.strip()


def _strip_heading_marks(s: str) -> str:
    return _LEADING_HASHES.sub("", s or "").strip()


_ALIAS_MAP: Optional[dict[str, str]] = None


def alias_map() -> dict[str, str]:
    """정규화된 별칭 → 무리 대표 이름.

    **새 상수를 정의하지 않고 이미 있는 값을 불러 만든다.** 값을 옮겨 적으면 한쪽만
    고쳐져 어긋난다(L0003 §2-4). 일본어 별칭은 없다(0355 T0009 에서 만들지 않기로 했다).

    이 표는 **조회에서만** 쓴다. ``tr_scope_service`` 의 신고 섹션 판정은 이 표로 조금도
    느슨해지지 않는다 — 그 파서는 여전히 제 정규식을 쓰므로 ``## 5. 변경 파일`` 을 받지
    않는다. 조회가 읽어 줬으니 신고 형식도 맞겠지 하고 넘기면 TR 이 반려된다.
    """
    global _ALIAS_MAP
    if _ALIAS_MAP is not None:
        return _ALIAS_MAP
    groups: list[tuple[str, ...]] = []
    try:
        from modules.flow_gate.services import tr_scope_service

        groups.append((
            _strip_heading_marks(tr_scope_service.SECTION_HEADING),
            _strip_heading_marks(tr_scope_service.SECTION_HEADING_EN),
        ))
    except Exception:  # noqa: BLE001 — 별칭이 없어도 조회 자체는 살아 있어야 한다
        pass
    try:
        from modules.flow_gate.services import test_run_service

        groups.append(tuple(test_run_service.TEST_CASES_SECTION_NAMES))
        groups.append(tuple(test_run_service.SETUP_SECTION_NAMES))
        groups.append(tuple(test_run_service.TEARDOWN_SECTION_NAMES))
    except Exception:  # noqa: BLE001
        pass

    mapping: dict[str, str] = {}
    for grp in groups:
        keys = [normalize_heading(name) for name in grp if name]
        keys = [k for k in keys if k]
        if not keys:
            continue
        canon = keys[0]
        for key in keys:
            mapping[key] = canon
            mapping[strip_number_prefix(key)] = canon
    _ALIAS_MAP = mapping
    return mapping


def reset_alias_map() -> None:
    """별칭 표 캐시를 비운다(테스트용)."""
    global _ALIAS_MAP
    _ALIAS_MAP = None


def apply_alias(t: str) -> str:
    return alias_map().get(t, t)


# 매칭 단계 — 위에서부터, 처음 걸리는 단계에서 멈춘다.
#   M1 정규화 없이 원문 그대로 / M2 normalize_heading / M3 M2+별칭 / M4 M3+번호 떼기
_STAGES = (1, 2, 3, 4)


def match_key(stage: int, s: str) -> str:
    if stage == 1:
        return s or ""
    normalized = normalize_heading(s)
    if stage == 2:
        return normalized
    if stage == 3:
        return apply_alias(normalized)
    return apply_alias(strip_number_prefix(normalized))


def resolve_by_name(items: list[dict], requested: str) -> list[dict]:
    """이름(또는 ``>`` 로 이은 제목 경로)으로 구간을 찾는다.

    단계를 한 번에 다 적용하지 않고 순서대로 두는 이유는, 문서 안에 ``2.1 구성`` 과
    ``구성`` 이 둘 다 있을 때 ``2.1 구성`` 으로 물으면 정확히 그 하나만 나오게 하기
    위해서다. 한 번에 번호까지 떼면 둘 다 걸려 공연히 애매해진다(L0003 §2-4).
    """
    requested = requested or ""
    if ">" in requested:
        return _resolve_by_path(items, requested)
    for stage in _STAGES:
        want = match_key(stage, requested)
        hits = [it for it in items if match_key(stage, it["title"]) == want]
        if hits:
            return hits
    return []


def _resolve_by_path(items: list[dict], requested: str) -> list[dict]:
    parts = [p.strip() for p in requested.split(">")]
    parts = [p for p in parts if p]
    if not parts:
        return []
    for stage in _STAGES:
        want = [match_key(stage, p) for p in parts]
        hits = []
        for it in items:
            path = [match_key(stage, x) for x in it["heading_path"]]
            if len(path) >= len(want) and path[-len(want):] == want:
                hits.append(it)
        if hits:
            return hits
    return []


def _bigrams(x: str) -> set[str]:
    return {x[i:i + 2] for i in range(len(x) - 1)}


def similarity(a: str, b: str) -> float:
    """두 글자 조각(bigram) Dice 계수.

    분모가 0(양쪽 다 한 글자 이하)이면 나눗셈을 하지 않고, 같으면 1.0, 다르면 0.0.
    """
    ba, bb = _bigrams(a or ""), _bigrams(b or "")
    denom = len(ba) + len(bb)
    if denom == 0:
        return 1.0 if (a or "") == (b or "") else 0.0
    return 2.0 * len(ba & bb) / denom


def similar_candidates(
    items: list[dict], requested: str, limit: int = CANDIDATE_MAX_NOT_FOUND
) -> list[dict]:
    """비슷한 제목을 문서 순서 우선, 점수 내림차순으로 고른다.

    ``CANDIDATE_MIN_SCORE`` 미만은 버린다. 남는 게 없으면 빈 목록이고 404 는 그대로다.
    """
    want = normalize_heading(requested)
    scored: list[tuple[float, int, dict]] = []
    for idx, it in enumerate(items):
        score = similarity(want, normalize_heading(it["title"]))
        if score >= CANDIDATE_MIN_SCORE:
            scored.append((score, idx, it))
    scored.sort(key=lambda t: (-t[0], t[1]))
    return [it for _, _, it in scored[:limit]]


# ── 2-6. 자르기 ──────────────────────────────────────────────────────────────────

def cut_to_limit(
    doc: DocumentText, line_start: int, line_end: int, max_chars: int
) -> tuple[int, bool]:
    """``(마지막으로 담은 줄, 잘렸는가)``.

    상한 안에서 **마지막으로 끝난 줄까지만** 준다. 그래서 실제 글자 수가 ``max_chars``
    보다 작을 수 있다. **첫 줄 하나가 이미 상한을 넘으면 그 줄은 통째로 준다** — 이렇게
    하지 않으면 한 글자도 못 주고 ``next_locator`` 가 제자리를 가리켜 무한히 같은 요청을
    반복하게 된다(L0003 §2-6).
    """
    taken = 0
    cursor = line_start
    while cursor <= line_end:
        need = len(doc.lines[cursor - 1]) + 1  # 개행 포함
        if taken + need > max_chars and cursor > line_start:
            break
        taken += need
        cursor += 1
    return cursor - 1, cursor <= line_end


# ── 로케이터 (P0002 §1-1) ────────────────────────────────────────────────────────

def make_ref(doc_id: str, revision_no: int, line_start: int, line_end: int) -> str:
    return f"{doc_id}@r{revision_no}#L{line_start}-{line_end}"


def build_locator(
    doc: DocumentText,
    doc_id: str,
    revision_no: int,
    line_start: int,
    line_end: int,
    enclosing: Optional[dict] = None,
    char_start: Optional[int] = None,
    char_end: Optional[int] = None,
) -> dict:
    """P0002 §1-1 의 로케이터 한 벌. 이름으로 묻든 줄로 묻든 같은 모양이 나온다."""
    cs = doc.char_start_of(line_start) if char_start is None else char_start
    ce = doc.char_end_of(line_end) if char_end is None else char_end
    return {
        "doc_id": doc_id,
        "revision_no": revision_no,
        "ref": make_ref(doc_id, revision_no, line_start, line_end),
        "unit": "line",
        "section_id": enclosing["section_id"] if enclosing else None,
        "heading_path": list(enclosing["heading_path"]) if enclosing else [],
        "level": enclosing["level"] if enclosing else None,
        "line_start": line_start,
        "line_end": line_end,
        "char_start": cs,
        "char_end": ce,
    }


def build_turn_locator(
    doc_id: str, revision_no: int, turn_seq: int, char_start: int, char_end: int
) -> dict:
    """대화 턴의 자리(P0002 §1-4). 줄이 뜻을 갖지 않으므로 ``unit`` 은 ``turn``."""
    return {
        "doc_id": doc_id,
        "revision_no": revision_no,
        "ref": f"{doc_id}@r{revision_no}#T{turn_seq}",
        "unit": "turn",
        "section_id": None,
        "heading_path": [],
        "level": None,
        "turn_seq": turn_seq,
        "line_start": None,
        "line_end": None,
        "char_start": char_start,
        "char_end": char_end,
    }


def candidate_entry(doc_id: str, revision_no: int, item: dict, doc: DocumentText,
                    include_children: bool = True) -> dict:
    """404/애매 응답에 싣는 후보 한 줄(P0002 시나리오 5·6)."""
    idx = doc.items.index(item)
    line_end = section_end_line(doc.items, idx, include_children, doc.document_lines)
    return {
        "section_id": item["section_id"],
        "heading_path": list(item["heading_path"]),
        "level": item["level"],
        "line_start": item["line_start"],
        "line_end": line_end,
        "ref": make_ref(doc_id, revision_no, item["line_start"], line_end),
    }


def outline_items(doc: DocumentText, max_level: int) -> tuple[list[dict], bool]:
    """목차 응답의 ``items`` 와 ``truncated``.

    ``max_level`` 로 걸러 내는 것은 **응답에 담을지**를 정할 뿐이고 번호는 거르기 전
    순번이다. 그래서 ``max_level=2`` 로 받은 목차의 ``section_id`` 를 그대로
    ``/section?section_id=`` 에 넣어도 같은 구간이 열린다(L0003 §2-2).
    """
    items = doc.items
    out: list[dict] = []
    truncated = False
    for idx, it in enumerate(items):
        if it["level"] > max_level:
            continue
        if len(out) >= OUTLINE_MAX_ITEMS:
            truncated = True
            break
        line_end = section_end_line(items, idx, True, doc.document_lines)
        char_start = doc.char_start_of(it["line_start"])
        char_end = doc.char_end_of(line_end)
        has_children = idx + 1 < len(items) and items[idx + 1]["level"] > it["level"]
        out.append({
            "section_id": it["section_id"],
            "parent_id": it["parent_id"],
            "level": it["level"],
            "title": it["title"],
            "heading_path": list(it["heading_path"]),
            "line_start": it["line_start"],
            "line_end": line_end,
            "char_start": char_start,
            "char_end": char_end,
            "lines": line_end - it["line_start"] + 1,
            "chars": char_end - char_start,
            "has_children": has_children,
        })
    return out, truncated


# ── 2-5. 준 위치를 실제 구간으로 바꾸기 ────────────────────────────────────────────

class LocatorError(Exception):
    """자리 지정 실패. ``status`` 와 응답에 덧붙일 ``extra`` 를 함께 나른다."""

    def __init__(self, status: int, message: str, extra: Optional[dict] = None):
        super().__init__(message)
        self.status = status
        self.message = message
        self.extra = extra or {}


class Resolved:
    """자리 지정 결과."""

    def __init__(self, resolved_by: str, line_start: int, line_end: int,
                 item: Optional[dict], ambiguous: bool, candidates: list[dict]):
        self.resolved_by = resolved_by
        self.line_start = line_start
        self.line_end = line_end
        self.item = item
        self.ambiguous = ambiguous
        self.candidates = candidates


_RANGE_RE = re.compile(r"^\s*(\d+)\s*-\s*(\d+)\s*$")


def resolve_locator(
    doc: DocumentText,
    doc_id: str,
    revision_no: int,
    *,
    section: Optional[str] = None,
    section_id: Optional[str] = None,
    lines: Optional[str] = None,
    chars: Optional[str] = None,
    include_children: bool = True,
) -> Resolved:
    """``section``·``section_id``·``lines``·``chars`` 중 **정확히 하나**를 구간으로 바꾼다.

    둘 이상이거나 하나도 없으면 422 다(P0002 시나리오 19). 검사 순서는 L0003 §4-1 을
    따른다 — 판이 달라졌다는 사실(409)은 이 함수에 들어오기 전에 이미 걸러진다.
    """
    given = [x for x in (section, section_id, lines, chars) if x is not None and x != ""]
    if len(given) != 1:
        raise LocatorError(
            422, "specify exactly one of section, section_id, lines, chars"
        )

    items = doc.items

    if section:
        hits = resolve_by_name(items, section)
        if not hits:
            cands = similar_candidates(items, section, CANDIDATE_MAX_NOT_FOUND)
            raise LocatorError(
                404,
                f"section not found: {section}",
                {
                    "doc_id": doc_id,
                    "revision_no": revision_no,
                    "section_total": len(items),
                    "candidates": [
                        candidate_entry(doc_id, revision_no, c, doc, include_children)
                        for c in cands
                    ],
                },
            )
        # 여럿이면 문서에서 먼저 나오는 것을 읽어 주고 애매했다고 말한다. 무인 작업에서
        # 멈춰 세우지 않는 것이 이 규칙의 목적이다(P0002 시나리오 5).
        hits.sort(key=lambda it: (it["line_start"], items.index(it)))
        chosen = hits[0]
        idx = items.index(chosen)
        return Resolved(
            "section",
            chosen["line_start"],
            section_end_line(items, idx, include_children, doc.document_lines),
            chosen,
            len(hits) > 1,
            [
                candidate_entry(doc_id, revision_no, c, doc, include_children)
                for c in hits[:CANDIDATE_MAX_AMBIGUOUS]
            ] if len(hits) > 1 else [],
        )

    if section_id:
        chosen = doc.find_by_section_id(section_id)
        if chosen is None:
            # 번호는 사람이 지어낸 값이 아니라 목차에서 받은 값이다. 틀렸다면 판이
            # 달라진 것이지 비슷한 번호를 권할 일이 아니다 — 대신 section_total 을 줘서
            # 목차를 다시 부르게 한다(L0003 §4-2).
            raise LocatorError(
                404,
                f"section not found: {section_id}",
                {
                    "doc_id": doc_id,
                    "revision_no": revision_no,
                    "section_total": len(items),
                    "candidates": [],
                },
            )
        idx = items.index(chosen)
        return Resolved(
            "section_id",
            chosen["line_start"],
            section_end_line(items, idx, include_children, doc.document_lines),
            chosen,
            False,
            [],
        )

    if lines:
        m = _RANGE_RE.match(lines)
        if m is None:
            raise LocatorError(422, "lines must look like 'a-b'")
        a, b = int(m.group(1)), int(m.group(2))
        if a < 1 or b < a:
            raise LocatorError(422, "lines must look like 'a-b' with 1 <= a <= b")
        if a > doc.document_lines:
            raise LocatorError(
                422,
                f"lines out of range: document has {doc.document_lines} lines",
                {"doc_id": doc_id, "revision_no": revision_no,
                 "document_lines": doc.document_lines},
            )
        line_start, line_end = a, min(b, doc.document_lines)
        return Resolved("lines", line_start, line_end,
                        enclosing_section(items, line_start), False, [])

    m = _RANGE_RE.match(chars or "")
    if m is None:
        raise LocatorError(422, "chars must look like 'c-d'")
    c, d = int(m.group(1)), int(m.group(2))
    if d <= c:
        raise LocatorError(422, "chars must look like 'c-d' with 0 <= c < d")
    if c >= doc.document_chars:
        raise LocatorError(
            422,
            f"chars out of range: document has {doc.document_chars} chars",
            {"doc_id": doc_id, "revision_no": revision_no,
             "document_chars": doc.document_chars},
        )
    # chars 는 끝 제외이므로 끝 줄을 구할 때 d - 1 을 쓴다. 이 한 칸을 빠뜨리면 구간 끝이
    # 다음 줄까지 한 줄 더 잡힌다. 줄 가운데에서 시작·끝나면 줄 단위로 넓힌다.
    line_start = doc.line_containing(c)
    line_end = doc.line_containing(min(d, doc.document_chars) - 1)
    line_end = max(line_end, line_start)
    return Resolved("chars", line_start, line_end,
                    enclosing_section(items, line_start), False, [])


def clamp_max_chars(value: Optional[int]) -> int:
    """``max_chars`` 를 ``[1, SECTION_MAX_CHARS_LIMIT]`` 로 자른다. 0 이하면 422."""
    if value is None:
        return SECTION_MAX_CHARS_DEFAULT
    try:
        v = int(value)
    except (TypeError, ValueError):
        raise LocatorError(422, "max_chars must be a positive integer")
    if v <= 0:
        raise LocatorError(422, "max_chars must be a positive integer")
    return min(v, SECTION_MAX_CHARS_LIMIT)


def clip_line(line: str, limit: int = CONTEXT_LINE_MAX_CHARS) -> str:
    """앞뒤 줄은 **원문 그대로** 담되 상한을 넘으면 자르고 ``…`` 를 붙인다.

    스니펫과 달리 공백을 접지 않는다 — 줄 모양이 보여야 자리를 가늠할 수 있다.
    """
    if len(line) <= limit:
        return line
    return line[:limit] + "…"
