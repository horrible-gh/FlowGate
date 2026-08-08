"""AST inventory for every production ai_invoke_service.start_run caller (0396)."""
from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

_SERVER_DIR = Path(__file__).resolve().parents[1]
_MODULES_DIR = _SERVER_DIR / "modules"


@dataclass(frozen=True)
class Caller:
    path: Path
    tree: ast.Module
    call: ast.Call
    scope: ast.AST | None


def _function_name(node: ast.AST | None) -> str:
    return node.name if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) else "<module>"


def _nearest_function(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> ast.AST | None:
    current = parents.get(node)
    while current is not None:
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return current
        current = parents.get(current)
    return None


def _is_start_run_call(node: ast.Call) -> bool:
    func = node.func
    return (
        isinstance(func, ast.Attribute)
        and isinstance(func.value, ast.Name)
        and func.value.id == "ai_invoke_service"
        and func.attr == "start_run"
    ) or (isinstance(func, ast.Name) and func.id == "start_run")


def _collect_callers() -> list[Caller]:
    callers: list[Caller] = []
    for path in sorted(_MODULES_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        parents = {
            child: parent
            for parent in ast.walk(tree)
            for child in ast.iter_child_nodes(parent)
        }
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _is_start_run_call(node):
                callers.append(Caller(path, tree, node, _nearest_function(node, parents)))
    return callers


def _declares_run_id(args: ast.arguments) -> bool:
    names = [arg.arg for arg in (*args.posonlyargs, *args.args, *args.kwonlyargs)]
    return "ai_run_id" in names or args.kwarg is not None


def _nearest_owner(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> ast.AST | None:
    current = parents.get(node)
    while current is not None:
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return current
        current = parents.get(current)
    return None


def _name_assignments(
    name: str,
    tree: ast.Module,
    scope: ast.AST | None,
) -> list[ast.AST]:
    parents = {
        child: parent
        for parent in ast.walk(tree)
        for child in ast.iter_child_nodes(parent)
    }
    values: list[ast.AST] = []
    for node in ast.walk(scope or tree):
        if _nearest_owner(node, parents) is not scope:
            continue
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == name for target in node.targets
        ):
            values.append(node.value)
        elif (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == name
            and node.value is not None
        ):
            values.append(node.value)
    return values


def _named_defs(name: str, tree: ast.Module) -> list[ast.AST]:
    return [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name
    ]


def _safe_builder(
    value: ast.AST,
    tree: ast.Module,
    scope: ast.AST | None,
    seen: frozenset[str] = frozenset(),
) -> tuple[bool, str]:
    if isinstance(value, ast.Constant) and value.value is None:
        return True, "engine-issued token"
    if isinstance(value, ast.Lambda):
        if _declares_run_id(value.args):
            return True, "lambda accepts ai_run_id"
        return False, "lambda does not accept ai_run_id or **kwargs"
    if isinstance(value, ast.Name):
        if value.id in seen:
            return False, f"recursive builder alias {value.id!r}"
        defs = _named_defs(value.id, tree)
        if defs:
            unsafe = [node for node in defs if not _declares_run_id(node.args)]
            if unsafe:
                return False, f"function {value.id} does not accept ai_run_id or **kwargs"
            return True, f"function {value.id} accepts ai_run_id"
        assignments = _name_assignments(value.id, tree, scope)
        if not assignments:
            return False, f"cannot resolve builder name {value.id!r}"
        verdicts = [
            _safe_builder(item, tree, scope, seen | {value.id})
            for item in assignments
        ]
        failures = [reason for ok, reason in verdicts if not ok]
        if failures:
            return False, "; ".join(failures)
        return True, f"all assignments to {value.id} are safe"
    return False, f"unsupported issue_builder expression {ast.dump(value, include_attributes=False)}"


def test_every_start_run_custom_issuer_accepts_the_run_id():
    callers = _collect_callers()
    assert callers, "AST inventory found zero start_run callers"
    assert len(callers) >= 5, (
        f"AST inventory found only {len(callers)} start_run callers; expected at least 5"
    )

    identities = {
        (
            caller.path.relative_to(_SERVER_DIR).as_posix(),
            _function_name(caller.scope),
        )
        for caller in callers
    }
    expected = {
        ("modules/flow_gate/api/v1/ai_invoke_routes.py", "start_ai_invoke"),
        ("modules/flow_gate/api/v1/qa_routes.py", "post_answer"),
        ("modules/flow_gate/services/q_answer_invoke_service.py", "dispatch_answer_run"),
        ("modules/flow_gate/services/ai_invoke_service.py", "_spawn_auto_resume"),
        ("modules/flow_gate/services/ai_invoke_service.py", "resume_chain"),
    }
    assert expected <= identities, (
        "start_run inventory missed known production callers: "
        + ", ".join(f"{path}:{func}" for path, func in sorted(expected - identities))
    )

    failures: list[str] = []
    for caller in callers:
        keyword = next(
            (item for item in caller.call.keywords if item.arg == "issue_builder"),
            None,
        )
        if keyword is None:
            continue
        ok, reason = _safe_builder(keyword.value, caller.tree, caller.scope)
        if not ok:
            rel = caller.path.relative_to(_SERVER_DIR).as_posix()
            failures.append(
                f"{rel}:{caller.call.lineno} ({_function_name(caller.scope)}): {reason}"
            )

    assert failures == [], (
        "Unsafe or unresolved start_run issue_builder(s):\n- "
        + "\n- ".join(failures)
        + "\nA worker from this caller will hit 403 GROUP_AI_RUN_OWNER_MISMATCH "
        "when it mutates its own run's group. Declare ai_run_id (or **kwargs), "
        "forward it to token issuance, or add a reviewed allowlist entry with a reason."
    )
