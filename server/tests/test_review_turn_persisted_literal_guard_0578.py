"""Persisted server literal regression guard (flowgate.default.0578 T0012 §2.5).

``_materialize_pending_conversation_run`` (T1, 0578 T0006 §2.2) stores a
``conversation[].message`` that is either (a) content passed through verbatim from a
caller -- the AI's own ``last_message`` -- or (b) an empty string paired with a
``message_code``/``message_params``, never a sentence authored inside this function.
Nothing enforces that AS A SOURCE STRUCTURE: ``test_review_turn_messages_0578.py``'s 64
tests pin runtime behaviour (which code appears under which condition), not the source
shape. This guard walks the AST of the function and fails when a ``"message"`` target
-- a dict-literal key OR a ``turn["message"] = ...`` subscript assignment, T0012 §2.5's
reopened rejection finding -- is assigned anything other than (a) an empty-string
placeholder or (b) an expression that resolves, through attribute/subscript/method-call
navigation, all the way back to names this function received from OUTSIDE itself: its
own parameters, or an ``except ... as name`` binding.

Automated review (rev1) found that this second branch was checked far too loosely: any
name the function had assigned to LOCALLY -- regardless of what it was assigned FROM --
was accepted outright. That let two aliasing bypasses through undetected:
``fresh = "새로 작성한 문장"; turn["message"] = fresh`` and
``fresh = _build_new_sentence(); conversation.append({"message": fresh})`` -- both
functionally identical to the direct literal/builder-call cases this guard already
caught, just one assignment hop removed. Fixed here by tracing a local name's OWN
right-hand side(s) back to trusted roots (this function's own parameters, or an
``except ... as name`` binding) instead of trusting the name on sight, exactly as
rev1 asked ("로컬 바인딩의 RHS까지 역추적해 파라미터 유래 값만 허용"):
- a non-empty string literal anywhere along that trace is still rejected;
- a call -- bare (``f(x)``) or off an object (``obj.f(x)``) -- is trusted only when at
  least one of its arguments itself traces back to a trusted root; a call given no
  arguments, or only untrusted ones, is never trusted regardless of how it is written.
  This is what distinguishes the two rejected aliases above (``_build_new_sentence()``
  takes no argument at all -- nothing ties its result back to anything this function
  received) from the real function's own chain -- ``db_git.get_session(merge_id)`` ->
  ``db_git.session_context(...)`` -> ``context.get("pending_conversation_run_id")`` ->
  ``_conversation_run_detail(run_id)`` -- where every hop is fed something that
  ultimately traces back to the ``merge_id`` parameter. An object-call's receiver
  being trusted (``detail.get("last_message")``, receiver ``detail`` already
  established) is an alternative, independent way to satisfy the same call, since
  that is genuine content-navigation rather than construction;
- a name that is neither a trusted root nor traceable to one (a module-level constant
  smuggled in by name) is still rejected.
"""
from __future__ import annotations

import ast
import os
import sys
from pathlib import Path

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")

_SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SERVER_DIR))

# flowgate.default.0578 T0012 §2.5: the turn-building function T1 already converted.
# If a sibling function starts writing `conversation[]`/session-stored turns directly
# (rather than through this one), add its name here too.
_GUARDED_FUNCTIONS = ("_materialize_pending_conversation_run",)


def _find_function(tree: ast.Module, name: str) -> ast.FunctionDef:
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"function {name!r} not found")


def _collect_target_names(target: ast.AST, names: set) -> None:
    if isinstance(target, ast.Name):
        names.add(target.id)
    elif isinstance(target, (ast.Tuple, ast.List)):
        for elt in target.elts:
            _collect_target_names(elt, names)
    elif isinstance(target, ast.Starred):
        _collect_target_names(target.value, names)


