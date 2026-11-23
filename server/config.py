from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import field_validator, model_validator
from typing import Optional
from enum import Enum
from sqloader.init import database_init
from sqloader.migrator import DatabaseMigrator
from migration_rename_repair import reconcile_renamed_migrations
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

class DBType(str, Enum):
    MYSQL = "mysql"
    SQLITE = "sqlite"
    SQLITE3 = "sqlite3"
    POSTGRES = "postgres"
    LOCAL = "local"

class Settings(BaseSettings):
    ALLOWED_ORIGIN: str
    SECRET_KEY: str
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    CONTEXT: str
    DB_TYPE: DBType
    DB_HOST: str = ""
    DB_PORT: int = 0
    DB_USER: str = ""
    DB_PASSWORD: str = ""
    DB_DATABASE: str = ""
    DB_SCHEMA: str = ""
    # Default off to match .env.sample:25-29's direction.
    DB_LOG: bool = False
    DB_PATH: str = ""

    # Bound concurrent queries with a semaphore; the pool must cover every permit.
    # Keep DB_POOL_MAX >= DB_MAX_PARALLEL_QUERIES and retain enough idle connections
    # to avoid reconnecting on every burst. SQLite ignores these pool settings.
    # acquire_timeout bounds permit waits; lifetime/idle limits recycle connections
    # before a proxy or database closes them underneath the pool.
    DB_MAX_PARALLEL_QUERIES: int = 20
    # psycopg2 closes returned connections above minconn. Match the permit count
    # so bursts within the concurrency limit reuse connections instead of reconnecting.
    DB_POOL_MIN: int = 20
    DB_POOL_MAX: int = 24
    # the whole-server connection budget this process must fit into.
    # Boot checks DB_POOL_MAX * DB_SERVER_PROCESSES against PostgreSQL's
    # max_connections - superuser_reserved_connections - DB_RESERVED_CONNECTIONS
    # (other clients: migrations, psql, test runners) and logs ERROR when over.
    DB_SERVER_PROCESSES: int = 3
    DB_RESERVED_CONNECTIONS: int = 20
    DB_ACQUIRE_TIMEOUT: float = 30.0
    DB_POOL_MAX_LIFETIME: Optional[float] = 1800.0
    DB_POOL_MAX_IDLE: Optional[float] = 300.0

    # Preset activation is managed by server/res/preset_hands.json.

    # Toggle maintenance with MAINTENANCE_MODE=true/false in .env
    # Message is delivered based on server/res/maintenance/maintenance_{lang}.txt file
    MAINTENANCE_MODE: bool = False

    RATE_LIMIT_DEFAULT: str = "100/hour"
    RATE_LIMIT_LOGIN: str = "5/minute"
    RATE_LIMIT_UPLOAD: str = "20/hour"
    RATE_LIMIT_DOWNLOAD: str = "50/hour"

    # Token peppers support rotation through the active key ID.
    FLOWGATE_TOKEN_PEPPER_ACTIVE_ID: str | None = None
    FLOWGATE_TOKEN_PEPPER_V1: str | None = None
    FLOWGATE_INBOX_CONTENT_MAX: int = 10485760  # 10 MB default
    # AI/worker attachment read ceiling in bytes. Kept as a string on purpose: a bad
    # value must fall back to 1 MiB with a warning, not crash the boot, so
    # attachments.constants.attach_read_max_bytes() parses it (default 1 MiB, cap 64 MiB).
    FLOWGATE_ATTACH_READ_MAX_BYTES: str | None = None

    # Git integration — base64-encoded 32-byte AES key for the
    # project git credential store. Provisioned by the docker entrypoint (or the
    # storage-root key-file fallback); _PREV enables rotation-time decryption.
    FLOWGATE_GIT_ENCRYPT_KEY: str | None = None
    FLOWGATE_GIT_ENCRYPT_KEY_PREV: str | None = None

    # Base64-encoded 32-byte TOTP AES key; _PREV permits rotation-time decryption.
    # Declare .env keys because Pydantic rejects unknown fields. A missing key
    # permits startup but prevents the first 2FA enrolment.
    FLOWGATE_TOTP_ENCRYPT_KEY: str | None = None
    FLOWGATE_TOTP_ENCRYPT_KEY_PREV: str | None = None

    # Provider credentials use a separate AES key so Git, TOTP and API keys rotate
    # independently. When blank, generate a key file under the storage root.
    # _PREV permits rotation-time decryption; declarations allow these keys in .env.
    FLOWGATE_AI_ENCRYPT_KEY: str | None = None
    FLOWGATE_AI_ENCRYPT_KEY_PREV: str | None = None
    FLOWGATE_AI_EVALUATION_HMAC_KEY: str | None = None
    FLOWGATE_AGENT_PEPPER: str | None = None
    FLOWGATE_AGENT_PEPPER_ID: str = "primary"

    # stg.py reads these from the environment after load_dotenv. Declare them
    # here so Pydantic accepts the same .env used by the systemd entry point.
    FLOWGATE_PORT: int = 8089
    FLOWGATE_BIND_HOST: str = "0.0.0.0"

    # Reload is for development. Keep one worker while SSE uses an in-process
    # publisher; events do not cross worker boundaries.
    FLOWGATE_RELOAD: bool = False
    FLOWGATE_WORKERS: int = 1

    # run schema migrations + backfill scan at every
    # boot. Set false on remote-DB deployments where boot latency matters and
    # migrations are applied at deploy time instead.
    AUTO_MIGRATION: bool = True

    # Canonical origin for EXTERNAL CLI providers, not the browser/operator URL.
    # Origin only (scheme://host[:port]); conversation/help/document/inbox paths
    # are appended from the operator base. When unset, the resolver uses loopback
    # with the operator's explicit port or FLOWGATE_PORT (normally 8089). A local
    # deployment should normally use http://127.0.0.1:8089. Invalid configured
    # values fail fast rather than risking bearer-token delivery to another origin.
    FLOWGATE_AGENT_API_BASE: str | None = None

    # the OPERATOR-facing origin (scheme://host[:port]) for AI starts that have no
    # HTTP request -- Graph node starts, card reviews / Q answers, recovery jobs. A remote Agent connects to it, so it
    # must be reachable from the Agent PC (never loopback). When unset, such a start uses the run user's latest
    # non-loopback request origin (as chat does), else loopback. Not the agent base above.
    FLOWGATE_OPERATOR_API_BASE: str | None = None

    @field_validator("FLOWGATE_INBOX_CONTENT_MAX", mode="before")
    @classmethod
    def _blank_int_uses_default(cls, v):
        # .env / OS env / compose can supply this key as an EMPTY string (e.g.
        # `.env.sample` ships `FLOWGATE_INBOX_CONTENT_MAX=` and setup copies it
        # verbatim without filling it in). pydantic only falls back to the
        # default when a key is ABSENT, so a present-but-blank value would try to
        # coerce "" -> int and crash the boot. Treat blank as "unset".
        if isinstance(v, str) and v.strip() == "":
            return 10485760
        return v

    @field_validator(
        "DB_MAX_PARALLEL_QUERIES",
        "DB_POOL_MIN",
        "DB_POOL_MAX",
        "DB_ACQUIRE_TIMEOUT",
        "DB_POOL_MAX_LIFETIME",
        "DB_POOL_MAX_IDLE",
        "DB_SERVER_PROCESSES",
        "DB_RESERVED_CONNECTIONS",
        mode="before",
    )
    @classmethod
    def _blank_pool_value_uses_default(cls, v, info):
        # Same blank-value hazard as FLOWGATE_INBOX_CONTENT_MAX above: a key that
        # is PRESENT but empty (`DB_POOL_MAX=` in a copied .env) never falls back
        # to the declared default and would crash the boot on "" -> int/float.
        # Resolve the field's own default instead of hardcoding one per field.
        if isinstance(v, str) and v.strip() == "":
            field = cls.model_fields[info.field_name]
            return field.default
        return v

    @model_validator(mode="after")
    def _check_pool_sizing(self):
        # sqloader raises ValueError for pool_max < max_parallel_queries, but only
        # at DatabaseSetting._init_db() time and only for postgres. Reject it here
        # instead: the message names the two .env keys the operator actually set,
        # and the check applies whatever the backend is.
        if self.DB_POOL_MAX < self.DB_MAX_PARALLEL_QUERIES:
            raise ValueError(
                f"DB_POOL_MAX ({self.DB_POOL_MAX}) must be >= "
                f"DB_MAX_PARALLEL_QUERIES ({self.DB_MAX_PARALLEL_QUERIES}): a "
                "pool smaller than the concurrency limit is exhausted before "
                "that limit is reached."
            )
        if self.DB_POOL_MIN > self.DB_POOL_MAX:
            raise ValueError(
                f"DB_POOL_MIN ({self.DB_POOL_MIN}) must be <= DB_POOL_MAX "
                f"({self.DB_POOL_MAX})."
            )
        return self

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

