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
        if root in _SYNC_MODULE_NAMES:
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

    for node in ast.walk(tree):
        if _is_route_handler(node):
            for child in ast.iter_child_nodes(node):
                if child in node.decorator_list:
                    continue
                walk(child, node.name, [], (node.name,))

    return findings


def _router_sources() -> list[Path]:
    return sorted(p for p in _API_DIR.rglob("*.py") if p.name != "__init__.py")


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
