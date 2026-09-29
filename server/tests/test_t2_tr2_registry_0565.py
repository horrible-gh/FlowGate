from pathlib import Path

def test_migrations_seed_both_types_and_are_idempotent():
    root = Path(__file__).resolve().parents[1] / "sql/migrations"
    for engine in ("sqlite", "postgres", "mysql"):
        sql = (root / engine / "113_seed_t2_tr2_doctypes.sql").read_text(encoding="utf-8")
        for code, series in (("T2", "instruction"), ("TR2", "work")):
            assert f"'{code}'" in sql
            assert f"'{series}'" in sql
        for locale in ("ko", "en", "ja"):
            assert f"'{locale}'" in sql
        assert "document_type_names" in sql
        assert "document_type_descriptions" in sql
        assert any(term in sql.upper() for term in ("ON CONFLICT", "INSERT OR IGNORE", "INSERT IGNORE", "ON DUPLICATE"))

def test_sqlite_113_replay_preserves_global_seed_rows(migrated_sqlite_db):
    import sqlite3
    db_path=migrated_sqlite_db("t2_tr2_113.db")
    migration=(Path(__file__).resolve().parents[1]/
        "sql/migrations/sqlite/113_seed_t2_tr2_doctypes.sql").read_text(encoding="utf-8")
    with sqlite3.connect(db_path) as conn:
        before=conn.execute(
            "SELECT type_code, COUNT(*) FROM document_types "
            "WHERE project_id IS NULL AND type_code IN ('T2','TR2') "
            "GROUP BY type_code ORDER BY type_code").fetchall()
        conn.executescript(migration)
        after=conn.execute(
            "SELECT type_code, COUNT(*) FROM document_types "
            "WHERE project_id IS NULL AND type_code IN ('T2','TR2') "
            "GROUP BY type_code ORDER BY type_code").fetchall()
        assert before==after==[("T2",1),("TR2",1)]
        for table in ("document_type_names","document_type_descriptions"):
            rows=conn.execute(
                f"SELECT d.type_code, COUNT(*) FROM {table} AS n "
                "JOIN document_types AS d ON d.id=n.document_type_id "
                "WHERE d.project_id IS NULL AND d.type_code IN ('T2','TR2') "
                "GROUP BY d.type_code ORDER BY d.type_code").fetchall()
            assert rows==[("T2",3),("TR2",3)]