settings = Settings()


# Pydantic reads .env without populating os.environ, which the crypto services use.
# Mirror missing keys for launchers that bypass stg.py/load_dotenv; explicit OS
# environment values retain precedence.
for _env_key in (
    "FLOWGATE_TOTP_ENCRYPT_KEY",
    "FLOWGATE_TOTP_ENCRYPT_KEY_PREV",
    "FLOWGATE_GIT_ENCRYPT_KEY",
    "FLOWGATE_GIT_ENCRYPT_KEY_PREV",
    "FLOWGATE_AI_ENCRYPT_KEY",
    "FLOWGATE_AI_ENCRYPT_KEY_PREV",
    "FLOWGATE_AI_EVALUATION_HMAC_KEY",
    "FLOWGATE_AGENT_PEPPER",
    "FLOWGATE_AGENT_PEPPER_ID",
    "FLOWGATE_ATTACH_READ_MAX_BYTES",
):
    _env_val = getattr(settings, _env_key, None)
    if _env_val and not os.environ.get(_env_key):
        os.environ[_env_key] = _env_val


def _pool_settings(pooled: bool) -> dict:
    """Connection-pool keys handed to sqloader's database_init (0288 recommendation  2).

    ``pooled=False`` (MySQL) emits only the two keys sqloader reads for that
    backend; sending the postgres-only keys there would be dead config.

    Keys unknown to an older installed sqloader are simply ignored by
    database_init (it reads the dict with .get), so this stays safe to run
    against a version predating the configurable pool — it just has no effect
    until sqloader>=0.2.16 is installed.
    """
    common = {
        "max_parallel_queries": settings.DB_MAX_PARALLEL_QUERIES,
        "acquire_timeout": settings.DB_ACQUIRE_TIMEOUT,
    }
    if not pooled:
        return common
    return {
        **common,
        "pool_min": settings.DB_POOL_MIN,
        "pool_max": settings.DB_POOL_MAX,
        "max_lifetime": settings.DB_POOL_MAX_LIFETIME,
        "max_idle": settings.DB_POOL_MAX_IDLE,
    }


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

        # this used to dump the whole Settings
        # object (str(settings)), which put SECRET_KEY, DB_PASSWORD and the
        # FLOWGATE_*_ENCRYPT_KEY values into logs/default.log in plain text on
        # every boot, independent of DB_LOG. Allow-list only the fields this
        # line exists to show (which DB, which pool/migration knobs).
        logger.debug(
            "settings",
            {
                "DB_TYPE": settings.DB_TYPE.value,
                "DB_HOST": settings.DB_HOST,
                "DB_PORT": settings.DB_PORT,
                "DB_DATABASE": settings.DB_DATABASE,
                "DB_SCHEMA": settings.DB_SCHEMA,
                "DB_LOG": settings.DB_LOG,
                "DB_POOL_MIN": settings.DB_POOL_MIN,
                "DB_POOL_MAX": settings.DB_POOL_MAX,
                "DB_MAX_PARALLEL_QUERIES": settings.DB_MAX_PARALLEL_QUERIES,
                "AUTO_MIGRATION": settings.AUTO_MIGRATION,
            },
        )

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
                    # MySqlWrapper opens a connection per
                    # transaction rather than pooling, so only the two
                    # concurrency knobs sqloader accepts here apply.
                    **_pool_settings(pooled=False),
                },
                "service": {
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
                    # this was hardcoded True, so DB_LOG had no
                    # effect on the DEFAULT engine — full SQL logging could not be
                    # turned off without editing the source. The mysql/postgres
                    # branches already honour settings.DB_LOG; match them.
                    "log": settings.DB_LOG,
                },
                "service": {
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
                    # without these the pool stayed
                    # at sqloader's 5/1/5 defaults regardless of .env — the
                    # 40-thread-vs-5-slot mismatch behind the regression's exhaustion.
                    **_pool_settings(pooled=True),
                },
                "service": {
                    "sqloder": SERVICE_SQLOADER
                },
                "migration": {
                    "auto_migration": settings.AUTO_MIGRATION,
                    "migration_path": MIGRATION_PATHS + "/postgres"
                },
            }
        else:
            # falling through left self.config == {} and handed
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

        # sqloader's migrator keys the `migrations` table on the FILE
        # NAME, so the duplicate-number cleanup (031a / 042a / ... / 076b) looks
        # like fourteen brand-new migrations to any DB that already applied them under
        # the old names — and re-running an ALTER TABLE ADD COLUMN aborts the boot.
        # Carry the bookkeeping rows over first. No-op on a fresh DB, and on a DB
        # that has already been carried over. Must precede database_init(), which
        # is what constructs (and runs) the migrator.
        try:
            from modules.flow_gate.db.migration_renames import apply_migration_renames

            carried = apply_migration_renames(
                settings.DB_TYPE.value,
                sqlite_path=settings.DB_PATH,
                host=settings.DB_HOST,
                port=settings.DB_PORT,
                user=settings.DB_USER,
                password=settings.DB_PASSWORD,
                database=settings.DB_DATABASE,
                schema=settings.DB_SCHEMA,
            )
            if carried:
                logger.debug(f"✅ migration bookkeeping: {carried} renamed file(s) carried over")
        except Exception as e:
            # Never block the boot here: the migrator that follows reports the
            # real consequence (a failed re-apply) with the file name attached.
            import traceback
            logger.error(f"⚠️ migration filename carry-over skipped: {e}")
            logger.error(traceback.format_exc())

        try:
            # sqloader applies pending migrations inside
            # database_init(), which leaves no seam to fix the migrations table
            # before files run. Take the connection config without the migration
            # block, then drive the migrator here — repair renamed filenames
            # first, apply only what is genuinely pending second.
            boot_config = {k: v for k, v in self.config.items() if k != "migration"}
            self.db_instance, self.sqloader, _ = database_init(boot_config)
            from modules.flow_gate.db.resilience import install_checkout_validation
            install_checkout_validation(self.db_instance)
            self.run_migrations(self.config.get("migration") or {})
            logger.debug(f"✅ DB initialized - type: {type(self.db_instance).__name__}, db_type: {getattr(self.db_instance, 'db_type', 'N/A')}")
        except Exception as e:
            import traceback
            logger.error(f"❌ database_init failed: {e}")
            logger.error(traceback.format_exc())
            raise

        # dialect-agnostic data backfills that replace SQLite-only JSON DML
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

        # legacy ai_invoke_runs.register_errors elements become
        # register_context_failures rows. Python, not DML in migration 094, for the same
        # reason as above — unrolling a JSON array needs jsonb_array_elements / JSON_TABLE /
        # json_each, three spellings that do not survive each other's engine. The source
        # column is left untouched, and the transform is idempotent on (correlation_id, boundary).
        try:
            from modules.flow_gate.db.backfills.register_context_failure_backfill import (
                run_register_context_failure_backfill,
            )
            n = run_register_context_failure_backfill(self.db_instance)
            if n:
                logger.debug(f"✅ register context failure backfill: {n} row(s) offered")
        except Exception as e:
            import traceback
            logger.error(f"⚠️ register context failure backfill skipped: {e}")
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

    def run_migrations(self, migration_config):
        """Apply pending migrations, after repairing renamed-file bookkeeping.

        Mirrors the migration block sqloader.init.database_init would have run —
        same console lines, same exit status on failure — with one step inserted
        ahead of it: sqloader recognises an applied migration only by its exact
        filename, so a file that was applied under an older name looks pending
        and gets executed a second time. See migration_rename_repair for what
        that did to the dev deployment.
        """
        migration_path = (migration_config or {}).get("migration_path")
        if not migration_path:
            return

        print(migration_config)
        print("Starting Database Migrator")
        try:
            # auto_run=False: this creates the migrations table (and nothing
            # else), which the repair below needs to read and write.
            self.migrator = DatabaseMigrator(self.db_instance, migration_path, False)
            for applied_as, filename in reconcile_renamed_migrations(
                self.db_instance, migration_path
            ):
                message = (
                    f"Migration {filename} already applied as {applied_as}; "
                    f"recorded under its current name instead of re-applying it."
                )
                print(message)
                logger.warning(message)
            if migration_config.get("auto_migration", False):
                self.migrator.apply_migrations()
            print("Database Migration Successfully")
        except Exception as e:
            print(f"Database Migration Failed.{e}")
            raise SystemExit(1)

    def get_db_instance(self):
        return self.db_instance

    def get_sqloader_instance(self):
        return self.sqloader

# 🔹 Create singleton instance
import os


def _enable_sqlite_wal() -> None:
    """Put the runtime SQLite DB into WAL journal mode, once, at boot.

    `PRAGMA journal_mode = WAL` existed only in
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
    # After the migrator has created/updated the DB file.
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
