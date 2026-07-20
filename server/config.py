from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import field_validator
from typing import Optional
from enum import Enum
from sqloader.init import database_init
from auth2fa import TwoFactorAuth, Auth2FAAdapter
import os
import re
import LogAssist.log as logger
from util import jsonutil as json
import os as _os
# Load logger.json relative to this file so tests running from other CWDs succeed
_LOGGER_JSON_PATH = _os.path.join(_os.path.dirname(__file__), "logger.json")
logger_config = json.json_read(_LOGGER_JSON_PATH)
logger.logger_init(logger_config)

SERVICE_SQLOADER = "sql/queries"
MIGRATION_PATHS = "sql/migrations"

# 🔹 Define DB_TYPE clearly using Enum
class DBType(str, Enum):
    MYSQL = "mysql"
    SQLITE = "sqlite"
    SQLITE3 = "sqlite3"
    POSTGRES = "postgres"
    LOCAL = "local"

# 🔹 Settings class (using Pydantic)
class Settings(BaseSettings):
    ALLOWED_ORIGIN: str
    SECRET_KEY: str
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    CONTEXT: str
    DB_TYPE: DBType  # Enum applied
    DB_HOST: str = ""
    DB_PORT: int = 0
    DB_USER: str = ""
    DB_PASSWORD: str = ""
    DB_DATABASE: str = ""
    DB_SCHEMA: str = ""
    DB_LOG: bool = True
    DB_PATH: str = ""

    # NOTE: preset activation is now managed by server/res/preset_hands.json
    # and not by environment settings. PRESET_HANDS removed per T072.

    # REL-010: Maintenance mode — toggle via MAINTENANCE_MODE=true/false in .env
    # Message is delivered based on server/res/maintenance/maintenance_{lang}.txt file
    MAINTENANCE_MODE: bool = False

    RATE_LIMIT_DEFAULT: str = "100/hour"
    RATE_LIMIT_LOGIN: str = "5/minute"
    RATE_LIMIT_UPLOAD: str = "20/hour"
    RATE_LIMIT_DOWNLOAD: str = "50/hour"

    # Phase 1 Token (D020) — R015 new env variables
    FLOWGATE_TOKEN_PEPPER_ACTIVE_ID: str | None = None
    FLOWGATE_TOKEN_PEPPER_V1: str | None = None
    FLOWGATE_INBOX_CONTENT_MAX: int = 10485760  # 10 MB default

    # Git integration (0115 L0006 §2.3) — base64-encoded 32-byte AES key for the
    # project git credential store. Provisioned by the docker entrypoint (or the
    # storage-root key-file fallback); _PREV enables rotation-time decryption.
    FLOWGATE_GIT_ENCRYPT_KEY: str | None = None
    FLOWGATE_GIT_ENCRYPT_KEY_PREV: str | None = None

    # TOTP secret encryption (0273 NR0003 P1-3) — base64-encoded 32-byte AES key
    # read by modules/flow_gate/auth/totp_service.py. Declared here so the setup
    # scripts can persist it in .env: pydantic forbids extra keys, so an
    # undeclared FLOWGATE_TOTP_ENCRYPT_KEY line would fail the boot outright.
    # Unset does NOT break startup — it breaks the first 2FA enrolment, which is
    # why this went unnoticed. _PREV enables rotation-time decryption.
    FLOWGATE_TOTP_ENCRYPT_KEY: str | None = None
    FLOWGATE_TOTP_ENCRYPT_KEY_PREV: str | None = None

    # Listen address (0273 NR0003 P1-2). stg.py — the entry point the systemd
    # unit runs — had port 8089 hardcoded, so a Linux install could not move off
    # a busy port without editing the source. Declared here for the same
    # extra_forbidden reason as the TOTP keys above; stg.py reads them from the
    # environment after load_dotenv().
    FLOWGATE_PORT: int = 8089
    FLOWGATE_BIND_HOST: str = "0.0.0.0"

    # 0275 T0007 (NR0003 원인 5): stg.py launch knobs. Reload is dev-only (the
    # file-watcher used to run unconditionally in production); keep workers at 1
    # while SSE matters — the publisher is in-process and does not span workers.
    # Declared for the same extra_forbidden reason as FLOWGATE_PORT above;
    # stg.py reads them from the environment after load_dotenv().
    FLOWGATE_RELOAD: bool = False
    FLOWGATE_WORKERS: int = 1

    # 0275 T0007 (NR0003 원인 5): run schema migrations + backfill scan at every
    # boot. Set false on remote-DB deployments where boot latency matters and
    # migrations are applied at deploy time instead.
    AUTO_MIGRATION: bool = True

    # Group 0235 D0005 §3-4 / L0008 §2-5: submission base for the EXTERNAL AGENT
    # (CLI provider) inbox POST. Origin only (scheme://host[:port]); the server
    # appends CONTEXT + /api/v1. When unset, the agent api base is derived by
    # swapping the operator host for loopback (same-host run). Set this when the
    # agent cannot reach the loopback:port (e.g. behind a reverse proxy).
    FLOWGATE_AGENT_API_BASE: str | None = None

    @field_validator("FLOWGATE_INBOX_CONTENT_MAX", mode="before")
    @classmethod
    def _blank_int_uses_default(cls, v):
        # .env / OS env / compose can supply this key as an EMPTY string (e.g.
        # `.env.sample` ships `FLOWGATE_INBOX_CONTENT_MAX=` and setup copies it
        # verbatim without filling it in). pydantic only falls back to the
        # default when a key is ABSENT, so a present-but-blank value would try to
        # coerce "" -> int and crash the boot (B0101). Treat blank as "unset".
        if isinstance(v, str) and v.strip() == "":
            return 10485760
        return v

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