def _trusted_root_names(func: ast.FunctionDef) -> set:
    """Names this function received from OUTSIDE itself: its own parameters, and any
    ``except ... as name`` binding (the caught exception object -- not authored text
    either). These are the only names allowed to ground a trust chain; a plain local
    assignment does NOT belong here any more -- see ``_assignment_rhs_map``."""
    names: set = set()
    args = func.args
    for arglist in (args.posonlyargs, args.args, args.kwonlyargs):
        for a in arglist:
            names.add(a.arg)
    if args.vararg:
        names.add(args.vararg.arg)
    if args.kwarg:
        names.add(args.kwarg.arg)
    for node in ast.walk(func):
        if isinstance(node, ast.ExceptHandler) and node.name:
            names.add(node.name)
    return names


def _assignment_rhs_map(func: ast.FunctionDef) -> dict:
    """Every name this function assigns locally, mapped to the right-hand side
    expression(s) it was assigned from (a name reassigned more than once maps to every
    RHS it was ever given). This is what lets the guard trace ``answer`` back through
    ``answer = (detail.get("last_message") or "").strip()`` to ``detail``, and ``detail``
    back through ``detail, lost = _conversation_run_detail(run_id)`` to the trusted
    ``run_id`` parameter -- instead of trusting ``answer``/``detail`` on sight."""
    rhs_map: dict = {}

    def add(names: set, value) -> None:
        if value is None:
            return
        for name in names:
            rhs_map.setdefault(name, []).append(value)

    for node in ast.walk(func):
        if isinstance(node, ast.Assign):
            names: set = set()
            for t in node.targets:
                _collect_target_names(t, names)
            add(names, node.value)
        elif isinstance(node, ast.AnnAssign):
            names = set()
            _collect_target_names(node.target, names)
            add(names, node.value)
        elif isinstance(node, ast.AugAssign):
            names = set()
            _collect_target_names(node.target, names)
            add(names, node.value)
        elif isinstance(node, ast.For):
            names = set()
            _collect_target_names(node.target, names)
            add(names, node.iter)
        elif isinstance(node, ast.withitem) and node.optional_vars is not None:
            names = set()
            _collect_target_names(node.optional_vars, names)
            add(names, node.context_expr)
    return rhs_map


def _is_trusted_message_value(
    node: ast.AST, roots: set, rhs_map: dict, seen: frozenset = frozenset(),
) -> bool:
    """True iff ``node`` resolves entirely to trusted roots (this function's own
    parameters, or an ``except ... as name`` binding) through attribute/subscript/
    call navigation and/or local-variable aliasing -- see the module docstring for
    the call-trust rule (an argument must trace back to a trusted root; a receiver
    that traces back is an independent, alternative way to satisfy the same call)."""
    if isinstance(node, ast.Constant):
        return not (isinstance(node.value, str) and node.value != "")
    if isinstance(node, ast.Name):
        if node.id in roots:
            return True
        if node.id in seen:
            # Already mid-resolution further up this same chain (e.g. `detail = detail
            # or {}` reassigning itself) -- don't let a self-reference count against it.
            return True
        candidates = rhs_map.get(node.id)
        if not candidates:
            return False
        next_seen = seen | {node.id}
        return all(_is_trusted_message_value(c, roots, rhs_map, next_seen) for c in candidates)
    if isinstance(node, ast.Attribute):
        return _is_trusted_message_value(node.value, roots, rhs_map, seen)
    if isinstance(node, ast.Subscript):
        return _is_trusted_message_value(node.value, roots, rhs_map, seen)
    if isinstance(node, ast.Call):
        call_args = list(node.args) + [kw.value for kw in node.keywords]
        args_trusted = any(_is_trusted_message_value(a, roots, rhs_map, seen) for a in call_args)
        if isinstance(node.func, ast.Attribute):
            receiver_trusted = _is_trusted_message_value(node.func.value, roots, rhs_map, seen)
            return receiver_trusted or args_trusted
        if isinstance(node.func, ast.Name):
            return args_trusted
        return False
    if isinstance(node, ast.BoolOp):
        return all(_is_trusted_message_value(v, roots, rhs_map, seen) for v in node.values)
    if isinstance(node, ast.IfExp):
        return _is_trusted_message_value(
            node.body, roots, rhs_map, seen
        ) and _is_trusted_message_value(node.orelse, roots, rhs_map, seen)
    if isinstance(node, ast.Starred):
        return _is_trusted_message_value(node.value, roots, rhs_map, seen)
    if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        return all(_is_trusted_message_value(e, roots, rhs_map, seen) for e in node.elts)
    if isinstance(node, ast.Dict):
        # A dict literal used as a fallback default (`detail or {}`) is not authored
        # message text -- only its own values (and keys) need to trace back.
        return all(
            _is_trusted_message_value(v, roots, rhs_map, seen) for v in node.values if v is not None
        ) and all(_is_trusted_message_value(k, roots, rhs_map, seen) for k in node.keys if k is not None)
    return False


