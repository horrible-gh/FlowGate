"""flowgate.default.0550 T0030 (NR0025 권고3): every facade-attribute patch a test in
this corpus performs on `git_service.<name>` must actually be reachable from inside the
`git/` package -- not shadowed by a module that bound its own early copy at import time,
and not read as a bare name inside the very module that defines it.

Two failure modes share one root cause (D0006 §3.2's split turned one `git_service.py`
module into a facade + a dozen `git/*.py` modules, and only call sites written as
`_gs.<name>` still see a later `setattr(git_service, "<name>", fake)`):

  * **B-1 (early binding).** A `git/*.py` module does
    ``from .other_module import <name>`` at its own top level. That import runs once,
    at package-import time, and binds `<name>` in the importing module's own
    `__dict__` -- a patch on `git_service.<name>` afterwards never touches that copy.

  * **B-2 (bare self-reference).** A `git/*.py` module defines `<name>` itself
    (`def`/`class`/top-level assignment) and some other function in the *same* module
    calls it back as a bare name instead of `_gs.<name>`. Before the module was split
    out of `git_service.py`, every such call WAS a `git_service.__dict__` lookup (there
    was only one module), so a patch on the facade reached it. After the split, the
    bare name resolves to the module's own `__dict__` instead, and the patch is quietly
    ignored.

This file makes both modes mechanical instead of asserted in prose (NR0025 §3's
one-off scan, generalized): it collects, by AST, every name any test in
`server/tests/**/*.py` actually patches on some alias of `git_service` (patch-form A),
collects every `git/*.py` module's early-bound imports (B-1) and its own bare
self-references (B-2), and fails loudly -- naming the exact symbol and every file:line
involved -- the moment a future edit reintroduces either shape for a name some test
relies on the facade seam for.

Design choices carried over from the model for this pattern, `server/tests/
test_ai_invoke_svc_seam_scope_0501.py` (T0012 §13-15 / NR0003 §28):
  1. The `git/*.py` module list comes from a glob, not a hardcoded tuple -- a future
     split must not have to remember to add itself to a test's private list.
  2. Patch detection is alias-agnostic -- `monkeypatch.setattr(gs, ...)`,
     `setattr(svc, ...)` and `patch.object(git_service, ...)` are the same patch
     regardless of the local name a test file imported `git_service` under.
0501 scans with regex; this file scans with `ast` instead, because B-1/B-2 need real
scope information (module-top-level vs. nested, Load vs. Store context, a function's
default-value sub-expressions vs. its body) that a line-oriented regex cannot see
reliably.
"""
from __future__ import annotations

import ast
from pathlib import Path

_SERVER_DIR = Path(__file__).resolve().parents[1]
_SERVICES_DIR = _SERVER_DIR / "modules" / "flow_gate" / "services"
_GIT_PKG_DIR = _SERVICES_DIR / "git"
_TESTS_DIR = Path(__file__).resolve().parent

# Physical module list, globbed rather than hardcoded (design choice 1 above).
# git_service.py itself (the facade) is not a target: patching its own __dict__ is
# exactly what a facade-attribute patch already changes, so it can never be "unreached".
_GIT_MODULE_PATHS = tuple(
    sorted(p for p in _GIT_PKG_DIR.glob("*.py") if p.stem != "__init__")
)

_FACADE_DOTTED_PREFIX = "modules.flow_gate.services.git_service."


# ─────────────────────────── A면: 시험이 패치하는 이름 ───────────────────────────

def _git_service_aliases(tree: ast.AST) -> set[str]:
    """Every local name this file's imports bind to the `git_service` module.

    Walks the whole tree (not just the top level) because several test files import
    `git_service` lazily inside a fixture or test function rather than at module scope.
    """
    aliases: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                name = alias.name
                if name == "modules.flow_gate.services.git_service" or name.endswith(
                    ".git_service"
                ):
                    aliases.add(alias.asname or name.rsplit(".", 1)[-1])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.name == "git_service":
                    aliases.add(alias.asname or "git_service")
    return aliases


def _is_setattr_delattr_call(func: ast.expr) -> bool:
    if isinstance(func, ast.Name):
        return func.id in ("setattr", "delattr")
    if isinstance(func, ast.Attribute):
        return func.attr in ("setattr", "delattr")
    return False


def _is_patch_object_call(func: ast.expr) -> bool:
    # Matches `patch.object(...)` and `mock.patch.object(...)` (and any other alias
    # chain that ends in `.patch.object(...)`), not just the literal spelling `patch`.
    if not (isinstance(func, ast.Attribute) and func.attr == "object"):
        return False
    value = func.value
    if isinstance(value, ast.Name):
        return value.id == "patch"
    if isinstance(value, ast.Attribute):
        return value.attr == "patch"
    return False