settings = Settings()


# 🔹 Mirror the credential keys from .env into os.environ (0273 NR0003 P1-3).
# totp_service.py and git_service.py read os.environ directly, but pydantic
# parses .env into `settings` WITHOUT populating os.environ. That only worked
# because stg.py calls load_dotenv() first — the systemd path. Launchers that
# import the app directly (run.bat / setup.ps1 -Start / `uvicorn routers.main:app`)
# skip stg.py entirely, so a key that is present in .env stayed invisible to both
# services. Fill in only what the real environment has not already set, so an
# explicit OS env var (docker-entrypoint, systemd Environment=) still wins.
for _env_key in (
    "FLOWGATE_TOTP_ENCRYPT_KEY",
    "FLOWGATE_TOTP_ENCRYPT_KEY_PREV",
    "FLOWGATE_GIT_ENCRYPT_KEY",
    "FLOWGATE_GIT_ENCRYPT_KEY_PREV",
):
    _env_val = getattr(settings, _env_key, None)
    if _env_val and not os.environ.get(_env_key):
        os.environ[_env_key] = _env_val


# 🔹 DB settings class (singleton pattern)
class DatabaseSetting:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(DatabaseSetting, cls).__new__(cls)
            cls._instance._init_db()
        return cls._instance

    def _init_db(self):
        """Initialize DB."""
        self.db_instance = None
        self.sqloader = None
        self.migrator = None
        self.tfa = None
        self.config = {}

        logger.debug("settings", settings)

        if settings.DB_TYPE.value == DBType.MYSQL:
            self.config = {
                "type": settings.DB_TYPE.value,
                # Shared query files (sql/queries) are authored with SQLite-style
                # "?" placeholders. Declaring "?" here lets SQLoader translate them
                # to the DB-native placeholder (%s for MySQL/PostgreSQL) at load
                # time. No-op for SQLite (native placeholder is also "?").
                "placeholder": "?",
                f"{settings.DB_TYPE.value}": {
                    "host": settings.DB_HOST,
                    "port": settings.DB_PORT,
                    "user": settings.DB_USER,
                    "password": settings.DB_PASSWORD,
                    "database": settings.DB_DATABASE,
                    "schema": settings.DB_SCHEMA,
                    "log": settings.DB_LOG,
                },
                "service": {
                    "log": True,
                    "sqloder": SERVICE_SQLOADER
                },
                "migration": {
                    "auto_migration": settings.AUTO_MIGRATION,
                    "migration_path": MIGRATION_PATHS + "/mysql"
                },
            }
        elif settings.DB_TYPE.value in (DBType.SQLITE, DBType.SQLITE3, DBType.LOCAL):
            self.config = {
                "type": settings.DB_TYPE.value,
                # No-op for SQLite (native placeholder is "?"); declared for
                # parity with the MySQL/PostgreSQL branches. See note above.
                "placeholder": "?",
                f"{settings.DB_TYPE.value}": {
                    "db_name": settings.DB_PATH,
                    # 0273 NR0003 §5-2: this was hardcoded True, so DB_LOG had no
                    # effect on the DEFAULT engine — full SQL logging could not be
                    # turned off without editing the source. The mysql/postgres
                    # branches already honour settings.DB_LOG; match them.
                    "log": settings.DB_LOG,
                },
                "service": {
                    "log": True,
                    "sqloder": SERVICE_SQLOADER
                },
                "migration": {
                    "auto_migration": settings.AUTO_MIGRATION,
                    "migration_path": MIGRATION_PATHS + "/sqlite"
                },
            }
        elif settings.DB_TYPE.value in (DBType.POSTGRES,):
            if settings.DB_PORT == 0:
                settings.DB_PORT = None
            self.config = {
                "type": settings.DB_TYPE.value,
                # Translate SQLite-style "?" placeholders in shared query files
                # to the DB-native "%s" at load time. See note in the MySQL branch.
                "placeholder": "?",
                f"{settings.DB_TYPE.value}": {
                    "host": settings.DB_HOST,
                    "port": settings.DB_PORT,
                    "user": settings.DB_USER,
                    "password": settings.DB_PASSWORD,
                    "database": settings.DB_DATABASE,
                    "schema": settings.DB_SCHEMA,
                    "log": settings.DB_LOG,
                },
                "service": {
                    "log": True,
                    "sqloder": SERVICE_SQLOADER
                },
                "migration": {
                    "auto_migration": settings.AUTO_MIGRATION,
                    "migration_path": MIGRATION_PATHS + "/postgres"
                },
            }
        else:
            # 0273 NR0003 §5-5: falling through left self.config == {} and handed
            # database_init({}) an empty dict, whose failure names neither the
            # setting nor the value that caused it. The DBType enum already
            # rejects unknown values at Settings() time, so this fires only when
            # a member is added to the enum without a branch here — say so.
            raise ValueError(
                f"DB_TYPE='{settings.DB_TYPE.value}' has no configuration branch "
                f"in DatabaseSetting._init_db (supported: "
                f"{', '.join(t.value for t in DBType)})."
            )

        self.instance_init()


    def instance_init(self):
        """Initialize DB instance."""
        logger.debug("config", self.config)

        try:
            self.db_instance, self.sqloader, self.migrator = database_init(self.config)
            logger.debug(f"✅ DB initialized - type: {type(self.db_instance).__name__}, db_type: {getattr(self.db_instance, 'db_type', 'N/A')}")
        except Exception as e:
            import traceback
            logger.error(f"❌ database_init failed: {e}")
            logger.error(traceback.format_exc())
            raise

        # B0091: dialect-agnostic data backfills that replace SQLite-only JSON DML
        # migrations (see db/backfills/). Idempotent — safe to run on every boot —
        # and must never block startup, so failures are logged, not raised.
        try:
            from modules.flow_gate.db.backfills.rejection_id_backfill import (
                run_rejection_id_backfill,
            )
            n = run_rejection_id_backfill(self.db_instance)
            if n:
                logger.debug(f"✅ rejection_id backfill: {n} document(s) updated")
        except Exception as e:
            import traceback
            logger.error(f"⚠️ rejection_id backfill skipped: {e}")
            logger.error(traceback.format_exc())

        # Initialize 2FA (using SQLStorage + Auth2FAAdapter)
        try:
            adapter = Auth2FAAdapter(self.db_instance)
            self.tfa = TwoFactorAuth(issuer="ChipSama", sq=adapter)
            logger.debug("✅ 2FA initialized with SQLStorage via Auth2FAAdapter")
        except Exception as e:
            import traceback
            logger.error(f"❌ 2FA initialization failed: {e}")
            logger.error(traceback.format_exc())
            raise

    def get_db_instance(self):
        return self.db_instance

    def get_sqloader_instance(self):
        return self.sqloader