def _is_message_subscript_target(target: ast.AST) -> bool:
    if not isinstance(target, ast.Subscript):
        return False
    sl = target.slice
    # ast.Index wrapped the slice before Python 3.9; unwrap it if present.
    if hasattr(ast, "Index") and isinstance(sl, ast.Index):  # pragma: no cover - py<3.9
        sl = sl.value
    return isinstance(sl, ast.Constant) and sl.value == "message"


def _message_value_violations(func: ast.FunctionDef, roots: set, rhs_map: dict) -> list:
    violations: list = []
    for node in ast.walk(func):
        if isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if not (isinstance(key, ast.Constant) and key.value == "message"):
                    continue
                if not _is_trusted_message_value(value, roots, rhs_map):
                    violations.append(f"line {getattr(value, 'lineno', '?')}: {ast.dump(value)}")
        elif isinstance(node, ast.Assign):
            if node.value is None:
                continue
            for target in node.targets:
                if _is_message_subscript_target(target) and not _is_trusted_message_value(
                    node.value, roots, rhs_map
                ):
                    violations.append(
                        f"line {getattr(node.value, 'lineno', '?')}: {ast.dump(node.value)}"
                    )
        elif isinstance(node, ast.AnnAssign):
            if node.value is not None and _is_message_subscript_target(node.target) and not (
                _is_trusted_message_value(node.value, roots, rhs_map)
            ):
                violations.append(
                    f"line {getattr(node.value, 'lineno', '?')}: {ast.dump(node.value)}"
                )
    return violations


def _violations_for_source(source: str) -> list:
    tree = ast.parse(source)
    func = _find_function(tree, "_materialize_pending_conversation_run")
    return _message_value_violations(func, _trusted_root_names(func), _assignment_rhs_map(func))


def test_synthetic_fresh_literal_in_message_key_is_caught():
    """RED proof: a fresh non-empty literal assigned directly to a ``"message"`` dict
    key is caught."""
    source = (
        "def _materialize_pending_conversation_run(x):\n"
        "    conversation.append({\n"
        "        'turn_id': 't', 'role': 'ai',\n"
        "        'message': 'the approval target changed, so nothing was applied',\n"
        "        'message_code': None, 'message_params': {},\n"
        "    })\n"
    )
    violations = _violations_for_source(source)
    assert violations, "the guard did not catch a freshly-authored literal in a 'message' key"


def test_synthetic_subscript_assignment_to_message_key_is_caught():
    """RED proof (T0012 §2.5 reopened finding): ``turn["message"] = "..."`` is a
    subscript assignment, not a dict-literal key -- the original guard only walked
    ``ast.Dict`` nodes and missed this shape entirely."""
    source = (
        "def _materialize_pending_conversation_run(x):\n"
        "    turn = {'turn_id': 't', 'role': 'ai'}\n"
        "    turn['message'] = '어떤 새 문장'\n"
        "    conversation.append(turn)\n"
    )
    violations = _violations_for_source(source)
    assert violations, "the guard did not catch turn['message'] = <literal> subscript assignment"


def test_synthetic_builder_call_and_unresolved_constant_in_message_key_are_caught():
    """RED proof (T0012 §2.5 reopened finding): a bare builder call or a reference to
    a name this function never bound has no non-empty string literal directly inside
    it, so the old "no literal anywhere in the expression" rule alone let both
    through. Neither is the caller's own text nor the code+params placeholder."""
    source = (
        "def _materialize_pending_conversation_run(x):\n"
        "    conversation.append({'turn_id': 't', 'role': 'ai', 'message': _build_new_sentence()})\n"
        "    conversation.append({'turn_id': 't2', 'role': 'ai', 'message': SOME_MODULE_CONSTANT})\n"
    )
    violations = _violations_for_source(source)
    assert len(violations) == 2, (
        "the guard should catch both the bare builder call and the unresolved constant name, "
        f"got {violations!r}"
    )


