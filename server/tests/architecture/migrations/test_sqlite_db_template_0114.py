"""Template DB contract (0114 T#1), independent of migration numbering tests."""
from pathlib import Path
import sqlite3

import pytest

import sqlite_db_support as support


MIGRATIONS = Path(__file__).resolve().parents[3] / "sql" / "migrations" / "sqlite"


def schema(path):
    with sqlite3.connect(path) as conn:
        objects = conn.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_master ORDER BY type, name"
        ).fetchall()
        tables = [name for kind, name, _, _ in objects if kind == "table"]
        details = {}
        for name in tables:
            quoted = '"' + name.replace('"', '""') + '"'
            details[name] = (
                conn.execute(f"PRAGMA table_xinfo({quoted})").fetchall(),
                conn.execute(f"PRAGMA foreign_key_list({quoted})").fetchall(),
                conn.execute(f"PRAGMA index_list({quoted})").fetchall(),
            )
        return objects, details


def test_real_template_and_direct_schema_are_identical(tmp_path):
    migrations = sorted(MIGRATIONS.glob("*.sql"))
    assert migrations
    template = support.SqliteDbFactory(tmp_path / "template", migrations)
    direct = support.SqliteDbFactory(tmp_path / "direct", migrations, "direct")
    copied = template("copied.db")
    applied = direct("applied.db")
    assert schema(copied) == schema(applied)
    assert not direct.template_path.exists()


def small_factory(tmp_path, mode="template"):
    migration = tmp_path / "001.sql"
    migration.write_text(
        "CREATE TABLE parent(id INTEGER PRIMARY KEY);"
        "CREATE TABLE child(id INTEGER REFERENCES parent(id));"
        "INSERT INTO parent VALUES(1);", encoding="utf-8",
    )
    return support.SqliteDbFactory(tmp_path / "dbs", [migration], mode)


@pytest.mark.parametrize("mode", ["template", "direct"])
def test_copies_with_the_same_name_and_seeds_are_isolated(tmp_path, mode):
    factory = small_factory(tmp_path, mode)
    first = factory("same.db", "INSERT INTO parent VALUES(2);")
    second = factory("same.db")
    assert first != second
    with sqlite3.connect(first) as conn:
        conn.execute("DELETE FROM parent")
        conn.execute("CREATE TABLE only_in_first(id)")
    with sqlite3.connect(second) as conn:
        assert conn.execute("SELECT * FROM parent").fetchall() == [(1,)]
        assert conn.execute("SELECT name FROM sqlite_master WHERE name='only_in_first'").fetchall() == []
    third = factory("same.db")
    assert schema(second) == schema(third)


@pytest.mark.parametrize("mode", ["template", "direct"])
def test_seed_failure_propagates_and_never_changes_the_template(tmp_path, mode):
    factory = small_factory(tmp_path, mode)
    with pytest.raises(sqlite3.IntegrityError):
        factory("bad.db", "INSERT INTO child VALUES(999);")
    assert not list(factory.root.glob("db-*/bad.db"))
    good = factory("good.db")
    with sqlite3.connect(good) as conn:
        assert conn.execute("SELECT * FROM child").fetchall() == []


def test_template_applies_once_but_direct_applies_for_each_call(tmp_path, monkeypatch):
    calls = []
    original = support.apply_migrations

    def record(*args, **kwargs):
        calls.append(args[0])
        return original(*args, **kwargs)

    monkeypatch.setattr(support, "apply_migrations", record)
    template = small_factory(tmp_path)
    template.prepare()
    template("one.db")
    template("two.db")
    assert len(calls) == 1
    direct = support.SqliteDbFactory(tmp_path / "direct", template.migrations, "direct")
    direct.prepare()
    direct("one.db")
    direct("two.db")
    assert len(calls) == 3


def test_failed_chain_is_not_published_or_retried(tmp_path, monkeypatch):
    factory = small_factory(tmp_path)
    broken = tmp_path / "002.sql"
    broken.write_text("THIS IS NOT SQL;", encoding="utf-8")
    factory.migrations += (broken,)
    with pytest.raises(sqlite3.OperationalError):
        factory.prepare()
    assert not factory.template_path.exists()
    assert not (factory.root / "template.building.db").exists()
    broken.write_text("CREATE TABLE would_hide_failure(id);", encoding="utf-8")
    with pytest.raises(RuntimeError, match="failed for this session"):
        factory("must_not_exist.db")
    assert not list(factory.root.glob("db-*"))


def test_shared_fixture_uses_the_requested_mode(migrated_sqlite_db, pytestconfig):
    assert migrated_sqlite_db.mode == pytestconfig.getoption("--fg-db-mode")
    first = migrated_sqlite_db("shared.db", "CREATE TABLE fixture_probe(id);")
    second = migrated_sqlite_db("shared.db")
    assert first != second
    with sqlite3.connect(second) as conn:
        assert conn.execute("SELECT name FROM sqlite_master WHERE name='fixture_probe'").fetchall() == []
