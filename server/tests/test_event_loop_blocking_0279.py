"""0279 T0005 — regression guard: no blocking work inside `async def` route handlers.

Why this test exists
--------------------
This bug has now shipped twice. 0275 T0005 fixed it in tree/list/document/git
routes; 0279 NR0003 found the identical pattern still live in remote_routes,
file_transfer_routes and legacy_misc_routes, where it was measured freezing the
whole server for 40 seconds.

The mechanism: FastAPI runs an `async def` path operation directly ON the event
loop. Any synchronous DB or filesystem call inside one therefore blocks *every*
concurrent request until it returns — an unrelated 0.15s DB read was observed
taking 40.2s, returning only when a `remote/grep` finished. A plain `def`
handler is instead run in the threadpool and does not have this problem.

So the rule is:

    An `async def` route handler may not reach synchronous DB or filesystem work.
    Either make the handler a plain `def`, or push the blocking call through
    `anyio.to_thread.run_sync` / `run_in_executor` / `run_in_threadpool`.

Two subtleties this guard handles, both of which hid real blocking in this
codebase:

1.  **Indirect calls.** `async def inbox()` looks clean — it only calls
    `_handle_new()`. The blocking DB writes are one level down. The check
    therefore follows calls into functions defined in the same module.

2.  **`await` is not an escape hatch.** Awaiting a *local coroutine* keeps the
    work on the event loop; only a genuine threadpool offload moves it. So the
    exemption is specifically the offload wrappers, not "appears under an await".

This is a static check — it parses the source and never imports the routers, so
it needs no DB, no app instance and no network.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

_SERVER_DIR = Path(__file__).resolve().parents[1]
_API_DIR = _SERVER_DIR / "modules" / "flow_gate" / "api"

# Module-level names whose attribute calls mean "synchronous DB / filesystem work".
#
# 0394 T0016 항목 4 (NR0003 §5.3): 이 목록은 0279 당시 존재하던 서비스 모듈 이름을 손으로
# 적은 것이었다. 그 뒤에 생긴 서비스(conversation_query_service, conversation_turn_service,
# ai_invoke_service, token_service, document_service …)는 목록에 없으므로 **그 호출을 async
# 핸들러에서 그냥 부르면 가드가 아무 말도 하지 않았다.** 실측으로 확인했다: worker 대화 조회
# 라우트의 `anyio.to_thread.run_sync` 를 걷어내고 `_list_authenticated(...)` 를 직접 부르게
# 바꿔도 이 가드는 초록이었다. 규칙은 전역인데 판정기가 국소였던 것이다.
#
# 그래서 이름을 하나씩 적는 대신 접미사로 판정한다 — 이 저장소의 서비스 계층은 예외 없이
# `*_service` 로 끝나고, 저장소 게이트웨이는 `*_store` / `db` 다. 넓힌 뒤 새로 드러난 위반은
# 0건이었다(현재 모든 async 핸들러가 이미 오프로드한다). 즉 이 확장의 값어치는 지금 잡히는
# 것이 아니라, 앞으로 새 서비스에서 오프로드를 빠뜨렸을 때 잡힌다는 데 있다.
_SYNC_MODULE_NAMES = {
    "service",
    "process_service",
    "remote_tool_service",
    "content_search_service",
    "dashboard_service",
    "git_service",
    "test_run_service",
    "_db",
    "db",
}
_SYNC_MODULE_SUFFIXES = ("_service", "_store")
# `from modules.flow_gate.db import documents as db_documents` — the db package is always
# aliased with this prefix in this codebase, and every call on it is a synchronous query.
_SYNC_MODULE_PREFIXES = ("db_",)


def _is_sync_module(root: str | None) -> bool:
    """True for a module name whose attribute calls reach synchronous DB/filesystem work."""
    if not root:
        return False
    return (
        root in _SYNC_MODULE_NAMES
        or root.endswith(_SYNC_MODULE_SUFFIXES)
        or root.startswith(_SYNC_MODULE_PREFIXES)
    )

# Bare calls that block on the filesystem or open a DB connection.
_BLOCKING_CALLS = {"open", "get_store"}
_BLOCKING_OS_FUNCS = {
    "makedirs", "mkdir", "remove", "unlink", "rename", "replace", "rmdir", "listdir",
}
_BLOCKING_PATH_METHODS = {"write_text", "read_text", "write_bytes", "read_bytes"}

# Calls whose *arguments* run in a worker thread — the one real escape hatch.
_OFFLOAD_FUNCS = {"run_sync", "to_thread", "run_in_executor", "run_in_threadpool"}

# A call site may opt out with this marker, for the rare case where the work is
# provably cheap and constant-time. Use sparingly — "it looks cheap" was wrong twice.
_OPT_OUT = "noqa: event-loop-blocking"

# ── Known outstanding debt ────────────────────────────────────────────────────
# Files still known to block, excluded here rather than left silently unchecked
# so the guard stays strict everywhere else. Every entry is itself verified by
# test_known_unfixed_entries_are_still_accurate below, which fails once an entry
# stops blocking — so a stale exemption cannot quietly re-open the hole.
#
# EMPTY, and it should stay that way. 0279 T0005 fixed remote_routes /
# file_transfer_routes / legacy_misc_routes; 0279 T0007 fixed inbox_routes (the
# last one, ranked #2 by NR0003). Add an entry only with a dated reason and a
# follow-up T.
_KNOWN_UNFIXED: set[str] = set()


def _is_route_handler(node: ast.AST) -> bool:
    """True for an `async def` decorated with @router.get/post/put/delete/patch."""
    if not isinstance(node, ast.AsyncFunctionDef):
        return False
    for dec in node.decorator_list:
        func = dec.func if isinstance(dec, ast.Call) else dec
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            if func.value.id == "router":
                return True
    return False


# 0394 T0016 항목 4 (NR0003 §5.3): 규칙은 "이벤트 루프에서 동기 작업을 하지 않는다" 인데
# 검사는 `@router.*` 가 붙은 핸들러만 봤다. `BaseHTTPMiddleware.dispatch` 도 똑같이 루프
# 위에서 돌고, 게다가 **모든** 요청을 지나가므로 여기서 막히면 라우트 하나가 아니라 서버
# 전체가 멈춘다. 검사 대상을 미들웨어의 dispatch 까지 넓힌다.
def _is_middleware_dispatch(node: ast.AST, class_bases: tuple[str, ...]) -> bool:
    """True for `async def dispatch` inside a BaseHTTPMiddleware subclass."""
    return (
        isinstance(node, ast.AsyncFunctionDef)
        and node.name == "dispatch"
        and "BaseHTTPMiddleware" in class_bases
    )


def _event_loop_entries(tree: ast.Module) -> list[ast.AsyncFunctionDef]:
    """Every `async def` that FastAPI/Starlette runs directly on the event loop."""
    entries = [node for node in ast.walk(tree) if _is_route_handler(node)]
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        bases = tuple(
            base.id if isinstance(base, ast.Name)
            else base.attr if isinstance(base, ast.Attribute) else ""
            for base in node.bases
        )
        entries.extend(
            child for child in node.body if _is_middleware_dispatch(child, bases)
        )
    return entries


def _offloaded_function_names(tree: ast.Module) -> set[str]:
    """Names handed to an offload wrapper as a callable — `run_sync(_mark)`.

    Without this the detector reports the body of `_mark` as blocking: the nested `def`
    is a child of the enclosing function, so the walk reaches it directly instead of
    through the `run_sync(...)` call whose subtree it skips. The work genuinely runs in a
    worker thread, so reporting it would be a false alarm — and one false alarm is all it
    takes for the next person to add an opt-out marker instead of a fix.
    """
    names: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or _called_name(node) not in _OFFLOAD_FUNCS:
            continue
        for arg in node.args:
            if isinstance(arg, ast.Name):
                names.add(arg.id)
    return names


def _root_name(node: ast.AST) -> str | None:
    """Leftmost Name of a dotted expression: `a.b.c()` -> 'a'."""
    while isinstance(node, ast.Attribute):
        node = node.value
    return node.id if isinstance(node, ast.Name) else None


def _called_name(call: ast.Call) -> str | None:
    """Final attribute or bare name being called: `a.b.c()` -> 'c', `f()` -> 'f'."""
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return None


def _describe_blocking_call(call: ast.Call) -> str | None:
    """Return a human-readable reason if `call` is synchronous blocking work."""
    func = call.func

    if isinstance(func, ast.Name):
        if func.id in _BLOCKING_CALLS:
            return f"{func.id}() — blocking I/O"
        return None

    if isinstance(func, ast.Attribute):
        root = _root_name(func)
        if _is_sync_module(root):
            return f"{root}.{func.attr}() — synchronous service/db call"
        if root == "os" and func.attr in _BLOCKING_OS_FUNCS:
            return f"os.{func.attr}() — blocking filesystem call"
        if func.attr in _BLOCKING_PATH_METHODS:
            return f".{func.attr}() — blocking file I/O"
        if func.attr in _BLOCKING_CALLS:
            return f".{func.attr}() — blocking I/O"
    return None


def find_blocking_calls(source: str, filename: str = "<src>") -> list[str]:
    """Report blocking work reachable on the event loop from `async def` handlers."""
    tree = ast.parse(source, filename=filename)
    lines = source.splitlines()

    # Module-level functions, so an indirect call can be followed into its body.
    local_funcs: dict[str, ast.AST] = {
        n.name: n
        for n in tree.body
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    }

    offloaded = _offloaded_function_names(tree)

    findings: list[str] = []
    seen: set[str] = set()

    def report(node: ast.Call, reason: str, handler: str, via: list[str]) -> None:
        line = lines[node.lineno - 1] if node.lineno <= len(lines) else ""
        if _OPT_OUT in line:
            return
        trail = " -> ".join(via + [reason]) if via else reason
        msg = f"{filename}:{node.lineno} {handler}(): {trail}"
        if msg not in seen:
            seen.add(msg)
            findings.append(msg)

    def walk(node: ast.AST, handler: str, via: list[str], stack: tuple[str, ...]) -> None:
        for child in ast.iter_child_nodes(node):
            # A nested def handed to run_sync() runs in a worker thread — see
            # _offloaded_function_names.
            if (
                isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                and child.name in offloaded
            ):
                continue
            if isinstance(child, ast.Call):
                name = _called_name(child)

                # The one escape hatch: arguments of an offload wrapper run in a
                # worker thread. Skip its subtree entirely.
                if name in _OFFLOAD_FUNCS:
                    continue

                reason = _describe_blocking_call(child)
                if reason is not None:
                    report(child, reason, handler, via)

                # Follow the call into a function defined in this same module.
                # Awaiting a local coroutine does NOT leave the event loop.
                if name in local_funcs and name not in stack:
                    walk(
                        local_funcs[name],
                        handler,
                        via + [f"{name}()"],
                        stack + (name,),
                    )

            walk(child, handler, via, stack)

    for node in _event_loop_entries(tree):
        for child in ast.iter_child_nodes(node):
            if child in node.decorator_list:
                continue
            walk(child, node.name, [], (node.name,))

    return findings


_MODULE_ROOT = _SERVER_DIR / "modules" / "flow_gate"


def _router_sources() -> list[Path]:
    """Every file that can define something Starlette runs on the event loop.

    0394 T0016 항목 4: 라우터 파일(api/)에 더해, 미들웨어를 정의하는 파일도 포함한다 —
    `GroupMutationPolicyMiddleware` 는 services/ 에 있어서 예전 범위(api/ 전용) 밖이었고,
    그래서 모든 변경 요청이 지나는 dispatch 가 한 번도 검사되지 않았다.
    """
    files = {p for p in _API_DIR.rglob("*.py") if p.name != "__init__.py"}
    for path in _MODULE_ROOT.rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        if "BaseHTTPMiddleware" in path.read_text(encoding="utf-8-sig"):
            files.add(path)
    return sorted(files)


@pytest.mark.skipif(not _API_DIR.is_dir(), reason="api source tree not present")
def test_no_blocking_calls_in_async_route_handlers():
    """No `async def` route handler may reach synchronous DB/filesystem work.

    See the module docstring. If this fails, do NOT add the opt-out marker
    reflexively — convert the handler to a plain `def`, or push the blocking call
    through `anyio.to_thread.run_sync`.
    """
    findings: list[str] = []
    for path in _router_sources():
        rel = path.relative_to(_SERVER_DIR).as_posix()
        if rel in _KNOWN_UNFIXED:
            continue
        findings.extend(find_blocking_calls(path.read_text(encoding="utf-8"), filename=rel))

    assert not findings, (
        "Blocking work found on the event loop in async route handlers "
        "(0275 T0005 / 0279 T0005 regression):\n  " + "\n  ".join(findings)
    )


@pytest.mark.skipif(not _API_DIR.is_dir(), reason="api source tree not present")
def test_known_unfixed_entries_are_still_accurate():
    """Every _KNOWN_UNFIXED entry must exist AND still be blocking.

    Without this, a stale allowlist would silently keep excluding a file long
    after it was fixed — re-opening the hole this guard exists to close.
    """
    for rel in sorted(_KNOWN_UNFIXED):
        path = _SERVER_DIR / rel
        assert path.is_file(), f"_KNOWN_UNFIXED names a missing file: {rel}"
        findings = find_blocking_calls(path.read_text(encoding="utf-8"), filename=rel)
        assert findings, (
            f"{rel} no longer blocks the event loop — remove it from "
            f"_KNOWN_UNFIXED so it is guarded from now on."
        )


# ── Self-tests for the detector itself ────────────────────────────────────────
# A guard that silently stops detecting is worse than no guard, so pin its
# behaviour on the exact code shapes this task changed.

_BUGGY = '''
@router.post("/remote/{operation}")
async def remote_tool(request: Request, operation: str):
    body = await request.json()
    status, payload = remote_tool_service.handle(operation, None, body)
    return status
'''

_FIXED_REFERENCE_FORM = '''
@router.post("/remote/{operation}")
async def remote_tool(request: Request, operation: str):
    body = await request.json()
    status, payload = await anyio.to_thread.run_sync(
        remote_tool_service.handle, operation, None, body
    )
    return status
'''

_FIXED_LAMBDA_FORM = '''
@router.post("/storage/folder")
async def api_create_folder(request: Request):
    body = await request.json()
    return await anyio.to_thread.run_sync(
        lambda: process_service.create_storage_folder(name=body.get("name", ""))
    )
'''

_PLAIN_DEF_IS_FINE = '''
@router.get("/brief")
def api_brief():
    return service.envelope("brief", service.get_brief())
'''

_BLOCKING_WRITE = '''
@router.post("/projects/{project_id}/files/upload")
async def upload_files(request: Request, project_id: str):
    os.makedirs("/x", exist_ok=True)
    with open("/x/y", "wb") as fh:
        fh.write(b"")
'''

# The inbox_routes shape: the handler itself looks clean, the blocking is one
# level down behind an `await` on a local coroutine.
_INDIRECT_VIA_AWAITED_LOCAL_COROUTINE = '''
@router.post("/inbox")
async def inbox(request: Request):
    body = await request.json()
    return await _handle_new(request, body)

async def _handle_new(request, body):
    target.write_text(body["content"], encoding="utf-8")
    return 200
'''


def test_detector_flags_the_original_bug():
    findings = find_blocking_calls(_BUGGY)
    assert len(findings) == 1, findings
    assert "remote_tool_service.handle()" in findings[0]


def test_detector_flags_blocking_filesystem_writes():
    reasons = " ".join(find_blocking_calls(_BLOCKING_WRITE))
    assert "os.makedirs()" in reasons
    assert "open()" in reasons


def test_detector_follows_awaited_local_coroutines():
    """awaiting a local coroutine does not leave the loop — this must be caught."""
    findings = find_blocking_calls(_INDIRECT_VIA_AWAITED_LOCAL_COROUTINE)
    assert len(findings) == 1, findings
    assert "_handle_new()" in findings[0], findings
    assert "write_text" in findings[0], findings


@pytest.mark.parametrize(
    "source",
    [_FIXED_REFERENCE_FORM, _FIXED_LAMBDA_FORM, _PLAIN_DEF_IS_FINE],
    ids=["to_thread-reference", "to_thread-lambda", "plain-def"],
)
def test_detector_accepts_correct_forms(source):
    assert find_blocking_calls(source) == []


def test_opt_out_marker_suppresses_a_finding():
    source = _BUGGY.replace(
        "remote_tool_service.handle(operation, None, body)",
        "remote_tool_service.handle(operation, None, body)  # noqa: event-loop-blocking",
    )
    assert find_blocking_calls(source) == []


def test_detector_terminates_on_recursive_helpers():
    """Mutually recursive helpers must not hang or blow the stack."""
    source = '''
@router.post("/x")
async def x(request: Request):
    return await a()

async def a():
    return await b()

async def b():
    open("/tmp/f")
    return await a()
'''
    findings = find_blocking_calls(source)
    assert len(findings) == 1, findings