def _scan_test_source(label: str, source: str) -> tuple[set[str], dict, list]:
    """Patch-form A for one test file's source text.

    Returns (patched_names, sites, unresolved):
      * patched_names -- every name this file patches on a `git_service` alias.
      * sites -- name -> [(label, lineno), ...], every call/assignment site that
        patched it (for failure-message provenance).
      * unresolved -- [(label, lineno), ...] dynamic patches this scanner cannot
        resolve to a literal name (§3.4.1: first arg is an alias, second is not a
        string constant) -- these must total 0 in the real corpus, or the scan itself
        is silently blind to whatever they do.
    """
    tree = ast.parse(source, filename=label)
    aliases = _git_service_aliases(tree)

    patched: set[str] = set()
    sites: dict = {}
    unresolved: list = []

    def _record(name: str, lineno: int) -> None:
        patched.add(name)
        sites.setdefault(name, []).append((label, lineno))

    if not aliases:
        return patched, sites, unresolved

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if (
                (_is_setattr_delattr_call(func) or _is_patch_object_call(func))
                and node.args
                and isinstance(node.args[0], ast.Name)
                and node.args[0].id in aliases
            ):
                if (
                    len(node.args) >= 2
                    and isinstance(node.args[1], ast.Constant)
                    and isinstance(node.args[1].value, str)
                ):
                    _record(node.args[1].value, node.lineno)
                else:
                    unresolved.append((label, node.lineno))
            # Form 2: a bare dotted-string patch target, e.g.
            # mock.patch("modules.flow_gate.services.git_service._run_git"), decorator
            # form included (ast.walk descends into decorator_list). Alias-independent
            # by design -- the string names the facade module directly.
            if node.args and isinstance(node.args[0], ast.Constant):
                value = node.args[0].value
                if isinstance(value, str) and value.startswith(_FACADE_DOTTED_PREFIX):
                    rest = value[len(_FACADE_DOTTED_PREFIX):]
                    if rest and "." not in rest:
                        _record(rest, node.lineno)
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if (
                    isinstance(target, ast.Attribute)
                    and isinstance(target.value, ast.Name)
                    and target.value.id in aliases
                ):
                    _record(target.attr, node.lineno)

    return patched, sites, unresolved


def _scan_test_corpus() -> tuple[set[str], dict, list]:
    patched: set[str] = set()
    sites: dict = {}
    unresolved: list = []
    for path in sorted(_TESTS_DIR.glob("**/*.py")):
        # utf-8-sig: a handful of files in this corpus carry a BOM: ast.parse rejects
        # a bare utf-8 decode of those ("invalid non-printable character U+FEFF") even
        # though they are otherwise ordinary UTF-8 source.
        source = path.read_text(encoding="utf-8-sig")
        label = str(path.relative_to(_SERVER_DIR))
        file_patched, file_sites, file_unresolved = _scan_test_source(label, source)
        patched |= file_patched
        for name, locations in file_sites.items():
            sites.setdefault(name, []).extend(locations)
        unresolved.extend(file_unresolved)
    return patched, sites, unresolved


# ───────────────────── B면: 파사드 패치가 닿지 않는 자리 ─────────────────────────

def _default_value_name_positions(tree: ast.AST) -> set[tuple[int, int]]:
    """(lineno, col_offset) of every `Name` node inside a function's default-value
    expressions (`args.defaults` / `args.kw_defaults`).

    A default-value expression is evaluated exactly once, at `def` time (module import
    time) -- long before any test gets a chance to monkeypatch anything. A name read
    only from such a position can never be reached by a facade patch regardless of
    whether the surrounding module binds it early or late, so §2.3/§3.4.2 requires
    excluding it rather than reporting an unfixable permanent red.
    """
    positions: set[tuple[int, int]] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for default in list(node.args.defaults) + list(node.args.kw_defaults):
                if default is None:
                    continue
                for sub in ast.walk(default):
                    if isinstance(sub, ast.Name):
                        positions.add((sub.lineno, sub.col_offset))
    return positions


