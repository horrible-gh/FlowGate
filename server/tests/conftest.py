"""Pytest configuration for FlowGate tests — shared fixtures and setup."""
import os
import shutil
import sqlite3
import subprocess
import tempfile
from pathlib import Path

import pytest

os.environ.setdefault("TESTING", "1")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")

_SERVER_DIR = Path(__file__).resolve().parents[1]
_MIGRATIONS_DIR = _SERVER_DIR / "sql" / "migrations" / "sqlite"
_REPO_ROOT = _SERVER_DIR.parent


# ── 저장소 오염 감시자 (0382 B0001 / NR0003 제안 2-b) ─────────────────────────
#
# 사고의 1단계는 "테스트가 저장소 안에 폴더를 만든다"였고, 그 폴더는 화면에 안 뜨는
# 종류라 아무도 못 봤다. 지침을 늘리는 대신 기계가 잡는다: 세션이 끝날 때 저장소에
# 새로 생긴 흔적이 있으면 **실패**시키고 무엇이 어디에 남았는지 이름을 찍는다.
# 경고로 두면 아무도 안 본다(NR §8-2 권고).

# 스크래치 디렉터리 이름 규칙. .gitignore 에 걸리면 git 은 안 보여주므로, 이름으로도
# 본다 — 저장소 안에 썼다는 사실 자체가 잡아야 할 일이다.
_SCRATCH_GLOB = ".test-tmp*"
# 전수 탐색(rglob)은 node_modules 때문에 세션마다 수 초를 먹는다. 실제로 흔적이
# 생겼던 자리만 본다: 저장소 루트, server/, server/tests/, client/.
_SCRATCH_SCAN_DIRS = (
    _REPO_ROOT, _SERVER_DIR, _SERVER_DIR / "tests", _REPO_ROOT / "client",
)
_POLLUTION_LIST_MAX = 20


def _repo_pollution_snapshot():
    """저장소에 남아 있는 흔적의 스냅샷. git 을 못 쓰면 None(감시 생략)."""
    if shutil.which("git") is None:
        return None
    proc = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard", "-z"],
        cwd=str(_REPO_ROOT), capture_output=True, text=True,
    )
    if proc.returncode != 0:
        return None
    found = {path for path in (proc.stdout or "").split("\0") if path}
    for base in _SCRATCH_SCAN_DIRS:
        if not base.is_dir():
            continue
        for path in base.glob(_SCRATCH_GLOB):
            found.add(str(path.relative_to(_REPO_ROOT)).replace("\\", "/"))
    return found


@pytest.fixture(scope="session", autouse=True)
def repo_pollution_guard():
    """테스트가 저장소 안에 남긴 것이 있으면 세션을 실패시킨다."""
    before = _repo_pollution_snapshot()
    yield
    after = _repo_pollution_snapshot()
    if before is None or after is None:
        return
    leaked = sorted(after - before)
    if not leaked:
        return
    shown = "\n".join(f"  - {path}" for path in leaked[:_POLLUTION_LIST_MAX])
    more = "" if len(leaked) <= _POLLUTION_LIST_MAX else f"\n  ... 외 {len(leaked) - _POLLUTION_LIST_MAX}개"
    pytest.fail(
        "테스트가 저장소 안에 파일을 남겼습니다 (0382 B0001 재발 방지).\n"
        f"새로 생긴 항목 {len(leaked)}개:\n{shown}{more}\n"
        "스크래치는 저장소 밖에 만드십시오 — server/tests/scratch_support.py 의 "
        "session_scratch() 를 쓰면 됩니다.",
        pytrace=False,
    )


def get_migration_files() -> list[Path]:
    """Get all migration files sorted in order."""
    files = sorted(_MIGRATIONS_DIR.glob("*.sql"))
    return files


@pytest.fixture(scope="session")
def all_migrations_db():
    """Create a test database with all migrations applied."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name

    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")

    # Apply all migrations in order
    for migration_file in get_migration_files():
        try:
            sql = migration_file.read_text(encoding="utf-8")
            conn.executescript(sql)
            print(f"✓ Applied: {migration_file.name}")
        except Exception as e:
            print(f"⚠ Error in {migration_file.name}: {e}")

    # Seed required data
    conn.executescript(
        """
        INSERT OR IGNORE INTO roles(role_id,role_name,is_system,created_at,updated_at)
            VALUES
                ('role_admin','Administrator',1,datetime('now'),datetime('now')),
                ('role_manager','Manager',1,datetime('now'),datetime('now')),
                ('role_worker','Worker',1,datetime('now'),datetime('now')),
                ('role_viewer','Viewer',1,datetime('now'),datetime('now'));

        INSERT OR IGNORE INTO permissions(permission_id,permission_name,created_at)
            VALUES
                ('system.settings.manage','Manage system settings',datetime('now')),
                ('system.user.read','View users',datetime('now')),
                ('system.user.create','Create user',datetime('now')),
                ('system.user.update','Update user',datetime('now')),
                ('system.user.delete','Delete user',datetime('now')),
                ('system.user.assign_role','Assign role',datetime('now')),
                ('project.settings.read','View project settings',datetime('now')),
                ('project.settings.edit','Edit project settings',datetime('now')),
                ('project.document_type.create','Create document type',datetime('now')),
                ('project.document_type.update','Update document type',datetime('now')),
                ('project.document_type.delete','Delete document type',datetime('now'));

        INSERT OR IGNORE INTO role_permissions(role_id,permission_id)
            VALUES
                ('role_admin','system.settings.manage'),
                ('role_admin','system.user.read'),
                ('role_admin','system.user.create'),
                ('role_admin','system.user.update'),
                ('role_admin','system.user.delete'),
                ('role_admin','system.user.assign_role'),
                ('role_admin','project.settings.read'),
                ('role_admin','project.settings.edit'),
                ('role_admin','project.document_type.create'),
                ('role_admin','project.document_type.update'),
                ('role_admin','project.document_type.delete'),
                ('role_manager','project.settings.read'),
                ('role_manager','project.settings.edit'),
                ('role_manager','project.document_type.create'),
                ('role_manager','project.document_type.update'),
                ('role_manager','project.document_type.delete');

        INSERT OR IGNORE INTO projects(project_id,project_name,is_active,created_at,updated_at)
            VALUES
                ('__SYSTEM__','[System]',1,datetime('now'),datetime('now'));

        INSERT OR IGNORE INTO users(user_id,username,email,password,is_active,is_admin,first_login_required,created_at,updated_at)
            VALUES
                ('usr_admin','admin','admin@test.com','hashed_pw',1,1,0,datetime('now'),datetime('now'));

        INSERT OR IGNORE INTO user_project_roles(user_id,project_id,role_id,granted_at)
            VALUES('usr_admin','__SYSTEM__','role_admin',datetime('now'));

        INSERT OR IGNORE INTO system_settings(setting_key,setting_value,value_type,updated_at)
            VALUES('storage_root','/data/flowgate','string',datetime('now'));
    """
    )

    conn.commit()
    yield conn
    conn.close()
    os.unlink(db_path)


@pytest.fixture
def test_db(all_migrations_db):
    """Provide a test database connection with all migrations applied."""
    yield all_migrations_db

