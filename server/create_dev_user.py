#!/usr/bin/env python3
"""Script for creating FlowGate development test accounts.

Usage:
    python create_dev_user.py
    python create_dev_user.py --username admin --email admin@flowgate.local --password "DevPass123!" --admin
    python create_dev_user.py --count 3
    python create_dev_user.py --list
    python create_dev_user.py --delete dev1@flowgate.local
    python create_dev_user.py --token dev1@flowgate.local

Defaults:
    username: dev{N}
    email   : dev{N}@flowgate.local
    password: DevPass123!
    role    : role_worker  (with --admin: role_admin + is_admin=1)
"""

import os
import sys
import io
import argparse
import uuid
from datetime import datetime, timedelta, timezone

# Force UTF-8 so Korean output does not break in Windows cp932/cp949 consoles
if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf8"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(BASE_DIR)
sys.path.insert(0, BASE_DIR)

# ──────────────────────────────────────────────
# Load .env
# ──────────────────────────────────────────────
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(BASE_DIR, ".env"))
except ImportError:
    env_path = os.path.join(BASE_DIR, ".env")
    if os.path.exists(env_path):
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    os.environ.setdefault(k.strip(), v.strip())

import db_bootstrap as dbb
from db_bootstrap import BootstrapDBError

# 0273 NR0003 P1-1: resolved from $DB_TYPE instead of assuming SQLite, so a
# mysql/postgres install can seed its first admin. main() re-resolves this from
# the environment before connecting; the sqlite default keeps the helpers below
# usable with a plain sqlite3 connection handed in directly (as the 0272 tests
# do) without any environment set up.
DB_TYPE    = dbb.SQLITE
SECRET_KEY = os.environ.get("SECRET_KEY", "")
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.environ.get("ACCESS_TOKEN_EXPIRE_MINUTES", "30"))

DEFAULT_PASSWORD = "DevPass123!"
SYSTEM_PROJECT   = "__SYSTEM__"

# ──────────────────────────────────────────────
# Password hashing (prefer bcrypt, fall back to sha256 if unavailable)
# ──────────────────────────────────────────────
try:
    from passlib.context import CryptContext
    _ctx = CryptContext(schemes=["bcrypt", "pbkdf2_sha256"], deprecated=["pbkdf2_sha256"])
    def hash_password(pw: str) -> str:
        return _ctx.hash(pw)
except ImportError:
    import hashlib
    def hash_password(pw: str) -> str:  # type: ignore[misc]
        print("❌  passlib not installed — cannot hash passwords securely, falling back to sha256 (not recommended for production!)")
        sys.exit(1)

# ──────────────────────────────────────────────
# JWT issuance (prefer pyjwt)
# ──────────────────────────────────────────────
try:
    import jwt as pyjwt
    def make_token(user_id: str, username: str, roles: list[str]) -> str:
        if not SECRET_KEY:
            return "(SECRET_KEY not set — cannot issue JWT)"
        jti = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        exp = now + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
        payload = {
            "sub": user_id,
            "username": username,
            "roles": roles,
            "jti": jti,
            "type": "access",
            "iat": int(now.timestamp()),
            "exp": int(exp.timestamp()),
        }
        return pyjwt.encode(payload, SECRET_KEY, algorithm="HS256")
except ImportError:
    def make_token(user_id: str, username: str, roles: list[str]) -> str:  # type: ignore[misc]
        return "(pyjwt not installed — cannot issue JWT)"


# ──────────────────────────────────────────────
# DB helpers (engine-neutral — see db_bootstrap.py)
# ──────────────────────────────────────────────
def get_conn():
    """Connect to whichever engine DB_TYPE names.

    0273 NR0003 P1-1: this used to be `sqlite3.connect(DB_PATH)`, which is why
    every install path skipped the admin bootstrap on mysql/postgres and left a
    finished install with no account that could log in.
    """
    try:
        return dbb.connect(DB_TYPE, BASE_DIR)
    except BootstrapDBError as exc:
        print(f"[!] {exc}")
        if DB_TYPE == dbb.SQLITE:
            print("    Run the server once to initialize the DB, then try again.")
        else:
            print(f"    Check the DB_* settings in {os.path.join(BASE_DIR, '.env')}.")
        sys.exit(1)


def user_exists_by_email(conn, email: str) -> bool:
    return dbb.one(dbb.execute(conn, DB_TYPE, "SELECT 1 FROM users WHERE email = ?", (email,))) is not None