def _scan_git_module_source(label: str, source: str) -> tuple[dict, dict, dict]:
    """B-1/B-2 raw material for one `git/*.py` module's source text.

    Returns (import_bound, defined, usage_sites):
      * import_bound -- name -> import statement lineno, for every name a top-level
        `Import`/`ImportFrom` binds (B-1 candidates). Function-local (lazy) imports are
        not top-level nodes and are correctly invisible here.
      * defined -- name -> lineno, for every name a top-level `def`/`class`/assignment
        defines in this module (B-2 candidates).
      * usage_sites -- name -> [(label, lineno), ...], every place in the WHOLE module
        (any nesting depth) that reads the name as a bare `Name` in `Load` context,
        excluding default-value positions. Used both as the B-2 membership test (a
        `defined` name only counts if it has at least one usage site) and as the
        informational "call site" list for B-1 failure messages.
    """
    tree = ast.parse(source, filename=label)

    import_bound: dict = {}
    defined: dict = {}

    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                bound = alias.asname or alias.name.split(".")[0]
                import_bound[bound] = node.lineno
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                bound = alias.asname or alias.name
                import_bound[bound] = node.lineno
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            defined[node.name] = node.lineno
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    defined[target.id] = node.lineno
        elif isinstance(node, ast.AnnAssign):
            if isinstance(node.target, ast.Name):
                defined[node.target.id] = node.lineno

    candidates = set(import_bound) | set(defined)
    excluded = _default_value_name_positions(tree)

    usage_sites: dict = {}
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Name)
            and isinstance(node.ctx, ast.Load)
            and node.id in candidates
            and (node.lineno, node.col_offset) not in excluded
        ):
            usage_sites.setdefault(node.id, []).append((label, node.lineno))

    return import_bound, defined, usage_sites


def _scan_git_modules() -> dict:
    """path -> (import_bound, defined, usage_sites) for every `git/*.py` module."""
    result: dict = {}
    for path in _GIT_MODULE_PATHS:
        label = str(path.relative_to(_SERVER_DIR))
        source = path.read_text(encoding="utf-8")
        result[label] = _scan_git_module_source(label, source)
    return result


def _b1_violations(patched: set[str], sites: dict, git_modules: dict) -> list[dict]:
    violations = []
    for module_label, (import_bound, _defined, usage_sites) in sorted(
        git_modules.items()
    ):
        for name in sorted(set(import_bound) & patched):
            violations.append({
                "name": name,
                "module": module_label,
                "early_bound_at": (module_label, import_bound[name]),
                "bare_call_sites": usage_sites.get(name, []),
                "patched_at": sites.get(name, []),
            })
    return violations


def _b2_violations(patched: set[str], sites: dict, git_modules: dict) -> list[dict]:
    violations = []
    for module_label, (_import_bound, defined, usage_sites) in sorted(
        git_modules.items()
    ):
        for name in sorted(set(defined) & patched):
            call_sites = usage_sites.get(name, [])
            if not call_sites:
                continue
            violations.append({
                "name": name,
                "module": module_label,
                "defined_at": (module_label, defined[name]),
                "bare_call_sites": call_sites,
                "patched_at": sites.get(name, []),
            })
    return violations


def _format_locations(locations) -> str:
    return ", ".join(f"{loc[0]}:{loc[1]}" for loc in locations) or "(none)"


# ──────────────────────────────── 시험 ────────────────────────────────────────

def test_no_facade_patch_target_is_early_bound_in_a_git_module():
    patched, sites, _unresolved = _scan_test_corpus()
    git_modules = _scan_git_modules()
    violations = _b1_violations(patched, sites, git_modules)

    lines = []
    for v in violations:
        lines.append(
            f"  - `{v['name']}`: early-bound import at "
            f"{_format_locations([v['early_bound_at']])}, "
            f"read bare at {_format_locations(v['bare_call_sites'])}, "
            f"patched by test(s) at {_format_locations(v['patched_at'])} "
            f"-- fix: remove the top-level import and call `_gs.{v['name']}(...)` "
            f"(lazy `from modules.flow_gate.services import git_service as _gs`) "
            f"instead of the bare name."
        )
    assert not violations, (
        f"{len(violations)} facade-patched name(s) are shadowed by an early-bound "
        f"import in a git/ module (NR0025 권고2):\n" + "\n".join(lines)
    )


def test_no_facade_patch_target_is_reached_bare_inside_its_module():
    patched, sites, _unresolved = _scan_test_corpus()
    git_modules = _scan_git_modules()
    violations = _b2_violations(patched, sites, git_modules)

    lines = []
    for v in violations:
        lines.append(
            f"  - `{v['name']}`: defined at {_format_locations([v['defined_at']])}, "
            f"called bare at {_format_locations(v['bare_call_sites'])}, "
            f"patched by test(s) at {_format_locations(v['patched_at'])} "
            f"-- fix: call `_gs.{v['name']}(...)` "
            f"(lazy `from modules.flow_gate.services import git_service as _gs`) "
            f"at every call site listed above, instead of the bare name."
        )
    assert not violations, (
        f"{len(violations)} facade-patched name(s) are called by bare name inside "
        f"the very module that defines them (NR0025 권고2/§9):\n" + "\n".join(lines)
    )


