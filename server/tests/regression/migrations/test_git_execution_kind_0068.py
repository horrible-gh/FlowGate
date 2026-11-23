"""The SQLite kind expansion preserves existing jobs, references and uniqueness."""
import sqlite3
from pathlib import Path


def test_git_execution_migration_preserves_jobs_and_references():
    migrations = Path(__file__).resolve().parents[3] / "sql" / "migrations" / "sqlite"
    old = (migrations / "161_operation_job_gate_cleanup_kind.sql").read_text(encoding="utf-8")
    create = old[old.index("CREATE TABLE operation_job_new ("):old.index("INSERT INTO operation_job_new (")]
    conn = sqlite3.connect(":memory:")
    conn.executescript("CREATE TABLE projects(project_id TEXT PRIMARY KEY);"
                       "CREATE TABLE server_instance(instance_id TEXT PRIMARY KEY);"
                       "INSERT INTO projects VALUES ('p');" + create.replace("operation_job_new", "operation_job"))
    columns = "job_id,kind,project_id,request_key,request_key_hash,request_fingerprint,status,available_at,created_at,updated_at"
    values = ("old", "base_publish", "p", "old", "a" * 64, "b" * 64, "pending", "now", "now", "now")
    conn.execute(f"INSERT INTO operation_job ({columns}) VALUES (?,?,?,?,?,?,?,?,?,?)", values)
    conn.execute("CREATE TABLE dependent(job_id TEXT REFERENCES operation_job(job_id))")
    conn.execute("INSERT INTO dependent VALUES ('old')")
    conn.commit()
    upgrade = (migrations / "211_operation_job_git_execution_kind.sql").read_text(encoding="utf-8")
    conn.executescript(upgrade)
    assert conn.execute(f"SELECT {columns} FROM operation_job").fetchone() == values
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    conn.execute(f"INSERT INTO operation_job ({columns}) VALUES (?,?,?,?,?,?,?,?,?,?)",
                 ("new", "git_execution", "p", "new", "c" * 64, "d" * 64, "pending", "now", "now", "now"))
    conn.commit()
    conn.executescript(upgrade)
    assert conn.execute("SELECT count(*) FROM operation_job").fetchone()[0] == 2
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
    indices = {r[1] for r in conn.execute("PRAGMA index_list(operation_job)")}
    assert {"idx_job_request", "idx_job_claim", "idx_job_lease", "idx_job_lease_owner", "idx_job_blocked", "idx_job_group"} <= indices
    conn.close()
