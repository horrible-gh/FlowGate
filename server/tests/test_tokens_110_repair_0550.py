"""Regression for 110: 108 must not leave the tokens schema behind 107/109 semantics."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "sql" / "migrations"


def test_110_repairs_failure_origin_columns_and_scope_for_every_dialect():
    for dialect in ("sqlite", "postgres", "mysql"):
        body = (ROOT / dialect / "110_repair_tokens_after_base_dirty_rebuild.sql").read_text(encoding="utf-8")
        assert "failure_origin_target_run_id" in body
        assert "failure_origin_before_marker" in body
        assert "failure_origin_review" in body
        assert "resolve_base_dirty" in body
        assert "source_access" in body


def test_postgres_108_is_the_historical_schema_loss_and_110_is_forward_repair():
    old = (ROOT / "postgres" / "108_tokens_resolve_base_dirty_scope.sql").read_text(encoding="utf-8")
    repair = (ROOT / "postgres" / "110_repair_tokens_after_base_dirty_rebuild.sql").read_text(encoding="utf-8")
    assert "failure_origin_target_run_id" not in old
    assert "failure_origin_before_marker" not in old
    assert "failure_origin_review" not in old
    assert "ADD COLUMN IF NOT EXISTS failure_origin_target_run_id" in repair
    assert "ADD COLUMN IF NOT EXISTS failure_origin_before_marker" in repair