def test_the_seam_scan_itself_is_not_vacuous():
    # 1. The module glob found a believable slice of the real package.
    git_modules = _scan_git_modules()
    assert len(git_modules) >= 12, (
        f"expected >=12 git/*.py modules, found {len(git_modules)}: "
        f"{sorted(git_modules)} -- the glob itself may be broken"
    )
    module_stems = {Path(label).stem for label in git_modules}
    for expected in ("credentials", "refs", "conflict", "cleanup"):
        assert expected in module_stems, (
            f"expected git/{expected}.py among scanned modules, got {sorted(module_stems)}"
        )

    # 2. The A-side (test corpus) scan found a believable inventory, not an empty set
    #    from a broken alias/AST walk.
    patched, _sites, unresolved = _scan_test_corpus()
    assert len(patched) >= 60, (
        f"expected >=60 facade-patched names, found {len(patched)}: {sorted(patched)} "
        "-- the test-corpus scan itself may be broken"
    )
    for expected in ("_run_git", "_project_name", "src_root", "db_git"):
        assert expected in patched, (
            f"expected `{expected}` among facade-patched names, got a scan of "
            f"{len(patched)} names that does not include it -- the scan may be broken"
        )

    # 3. No dynamic patch this scanner cannot resolve to a literal name (§3.4.1's
    #    "해석 불가능한 동적 패치" list). The baseline for this corpus is 0; a nonzero
    #    count means some test patches a `git_service` alias with a computed attribute
    #    name that this scanner -- and therefore §3.4's whole guarantee -- cannot see.
    assert unresolved == [], (
        f"{len(unresolved)} dynamic (non-literal-name) facade patch(es) found -- this "
        f"scanner cannot verify these are reachable: {unresolved!r}"
    )

    # 4. Mutation proof: feed synthetic sources through the same scanner functions the
    #    real tests use, and confirm each failure mode is actually detected. Without
    #    this, all the assertions above could pass merely because the scanner finds
    #    nothing everywhere (a scanner that always returns empty sets would make both
    #    seam tests vacuously green).
    fake_test_source = (
        "from modules.flow_gate.services import git_service as svc\n"
        "\n"
        "def test_x(monkeypatch):\n"
        "    monkeypatch.setattr(svc, \"_mutation_probe_name\", lambda: None)\n"
    )
    fake_patched, fake_sites, _ = _scan_test_source("<synthetic-test>", fake_test_source)
    assert "_mutation_probe_name" in fake_patched, (
        "synthetic patch on a git_service alias was not detected by "
        "_scan_test_source -- the A-side scanner is not actually scanning"
    )
    assert fake_sites["_mutation_probe_name"] == [("<synthetic-test>", 4)]

    fake_module_source_b1 = (
        "from .credentials import _mutation_probe_name\n"
        "\n"
        "def use_it():\n"
        "    return _mutation_probe_name()\n"
    )
    fake_import_bound, _fake_defined, fake_usage = _scan_git_module_source(
        "<synthetic-module-b1>", fake_module_source_b1
    )
    assert fake_import_bound.get("_mutation_probe_name") == 1, (
        "synthetic top-level import was not detected by _scan_git_module_source -- "
        "the B-1 scanner is not actually scanning"
    )
    assert fake_usage.get("_mutation_probe_name") == [("<synthetic-module-b1>", 4)]

    fake_module_source_b2 = (
        "def _mutation_probe_name():\n"
        "    return 1\n"
        "\n"
        "def use_it():\n"
        "    return _mutation_probe_name()\n"
    )
    _fake_import_bound2, fake_defined2, fake_usage2 = _scan_git_module_source(
        "<synthetic-module-b2>", fake_module_source_b2
    )
    assert fake_defined2.get("_mutation_probe_name") == 1, (
        "synthetic top-level def was not detected by _scan_git_module_source -- "
        "the B-2 scanner is not actually scanning"
    )
    assert fake_usage2.get("_mutation_probe_name") == [("<synthetic-module-b2>", 5)]

    # §3.8 ② -- the def-time-default exclusion is load-bearing: without it a name used
    # only inside a default-value expression would be an unfixable permanent red.
    fake_module_source_default = (
        "_WAIT = 5\n"
        "\n"
        "def _acquire(wait=_WAIT):\n"
        "    return wait\n"
    )
    _fake_import_bound3, fake_defined3, fake_usage3 = _scan_git_module_source(
        "<synthetic-module-default>", fake_module_source_default
    )
    assert fake_defined3.get("_WAIT") == 1
    assert fake_usage3.get("_WAIT", []) == [], (
        "a Name read only inside a function's default-value expression must be "
        "excluded from B-2 usage sites (def-time evaluation, §2.3/§3.4.2) -- got "
        f"{fake_usage3.get('_WAIT')!r}"
    )
