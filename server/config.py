from pydantic_settings import BaseSettings, SettingsConfigDict
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

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

settings = Settings()



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
                    "auto_migration": True,
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
                    "db_name": settings.DB_PATH
                },
                "service": {
                    "log": True,
                    "sqloder": SERVICE_SQLOADER
                },
                "migration": {
                    "auto_migration": True,
                    "migration_path": MIGRATION_PATHS + "/sqlite"
                },
            }
        elif settings.DB_TYPE.value in (DBType.POSTGRES):
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
                    "auto_migration": True,
                    "migration_path": MIGRATION_PATHS + "/postgres"
                },
            }

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

# During test runs, avoid initializing the full DB/migrator which may require
# resources not present in the test environment. Set environment variable
# `TESTING=1` to skip DB initialization.
if os.getenv("TESTING", "0") != "1":
    db = DatabaseSetting()
    # Maintain backward import compatibility
    tfa = db.tfa
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
