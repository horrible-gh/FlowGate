"""Test for 0505 T0009: _no_output_detail CLI/API branching."""
import pytest


def _make_run(exec_type="cli", **kw):
    """Create a minimal run dict with provider exec_type."""
    run = {
        "run_id": "r_test",
        "exit_code": None,
        "attempt_no": 1,
        "started_mono": 0,
        "provider_id": "aip_test",
        "provider": {"name": "TestProvider", "exec_type": exec_type},
        "last_message": None,
        "register_errors": [],
        "tool_call_misses": 0,
        "turn_limit_exhausted": False,
        "oracle_mismatch": False,
    }
    run.update(kw)
    return run


class TestNoOutputDetailBranching:
    """CLI vs API provider branching in _no_output_detail."""

    def test_cli_provider_generic_form(self):
        """CLI provider should return generic 'worker exited' form."""
        from modules.flow_gate.services.ai_invoke_part2_worker import _no_output_detail

        run = _make_run(exec_type="cli", exit_code=1)
        detail = _no_output_detail(run)

        assert "worker exited 1" in detail
        assert "without registering a document" in detail

    def test_cli_provider_with_last_message(self):
        """CLI provider should include last_message if available."""
        from modules.flow_gate.services.ai_invoke_part2_worker import _no_output_detail

        run = _make_run(
            exec_type="cli",
            exit_code=1,
            last_message="Some error message from CLI"
        )
        detail = _no_output_detail(run)

        assert "worker exited 1" in detail
        assert "last message" in detail
        assert "Some error message" in detail

    def test_api_provider_register_errors(self):
        """API provider should return register_errors diagnosis."""
        from modules.flow_gate.services.ai_invoke_part2_worker import _no_output_detail

        run = _make_run(
            exec_type="api",
            register_errors=[
                {"reason": "conflict", "status": 409},
                {"reason": "invalid", "status": 422}
            ]
        )
        detail = _no_output_detail(run)

        assert "register failed" in detail
        assert "conflict/409" in detail
        assert "invalid/422" in detail
        # Should NOT include generic form
        assert "worker exited" not in detail

    def test_api_provider_register_errors_no_status(self):
        """API provider register_errors without HTTP status."""
        from modules.flow_gate.services.ai_invoke_part2_worker import _no_output_detail

        run = _make_run(
            exec_type="api",
            register_errors=[{"reason": "unknown error"}]
        )
        detail = _no_output_detail(run)

        assert "register failed" in detail
        assert "unknown error" in detail

    def test_api_provider_turn_limit_exhausted(self):
        """API provider should return turn_limit_exhausted diagnosis."""
        from modules.flow_gate.services.ai_invoke_part2_worker import _no_output_detail

        run = _make_run(
            exec_type="api",
            turn_limit_exhausted=True
        )
        detail = _no_output_detail(run)

        assert "turn limit exhausted" in detail
        assert "worker exited" not in detail

    def test_api_provider_tool_call_misses(self):
        """API provider should return tool_call_misses diagnosis."""
        from modules.flow_gate.services.ai_invoke_part2_worker import _no_output_detail

        run = _make_run(
            exec_type="api",
            tool_call_misses=3
        )
        detail = _no_output_detail(run)

        assert "tool not called" in detail
        assert "3" in detail
        assert "worker exited" not in detail

    def test_api_provider_oracle_mismatch(self):
        """API provider should return oracle_mismatch diagnosis."""
        from modules.flow_gate.services.ai_invoke_part2_worker import _no_output_detail

        run = _make_run(
            exec_type="api",
            oracle_mismatch=True
        )
        detail = _no_output_detail(run)

        assert "oracle mismatch" in detail
        assert "worker exited" not in detail

    def test_api_provider_priority_register_errors_over_others(self):
        """register_errors take priority when multiple diagnostics present."""
        from modules.flow_gate.services.ai_invoke_part2_worker import _no_output_detail

        run = _make_run(
            exec_type="api",
            register_errors=[{"reason": "conflict"}],
            turn_limit_exhausted=True,
            tool_call_misses=2
        )
        detail = _no_output_detail(run)

        assert "register failed" in detail
        assert "turn limit" not in detail
        assert "tool not called" not in detail

    def test_api_provider_fallback_to_generic(self):
        """API provider falls back to generic form if no diagnostic matched."""
        from modules.flow_gate.services.ai_invoke_part2_worker import _no_output_detail

        run = _make_run(
            exec_type="api",
            exit_code=2
        )
        detail = _no_output_detail(run)

        # Should fall back to generic form
        assert "worker exited 2" in detail

    def test_api_provider_no_provider_field(self):
        """Gracefully handle missing provider field."""
        from modules.flow_gate.services.ai_invoke_part2_worker import _no_output_detail

        run = _make_run(exit_code=1)
        run["provider"] = None  # Missing provider
        detail = _no_output_detail(run)

        # Should default to CLI-style behavior
        assert "worker exited 1" in detail
