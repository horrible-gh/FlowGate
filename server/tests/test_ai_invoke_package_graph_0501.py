"""flowgate.default.0501 T6: the ai_invoke architecture is a real Python package.

T0014 rejected the T1-T5 result for not being NR0003 §12's structure: the engine was
still a flat row of `ai_invoke_*.py` siblings next to every other service, with
admission / oracle / finalize / diagnostics never separated at all. T6 built §12's
package. This file locks the four properties that make it a package rather than a
rename, none of which any other regression test would notice:

  * §12 layout -- the package exists and holds exactly the modules §12 names, and the
    flat `ai_invoke_*.py` siblings (and, still, 0497's exec()-assembled part files) are
    gone from the services directory.
  * no assembly magic -- `_load_parts`, `_PART_FILES`, `exec(code, globals())`,
    `globals().update(...)` and a module-level `__getattr__` are all absent. Every name
    a module has arrives through an import statement that names it.
  * §28 dependency direction -- `runtime` imports nothing from the package, `oracle`
    imports only `runtime`, `review` never imports `chain`, provider transports never
    import `chain`/`review`, and NOTHING inside the package imports `facade` or
    `ai_invoke_service` at module level (the compatibility seam is `_svc()`, resolved at
    call time). None of those named rules, individually or together, proves the WHOLE
    graph is acyclic -- a module could still cycle through two OTHER modules neither
    rule mentions, which is exactly what rev0 of this group shipped (admission<->chain,
    admission<->worker, chain<->diagnostics, chain->finalize->worker->chain). The whole-
    graph DFS test below is the one that actually proves acyclicity; the fresh-process
    test after it is the end-to-end confirmation that a real interpreter agrees.
  * fresh-process import -- a brand-new interpreter can enter the graph at ANY module,
    including `facade`, and still finish loading with `ai_invoke_service` intact.
"""
from __future__ import annotations

import ast
import os
import subprocess
import sys
from pathlib import Path

import pytest

_SERVER_DIR = Path(__file__).resolve().parents[1]
_SERVICES_DIR = _SERVER_DIR / "modules" / "flow_gate" / "services"
_PKG_DIR = _SERVICES_DIR / "ai_invoke"

# NR0003 §12's recommended final structure, verbatim.
_EXPECTED_MODULES = {
    "__init__", "facade", "runtime", "admission", "worker", "provider_api",
    "provider_cli", "oracle", "finalize", "chain", "review", "diagnostics",
}
# The flat layout T1-T5 produced, plus 0497's exec()-assembled part files. None of
# these may survive beside the package: two spellings of the same engine is exactly
# the "which file is this symbol really in?" problem the move was ordered to end.
_RETIRED_FILES = (
    "ai_invoke_part2_worker.py", "ai_invoke_part3_chain.py",
    "ai_invoke_worker.py", "ai_invoke_chain.py", "ai_invoke_review.py",
    "ai_invoke_provider_api.py", "ai_invoke_provider_cli.py",
    "ai_invoke_runtime.py", "ai_invoke_helpers.py",
)
_ENTRY_POINTS = tuple(sorted(_EXPECTED_MODULES - {"__init__"}))


def _package_imports(module: str) -> set[str]:
    """Every sibling of the package `module` reaches at module level, by any spelling."""
    path = _PKG_DIR / f"{module}.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if node.level and not mod:                       # from . import x, y
                found.update(a.name for a in node.names)
            elif node.level:                                 # from .x import ...
                found.add(mod.split(".")[0])
            elif mod.startswith("modules.flow_gate.services.ai_invoke"):
                tail = mod.split("ai_invoke", 1)[1].lstrip(".")
                found.add(tail.split(".")[0] if tail else "ai_invoke")
                found.update(a.name for a in node.names if not tail)
        elif isinstance(node, ast.Import):
            for a in node.names:
                if "ai_invoke" in a.name:
                    found.add(a.name.rsplit(".", 1)[-1])
    return found