def user_exists_by_username(conn, username: str) -> bool:
    return dbb.one(dbb.execute(conn, DB_TYPE, "SELECT 1 FROM users WHERE username = ?", (username,))) is not None


def missing_rbac_seed(conn) -> list[str]:
    """Names of RBAC rows user_project_roles' foreign keys need but that are absent.

    004_rbac.sql seeds them, so an empty result here means the DB was opened
    before the migrations finished. Reporting that beats letting the INSERT
    below fail with a bare `FOREIGN KEY constraint failed`, which names neither
    the missing row nor the reason it is missing.
    """
    missing = []
    try:
        if dbb.one(dbb.execute(
            conn, DB_TYPE, "SELECT 1 FROM projects WHERE project_id = ?", (SYSTEM_PROJECT,)
        )) is None:
            missing.append(f"projects.{SYSTEM_PROJECT}")
        for role_id in ("role_admin", "role_worker"):
            if dbb.one(dbb.execute(
                conn, DB_TYPE, "SELECT 1 FROM roles WHERE role_id = ?", (role_id,)
            )) is None:
                missing.append(f"roles.{role_id}")
    except Exception as exc:
        # Table itself absent — migrations are even further behind. Each driver
        # raises its own error class here (sqlite3.OperationalError,
        # pymysql.err.ProgrammingError, psycopg2.errors.UndefinedTable), so this
        # catches broadly on purpose; the message is reported verbatim.
        missing.append(f"({exc})")
        # PostgreSQL aborts the whole transaction on a failed statement, so every
        # later query in this connection would raise InFailedSqlTransaction until
        # the block is unwound. Nothing above this point wrote anything.
        try:
            conn.rollback()
        except Exception:
            pass
    return missing


def create_user(
    conn,
    username: str,
    email: str,
    password: str,
    is_admin: bool = False,
) -> dict:
    user_id = str(uuid.uuid4())
    hashed  = hash_password(password)
    now     = datetime.now(timezone.utc).isoformat()
    role_id = "role_admin" if is_admin else "role_worker"

    dbb.execute(
        conn, DB_TYPE,
        """INSERT INTO users
               (user_id, username, email, password, is_active, is_admin,
                first_login_required, created_at, updated_at)
           VALUES (?, ?, ?, ?, 1, ?, 0, ?, ?)""",
        (user_id, username, email, hashed, 1 if is_admin else 0, now, now),
    )

    # Assign the __SYSTEM__ project role (upsert spelling differs per engine)
    dbb.upsert_user_project_role(
        conn, DB_TYPE, (user_id, SYSTEM_PROJECT, role_id, now)
    )
    conn.commit()

    token = make_token(user_id, username, [role_id])
    return {
        "user_id":  user_id,
        "username": username,
        "email":    email,
        "password": password,
        "role":     role_id,
        "is_admin": is_admin,
        "token":    token,
    }


def list_users(conn) -> list[dict]:
    # Every non-aggregated column is named in GROUP BY: SQLite tolerates a bare
    # `GROUP BY u.user_id`, but PostgreSQL rejects it outright and MySQL does too
    # under the default ONLY_FULL_GROUP_BY.
    return dbb.rows(dbb.execute(
        conn, DB_TYPE,
        f"""SELECT u.user_id, u.username, u.email, u.is_admin, u.is_active, u.created_at,
                  {dbb.role_aggregate(DB_TYPE)} as roles
           FROM users u
           LEFT JOIN user_project_roles upr
                  ON u.user_id = upr.user_id AND upr.project_id = ?
           GROUP BY u.user_id, u.username, u.email, u.is_admin, u.is_active, u.created_at
           ORDER BY u.created_at""",
        (SYSTEM_PROJECT,),
    ))


def delete_user(conn, email: str) -> bool:
    cur = dbb.execute(conn, DB_TYPE, "DELETE FROM users WHERE email = ?", (email,))
    conn.commit()
    return cur.rowcount > 0


def get_user_by_email(conn, email: str):
    return dbb.one(dbb.execute(conn, DB_TYPE, "SELECT * FROM users WHERE email = ?", (email,)))


# ──────────────────────────────────────────────
# Output helpers
# ──────────────────────────────────────────────
def print_user(info: dict, idx: int = 1) -> None:
    admin_tag = " 👑 Admin" if info["is_admin"] else ""
    print(f"\n  [{idx}] Account created{admin_tag}")
    print(f"      username: {info['username']}")
    print(f"      email   : {info['email']}")
    print(f"      password: {info['password']}")
    print(f"      role    : {info['role']}")
    print(f"      token   : {info['token']}")


