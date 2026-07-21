"""Request-scoped read memoization + per-request query accounting (0291 NR0003 P3-1 / 4-8).

NR0003 발견 5: 한 요청 안에서 같은 행을 같은 파라미터로 반복해서 읽는다 —
``users`` 3회, ``documents WHERE doc_id='…0010-AC'`` 3회, ``documents WHERE group_id
AND seq=7`` 3회, ``workflow_return_points WHERE group_id`` 2회, ``projects`` 2회,
``project_settings`` 2회. 원인은 하나다: 서비스 진입점마다 각자 조회하고, 서로가 이미
읽었다는 것을 모른다.

이 문제에 TTL 캐시를 더 붙이는 것은 잘못된 답이다. ``meta_cache`` / ``auth_cache`` 가
쓰는 5초 TTL 은 **시간 기반**이라 초 경계에서 새고, 커버리지를 넓힐수록 "쓰기 직후 읽기가
옛 값을 본다" 는 위험이 테이블 수만큼 늘어난다. NR0003 P3-1 이 요청 스코프를 지목한
이유가 그것이다 — **stale 위험이 원리적으로 없다.** 캐시가 요청 경계에서 통째로 폐기되므로
살아 있을 수 있는 시간의 상한이 요청 하나이고, 그 안에서 일어난 쓰기는 아래 규칙 2가 지운다.

정책
----

1. **스코프 밖에서는 아무 일도 하지 않는다.** 캐시는 ``request_scope()`` 안에서만 산다.
   백그라운드 워커(``TestRunWorker`` / ``NumberingWorker``)와 부팅 경로는 스코프를 열지
   않으므로 동작이 그대로다. 무엇이 캐시되는지가 "요청 처리 중인가" 하나로 결정된다.

2. **그 요청 안의 쓰기 한 번이 그 요청의 캐시를 통째로 비운다.** 테이블별 의존성 추적을
   하지 않는다 — 어떤 SELECT 가 어떤 UPDATE 의 영향을 받는지는 SQL 문자열만 보고 알 수
   없고, 틀리면 read-your-writes 가 깨진다. 요청당 쓰기는 보통 0~수 회고 읽기가 수십 회라,
   전부 버려도 남는 이득이 크다. **정확성을 성능과 바꾸지 않는다.**

3. **트랜잭션 안의 읽기는 캐시하지 않는다.** 트랜잭션은 쓰기 단위다. 그 안의 SELECT 는
   방금 쓴 것을 다시 읽는 경우가 많고, 트랜잭션 핸들을 통해 실행된 문장의 효과를 캐시가
   가릴 수 있다. 규칙 2가 있어도 이 경계는 따로 둔다 — 규칙 2는 스코프 캐시를 지우지만
   트랜잭션 도중의 읽기 자체를 막지는 않기 때문이다.

4. **넣을 때도 꺼낼 때도 복사한다.** 호출부가 반환된 dict 를 그대로 변형하는 자리가
   있다(예: ``get_rejected_documents_with_reasons()`` 가 ``doc["reject_events"]`` 를
   붙인다). 복사하지 않으면 캐시 항목이 오염되고, 그건 원래 없던 버그가 된다.
   ``meta_cache`` 의 copy-out 관례와 같은 이유다.

5. **TESTING 에서는 기본 OFF.** ``meta_cache`` 와 같은 판단이다. 테스트 일부가 raw SQL 로
   테이블을 직접 건드리므로(규칙 2의 무효화를 우회한다) 기본으로 켜면 실패가 순서
   의존적이 된다. 캐시 동작 자체는 0291 테스트가 명시적으로 켜서 검증한다.
   ``FLOWGATE_REQUEST_CACHE`` 로 강제할 수 있다 (``1``/``0``).

계측 (NR0003 4-8)
-----------------

같은 스코프가 **요청당 쿼리 수 · 캐시 적중 수 · 쓰기 수**를 센다. NR0003 §1-2 는
엔드포인트 귀속이 "추정" 이라고 명시했다 — SQL 로그에 요청 상관ID가 없어 시간순 인접성으로
묶었기 때문이다. 스코프가 요청 경계 그 자체이므로, 여기서 세면 추정이 아니라 실측이 된다.

SQL 로그 자체에 상관ID 를 심는 쪽(NR0003 4-8 의 원안)은 택하지 않았다. 로그를 내보내는
것은 third-party ``sqloader`` 이고 이 트리 밖이다. 요청 경계에서 집계하면 같은 질문
("이 엔드포인트가 쿼리를 몇 개 쓰는가", "P1~P3 전후가 같은 척도로 얼마나 줄었는가")에
패키지를 건드리지 않고 답할 수 있다.
"""
from __future__ import annotations

import copy
import os
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Iterator, Optional