class TestLayoutIsNR0003Section12:
    def test_the_package_exists_with_exactly_the_recommended_modules(self):
        assert _PKG_DIR.is_dir(), "server/modules/flow_gate/services/ai_invoke/ is missing"
        actual = {p.stem for p in _PKG_DIR.glob("*.py")}
        assert actual == _EXPECTED_MODULES, (
            f"package layout drifted from NR0003 §12: missing="
            f"{sorted(_EXPECTED_MODULES - actual)} unexpected={sorted(actual - _EXPECTED_MODULES)}"
        )

    def test_the_flat_and_part_file_layouts_are_gone(self):
        for name in _RETIRED_FILES:
            assert not (_SERVICES_DIR / name).exists(), f"{name} still exists beside the package"

    def test_ai_invoke_service_is_a_shim_that_defines_nothing(self):
        """NR0003 §20: the old import path survives as the compatibility surface, but it
        must not become a second home for implementation."""
        path = _SERVICES_DIR / "ai_invoke_service.py"
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        defined = [n.name for n in tree.body
                   if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
        assert not defined, f"ai_invoke_service.py should define nothing, found {defined}"


class TestNoAssemblyMagic:
    """T0012 §21, carried forward: nothing in the engine may conjure names at runtime."""

    @pytest.mark.parametrize("module", sorted(_EXPECTED_MODULES))
    def test_module_has_no_loader_exec_or_module_getattr(self, module):
        source = (_PKG_DIR / f"{module}.py").read_text(encoding="utf-8")
        for banned in ("_load_parts", "_PART_FILES", "exec(code, globals())",
                       "globals().update("):
            assert banned not in source, f"{module}.py still contains {banned}"
        tree = ast.parse(source, filename=f"{module}.py")
        top_level_defs = [n.name for n in tree.body
                          if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
        assert "__getattr__" not in top_level_defs, (
            f"{module}.py defines a module-level __getattr__ -- symbol ownership must be "
            f"a literal import, not a runtime hook (T0012 §12/§27)"
        )


class TestDependencyDirection:
    """NR0003 §28: an architectural test, so the package cannot drift back into a
    god-module one convenient import at a time."""

    def test_runtime_imports_nothing_from_the_package(self):
        assert _package_imports("runtime") == set(), (
            "runtime.py is the bottom of the graph -- every other module may import it "
            "and it may import none of them"
        )

    def test_oracle_imports_only_runtime(self):
        assert _package_imports("oracle") <= {"runtime"}

    def test_review_never_imports_chain(self):
        """T0012 §8 / NR0003 §18: chain imports review, never the other way round."""
        assert "chain" not in _package_imports("review")
        assert "review" in _package_imports("chain"), (
            "the allowed direction of the pair disappeared -- chain.py should be the one "
            "that reaches review.py"
        )

    def test_provider_transports_never_import_chain_or_review(self):
        """NR0003 §28's example rule, verbatim: transport must not import review. `worker`
        is added to the banned set alongside it (T6 rev1): provider_cli.py used to reach
        five watchdog helpers there, which is the same "transport knows the orchestration
        it transports for" shape §28 names for chain/review."""
        for module in ("provider_api", "provider_cli"):
            reached = _package_imports(module)
            assert not (reached & {"chain", "review", "worker"}), f"{module}.py reaches {reached}"

    def test_admission_and_diagnostics_never_import_chain_or_worker(self):
        """T6 rev0 shipped with direct cycles here (rejected): admission.py <-> chain.py
        (both reached each other's `_provider_brief`/`_continuation_docs_target`),
        admission.py <-> worker.py (both reached each other), and chain.py <-> diagnostics.py
        (both reached each other's `get_status`/`_run_detail_from_row`). rev1 moved the
        pure/constant-only names each side needed onto `oracle`/`runtime`, so admission.py
        and diagnostics.py no longer need `chain` or `worker` as siblings at all -- only
        the allowed direction (`chain` -> `admission`, `chain` -> `diagnostics`) remains."""
        assert not (_package_imports("admission") & {"chain", "worker"}), (
            "admission.py reaches chain/worker again -- this reopens the T6 rev0 cycles"
        )
        assert not (_package_imports("diagnostics") & {"chain"}), (
            "diagnostics.py reaches chain again -- this reopens the chain<->diagnostics cycle"
        )
        assert "admission" in _package_imports("chain"), (
            "the allowed chain -> admission direction disappeared"
        )
        assert "diagnostics" in _package_imports("chain"), (
            "the allowed chain -> diagnostics direction disappeared"
        )

    def test_the_whole_package_import_graph_has_no_cycle(self):
        """NR0003 §28, exhaustively: the edge-specific assertions above only ban the
        pairs someone thought to name. T6 rev0 passed every one of them while the
        checked-in graph still had direct cycles nobody had written a rule against
        (admission<->chain, admission<->worker, chain<->diagnostics, and a
        chain->finalize->worker->chain triangle) -- this test instead walks the FULL
        module-level graph (every sibling `_package_imports` finds, `facade` included)
        and fails if DFS finds a back-edge anywhere, naming the actual cycle instead of
        trusting the graph is acyclic because a few named edges are absent."""
        graph = {module: (_package_imports(module) & _EXPECTED_MODULES) - {"__init__"}
                 for module in _EXPECTED_MODULES - {"__init__"}}

        WHITE, GRAY, BLACK = 0, 1, 2
        color = {module: WHITE for module in graph}
        path: list[str] = []

        def _visit(node: str) -> list[str] | None:
            color[node] = GRAY
            path.append(node)
            for neighbor in sorted(graph[node]):
                if color[neighbor] == GRAY:
                    return path[path.index(neighbor):] + [neighbor]
                if color[neighbor] == WHITE:
                    cycle = _visit(neighbor)
                    if cycle is not None:
                        return cycle
            path.pop()
            color[node] = BLACK
            return None

        cycle = None
        for module in sorted(graph):
            if color[module] == WHITE:
                cycle = _visit(module)
                if cycle is not None:
                    break

        assert cycle is None, (
            "ai_invoke package import graph has a cycle: " + " -> ".join(cycle)
        )

    @pytest.mark.parametrize("module", _ENTRY_POINTS)
    def test_no_module_imports_the_facade_or_the_shim_at_module_level(self, module):
        """The property that makes the package's own import graph acyclic. `facade` is
        imported by `ai_invoke_service` only; a compatibility seam inside the package is
        `_svc()`, a function-local import resolved at call time."""
        if module == "facade":
            pytest.skip("facade is the one module allowed to import every sibling")
        assert "facade" not in _package_imports(module)
        path = _PKG_DIR / f"{module}.py"
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in tree.body:          # module level only -- inside _svc() is the point
            if isinstance(node, ast.ImportFrom):
                assert not (node.module or "").endswith("ai_invoke_service"), module
                assert "ai_invoke_service" not in {a.name for a in node.names}, module
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.endswith("ai_invoke_service"), module


def _run_fresh_import(dotted: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.setdefault("TESTING", "1")
    env.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
    env.setdefault("ALLOWED_ORIGIN", "http://localhost")
    env.setdefault("CONTEXT", "/flowgate")
    env.setdefault("DB_TYPE", "sqlite")
    code = (
        "import sys; sys.path.insert(0, r'%s'); "
        "import %s; "
        "from modules.flow_gate.services import ai_invoke_service as s; "
        "assert callable(s.start_run) and callable(s._worker) and s._runs is not None"
        % (str(_SERVER_DIR), dotted)
    )
    return subprocess.run([sys.executable, "-c", code], cwd=str(_SERVER_DIR), env=env,
                          capture_output=True, text=True, timeout=120)


@pytest.mark.parametrize("entry", ("modules.flow_gate.services.ai_invoke_service",)
                         + tuple(f"modules.flow_gate.services.ai_invoke.{m}" for m in _ENTRY_POINTS))
def test_fresh_process_import_from_any_entry_point(entry):
    """T0012 §20, widened to the package: whichever module a fresh interpreter reaches
    first, the rest of the graph must load behind it AND `ai_invoke_service` must end up
    carrying the full surface. Entering at `facade` is the case that catches a
    reintroduced cycle -- it is the module every other one would have to be finished for."""
    result = _run_fresh_import(entry)
    assert result.returncode == 0, (
        f"fresh-process import of {entry} failed:\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
