"""flowgate.default.0501 T0006 (T2), retargeted by T6: ai_invoke/oracle.py boundary.

T2 first extracted the engine's stateless helpers as a flat `ai_invoke_helpers.py`
sibling. T6 moved them into `ai_invoke/oracle.py`, which is where NR0003 §12 puts the
package's stateless layer and which §21 Phase 1 names as the first thing to extract for
exactly the reason T2 gave: it owns no run state, so it carries no risk.

What T0006 §9/§7 asked for is unchanged and still checked here:

  * the module imports standalone, with no dependency on the engine's assembled globals()
  * it never reverse-imports ai_invoke_service and never uses exec()/globals().update()
  * its pure functions produce results identical to the `svc` surface that still exposes
    the same names (compatibility principle 3 of T0006 §4)
"""
from __future__ import annotations

import ast
import os
import sys
from pathlib import Path

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")
os.environ.setdefault("ALLOWED_ORIGIN", "http://localhost")
os.environ.setdefault("CONTEXT", "/flowgate")
os.environ.setdefault("DB_TYPE", "sqlite")

_SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_SERVER_DIR))
_ORACLE_PATH = (
    _SERVER_DIR / "modules" / "flow_gate" / "services" / "ai_invoke" / "oracle.py"
)

# The pure helpers this module owns, and the name each one still answers to on the
# ai_invoke_service surface. Listed rather than derived: the point of the class below
# is that the two spellings agree, so deriving one from the other would prove nothing.
_PURE_HELPERS = {
    "map_lookup": "_map_lookup",
    "review_key": "_review_key",
    "review_findings": "_review_findings",
    "normalize_ws": "_normalize_ws",
    "review_finding_digest": "review_finding_digest",
    "prioritize_chain": "_prioritize_chain",
}


class TestModuleIsImportOnly:
    def test_direct_import_succeeds_standalone(self):
        """Importing the module alone must not raise or require any fixture/app state."""
        import importlib

        from modules.flow_gate.services.ai_invoke import oracle

        importlib.reload(oracle)  # re-executes the module body in isolation
        assert callable(oracle.map_lookup)

    def test_source_never_imports_ai_invoke_service_or_uses_exec_globals_update(self):
        """AST guard for T0006 §4 principles 1/2: no reverse import, no exec/globals().update().

        T6 makes this stronger than a guard on one file: oracle.py is the ONE module of
        the package that reaches nothing through the compatibility shim at all (it has no
        `_svc()` helper), which is what the first assertion below pins."""
        source = _ORACLE_PATH.read_text(encoding="utf-8")
        assert "_svc()" not in source, (
            "oracle.py acquired a compatibility seam -- the stateless layer should have "
            "nothing to reach on ai_invoke_service"
        )
        tree = ast.parse(source, filename=str(_ORACLE_PATH))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert node.module != "ai_invoke_service"
                assert not (node.module or "").endswith(".ai_invoke_service")
                assert "ai_invoke_service" not in {a.name for a in node.names}
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.endswith("ai_invoke_service")
            if isinstance(node, ast.Call):
                name = node.func.id if isinstance(node.func, ast.Name) else None
                assert name != "exec"
                if (
                    isinstance(node.func, ast.Attribute)
                    and node.func.attr == "update"
                    and isinstance(node.func.value, ast.Call)
                    and isinstance(node.func.value.func, ast.Name)
                    and node.func.value.func.id == "globals"
                ):
                    raise AssertionError("globals().update(...) found in ai_invoke/oracle.py")

    def test_every_pure_helper_is_still_reachable_under_both_names(self):
        from modules.flow_gate.services import ai_invoke_service as svc
        from modules.flow_gate.services.ai_invoke import oracle

        for own, exported in _PURE_HELPERS.items():
            assert callable(getattr(oracle, own)), own
            surfaced = getattr(svc, exported)
            # Identity would be the sharper assertion, but the reload above replaces
            # this module's function objects while the facade still holds the originals,
            # so identity is not stable across this file. Origin is: a wrapper would
            # report facade.py (or ai_invoke_service) as its module, and a renamed copy
            # would not answer to `own`.
            assert surfaced.__module__.endswith("ai_invoke.oracle"), (
                f"svc.{exported} came from {surfaced.__module__}, not ai_invoke/oracle.py"
            )
            assert surfaced.__name__ == own, (
                f"svc.{exported} resolves to {surfaced.__name__}, not oracle.{own}"
            )


