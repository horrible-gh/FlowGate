"""flowgate.v02.0078 T0004 — Agent Registry pepper: provisioning, preservation, 503 contract.

0003-NR: agent_registry.digest() answers 503 temporarily_unavailable whenever
FLOWGATE_AGENT_PEPPER is absent, and no install path ever created it. These tests pin:

  * every install path (setup.sh / setup.ps1 / docker-entrypoint.sh) generates it once,
    persists it where the server reads it, and never overwrites an existing value;
  * none of them generates a replacement when Agents are already registered (the
    original pepper was lost — a new one would never authenticate them);
  * check_agent_pepper.py, which makes that decision, reads the DB without writing it,
    allows generation on a new DB only when BOTH agent tables are absent (per the
    catalog), and refuses on a partial schema, an unreadable catalog / registration
    query, or a damaged schema — a failed query is never read as "fresh install";
  * the 503 path still writes nothing and keeps the pending token, and names the
    missing setting without leaking any value.

The shell blocks are executed for real where the host has the interpreter (sh/bash for
setup.sh and the entrypoint, pwsh/powershell for setup.ps1); otherwise they skip.
Pepper values used here are test-only strings; nothing prints them.
"""
from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("FLOWGATE_AGENT_PEPPER", "test-only-pepper")

from modules.flow_gate.services import agent_registry as ar  # noqa: E402
from modules.flow_gate.api.v1 import agent_routes as routes  # noqa: E402

SERVER = Path(__file__).resolve().parents[1]
REPO = SERVER.parent
MIG = SERVER / "sql" / "migrations" / "sqlite" / "121_agent_registry.sql"
PY = sys.executable
HEX64 = re.compile(r"^[0-9a-f]{64}$")


def _read(rel):
    return (REPO / rel).read_text(encoding="utf-8")


# ── helpers: an agent-registry sqlite DB in a given state ────────────────────

def _registry_db(path: Path, *, live=0, revoked=0, pending=0, expired=0):
    db = sqlite3.connect(path)
    db.executescript(MIG.read_text(encoding="utf-8"))
    ts = "2026-01-01T00:00:00Z"
    future = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat(timespec="seconds").replace("+00:00", "Z")
    n = 0

    def _agent(kind):
        aid = f"agt_{kind}{n}"
        db.execute("INSERT INTO agents(agent_id,name,enabled,location,connection_mode,created_at,updated_at) "
                   "VALUES(?,?,?,?,?,?,?)", (aid, aid, 1, "remote", "agent_pull", ts, ts))
        return aid

    for kind, count, revoked_at in (("live", live, None), ("revoked", revoked, ts)):
        for _i in range(count):
            n += 1
            aid = _agent(kind)
            db.execute("INSERT INTO agent_credentials(credential_id,agent_id,credential_digest,pepper_id,issued_at,revoked_at) "
                       "VALUES(?,?,?,?,?,?)", (f"c{n}", aid, f"{n:064d}", "primary", ts, revoked_at))
    for kind, count, exp in (("pending", pending, future), ("expired", expired, "2026-01-01T00:10:00Z")):
        for _i in range(count):
            n += 1
            aid = _agent(kind)
            db.execute("INSERT INTO agent_enrollments(enrollment_id,agent_id,token_digest,pepper_id,created_at,expires_at) "
                       "VALUES(?,?,?,?,?,?)", (f"e{n}", aid, f"{n:064d}", "primary", ts, exp))
    db.commit()
    db.close()
    return path


def _clean_env(**extra):
    env = {k: v for k, v in os.environ.items()
           if not (k.startswith("FLOWGATE_") or k.startswith("DB_") or k in ("SECRET_KEY", "PYTEST_CURRENT_TEST"))}
    env.update({k: str(v) for k, v in extra.items()})
    return env


def _helper(tmp_path, db_path=None, **env):
    """Run check_agent_pepper.py from an isolated copy, so the worktree's server/.env never leaks in."""
    root = tmp_path / "helper_root"
    if not root.exists():
        root.mkdir()
        for name in ("check_agent_pepper.py", "db_bootstrap.py"):
            shutil.copy(SERVER / name, root / name)
    e = _clean_env(DB_TYPE=env.pop("DB_TYPE", "sqlite3"), **env)
    if db_path is not None:
        e["DB_PATH"] = str(db_path)
    return subprocess.run([PY, str(root / "check_agent_pepper.py")], env=e, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)


