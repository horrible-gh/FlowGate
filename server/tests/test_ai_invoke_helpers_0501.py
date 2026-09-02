"""flowgate.default.0501 T0006 (T2): ai_invoke_helpers module boundary.

Covers the completion conditions T0006 §9/§7 asks for on the NEW pure-helper module:

  * it imports standalone, with no dependency on ai_invoke_service's assembled globals()
  * it never reverse-imports ai_invoke_service and never uses exec()/globals().update()
  * its pure functions produce results identical to the assembled `svc` surface that
    still exposes the same names (compatibility principle 3 of T0006 §4)
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
_HELPERS_PATH = (
    _SERVER_DIR / "modules" / "flow_gate" / "services" / "ai_invoke_helpers.py"
)


class TestModuleIsImportOnly:
    def test_direct_import_succeeds_standalone(self):
        """Importing the helper module alone must not raise or require any fixture/app state."""
        import importlib

        from modules.flow_gate.services import ai_invoke_helpers

        importlib.reload(ai_invoke_helpers)  # re-executes the module body in isolation
        assert callable(ai_invoke_helpers.map_lookup)

    def test_source_never_imports_ai_invoke_service_or_uses_exec_globals_update(self):
        """AST guard for T0006 §4 원칙 1/2: no reverse import, no exec/globals().update()."""
        tree = ast.parse(_HELPERS_PATH.read_text(encoding="utf-8"), filename=str(_HELPERS_PATH))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert node.module != "ai_invoke_service"
                assert not (node.module or "").endswith(".ai_invoke_service")
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
                    raise AssertionError("globals().update(...) found in ai_invoke_helpers.py")


class TestPureHelpersMatchAssembledSurface:
    """Same inputs, same outputs — through `svc.<name>` and through the new module."""

    def test_map_lookup(self):
        from modules.flow_gate.services import ai_invoke_helpers as helpers
        from modules.flow_gate.services import ai_invoke_service as svc

        for overrides, item_seq in [({"5": "x"}, 5), ({5: "y"}, 5), (None, 5), ({"5": "x"}, None)]:
            assert helpers.map_lookup(overrides, item_seq) == svc._map_lookup(overrides, item_seq)

    def test_review_key(self):
        from modules.flow_gate.services import ai_invoke_helpers as helpers
        from modules.flow_gate.services import ai_invoke_service as svc

        for value in [244, "244", " 244 ", 0, -1, True, None, "abc", ""]:
            assert helpers.review_key(value) == svc._review_key(value)

    def test_review_findings_and_digest(self):
        from modules.flow_gate.services import ai_invoke_helpers as helpers
        from modules.flow_gate.services import ai_invoke_service as svc

        review = {"findings": [{"locus": "a", "note": "b"}, "plain text"]}
        assert helpers.review_findings(review) == svc._review_findings(review)
        assert helpers.review_finding_digest(review) == svc.review_finding_digest(review)

        review_str = {"findings": '[{"locus": "x", "note": "y"}]'}
        assert helpers.review_findings(review_str) == svc._review_findings(review_str)
        assert helpers.review_finding_digest(review_str) == svc.review_finding_digest(review_str)

    def test_normalize_ws(self):
        from modules.flow_gate.services import ai_invoke_helpers as helpers
        from modules.flow_gate.services import ai_invoke_service as svc

        for value in ["  a   b\n c ", None, "", "already-normal"]:
            assert helpers.normalize_ws(value) == svc._normalize_ws(value)

    def test_resolve_round_limit_and_rounds_remain(self):
        from modules.flow_gate.services import ai_invoke_helpers as helpers
        from modules.flow_gate.services import ai_invoke_service as svc

        no_limit = svc.REVIEW_ROUNDS_NO_LIMIT
        for count in [-1, 0, 1, 3]:
            assert helpers.resolve_round_limit(count, no_limit) == svc.resolve_round_limit(count)
        limit = helpers.resolve_round_limit(-1, no_limit)
        for rounds_used in [0, 10**6]:
            assert helpers.review_rounds_remain(rounds_used, limit, no_limit) == (
                svc.review_rounds_remain(rounds_used, limit)
            )

    def test_prioritize_chain(self):
        from modules.flow_gate.services import ai_invoke_helpers as helpers
        from modules.flow_gate.services import ai_invoke_service as svc

        chain = [{"id": "a"}, {"id": "b"}, {"id": "c"}]
        assert helpers.prioritize_chain(chain, "c") == svc._prioritize_chain(chain, "c")
        assert helpers.prioritize_chain(chain, "zzz") == svc._prioritize_chain(chain, "zzz")
