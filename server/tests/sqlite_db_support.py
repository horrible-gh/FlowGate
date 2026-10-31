"""Session-local SQLite templates for tests; no cache survives a pytest worker."""
from pathlib import Path
import shutil
import sqlite3
import tempfile


def apply_migrations(path, migrations, seed_sql=""):
    """The direct path: apply every migration in order, propagating every error."""
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        for migration in migrations:
            conn.executescript(migration.read_text(encoding="utf-8"))
        if seed_sql:
            conn.executescript(seed_sql)
        conn.commit()
    finally:
        conn.close()


class SqliteDbFactory:
    """One closed, unseeded template and independent files for every factory call.

    The session fixture calls prepare before handing this factory to consumers, so
    a broken migration errors every dependent setup. A failed build is remembered
    and never published or retried within the session.
    """

    def __init__(self, root, migrations, mode="template"):
        if mode not in ("template", "direct"):
            raise ValueError(f"unknown SQLite DB mode: {mode}")
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.migrations = tuple(migrations)
        self.mode = mode
        self.template_path = self.root / "template.db"
        self._ready = False
        self._failure = None

    def prepare(self):
        if self._failure is not None:
            raise RuntimeError("SQLite template migration failed for this session") from self._failure
        if self.mode == "direct" or self._ready:
            return
        staging = self.root / "template.building.db"
        try:
            apply_migrations(staging, self.migrations)
            # Publish only after the complete chain succeeds and the connection closes.
            staging.replace(self.template_path)
            self._ready = True
        except Exception as exc:
            self._failure = exc
            raise
        finally:
            staging.unlink(missing_ok=True)

    def __call__(self, name, seed_sql=""):
        self.prepare()
        if not name or Path(name).name != name:
            raise ValueError("SQLite test DB name must be a single filename")
        path = Path(tempfile.mkdtemp(prefix="db-", dir=self.root)) / name
        try:
            if self.mode == "direct":
                apply_migrations(path, self.migrations, seed_sql)
            else:
                shutil.copyfile(self.template_path, path)
                if seed_sql:
                    conn = sqlite3.connect(str(path))
                    try:
                        conn.execute("PRAGMA foreign_keys = ON")
                        conn.executescript(seed_sql)
                        conn.commit()
                    finally:
                        conn.close()
        except Exception:
            path.unlink(missing_ok=True)
            raise
        return str(path)