# ──────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="FlowGate development test account creator",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("--username", help="username (default: dev{N})")
    p.add_argument("--email",    help="email (default: dev{N}@flowgate.local)")
    p.add_argument("--password", default=DEFAULT_PASSWORD,
                   help=f"password (default: {DEFAULT_PASSWORD})")
    p.add_argument("--admin",    action="store_true",
                   help="Create as admin account (is_admin=1, role_admin granted)")
    p.add_argument("--count",    type=int, default=1, metavar="N",
                   help="Number of accounts to create (if --email/--username omitted, dev1~devN are created)")
    p.add_argument("--list",     action="store_true", help="Show registered accounts list")
    p.add_argument("--delete",   metavar="EMAIL", help="Delete account with specified email")
    p.add_argument("--token",    metavar="EMAIL", help="Reissue JWT for existing account")
    return p


def main() -> None:
    args = build_parser().parse_args()

    # Resolved after parsing so --help never depends on the DB settings.
    global DB_TYPE
    try:
        DB_TYPE = dbb.resolve_db_type()
    except BootstrapDBError as exc:
        print(f"[!] {exc}")
        sys.exit(1)

    conn = get_conn()

    # ── List
    if args.list:
        users = list_users(conn)
        if not users:
            print("No registered accounts found.")
        else:
            print(f"\n{'Username':<20} {'Email':<35} {'Role':<15} {'Admin':<6} {'Created at'}")
            print("-" * 95)
            for u in users:
                admin = "✅" if u["is_admin"] else "  "
                roles = u["roles"] or "(none)"
                print(f"{u['username']:<20} {u['email']:<35} {roles:<15} {admin:<6} {u['created_at'][:19]}")
        conn.close()
        return

    # ── Delete
    if args.delete:
        if delete_user(conn, args.delete):
            print(f"✅ Account deleted: {args.delete}")
        else:
            print(f"[!] Account not found: {args.delete}")
        conn.close()
        return

    # ── Reissue token
    if args.token:
        row = get_user_by_email(conn, args.token)
        if row:
            u = dict(row)
            roles_row = dbb.one(dbb.execute(
                conn, DB_TYPE,
                "SELECT role_id FROM user_project_roles WHERE user_id = ? AND project_id = ?",
                (u["user_id"], SYSTEM_PROJECT),
            ))
            role = roles_row["role_id"] if roles_row else "role_worker"
            token = make_token(u["user_id"], u["username"], [role])
            print(f"\n  email   : {args.token}")
            print(f"  username: {u['username']}")
            print(f"  role    : {role}")
            print(f"  token   : {token}")
        else:
            print(f"[!] Account not found: {args.token}")
        conn.close()
        return

    # ── Create
    if args.username or args.email:
        username = args.username or (args.email.split("@")[0] if args.email else "dev1")
        email    = args.email    or f"{username}@flowgate.local"
        candidates = [(username, email)]
    else:
        candidates = [(f"dev{i}", f"dev{i}@flowgate.local") for i in range(1, args.count + 1)]

    missing = missing_rbac_seed(conn)
    if missing:
        migrations_dir = f"sql/migrations/{dbb.migrations_dirname(DB_TYPE)}"
        print(f"\n[!] DB is not fully migrated — missing: {', '.join(missing)}")
        print(f"    These rows are seeded by {migrations_dir}/004_rbac.sql, which the")
        print("    server applies on boot. Start the server, wait for migrations to finish")
        print("    (python server/check_db_ready.py --wait 300), then re-run this command.")
        conn.close()
        sys.exit(1)

    print(f"\n🚀 Creating FlowGate test accounts (DB: {dbb.describe_target(DB_TYPE, BASE_DIR)})")
    created, skipped = [], []

    for idx, (uname, email) in enumerate(candidates, start=1):
        if user_exists_by_email(conn, email):
            skipped.append(email)
            print(f"  [skip] already exists: {email}")
            continue
        if user_exists_by_username(conn, uname):
            skipped.append(uname)
            print(f"  [skip] username conflict: {uname}")
            continue
        info = create_user(conn, uname, email, args.password, is_admin=args.admin)
        created.append(info)
        print_user(info, idx)

    conn.close()
    print(f"\nDone: created {len(created)}, skipped {len(skipped)}")
    if not SECRET_KEY:
        print("\n⚠️  SECRET_KEY not set — issued tokens cannot be used by the server.")
        print("   Set SECRET_KEY in your .env file.")


if __name__ == "__main__":
    main()