# 🔹 Create singleton instance
import os


def _enable_sqlite_wal() -> None:
    """Put the runtime SQLite DB into WAL journal mode, once, at boot.

    0279 T0005 (NR0003 §4): `PRAGMA journal_mode = WAL` existed only in
    db/migrations/migrate.py — a standalone legacy script that builds
    flow_gate_new.db and is not on the boot path. The runtime DB was therefore
    still in rollback-journal mode, where a write transaction takes an EXCLUSIVE
    lock that blocks every concurrent READ. Under WAL, readers and a writer
    proceed together, which removes that amplifier.

    journal_mode is persisted in the database file itself, so applying it once
    per boot is sufficient and idempotent — unlike busy_timeout, which is
    per-connection (handled in db/connection.py).

    Best-effort by design: a failure here must never stop the server booting,
    since the previous journal mode remains perfectly functional.
    """
    if settings.DB_TYPE.value not in (DBType.SQLITE, DBType.SQLITE3, DBType.LOCAL):
        return
    path = (settings.DB_PATH or "").strip()
    if not path or not os.path.exists(path):
        return
    try:
        import sqlite3

        conn = sqlite3.connect(path, timeout=15)
        try:
            mode = conn.execute("PRAGMA journal_mode = WAL").fetchone()
            logger.debug(f"✅ SQLite journal_mode={mode[0] if mode else '?'} ({path})")
        finally:
            conn.close()
    except Exception as e:
        logger.warning(f"⚠️ Could not enable SQLite WAL mode ({path}): {e}")


# During test runs, avoid initializing the full DB/migrator which may require
# resources not present in the test environment. Set environment variable
# `TESTING=1` to skip DB initialization.
if os.getenv("TESTING", "0") != "1":
    db = DatabaseSetting()
    # Maintain backward import compatibility
    tfa = db.tfa
    # After the migrator has created/updated the DB file (0279 T0005).
    _enable_sqlite_wal()
else:
    class _DummyDB:
        def __init__(self):
            self.db_instance = None
            self.sqloader = None
            self.migrator = None
            self.tfa = None

        def get_db_instance(self):
            return None

        def get_sqloader_instance(self):
            return None

    db = _DummyDB()
    tfa = None

# 🔹 Functions used as dependency injections in FastAPI
def get_db_instance():
    return db.get_db_instance()

def get_sqloader_instance():
    return db.get_sqloader_instance()