class TestPureHelpersMatchAssembledSurface:
    """Same inputs, same outputs -- through `svc.<name>` and through the new module."""

    def test_map_lookup(self):
        from modules.flow_gate.services import ai_invoke_service as svc
        from modules.flow_gate.services.ai_invoke import oracle

        for overrides, item_seq in [({"5": "x"}, 5), ({5: "y"}, 5), (None, 5), ({"5": "x"}, None)]:
            assert oracle.map_lookup(overrides, item_seq) == svc._map_lookup(overrides, item_seq)

    def test_review_key(self):
        from modules.flow_gate.services import ai_invoke_service as svc
        from modules.flow_gate.services.ai_invoke import oracle

        for value in [244, "244", " 244 ", 0, -1, True, None, "abc", ""]:
            assert oracle.review_key(value) == svc._review_key(value)

    def test_review_findings_and_digest(self):
        from modules.flow_gate.services import ai_invoke_service as svc
        from modules.flow_gate.services.ai_invoke import oracle

        review = {"findings": [{"locus": "a", "note": "b"}, "plain text"]}
        assert oracle.review_findings(review) == svc._review_findings(review)
        assert oracle.review_finding_digest(review) == svc.review_finding_digest(review)

        review_str = {"findings": '[{"locus": "x", "note": "y"}]'}
        assert oracle.review_findings(review_str) == svc._review_findings(review_str)
        assert oracle.review_finding_digest(review_str) == svc.review_finding_digest(review_str)

    def test_normalize_ws(self):
        from modules.flow_gate.services import ai_invoke_service as svc
        from modules.flow_gate.services.ai_invoke import oracle

        for value in ["  a   b\n c ", None, "", "already-normal"]:
            assert oracle.normalize_ws(value) == svc._normalize_ws(value)

    def test_resolve_round_limit_and_rounds_remain(self):
        """The two-argument pure form (oracle) and the one-argument policy form
        (review.py, which binds REVIEW_ROUNDS_NO_LIMIT) must still agree. Both names
        exist on purpose -- T6 kept the pair rather than collapsing it."""
        from modules.flow_gate.services import ai_invoke_service as svc
        from modules.flow_gate.services.ai_invoke import oracle

        no_limit = svc.REVIEW_ROUNDS_NO_LIMIT
        for count in [-1, 0, 1, 3]:
            assert oracle.resolve_round_limit(count, no_limit) == svc.resolve_round_limit(count)
        limit = oracle.resolve_round_limit(-1, no_limit)
        for rounds_used in [0, 10**6]:
            assert oracle.review_rounds_remain(rounds_used, limit, no_limit) == (
                svc.review_rounds_remain(rounds_used, limit)
            )

    def test_prioritize_chain(self):
        from modules.flow_gate.services import ai_invoke_service as svc
        from modules.flow_gate.services.ai_invoke import oracle

        chain = [{"id": "a"}, {"id": "b"}, {"id": "c"}]
        assert oracle.prioritize_chain(chain, "c") == svc._prioritize_chain(chain, "c")
        assert oracle.prioritize_chain(chain, "zzz") == svc._prioritize_chain(chain, "zzz")


class TestScopeOraclesLiveHereToo:
    """T6 put NR0003 §16's actual subject in this module: the completion probes, one per
    thing a run's token is allowed to write. They are the reason the file is named
    oracle.py, so their inventory is pinned here rather than left implicit."""

    def test_every_writable_scope_has_a_probe(self):
        from modules.flow_gate.services.ai_invoke import oracle

        assert set(oracle._SCOPE_PROBES) == {
            "chat", "edit", "review", "test_run", "workflow_sequence_edit",
            # flowgate.default.0482 T0011's scope, merged in by 0501 T0019: judged by
            # tracked base-dirty file count and keyed by project_id rather than a
            # document id, but a row-it-may-write probe exactly like the other five.
            "resolve_base_dirty",
        }
        for scope, probe in oracle._SCOPE_PROBES.items():
            assert callable(probe), scope
