"""0138 PostgreSQL: dedicated opt-in DB, or a new local cluster in pytest scratch.

No production DSN fallback. An owned local postgres process is stopped before the
fixture exits. Every migration and every write stays in the isolated database.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import multiprocessing
import os
import re
from pathlib import Path
import shutil
import socket
import subprocess
import time
from uuid import uuid4
import pytest
from modules.flow_gate.db import connection, ai_run_identity as identity, ai_invoke_runs as records


def _postgres_bin():
    found = shutil.which("initdb")
    if found:
        return Path(found).parent
    candidates = sorted(Path("C:/Program Files/PostgreSQL").glob("*/bin/initdb.exe"), reverse=True)
    return candidates[0].parent if candidates else None


@pytest.fixture
def isolated_postgres(monkeypatch, tmp_path):
    pg = pytest.importorskip("psycopg2")
    from psycopg2.extensions import parse_dsn
    dsn = os.environ.get("FLOWGATE_0138_ISOLATED_PG_DSN")
    if dsn:
        if not parse_dsn(dsn).get("dbname", "").startswith("flowgate_0138_test"):
            pytest.fail("Dedicated database name must start with flowgate_0138_test")
        yield dsn
        return
    binaries = _postgres_bin()
    if binaries is None:
        pytest.skip("No isolated PostgreSQL DSN or local initdb installation")
    data = tmp_path / "postgres-data"
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    initialized = subprocess.run([str(binaries / "initdb"), "-D", str(data), "-U", "flowgate_0138_test",
                                  "-A", "trust", "--no-locale", "--encoding=UTF8"],
                                 capture_output=True, timeout=90, creationflags=flags)
    assert initialized.returncode == 0, initialized.stderr.decode("utf-8", errors="replace")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    log = (tmp_path / "postgres.log").open("wb")
    process = subprocess.Popen([str(binaries / "postgres"), "-D", str(data), "-p", str(port),
                                "-h", "127.0.0.1", "-c", "max_connections=20"],
                               stdout=log, stderr=subprocess.STDOUT, creationflags=flags)
    base = f"host=127.0.0.1 port={port} user=flowgate_0138_test connect_timeout=2"
    try:
        admin = None
        for _ in range(100):
            if process.poll() is not None:
                pytest.fail("Owned isolated PostgreSQL exited at startup; see postgres.log")
            try:
                admin = pg.connect(base + " dbname=postgres")
                break
            except pg.OperationalError:
                time.sleep(0.1)
        assert admin is not None, "isolated PostgreSQL did not become ready"
        admin.autocommit = True
        with admin.cursor() as cur:
            cur.execute("CREATE DATABASE flowgate_0138_test")
        admin.close()
        dsn = base + " dbname=flowgate_0138_test"
        monkeypatch.setenv("FLOWGATE_0138_ISOLATED_PG_DSN", dsn)
        yield dsn
    finally:
        # pg_ctl targets ONLY the data directory created above, never an existing service.
        stopped = subprocess.run([str(binaries / "pg_ctl"), "-D", str(data), "-m", "fast", "-w", "stop"],
                                 capture_output=True, timeout=45, creationflags=flags)
        if process.poll() is None:
            process.terminate()
        process.wait(timeout=20)
        log.close()
        assert stopped.returncode == 0, "isolated PostgreSQL shutdown failed"


def pg_store(dsn, schema):
    import psycopg2 as pg
    from psycopg2 import sql
    from psycopg2.extras import RealDictCursor
    class Backend:
        db_type = 3
        def __init__(self):
            self.db = pg.connect(dsn)
            with self.db.cursor() as cur:
                cur.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
            self.db.commit()
        @contextmanager
        def begin_transaction(self):
            cursor = self.db.cursor(cursor_factory=RealDictCursor)
            class Transaction:
                def execute(self, query, params=()):
                    cursor.execute(query, params)
                    return cursor
                def fetchone(self):
                    return cursor.fetchone()
                def fetchall(self):
                    return cursor.fetchall()
            try:
                yield Transaction()
                self.db.commit()
            except BaseException:
                self.db.rollback()
                raise
            finally:
                cursor.close()
        def fetch_one(self, query, params):
            with self.begin_transaction() as tx:
                tx.execute(query, params)
                return tx.fetchone()
        def fetch_all(self, query, params):
            with self.begin_transaction() as tx:
                tx.execute(query, params)
                return tx.fetchall()
        def execute(self, query, params):
            with self.begin_transaction() as tx:
                tx.execute(query, params)
    store = connection.FlowGateStore()
    store._db = Backend()
    return store


def _pg_worker(schema, output, count):
    connection.STORE = pg_store(os.environ["FLOWGATE_0138_ISOLATED_PG_DSN"], schema)
    try:
        day = datetime(2026, 11, 6, tzinfo=timezone.utc)
        values = [identity.allocate(at=day) for _ in range(count)]
        Path(output).write_text(json.dumps(values), encoding="utf-8")
    finally:
        connection.STORE._db.db.close()


def _pg_legacy_writer(schema, run_id, output):
    import psycopg2 as pg
    from psycopg2 import sql
    conn = pg.connect(os.environ["FLOWGATE_0138_ISOLATED_PG_DSN"])
    conn.autocommit = True
    result = {"sqlstate": None}
    try:
        with conn.cursor() as cur:
            cur.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
            try:
                cur.execute("UPDATE ai_invoke_runs SET token_id = 'legacy-overwrite' WHERE run_id = %s", [run_id])
            except pg.Error as exc:
                result["sqlstate"] = exc.pgcode
        Path(output).write_text(json.dumps(result), encoding="utf-8")
    finally:
        conn.close()


def test_postgresql_full_migrations_and_multiprocess_allocator(isolated_postgres, monkeypatch, tmp_path):
    import psycopg2 as pg
    from psycopg2 import sql
    dsn = isolated_postgres
    monkeypatch.setenv("FLOWGATE_0138_ISOLATED_PG_DSN", dsn)
    schema = "identity_" + uuid4().hex
    admin = pg.connect(dsn)
    admin.autocommit = True
    store = None
    try:
        with admin.cursor() as cur:
            cur.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
            cur.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(schema)))
            directory = Path(__file__).resolve().parents[4] / "server" / "sql" / "migrations" / "postgres"
            migrations = sorted(directory.glob("*.sql"))
            assert migrations
            for migration in migrations:
                content = migration.read_text(encoding="utf-8")
                if re.sub(r"/\*.*?\*/|--[^\n]*", "", content, flags=re.S).strip():
                    try:
                        cur.execute(content)
                    except Exception as exc:
                        raise AssertionError(f"PostgreSQL migration failed: {migration.name}") from exc
                # Historical migrations copy explicit integer IDs into replacement
                # tables. Match the existing data-import sequence reset, in this
                # disposable schema only, before the next migration inserts defaults.
                cur.execute("SELECT table_name, column_name FROM information_schema.columns "
                            "WHERE table_schema = %s AND column_default LIKE 'nextval(%%'", [schema])
                for table, column in cur.fetchall():
                    qualified = sql.Identifier(schema, table).as_string(admin)
                    cur.execute(sql.SQL("SELECT setval(pg_get_serial_sequence(%s, %s), "
                                        "COALESCE(MAX({}), 0) + 1, false) FROM {}").format(
                                            sql.Identifier(column), sql.Identifier(schema, table)),
                                [qualified, column])
        store = pg_store(dsn, schema)
        monkeypatch.setattr(connection, "STORE", store)
        identity.initialize(scratch_roots=[], all_issuers_stopped=True)
        ctx = multiprocessing.get_context("spawn")
        all_ids = set()
        for workers in (2, 4):
            outputs = [tmp_path / f"pg-{workers}-{n}.json" for n in range(workers)]
            procs = [ctx.Process(target=_pg_worker, args=(schema, str(out), 10000)) for out in outputs]
            try:
                for proc in procs:
                    proc.start()
                for proc in procs:
                    proc.join(500)
                    assert proc.exitcode == 0
                values = [rid for out in outputs for rid in json.loads(out.read_text(encoding="utf-8"))]
                assert len(values) == workers * 10000 == len(set(values))
                assert all_ids.isdisjoint(values)
                all_ids.update(values)
                print(f"0138 PostgreSQL workers={workers} allocations={len(values)} duplicates=0")
            finally:
                for proc in procs:
                    if proc.is_alive():
                        proc.terminate()
                        proc.join(10)
        with admin.cursor() as cur:
            cur.execute("SELECT serial FROM ai_run_id_counters WHERE utc_date = %s", ["20261106"])
            assert cur.fetchone()[0] == 60000
            cur.execute("SELECT version()")
            print("0138 isolated database:", cur.fetchone()[0])
        # Real dialect round-trip, legacy collision, and same-nonce retry.
        from tests.integration.ai_run_identity.test_ai_run_identity_0138 import owner, record, DAY
        store._execute("INSERT INTO projects (project_id, project_name, created_at, updated_at) VALUES (?, ?, ?, ?)",
                       ["flowgate", "FlowGate", DAY.isoformat(), DAY.isoformat()])
        store._execute("INSERT INTO groups (group_id, project_id, module, title, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                       ["flowgate.v02.0138", "flowgate", "v02", "Identity", DAY.isoformat(), DAY.isoformat()])
        row = owner()
        identity.claim(row)
        records.upsert(record(row))
        records.upsert({**record(row), "outcome": "partial"})
        with pytest.raises(identity.RunIdentityConflict):
            records.upsert({**record(row), "execution_nonce": str(uuid4())})
        assert records.get(row["run_id"])["outcome"] == "partial"
        # Feasibility only: this session-nonce guard is NOT installed by migration
        # 176. Legacy binaries fail with SQLSTATE 55000 until all writers implement
        # the session contract; an in-place rollout would break those writers.
        with admin.cursor() as cur:
            cur.execute("""CREATE FUNCTION require_execution_context() RETURNS trigger AS $$
                BEGIN
                    IF current_setting('flowgate.execution_nonce', true) IS DISTINCT FROM NEW.execution_nonce THEN
                        RAISE EXCEPTION 'execution context required' USING ERRCODE = '55000';
                    END IF;
                    RETURN NEW;
                END;
                $$ LANGUAGE plpgsql;
                CREATE TRIGGER identity_context BEFORE INSERT OR UPDATE ON ai_invoke_runs
                FOR EACH ROW EXECUTE FUNCTION require_execution_context();""")
        output = tmp_path / "legacy-guard-result.json"
        proc = ctx.Process(target=_pg_legacy_writer, args=(schema, row["run_id"], str(output)))
        try:
            proc.start()
            proc.join(30)
            assert proc.exitcode == 0
            assert json.loads(output.read_text(encoding="utf-8"))["sqlstate"] == "55000"
            assert records.get(row["run_id"])["token_id"] != "legacy-overwrite"
            with store.transaction():
                store._fetch_one("SELECT set_config('flowgate.execution_nonce', ?, true)", [row["execution_nonce"]])
                records.upsert({**record(row), "outcome": "complete"})
            assert records.get(row["run_id"])["outcome"] == "complete"
            print("0138 isolated session-nonce trigger: legacy process SQLSTATE=55000; matching context succeeds")
        finally:
            if proc.is_alive():
                proc.terminate()
                proc.join(10)
    finally:
        if store:
            store._db.db.close()
        with admin.cursor() as cur:
            cur.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(schema)))
        admin.close()