# ── check_agent_pepper.py: the decision every installer delegates to ─────────

class TestCheckAgentPepper:
    def test_no_database_yet_is_safe(self, tmp_path):
        r = _helper(tmp_path, tmp_path / "missing.db")
        assert r.returncode == 0, r.stdout + r.stderr
        assert not (tmp_path / "missing.db").exists(), "the check must not create the DB"

    def test_database_without_agent_tables_is_safe(self, tmp_path):
        p = tmp_path / "bare.db"
        sqlite3.connect(p).close()
        assert _helper(tmp_path, p).returncode == 0

    def test_registry_without_live_registrations_is_safe(self, tmp_path):
        p = _registry_db(tmp_path / "r.db", revoked=2, expired=1)
        r = _helper(tmp_path, p)
        assert r.returncode == 0, r.stdout + r.stderr

    @pytest.mark.parametrize("state", [{"live": 1}, {"pending": 1}, {"live": 2, "revoked": 1, "pending": 1}])
    def test_live_registrations_block_generation_and_say_restore(self, tmp_path, state):
        p = _registry_db(tmp_path / "r.db", **state)
        before = p.read_bytes()
        r = _helper(tmp_path, p)
        assert r.returncode == 3, r.stdout + r.stderr
        assert "ORIGINAL" in r.stdout and "restore" in r.stdout
        assert p.read_bytes() == before, "the check must not write the DB"

    def test_uninspectable_database_is_unknown_not_safe(self, tmp_path):
        r = _helper(tmp_path, DB_TYPE="postgres", DB_HOST="127.0.0.1", DB_PORT="1",
                    DB_USER="x", DB_PASSWORD="x", DB_DATABASE="x")
        assert r.returncode == 2, r.stdout + r.stderr

    # 0078 TR0005 rework: only "both tables absent" is a new DB. Every other way the
    # registrations cannot be read must refuse (exit 2), never fall back to "safe".

    def test_complete_but_empty_registry_is_safe(self, tmp_path):
        r = _helper(tmp_path, _registry_db(tmp_path / "r.db"))
        assert r.returncode == 0, r.stdout + r.stderr

    @pytest.mark.parametrize("dropped,kept", [("agent_enrollments", "agent_credentials"),
                                              ("agent_credentials", "agent_enrollments")])
    def test_one_agent_table_missing_is_refused(self, tmp_path, dropped, kept):
        p = _registry_db(tmp_path / "r.db")
        db = sqlite3.connect(p)
        db.execute(f"DROP TABLE {dropped}")
        db.commit()
        db.close()
        before = p.read_bytes()
        r = _helper(tmp_path, p)
        assert r.returncode == 2, r.stdout + r.stderr
        assert "inconsistent Agent registry schema" in r.stdout
        assert f"{dropped} is missing" in r.stdout and kept in r.stdout
        assert "not generated" in r.stdout
        assert p.read_bytes() == before, "the check must not write the DB"

    def test_damaged_registry_schema_is_refused(self, tmp_path):
        # Both tables exist, but agent_credentials lost the column the count needs.
        p = _registry_db(tmp_path / "r.db")
        db = sqlite3.connect(p)
        db.execute("DROP TABLE agent_credentials")
        db.execute("CREATE TABLE agent_credentials(credential_id TEXT PRIMARY KEY)")
        db.commit()
        db.close()
        r = _helper(tmp_path, p)
        assert r.returncode == 2, r.stdout + r.stderr
        assert "Agent registrations could not be read" in r.stdout and "revoked_at" in r.stdout

    def test_corrupt_database_file_is_refused(self, tmp_path):
        p = tmp_path / "corrupt.db"
        p.write_bytes(b"this is not an sqlite database" * 64)
        r = _helper(tmp_path, p)
        assert r.returncode == 2, r.stdout + r.stderr
        assert "table catalog could not be read" in r.stdout

    def test_locked_database_is_refused(self, tmp_path):
        p = _registry_db(tmp_path / "r.db", live=1)
        holder = sqlite3.connect(p, isolation_level=None)
        holder.execute("BEGIN EXCLUSIVE")
        try:
            r = _helper(tmp_path, p)
        finally:
            holder.execute("ROLLBACK")
            holder.close()
        assert r.returncode == 2, r.stdout + r.stderr
        assert "locked" in r.stdout