# 캐시 키가 이보다 길어지면 캐시하지 않는다. `doc_id IN (?×900)` 같은 호출부가 남아 있고,
# 그런 쿼리는 (a) 키를 만드는 것부터 비싸고 (b) 같은 요청 안에서 파라미터까지 똑같이
# 반복될 일이 거의 없다 — 캐시가 값을 못 하면서 비용만 낸다.
_MAX_KEY_LEN = 4096

_MISS = object()


@dataclass
class RequestScope:
    """한 요청의 캐시와 카운터."""

    label: str = ""
    entries: dict[tuple[str, str], Any] = field(default_factory=dict)
    queries: int = 0       # DB 까지 간 읽기 (uncacheable 을 포함한다)
    hits: int = 0          # 캐시가 받아낸 읽기 — DB 에 가지 않은 것
    writes: int = 0        # 캐시를 비운 쓰기
    uncacheable: int = 0   # queries 중 캐시에 넣지 못한 것 (트랜잭션 안 / 키 초과 / OFF)

    @property
    def reads(self) -> int:
        """이 요청이 요구한 읽기 총량. 이 값이 NR0003 §1-2 가 추정으로 남긴 수치다."""
        return self.queries + self.hits

    def summary(self) -> str:
        return (
            f"{self.label} reads={self.reads} db={self.queries} cached={self.hits} "
            f"skipped={self.uncacheable} writes={self.writes}"
        )


_scope: ContextVar[Optional[RequestScope]] = ContextVar("flowgate_request_scope", default=None)


def enabled() -> bool:
    raw = os.environ.get("FLOWGATE_REQUEST_CACHE")
    if raw is None or raw.strip() == "":
        return not os.environ.get("TESTING")
    return raw.strip() not in ("0", "false", "False", "")


@contextmanager
def request_scope(label: str = "") -> Iterator[RequestScope]:
    """요청 하나의 캐시 스코프를 연다.

    중첩되면 바깥 스코프를 그대로 쓴다 — 스코프의 의미는 "요청 하나" 이고, 그 안에서
    새 스코프를 여는 것은 경계를 잘못 그은 것이다. 조용히 합류시키는 편이 캐시가 절반만
    도는 것보다 낫다.

    항상 스코프 객체를 준다(캐시가 꺼져 있어도). 계측은 캐시와 독립이며, 카운터가
    ``enabled()`` 에 따라 나타났다 사라지면 그 수치를 신뢰할 수 없게 된다.
    """
    existing = _scope.get()
    if existing is not None:
        yield existing
        return
    scope = RequestScope(label=label)
    token = _scope.set(scope)
    try:
        yield scope
    finally:
        _scope.reset(token)


def current() -> Optional[RequestScope]:
    return _scope.get()


def _key(sql: str, params) -> Optional[tuple[str, str]]:
    try:
        rendered = repr(tuple(params or ()))
    except Exception:
        # 파라미터가 repr 로 안정적으로 표현되지 않으면 동일성을 판단할 수 없다.
        return None
    if len(sql) + len(rendered) > _MAX_KEY_LEN:
        return None
    return (sql, rendered)


def _cache_key(scope: RequestScope, sql: str, params, in_transaction: bool):
    """이 읽기를 메모이즈할 수 있으면 키를, 아니면 None 을 준다.

    ``lookup()`` 과 ``store()`` 가 같은 판단을 해야 카운터가 어긋나지 않으므로 한 곳에 둔다.
    """
    if in_transaction or not enabled():
        return None
    return _key(sql, params)


def lookup(sql: str, params, in_transaction: bool) -> Any:
    """캐시된 결과를 돌려주거나, 없으면 ``MISS`` 를 돌려준다.

    MISS 는 카운터를 올리지 않는다 — 호출부가 DB 를 친 뒤 ``store()`` 에서 올린다.
    그래야 ``queries`` 가 실제 DB 왕복 횟수와 정확히 같아진다.
    """
    scope = _scope.get()
    if scope is None:
        return _MISS
    key = _cache_key(scope, sql, params, in_transaction)
    if key is not None and key in scope.entries:
        scope.hits += 1
        return copy.deepcopy(scope.entries[key])
    return _MISS


def store(sql: str, params, value: Any, in_transaction: bool) -> None:
    """DB 가 방금 돌려준 결과를 기록한다."""
    scope = _scope.get()
    if scope is None:
        return
    scope.queries += 1
    key = _cache_key(scope, sql, params, in_transaction)
    if key is None:
        scope.uncacheable += 1
        return
    scope.entries[key] = copy.deepcopy(value)


def invalidate() -> None:
    """이 요청 안에서 쓰기가 일어났다 — 스코프 캐시를 통째로 버린다 (규칙 2)."""
    scope = _scope.get()
    if scope is None:
        return
    scope.writes += 1
    scope.entries.clear()


def is_miss(value: Any) -> bool:
    return value is _MISS