def test_synthetic_local_alias_of_fresh_literal_in_message_key_is_caught():
    """RED proof (automated review rev1 finding): aliasing a fresh literal through an
    intermediate local variable before the subscript assignment must not launder it.
    ``fresh`` is a local binding, but its OWN right-hand side is a freshly-authored
    non-empty string literal, not anything traceable to a parameter -- the pre-fix
    guard trusted any locally-assigned name outright and missed this."""
    source = (
        "def _materialize_pending_conversation_run(x):\n"
        "    fresh = '새로 작성한 문장'\n"
        "    turn = {'turn_id': 't', 'role': 'ai'}\n"
        "    turn['message'] = fresh\n"
        "    conversation.append(turn)\n"
    )
    violations = _violations_for_source(source)
    assert violations, "the guard did not catch a literal laundered through a local alias"


def test_synthetic_local_alias_of_builder_call_in_message_key_is_caught():
    """RED proof (automated review rev1 finding): aliasing a bare builder-call result
    through an intermediate local variable before the dict-literal key must not
    launder it either. ``fresh`` is a local binding, but its own right-hand side is a
    zero-argument call to a plain identifier -- never traceable to a parameter."""
    source = (
        "def _materialize_pending_conversation_run(x):\n"
        "    fresh = _build_new_sentence()\n"
        "    conversation.append({'turn_id': 't', 'role': 'ai', 'message': fresh})\n"
    )
    violations = _violations_for_source(source)
    assert violations, "the guard did not catch a builder call laundered through a local alias"


def test_synthetic_passthrough_and_empty_message_values_are_allowed():
    """Passthrough content reached through the real function's own shape -- a call
    that receives the trusted parameter (`_conversation_run_detail(x)`, mirroring
    ``_conversation_run_detail(run_id)``), tuple-unpacked into a local, then navigated
    via ``.get(...)``/``.strip()`` -- must still be allowed, alongside an empty-string
    placeholder. This is what distinguishes a legitimate one-hop bare call (trusted
    only because it is given the trusted parameter as an argument) from the two alias
    bypasses above (a literal, or a zero-argument call untethered from any parameter)."""
    source = (
        "def _materialize_pending_conversation_run(x):\n"
        "    detail, lost = _conversation_run_detail(x)\n"
        "    detail = detail or {}\n"
        "    answer = (detail.get('last_message') or '').strip()\n"
        "    conversation.append({'turn_id': 't', 'role': 'ai', 'message': answer})\n"
        "    conversation.append({'turn_id': 't2', 'role': 'ai', 'message': ''})\n"
    )
    assert _violations_for_source(source) == []


def test_synthetic_alias_call_with_untrusted_argument_is_still_caught():
    """A one-hop bare call is only trusted when it is actually GIVEN a trusted
    argument -- passing a fresh literal into the call must not launder it either."""
    source = (
        "def _materialize_pending_conversation_run(x):\n"
        "    fresh = _build_new_sentence('전에 없던 문장')\n"
        "    conversation.append({'turn_id': 't', 'role': 'ai', 'message': fresh})\n"
    )
    violations = _violations_for_source(source)
    assert violations, "a bare call fed only a fresh literal argument must not be trusted"


def test_git_service_materialize_pending_conversation_run_has_no_fresh_message_literal():
    """The real guard, run against the actual source."""
    path = _SERVER_DIR / "modules" / "flow_gate" / "services" / "git_service.py"
    tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
    for name in _GUARDED_FUNCTIONS:
        func = _find_function(tree, name)
        roots = _trusted_root_names(func)
        rhs_map = _assignment_rhs_map(func)
        violations = _message_value_violations(func, roots, rhs_map)
        assert not violations, (
            f"{name} assigns a disallowed value to a stored 'message' key/target:\n"
            + "\n".join(violations)
        )