class _FakeCursor:
    def __init__(self, conn):
        self.conn, self.description, self._rows = conn, None, []

    def execute(self, sql, params=()):
        self.conn.statements.append((sql, tuple(params)))
        if "information_schema" in sql:
            if self.conn.catalog_error:
                raise self.conn.catalog_error
            self.description, self._rows = [("name",)], [(t,) for t in self.conn.tables]
        elif "COUNT(*)" in sql:
            if self.conn.count_error:
                raise self.conn.count_error
            self.description, self._rows = [("n",)], [(self.conn.live,)]
        else:
            self.description, self._rows = [("expires_at",)], []

    def fetchall(self):
        return self._rows


class _FakeConn:
    """A mysql/postgres DB-API connection stand-in: catalog rows and failures on demand."""

    def __init__(self, tables=(), live=0, catalog_error=None, count_error=None):
        self.tables, self.live = tables, live
        self.catalog_error, self.count_error = catalog_error, count_error
        self.statements = []

    def cursor(self):
        return _FakeCursor(self)


@pytest.fixture
def pepper_check(tmp_path, monkeypatch):
    """check_agent_pepper imported from an isolated copy (no worktree .env is loaded)."""
    root = tmp_path / "import_root"
    root.mkdir()
    shutil.copy(SERVER / "check_agent_pepper.py", root / "check_agent_pepper.py")
    monkeypatch.setattr(sys, "stdout", io.TextIOWrapper(io.BytesIO(), encoding="utf-8"))
    monkeypatch.delenv("DB_SCHEMA", raising=False)
    spec = importlib.util.spec_from_file_location("check_agent_pepper_0078", root / "check_agent_pepper.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestCheckAgentPepperNetworkedEngines:
    """The same decision for mysql/postgres, which the host cannot run: driven through the catalog."""

    @pytest.mark.parametrize("db_type", ["mysql", "postgres"])
    def test_new_database_without_both_tables_is_safe(self, pepper_check, db_type):
        conn = _FakeConn(tables=())
        assert pepper_check.judge(conn, db_type)[0] == 0
        catalog_sql, params = conn.statements[0]
        assert "information_schema.tables" in catalog_sql and "%s" in catalog_sql
        assert "agent_credentials" in params and "agent_enrollments" in params
        assert len(conn.statements) == 1, "no registration query on a new DB"

    def test_postgres_catalog_is_scoped_to_the_configured_schema(self, pepper_check, monkeypatch):
        monkeypatch.setenv("DB_SCHEMA", "flowgate_app")
        conn = _FakeConn(tables=())
        pepper_check.judge(conn, "postgres")
        assert conn.statements[0][1][0] == "flowgate_app"

    def test_mysql_catalog_is_scoped_to_the_current_database(self, pepper_check):
        conn = _FakeConn(tables=())
        pepper_check.judge(conn, "mysql")
        assert "DATABASE()" in conn.statements[0][0]

    @pytest.mark.parametrize("db_type", ["mysql", "postgres"])
    @pytest.mark.parametrize("tables", [("agent_credentials",), ("AGENT_ENROLLMENTS",)])
    def test_one_table_missing_is_refused(self, pepper_check, db_type, tables):
        code, message = pepper_check.judge(_FakeConn(tables=tables), db_type)
        assert code == 2 and "inconsistent Agent registry schema" in message

    @pytest.mark.parametrize("db_type", ["mysql", "postgres"])
    def test_catalog_query_failure_is_refused(self, pepper_check, db_type):
        code, message = pepper_check.judge(
            _FakeConn(catalog_error=PermissionError("permission denied for schema information_schema")), db_type)
        assert code == 2 and "table catalog could not be read" in message and "PermissionError" in message

    @pytest.mark.parametrize("db_type", ["mysql", "postgres"])
    def test_registration_query_failure_is_refused(self, pepper_check, db_type):
        conn = _FakeConn(tables=("agent_credentials", "agent_enrollments"),
                         count_error=RuntimeError('column "revoked_at" does not exist'))
        code, message = pepper_check.judge(conn, db_type)
        assert code == 2 and "Agent registrations could not be read" in message and "revoked_at" in message

    @pytest.mark.parametrize("db_type", ["mysql", "postgres"])
    def test_live_credentials_still_block(self, pepper_check, db_type):
        conn = _FakeConn(tables=("agent_credentials", "agent_enrollments"), live=2)
        code, message = pepper_check.judge(conn, db_type)
        assert code == 3 and "ORIGINAL" in message

    @pytest.mark.parametrize("db_type", ["mysql", "postgres"])
    def test_complete_empty_registry_is_safe(self, pepper_check, db_type):
        conn = _FakeConn(tables=("agent_credentials", "agent_enrollments"), live=0)
        assert pepper_check.judge(conn, db_type)[0] == 0


# ── the install paths ────────────────────────────────────────────────────────

class TestInstallPathsStatic:
    @pytest.mark.parametrize("path", ["server/.env.sample", "docker-compose.yml",
                                      "deploy/docker-entrypoint.sh", "setup.sh", "setup.ps1"])
    def test_every_install_path_knows_the_pepper(self, path):
        assert "FLOWGATE_AGENT_PEPPER" in _read(path)

    @pytest.mark.parametrize("path,guard", [
        ("setup.sh", "if env_unset FLOWGATE_AGENT_PEPPER; then"),
        ("setup.ps1", "if (-not (Test-EnvSet 'FLOWGATE_AGENT_PEPPER')) {"),
        ("deploy/docker-entrypoint.sh", 'if [ -z "${FLOWGATE_AGENT_PEPPER:-}" ]; then'),
    ])
    def test_generation_is_guarded_by_unset_and_the_registration_check(self, path, guard):
        body = _read(path)
        assert guard in body
        block = body.split(guard, 1)[1]
        assert block.index("check_agent_pepper.py") < block.index("FLOWGATE_AGENT_PEPPER")

    def test_entrypoint_generates_after_the_db_wait(self):
        body = _read("deploy/docker-entrypoint.sh")
        assert body.index("waiting for $DB_HOST:$DB_PORT") < body.index('if [ -z "${FLOWGATE_AGENT_PEPPER:-}" ]')

    def test_env_sample_section_is_not_inside_the_totp_block(self):
        body = _read("server/.env.sample")
        assert body.index("FLOWGATE_TOTP_ENCRYPT_KEY_PREV=") < body.index("FLOWGATE_AGENT_PEPPER=")
        assert re.search(r"^FLOWGATE_AGENT_PEPPER=$", body, re.M), "the sample must not ship a value"

    def test_setup_sh_has_no_stray_nan_line(self):
        assert not re.search(r"^NaN$", _read("setup.sh"), re.M)

    def test_no_install_path_echoes_the_value(self):
        for path in ("setup.sh", "deploy/docker-entrypoint.sh"):
            assert not re.search(r'echo[^\n]*\$\{?FLOWGATE_AGENT_PEPPER', _read(path)), path
        assert not re.search(r"Write-Host[^\n]*\$FLOWGATE_AGENT_PEPPER", _read("setup.ps1"))

    def test_settings_declare_and_mirror_the_pepper(self):
        # Read as text: importing config builds Settings() from the process environment.
        source = (SERVER / "config.py").read_text(encoding="utf-8")
        settings_body = source.split("class Settings(BaseSettings):")[1].split("\nsettings = Settings()")[0]
        # A .env line for the key must not break boot (extra keys are forbidden) ...
        assert re.search(r"^    FLOWGATE_AGENT_PEPPER: str \| None = None$", settings_body, re.M)
        assert re.search(r"^    FLOWGATE_AGENT_PEPPER_ID: str = \"primary\"$", settings_body, re.M)
        # ... and config.py mirrors it into os.environ, where agent_registry.digest() reads it.
        mirrored = source.split("for _env_key in (")[1].split(")")[0]
        assert '"FLOWGATE_AGENT_PEPPER",' in mirrored


def _posix_shell():
    for name in ("bash", "sh"):
        found = shutil.which(name)
        # On Windows, System32\bash.exe is WSL: a different filesystem, not a POSIX shell for these paths.
        if found and "system32" not in found.lower():
            return found
    return None


class TestWholeScriptSyntax:
    """The blocks below run in isolation; this keeps the full scripts parseable around them."""

    @pytest.mark.parametrize("path", ["setup.sh", "deploy/docker-entrypoint.sh"])
    def test_posix_scripts_parse(self, path):
        sh = _posix_shell()
        if not sh:
            pytest.skip("no POSIX shell (bash/sh) on this host")
        r = subprocess.run([sh, "-n", (REPO / path).as_posix()], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=60)
        assert r.returncode == 0, r.stderr

    def test_setup_ps1_parses(self, tmp_path):
        ps = _powershell()
        if not ps:
            pytest.skip("no PowerShell (pwsh/powershell) on this host")
        probe = tmp_path / "parse.ps1"
        probe.write_text("$errs = $null; $tok = $null\n"
                         "[void][System.Management.Automation.Language.Parser]::ParseFile($args[0], [ref]$tok, [ref]$errs)\n"
                         "if ($errs.Count) { $errs | ForEach-Object { Write-Output $_.ToString() }; exit 1 }\n",
                         encoding="utf-8")
        r = subprocess.run([ps, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(probe),
                            str(REPO / "setup.ps1")], capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=120)
        assert r.returncode == 0, r.stdout + r.stderr


def _block(text, start):
    """The block that begins at `start` through its closing column-0 `fi` / `}`."""
    i = text.index(start)
    end = "\nfi\n" if start.endswith("then") else "\n}\n"
    return text[i:text.index(end, i) + len(end)]


def _line(text, start):
    i = text.index(start)
    return text[i:text.index("\n", i) + 1]


def _wrapper(path: Path, target: str):
    path.write_text(f'#!/bin/sh\nexec "{Path(target).as_posix()}" "$@"\n', encoding="utf-8", newline="\n")
    path.chmod(0o755)


def _env_value(env_file: Path, key="FLOWGATE_AGENT_PEPPER"):
    for line in env_file.read_text(encoding="utf-8").splitlines():
        if line.startswith(key + "="):
            return line.split("=", 1)[1]
    return None


def _install_root(tmp_path, env_text):
    root = tmp_path / "install"
    (root / "server").mkdir(parents=True)
    for name in ("check_agent_pepper.py", "db_bootstrap.py"):
        shutil.copy(SERVER / name, root / "server" / name)
    (root / "server" / ".env").write_text(env_text, encoding="utf-8", newline="\n")
    return root


def _sqlite_env(tmp_path, pepper=""):
    return (f"DB_TYPE=sqlite3\nDB_PATH={(tmp_path / 'flowgate.db').as_posix()}\n"
            f"FLOWGATE_AGENT_PEPPER={pepper}\nFLOWGATE_AGENT_PEPPER_ID=primary\n")


class TestSetupShBlock:
    """The pepper block of setup.sh, run with setup.sh's own set_env / env_unset helpers."""

    def _prepare(self, tmp_path, env_text):
        sh = _posix_shell()
        if not sh:
            pytest.skip("no POSIX shell (bash/sh) on this host")
        root = _install_root(tmp_path, env_text)
        (root / ".venv" / "bin").mkdir(parents=True)
        _wrapper(root / ".venv" / "bin" / "python", PY)
        bindir = tmp_path / "bin"
        bindir.mkdir()
        _wrapper(bindir / "python3", PY)
        src = _read("setup.sh")
        script = "\n".join([
            "set -euo pipefail",
            f'ROOT="{root.as_posix()}"',
            'ENV_FILE="$ROOT/server/.env"',
            _block(src, "set_env() {"),
            _line(src, "env_unset() {"),
            _block(src, "if env_unset FLOWGATE_AGENT_PEPPER; then"),
        ])
        (tmp_path / "block.sh").write_text(script, encoding="utf-8", newline="\n")
        env = _clean_env(PATH=bindir.as_posix() + ":" + os.environ.get("PATH", "") if os.name != "nt"
                         else str(bindir) + os.pathsep + os.environ.get("PATH", ""))
        run = lambda: subprocess.run([sh, (tmp_path / "block.sh").as_posix()], env=env,
                                     capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
        return root / "server" / ".env", run

    def test_fresh_install_generates_once_and_rerun_keeps_it(self, tmp_path):
        env_file, run = self._prepare(tmp_path, _sqlite_env(tmp_path))
        r = run()
        assert r.returncode == 0, r.stdout + r.stderr
        first = _env_value(env_file)
        assert first and HEX64.match(first)
        assert first not in r.stdout + r.stderr, "the value must not be printed"
        r2 = run()  # re-install over the same .env
        assert r2.returncode == 0, r2.stdout + r2.stderr
        assert _env_value(env_file) == first
        assert env_file.read_text(encoding="utf-8").count("FLOWGATE_AGENT_PEPPER=") == 1

    def test_existing_value_is_never_touched(self, tmp_path):
        env_file, run = self._prepare(tmp_path, _sqlite_env(tmp_path, "operator-chosen-test-only"))
        r = run()
        assert r.returncode == 0, r.stdout + r.stderr
        assert _env_value(env_file) == "operator-chosen-test-only"

    def test_lost_pepper_with_registered_agents_is_not_replaced(self, tmp_path):
        _registry_db(tmp_path / "flowgate.db", live=1)
        env_file, run = self._prepare(tmp_path, _sqlite_env(tmp_path))
        r = run()
        assert r.returncode == 0, "the installer goes on; only the pepper is withheld"
        assert _env_value(env_file) == ""
        assert "NOT generated" in r.stdout and "ORIGINAL" in r.stdout


def _powershell():
    for name in ("pwsh", "powershell"):
        found = shutil.which(name)
        if found:
            return found
    return None


class TestSetupPs1Block:
    """The pepper block of setup.ps1, run with setup.ps1's own Set-EnvVar / Test-EnvSet / New-Secret."""

    def _prepare(self, tmp_path, env_text):
        ps = _powershell()
        if not ps:
            pytest.skip("no PowerShell (pwsh/powershell) on this host")
        root = _install_root(tmp_path, env_text)
        src = _read("setup.ps1")
        block = _block(src, "if (-not (Test-EnvSet 'FLOWGATE_AGENT_PEPPER')) {")
        sep = "\\" if os.name == "nt" else "/"
        script = "\n".join([
            "$ErrorActionPreference = 'Stop'",
            f"$Root = '{root}'",
            f"$EnvFile = Join-Path $Root 'server{sep}.env'",
            f"$VenvPython = '{PY}'",
            _block(src, "function Set-EnvVar("),
            _block(src, "function Test-EnvSet("),
            _line(src, "function New-Secret"),
            block.replace("'server\\check_agent_pepper.py'", f"'server{sep}check_agent_pepper.py'"),
        ])
        (tmp_path / "block.ps1").write_text(script, encoding="utf-8-sig")
        run = lambda: subprocess.run([ps, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                                      "-File", str(tmp_path / "block.ps1")],
                                     env=_clean_env(), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180)
        return root / "server" / ".env", run

    def test_fresh_install_generates_once_and_rerun_keeps_it(self, tmp_path):
        env_file, run = self._prepare(tmp_path, _sqlite_env(tmp_path))
        r = run()
        assert r.returncode == 0, r.stdout + r.stderr
        first = _env_value(env_file)
        assert first and HEX64.match(first)
        assert first not in r.stdout + r.stderr
        assert not env_file.read_bytes().startswith(b"\xef\xbb\xbf"), ".env must stay BOM-free"
        r2 = run()
        assert r2.returncode == 0, r2.stdout + r2.stderr
        assert _env_value(env_file) == first
        assert env_file.read_text(encoding="utf-8").count("FLOWGATE_AGENT_PEPPER=") == 1

    def test_existing_value_is_never_touched(self, tmp_path):
        env_file, run = self._prepare(tmp_path, _sqlite_env(tmp_path, "operator-chosen-test-only"))
        r = run()
        assert r.returncode == 0, r.stdout + r.stderr
        assert _env_value(env_file) == "operator-chosen-test-only"

    def test_lost_pepper_with_registered_agents_is_not_replaced(self, tmp_path):
        _registry_db(tmp_path / "flowgate.db", live=1)
        env_file, run = self._prepare(tmp_path, _sqlite_env(tmp_path))
        r = run()
        assert r.returncode == 0, r.stdout + r.stderr
        assert _env_value(env_file) == ""
        assert "NOT generated" in r.stdout and "ORIGINAL" in r.stdout


class TestDockerEntrypoint:
    """deploy/docker-entrypoint.sh run as-is (outside a container) against a temp data volume.

    Each run is a fresh process with no FLOWGATE_* in its environment — exactly what a
    re-created container sees; only the data directory survives between runs.
    """

    def _run(self, tmp_path, data, **env):
        sh = _posix_shell()
        if not sh:
            pytest.skip("no POSIX shell (bash/sh) on this host")
        probe = tmp_path / "probe.py"
        probe.write_text("import hashlib,os,sys\nv=os.environ.get('FLOWGATE_AGENT_PEPPER','')\n"
                         "open(sys.argv[1],'w').write(hashlib.sha256(v.encode()).hexdigest() if v else '')\n",
                         encoding="utf-8")
        out = tmp_path / "seen.txt"
        if out.exists():
            out.unlink()
        e = _clean_env(FLOWGATE_STORAGE_DIR=data.as_posix(), FLOWGATE_SERVER_DIR=SERVER.as_posix(),
                       PATH=str(Path(PY).parent) + os.pathsep + os.environ.get("PATH", ""), **env)
        r = subprocess.run([sh, (REPO / "deploy" / "docker-entrypoint.sh").as_posix(),
                            Path(PY).as_posix(), probe.as_posix(), out.as_posix()],
                           env=e, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180)
        return r, (out.read_text() if out.exists() else None)

    def _persisted(self, data):
        f = data / ".flowgate-secrets.env"
        if not f.exists():
            return []
        return [l.split("=", 1)[1] for l in f.read_text().splitlines() if l.startswith("FLOWGATE_AGENT_PEPPER=")]

    def test_fresh_volume_generates_and_recreation_and_restart_keep_it(self, tmp_path):
        data = tmp_path / "data"
        r, seen = self._run(tmp_path, data)
        assert r.returncode == 0, r.stdout + r.stderr
        values = self._persisted(data)
        assert len(values) == 1 and HEX64.match(values[0])
        assert values[0] not in r.stdout + r.stderr
        assert seen == hashlib.sha256(values[0].encode()).hexdigest(), "the server process got the persisted value"
        for _ in range(2):  # container re-creation, then a restart: same volume, empty env
            r, again = self._run(tmp_path, data)
            assert r.returncode == 0, r.stdout + r.stderr
            assert again == seen
        assert self._persisted(data) == values, "never appended again, never rotated"

    def test_explicit_environment_value_wins_and_is_not_persisted(self, tmp_path):
        data = tmp_path / "data"
        r, seen = self._run(tmp_path, data, FLOWGATE_AGENT_PEPPER="explicit-test-only")
        assert r.returncode == 0, r.stdout + r.stderr
        assert seen == hashlib.sha256(b"explicit-test-only").hexdigest()
        assert self._persisted(data) == []

    def test_lost_pepper_with_registered_agents_is_not_replaced(self, tmp_path):
        data = tmp_path / "data"
        data.mkdir()
        _registry_db(data / "flowgate.db", live=1)
        r, seen = self._run(tmp_path, data)
        assert r.returncode == 0, "the server still starts; only the pepper is withheld"
        assert seen == ""
        assert self._persisted(data) == []
        assert "not generated" in r.stdout and "ORIGINAL" in r.stdout


# ── server side: the 503 contract and the boot warning ───────────────────────

def _store(db):
    class Store:
        @contextmanager
        def transaction(self):
            with db:
                yield self

        def _fetch_one(self, sql, params):
            c = db.execute(sql, params)
            row = c.fetchone()
            return dict(zip([d[0] for d in c.description], row)) if row else None

        def _fetch_all(self, sql, params=()):
            c = db.execute(sql, params)
            return [dict(zip([d[0] for d in c.description], r)) for r in c.fetchall()]

        def _execute(self, sql, params):
            return db.execute(sql, params)

        def _execute_affected(self, sql, params):
            return db.execute(sql, params).rowcount
    return Store()


@pytest.fixture
def registry():
    db = sqlite3.connect(":memory:", check_same_thread=False)
    db.execute("PRAGMA foreign_keys=ON")
    db.executescript(MIG.read_text(encoding="utf-8"))
    ts = "2026-01-01T00:00:00Z"
    db.execute("INSERT INTO agents(agent_id,name,enabled,location,connection_mode,created_at,updated_at) "
               "VALUES('agt_x','r',1,'remote','agent_pull',?,?)", (ts, ts))
    db.commit()
    yield db, ar.AgentService(_store(db))
    db.close()


def _snapshot(db):
    return {t: db.execute(f"SELECT * FROM {t} ORDER BY 1").fetchall()
            for t in ("agents", "agent_credentials", "agent_enrollments")}


ENROLL = {"protocol_version": 1, "agent_version": "1", "os": "linux", "architecture": "amd64"}
HEARTBEAT = {"agent_id": "agt_x", "protocol_version": 1, "agent_version": "1", "os": "linux", "architecture": "amd64",
             "reported_capabilities": {"ai_cli": True, "ai_api": False, "storage": False}}


def test_missing_pepper_503_names_the_setting_without_a_value(monkeypatch):
    monkeypatch.delenv("FLOWGATE_AGENT_PEPPER", raising=False)
    with pytest.raises(ar.AgentError) as e:
        ar.digest("enrollment", "enr_x")
    assert (e.value.status, e.value.code) == (503, "temporarily_unavailable")
    assert e.value.details == {"reason": "agent_pepper_not_configured"}
    body = json.loads(routes.err(e.value).body)
    assert "FLOWGATE_AGENT_PEPPER" in body["error"]["message"] and "enr_x" not in json.dumps(body)


def test_issue_without_pepper_writes_nothing_and_keeps_the_pending_token(registry, monkeypatch):
    db, svc = registry
    monkeypatch.setenv("FLOWGATE_AGENT_PEPPER", "test-only-pepper-0078")
    pending = svc.issue("agt_x")["enrollment_token"]
    before = _snapshot(db)
    monkeypatch.delenv("FLOWGATE_AGENT_PEPPER")
    with pytest.raises(ar.AgentError) as e:
        svc.issue("agt_x")
    assert e.value.status == 503
    assert _snapshot(db) == before, "revocation of the pending token and the INSERT both rolled back"
    monkeypatch.setenv("FLOWGATE_AGENT_PEPPER", "test-only-pepper-0078")
    assert svc.enroll({"enrollment_token": pending, **ENROLL})["agent_id"] == "agt_x"


def test_issue_success_contract_is_unchanged(registry, monkeypatch):
    db, svc = registry
    monkeypatch.setenv("FLOWGATE_AGENT_PEPPER", "test-only-pepper-0078")
    svc.issue("agt_x")
    second = svc.issue("agt_x")
    assert second["enrollment_token"].startswith("enr_")
    exp = datetime.fromisoformat(second["expires_at"].replace("Z", "+00:00"))
    assert timedelta(seconds=590) < exp - datetime.now(timezone.utc) <= timedelta(seconds=600)
    assert db.execute("SELECT COUNT(*) FROM agent_enrollments "
                      "WHERE revoked_at IS NULL AND consumed_at IS NULL").fetchone()[0] == 1
    assert db.execute("SELECT COUNT(*) FROM agent_enrollments WHERE token_digest=?",
                      (second["enrollment_token"],)).fetchone()[0] == 0, "only the digest is stored"


def test_registered_agent_survives_a_restart_with_the_same_pepper_only(registry, monkeypatch):
    db, svc = registry
    monkeypatch.setenv("FLOWGATE_AGENT_PEPPER", "persisted-test-only")
    token = svc.issue("agt_x")["enrollment_token"]
    cred = svc.enroll({"enrollment_token": token, **ENROLL})["agent_credential"]
    assert svc.heartbeat(cred, HEARTBEAT)["accepted"] is True
    # restart: a new service re-reads the same persisted value
    assert ar.AgentService(_store(db)).heartbeat(cred, HEARTBEAT)["accepted"] is True
    # what a silent replacement would do — the reason the installers refuse to generate one
    monkeypatch.setenv("FLOWGATE_AGENT_PEPPER", "replacement-test-only")
    with pytest.raises(ar.AgentError) as e:
        ar.AgentService(_store(db)).heartbeat(cred, HEARTBEAT)
    assert (e.value.status, e.value.code) == (401, "unauthorized")


class TestBootWarning:
    def _capture(self, monkeypatch):
        import startup
        seen = []
        monkeypatch.setattr(startup.logger, "warning", lambda m, *a, **k: seen.append(str(m)))
        return startup, seen

    def test_silent_when_configured(self, monkeypatch):
        startup, seen = self._capture(monkeypatch)
        monkeypatch.setenv("FLOWGATE_AGENT_PEPPER", "configured-test-only")
        startup.check_agent_pepper()
        assert seen == []

    def test_missing_pepper_with_registered_agents_asks_for_manual_recovery(self, monkeypatch, registry):
        db, _svc = registry
        db.execute("INSERT INTO agent_credentials(credential_id,agent_id,credential_digest,pepper_id,issued_at) "
                   "VALUES('c1','agt_x',?,'primary','2026-01-01T00:00:00Z')", ("0" * 64,))
        db.commit()
        startup, seen = self._capture(monkeypatch)
        import modules.flow_gate.db.connection as connection
        monkeypatch.setattr(connection, "get_store", lambda: _store(db))
        monkeypatch.delenv("FLOWGATE_AGENT_PEPPER", raising=False)
        startup.check_agent_pepper()
        assert len(seen) == 1
        assert "FLOWGATE_AGENT_PEPPER is not set" in seen[0]
        assert "1 registered Agent credential" in seen[0] and "ORIGINAL" in seen[0]

    def test_boot_sequence_runs_the_check(self):
        source = (SERVER / "startup.py").read_text(encoding="utf-8")
        assert "check_agent_pepper()" in source.split("def run_all")[1]
