"""flowgate.default.0060 — document attachments, end to end against a real SQLite schema.

T0016 §9-1 names the boundary cases from L0012 §5 that must be covered; this file is that
list. Each test says which rule it pins, because a green assertion with no rule attached is
the thing that lets a rule quietly die.

Nothing here is mocked away that matters: the registry is the real `attachments` table built
by every migration in order, the storage jail is the real `storage/paths.py`, and the files
are really written to a temp storage root. The two seams are `git_service`'s worktree
lookup (there is no git repo in a unit test) and `db.connection.STORE`, swapped for a store
over the temp DB.
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import sqlite3
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

os.environ["TESTING"] = "1"
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only-32c")

_SERVER_DIR = Path(__file__).resolve().parents[1]
if str(_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(_SERVER_DIR))

PROJECT_ID = "flowgate"
GROUP_ID = "flowgate.default.0060"
DOC_ID = "flowgate.default.0060.0001-R"
OTHER_DOC_ID = "flowgate.default.0060.0002-N"


# ── fixtures ────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def attachments_db(migrated_sqlite_db):
    """Every migration in order, then the rows these tests need (conftest factory)."""
    return migrated_sqlite_db(
        "test_attachments_0060.db",
        seed_sql=f"""
        INSERT OR IGNORE INTO projects(project_id,project_name,is_active,created_at,updated_at)
            VALUES('{PROJECT_ID}','FlowGate',1,datetime('now'),datetime('now'));
        INSERT OR IGNORE INTO users(user_id,username,email,password,is_active,is_admin,
                                    first_login_required,created_at,updated_at)
            VALUES('usr_admin','admin','admin@test.com','pw',1,1,0,datetime('now'),datetime('now'));
        INSERT OR IGNORE INTO project_settings(project_id,group_structure,digits_group,
                                    digits_sub_group,digits_type,updated_at,branch)
            VALUES('{PROJECT_ID}',1,4,3,4,datetime('now'),'main');
        INSERT OR IGNORE INTO groups(group_id,project_id,module,title,status,created_at,updated_at)
            VALUES('{GROUP_ID}','{PROJECT_ID}','default','attachments',
                   'in_progress',datetime('now'),datetime('now'));
        INSERT OR IGNORE INTO documents(doc_id,project_id,module,group_id,type_code,seq,title,
                                    file_path,status,created_at,updated_at)
            VALUES('{DOC_ID}','{PROJECT_ID}','default','{GROUP_ID}','R',1,'attachment target',
                   'documents/{PROJECT_ID}/main/default/0060/0001-R_document.md',
                   'open',datetime('now'),datetime('now')),
                  ('{OTHER_DOC_ID}','{PROJECT_ID}','default','{GROUP_ID}','N',2,'other doc',
                   'documents/{PROJECT_ID}/main/default/0060/0002-N_document.md',
                   'open',datetime('now'),datetime('now'));
        """,
    )


class _Store:
    """The db.connection surface the attachment code uses, over one sqlite file."""

    def __init__(self, db_path: str) -> None:
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")

    def _execute(self, sql, params=None):
        self._conn.execute(sql, params or [])
        self._conn.commit()

    def _fetch_one(self, sql, params=None):
        row = self._conn.execute(sql, params or []).fetchone()
        return dict(row) if row else None

    def _fetch_all(self, sql, params=None):
        return [dict(r) for r in self._conn.execute(sql, params or []).fetchall()]

    @contextmanager
    def transaction(self):
        try:
            yield self
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise


@pytest.fixture
def env(attachments_db, tmp_path, monkeypatch):
    """Temp storage root + the real document body, with the store pointed at the temp DB.

    The body file has to exist: `resolve_attach_dir` prefers where the body ACTUALLY is
    (L0012 §2-1 ①) and only falls back to the calculated path when it cannot resolve.
    """
    from modules.flow_gate.db import connection as conn_mod

    storage_root = tmp_path / "storage"
    group_dir = storage_root / "documents" / PROJECT_ID / "main" / "default" / "0060"
    group_dir.mkdir(parents=True)
    (group_dir / "0001-R_document.md").write_text("# body", encoding="utf-8")
    (group_dir / "0002-N_document.md").write_text("# other", encoding="utf-8")
    monkeypatch.setenv("FLOWGATE_STORAGE_DIR", str(storage_root))

    store = _Store(attachments_db)
    # Swap the STORE object, not `get_store` on each module that already imported it.
    # Patching the function leaves every module imported LATER in the same session bound to
    # the patch — `from .connection import get_store` copies whatever is bound at import time
    # — so a suite running after this one wrote its rows into this temp DB while reading them
    # back through the real store, and died on a foreign key that was never missing
    # (test_work_plan_0395's `patch_store` pins the same rule for the same reason).
    # `get_store()` returns this global, so one assignment covers callers imported either way.
    original_store = conn_mod.STORE
    conn_mod.STORE = store
    store._execute("DELETE FROM attachments")
    try:
        yield {"store": store, "storage_root": storage_root, "group_dir": group_dir}
    finally:
        conn_mod.STORE = original_store


class FakePart:
    """The slice of Starlette's UploadFile the upload service touches."""

    def __init__(self, filename, data: bytes) -> None:
        self.filename = filename
        self._data = data
        self._pos = 0

    async def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            chunk, self._pos = self._data[self._pos:], len(self._data)
            return chunk
        chunk = self._data[self._pos:self._pos + size]
        self._pos += len(chunk)
        return chunk


ACTOR = {"user_id": "usr_admin"}


def upload(parts, doc_id: str = DOC_ID, content_length=None):
    from modules.flow_gate.documents.attachments import upload_attachments

    return asyncio.run(upload_attachments(doc_id, parts, ACTOR, content_length))


def room_of(env) -> Path:
    return env["group_dir"] / "0001-R"


# ── migration ───────────────────────────────────────────────────────────────────

def test_migration_080_creates_the_registry_and_is_safe_to_reapply(env):
    """DB0013 §3-1 — table + five indexes exist, and the guarded DDL is idempotent."""
    conn = env["store"]._conn
    expected_indexes = {
        "ux_attachments_doc_filename",
        "ux_attachments_file_path",
        "idx_attachments_doc_uploaded",
        "idx_attachments_doc_sha",
        "idx_attachments_uploaded_by",
    }

    assert conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'attachments'"
    ).fetchone()
    assert expected_indexes <= {
        row[1] for row in conn.execute("PRAGMA index_list('attachments')").fetchall()
    }

    migration = _SERVER_DIR / "sql" / "migrations" / "sqlite" / "083_attachment_registry.sql"
    conn.executescript(migration.read_text(encoding="utf-8"))

    assert expected_indexes <= {
        row[1] for row in conn.execute("PRAGMA index_list('attachments')").fetchall()
    }


# ── upload ──────────────────────────────────────────────────────────────────────

def test_upload_writes_the_file_next_to_the_body_and_records_a_relative_path(env):
    """D0010 §3-1 (colocate) + NR0003 G4 / P0011 §9 (no absolute path ever leaves)."""
    result = upload([FakePart("요구사항.xlsx", b"hello")])

    assert result["count"] == 1
    item = result["attachments"][0]
    assert item["path"] == f"documents/{PROJECT_ID}/main/default/0060/0001-R/요구사항.xlsx"
    assert item["path_base"] == "storage"
    assert not Path(item["path"]).is_absolute()
    assert item["content_sha256"] == hashlib.sha256(b"hello").hexdigest()
    assert (room_of(env) / "요구사항.xlsx").read_bytes() == b"hello"


def test_zero_byte_upload_is_accepted(env):
    """L0012 §5 — "빈 자료도 자료다". size 0 with the empty-input sha256."""
    item = upload([FakePart("empty.txt", b"")])["attachments"][0]

    assert item["size"] == 0
    assert item["content_sha256"] == hashlib.sha256(b"").hexdigest()
    assert (room_of(env) / "empty.txt").exists()


def test_same_name_parts_in_one_request_do_not_overwrite_each_other(env):
    """L0012 §2-4 — the defect the single-shot epoch dedupe had.

    Three parts named `a.txt` in one request used to compute the same `a_{epoch}.txt` in the
    same second and silently overwrite one another. D2 (in-request reservation) + D5
    (O_CREAT|O_EXCL) must produce three distinct names holding three distinct bodies.
    """
    result = upload([
        FakePart("a.txt", b"first"),
        FakePart("a.txt", b"second"),
        FakePart("a.txt", b"third"),
    ])

    names = [item["filename"] for item in result["attachments"]]
    assert len(set(names)) == 3, names
    bodies = sorted((room_of(env) / n).read_bytes() for n in names)
    assert bodies == [b"first", b"second", b"third"]
    # response order == request order (P0011 §2)
    assert [item["size"] for item in result["attachments"]] == [5, 6, 5]


def test_backslash_path_is_not_smuggled_through_as_one_filename(env):
    """L0012 §2-2 S3 — `Path(name).name` let this through as a single component on Linux."""
    item = upload([FakePart(r"..\..\etc\passwd", b"x")])["attachments"][0]

    assert item["filename"] == "passwd"
    assert (room_of(env) / "passwd").exists()
    assert item["path"].endswith("/0001-R/passwd")


def test_upload_over_the_size_limit_is_cut_and_leaves_nothing_behind(env):
    """L0012 §2-3 U2 + §2-5 A9 — 413, and the rollback removes the partial file."""
    from modules.flow_gate.documents.attachments import AttachmentError
    from modules.flow_gate.documents.attachments.constants import ATTACH_MAX_UPLOAD_BYTES

    with pytest.raises(AttachmentError) as excinfo:
        upload([FakePart("big.bin", b"x" * (ATTACH_MAX_UPLOAD_BYTES + 1))])

    error = excinfo.value
    assert (error.status_code, error.code) == (413, "ATTACHMENT_TOO_LARGE")
    assert error.details["limit_bytes"] == ATTACH_MAX_UPLOAD_BYTES
    # the number reported is what was received before the cut, so it always exceeds the limit
    assert error.details["size"] > ATTACH_MAX_UPLOAD_BYTES
    assert list(room_of(env).iterdir()) == []
    assert env["store"]._fetch_all("SELECT * FROM attachments") == []


def test_no_extension_is_refused_any_more(env):
    """TR0017 rev3 반려 — "첨부로 받을 수 없는 확장자 입니다." + 물음표 한 화면.

    §1-3 used to refuse these at the door, and a person could not attach their own work
    file. This walks the whole former deny list through a real upload: every one of them
    lands on disk with a row, and the mixed request that used to be killed by its one "bad"
    part now succeeds whole.
    """
    from modules.flow_gate.documents.attachments.constants import (
        ATTACH_EXECUTABLE_EXTENSIONS,
        ATTACH_MAX_FILES_PER_REQUEST,
    )

    names = [f"payload.{ext}" for ext in sorted(ATTACH_EXECUTABLE_EXTENSIONS)]
    stored: list[str] = []
    for start in range(0, len(names), ATTACH_MAX_FILES_PER_REQUEST):
        batch = names[start:start + ATTACH_MAX_FILES_PER_REQUEST]
        result = upload([FakePart(name, b"x") for name in batch])
        assert result["count"] == len(batch)
        stored += [item["filename"] for item in result["attachments"]]

    assert stored == names
    for name in names:
        assert (room_of(env) / name).read_bytes() == b"x"

    # the mixed request — the exact shape that used to fail whole because of one part
    mixed = upload([FakePart("ok.txt", b"a"), FakePart("installer.exe", b"b")])
    assert [item["filename"] for item in mixed["attachments"]] == ["ok.txt", "installer.exe"]

    # …and none of them is recorded under a media type a browser would run
    rows = env["store"]._fetch_all(
        "SELECT filename, content_type FROM attachments WHERE filename != 'ok.txt'"
    )
    assert {row["content_type"] for row in rows} == {"application/octet-stream"}


def test_an_executable_is_served_as_bytes_rather_than_refused(env):
    """§1-3 — the table decides how a file is *served*, not whether it is *accepted*."""
    from modules.flow_gate.documents.attachments import resolve_download

    upload([FakePart("build.js", b"alert(1)")])          # guesses as text/javascript

    path, meta = resolve_download(DOC_ID, "build.js")
    assert meta["media_type"] == "application/octet-stream"
    assert meta["headers"]["Content-Disposition"].startswith("attachment;")
    assert meta["headers"]["X-Content-Type-Options"] == "nosniff"
    assert path.read_bytes() == b"alert(1)"


def test_a_row_that_still_claims_a_runnable_type_is_forced_back_to_bytes(env):
    """The download arm has to hold on its own.

    Upload writes octet-stream for these kinds, but a row can carry something else: rows
    written before rev3, and rows the legacy migration wrote from files that never went
    through an upload check at all.
    """
    from modules.flow_gate.documents.attachments import resolve_download

    upload([FakePart("old.js", b"x")])
    env["store"]._execute(
        "UPDATE attachments SET content_type = 'text/javascript' WHERE filename = 'old.js'"
    )

    _, meta = resolve_download(DOC_ID, "old.js")
    assert meta["media_type"] == "application/octet-stream"


def test_read_no_longer_refuses_a_script_it_just_accepted(env):
    """§2-9 R3 is gone (rev3).

    Its whole justification was "upload already refused these". Now that upload accepts a
    ``.ps1``, answering 415 when the same screen asks to read it back is the same wall one
    door further in — and ``auto`` already has the right answer for real binaries (R6).
    """
    from modules.flow_gate.documents.attachments import read_attachment

    upload([FakePart("setup.ps1", "Write-Host '첨부'".encode()), FakePart("tool.exe", b"MZ\x00\x01")])

    script = read_attachment(DOC_ID, "setup.ps1")
    assert (script["kind"], script["content"]) == ("text", "Write-Host '첨부'")

    binary = read_attachment(DOC_ID, "tool.exe")
    assert binary["kind"] == "binary" and binary["content_encoding"] == "base64"


def test_trailing_dot_is_stripped_before_the_extension_is_read(env):
    """L0012 §2-2 S5 — Windows drops a trailing dot, so the sanitizer does it first.

    The rule outlived the deny list it was written for: it is what makes ``report.txt.`` land
    as ``report.txt`` and be typed from ``txt`` instead of from an empty extension.
    """
    stored = upload([FakePart("report.txt.", b"b")])["attachments"][0]
    assert stored["filename"] == "report.txt"
    assert stored["content_type"] == "text/plain"

    # only the LAST extension is ever read
    assert upload([FakePart("report.exe.txt", b"b")])["count"] == 1


def test_empty_multipart_is_422(env):
    """P0011 §2 [엣지 — 빈 multipart]."""
    from modules.flow_gate.documents.attachments import AttachmentError

    with pytest.raises(AttachmentError) as excinfo:
        upload([])
    assert (excinfo.value.status_code, excinfo.value.code) == (422, "INVALID_REQUEST")
    assert excinfo.value.details["field"] == "file"


def test_a_missing_document_is_404_across_all_six_paths(env):
    """P0011 §8 — every attachment path rejects an unknown document consistently."""
    from modules.flow_gate.documents.attachments import (
        AttachmentError,
        copy_to_source,
        delete_attachment,
        list_attachments,
        read_attachment,
        resolve_download,
    )

    missing_doc_id = "flowgate.default.0060.9999-N"
    calls = {
        "upload": lambda: upload([FakePart("notes.txt", b"x")], doc_id=missing_doc_id),
        "list": lambda: list_attachments(missing_doc_id),
        "download": lambda: resolve_download(missing_doc_id, "notes.txt"),
        "delete": lambda: delete_attachment(missing_doc_id, "notes.txt", ACTOR),
        "read": lambda: read_attachment(missing_doc_id, "notes.txt"),
        "copy": lambda: copy_to_source(
            missing_doc_id, "notes.txt", "imports/notes.txt", GROUP_ID, ACTOR
        ),
    }

    for operation, invoke in calls.items():
        with pytest.raises(AttachmentError) as excinfo:
            invoke()
        assert (excinfo.value.status_code, excinfo.value.code) == (
            404,
            "DOCUMENT_NOT_FOUND",
        ), operation


# ── list ────────────────────────────────────────────────────────────────────────

def test_list_of_a_document_with_no_attachments_is_200_and_empty(env):
    """P0011 §3 [엣지 — 빈 목록]. Not a 404; the card's empty state is drawn from this."""
    from modules.flow_gate.documents.attachments import list_attachments

    result = list_attachments(OTHER_DOC_ID)
    assert result == {"doc_id": OTHER_DOC_ID, "attachments": [], "count": 0}


def test_list_is_ordered_by_uploaded_at_then_filename(env):
    """P0011 §3 / DB0013 §4 idx_attachments_doc_uploaded."""
    from modules.flow_gate.documents.attachments import list_attachments

    upload([FakePart("b.txt", b"b"), FakePart("a.txt", b"a")])
    env["store"]._execute(
        "UPDATE attachments SET uploaded_at = ? WHERE filename = ?",
        ["2026-01-01T00:00:00Z", "b.txt"],
    )

    names = [item["filename"] for item in list_attachments(DOC_ID)["attachments"]]
    assert names == ["b.txt", "a.txt"]


# ── path jail ───────────────────────────────────────────────────────────────────

def test_a_dotdot_name_never_reaches_the_filesystem(env):
    """L0012 §2-6 J1 — the URL segment must be a bare name; it is a lookup key, not a path."""
    from modules.flow_gate.documents.attachments import AttachmentError, resolve_download

    for hostile in ("..", "../secret.txt", r"..\secret.txt", "a/b.txt"):
        with pytest.raises(AttachmentError) as excinfo:
            resolve_download(DOC_ID, hostile)
        assert (excinfo.value.status_code, excinfo.value.code) == (400, "INVALID_FILENAME")


def test_a_registry_row_pointing_at_another_documents_room_is_refused(env):
    """L0012 §2-6 J5 — being inside the storage root is not enough.

    This is the poisoned-row case: the path is perfectly legal storage, just not THIS
    document's. 403, not 404 — the row exists, it is the location that is wrong.
    """
    from modules.flow_gate.documents.attachments import AttachmentError, resolve_download

    upload([FakePart("notes.txt", b"x")], doc_id=OTHER_DOC_ID)
    env["store"]._execute(
        "UPDATE attachments SET doc_id = ? WHERE doc_id = ?", [DOC_ID, OTHER_DOC_ID]
    )

    with pytest.raises(AttachmentError) as excinfo:
        resolve_download(DOC_ID, "notes.txt")
    assert (excinfo.value.status_code, excinfo.value.code) == (403, "STORAGE_PATH_OUTSIDE_ROOT")


def test_an_absolute_registry_path_is_refused(env):
    """L0012 §2-6 J3 — corrupted rows are caught before anything is opened."""
    from modules.flow_gate.documents.attachments import AttachmentError, resolve_download

    upload([FakePart("notes.txt", b"x")])
    env["store"]._execute(
        "UPDATE attachments SET file_path = ? WHERE filename = 'notes.txt'",
        ["C:/Windows/system32/drivers/etc/hosts"],
    )

    with pytest.raises(AttachmentError) as excinfo:
        resolve_download(DOC_ID, "notes.txt")
    assert excinfo.value.code == "STORAGE_PATH_OUTSIDE_ROOT"


def test_a_symlinked_component_inside_the_room_is_refused(env):
    """L0012 §2-6 J6 — checked per component, not just at the leaf.

    The skip is decided by ATTEMPTING the symlink, not by `os.name`: this host creates them
    fine (Windows Developer Mode), and a blanket skipif would have reported green here
    without ever running the check.
    """
    from modules.flow_gate.documents.attachments import AttachmentError, resolve_download

    upload([FakePart("notes.txt", b"x")])
    room = room_of(env)
    outside = env["storage_root"].parent / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    (room / "notes.txt").unlink()
    try:
        (room / "notes.txt").symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not supported on this platform")

    with pytest.raises(AttachmentError) as excinfo:
        resolve_download(DOC_ID, "notes.txt")
    assert excinfo.value.code == "STORAGE_PATH_OUTSIDE_ROOT"


def test_a_row_whose_file_vanished_reads_as_404(env):
    """L0012 §2-6 J7 / §3-1 ghost_row — externally indistinguishable from "no such row"."""
    from modules.flow_gate.documents.attachments import AttachmentError, resolve_download

    upload([FakePart("notes.txt", b"x")])
    (room_of(env) / "notes.txt").unlink()

    with pytest.raises(AttachmentError) as excinfo:
        resolve_download(DOC_ID, "notes.txt")
    assert (excinfo.value.status_code, excinfo.value.code) == (404, "ATTACHMENT_NOT_FOUND")


# ── download ────────────────────────────────────────────────────────────────────

def test_download_headers_carry_rfc5987_and_an_etag(env):
    """L0012 §2-7 / P0011 §4 — `filename*` always; ASCII `filename` only when it is ASCII."""
    from modules.flow_gate.documents.attachments import resolve_download

    upload([FakePart("요구사항.xlsx", b"x"), FakePart("plain.txt", b"y")])

    _, meta = resolve_download(DOC_ID, "요구사항.xlsx")
    disposition = meta["headers"]["Content-Disposition"]
    assert "filename*=UTF-8''" in disposition
    assert "filename=\"" not in disposition          # no mojibake fallback for a Hangul name
    assert meta["headers"]["X-Content-Type-Options"] == "nosniff"
    assert meta["headers"]["ETag"].startswith('"sha256-')

    _, ascii_meta = resolve_download(DOC_ID, "plain.txt")
    assert 'filename="plain.txt"' in ascii_meta["headers"]["Content-Disposition"]


def test_html_is_downloaded_as_octet_stream(env):
    """L0012 §1-2 attach_download_forced_octet_types — never render an attachment inline."""
    from modules.flow_gate.documents.attachments import resolve_download

    upload([FakePart("page.html", b"<b>hi</b>")])
    _, meta = resolve_download(DOC_ID, "page.html")
    assert meta["media_type"] == "application/octet-stream"


# ── delete ──────────────────────────────────────────────────────────────────────

def test_delete_removes_both_the_file_and_the_row(env):
    """P0011 §5 — a 200 means the next identical delete is a 404."""
    from modules.flow_gate.documents.attachments import AttachmentError, delete_attachment

    upload([FakePart("notes.txt", b"x")])

    result = delete_attachment(DOC_ID, "notes.txt", ACTOR)
    assert result["file_deleted"] is True and result["metadata_deleted"] is True
    assert not (room_of(env) / "notes.txt").exists()
    assert env["store"]._fetch_all("SELECT * FROM attachments") == []

    with pytest.raises(AttachmentError) as excinfo:
        delete_attachment(DOC_ID, "notes.txt", ACTOR)
    assert (excinfo.value.status_code, excinfo.value.code) == (404, "ATTACHMENT_NOT_FOUND")


def test_a_ghost_row_heals_itself_on_the_next_identical_delete(env):
    """L0012 §2-8 X4/X5 + §3-2 — the reason the file is removed BEFORE the row.

    A row whose file is already gone (the metadata step failed last time, or something
    outside removed the file) must not be a permanent 500. Repeating the same delete finds
    the row, sees no file, treats that as the target state, and clears the row.
    """
    from modules.flow_gate.documents.attachments import delete_attachment

    upload([FakePart("notes.txt", b"x")])
    (room_of(env) / "notes.txt").unlink()          # ghost_row

    result = delete_attachment(DOC_ID, "notes.txt", ACTOR)
    assert result["metadata_deleted"] is True
    assert env["store"]._fetch_all("SELECT * FROM attachments") == []


# ── read ────────────────────────────────────────────────────────────────────────

def test_read_auto_returns_text_for_text_and_base64_for_binary(env):
    """L0012 §2-9 R8 / §4-3 — `auto` never answers 415; failing to decode IS the answer."""
    from modules.flow_gate.documents.attachments import read_attachment

    upload([FakePart("notes.txt", "FlowGate 첨부".encode()), FakePart("pixel.bin", b"\x00\x01\x02")])

    text = read_attachment(DOC_ID, "notes.txt")
    assert (text["kind"], text["content"], text["truncated"]) == ("text", "FlowGate 첨부", False)

    binary = read_attachment(DOC_ID, "pixel.bin")
    assert binary["kind"] == "binary"
    assert binary["content_encoding"] == "base64"


def test_read_text_mode_refuses_undecodable_bytes(env):
    """L0012 §2-9 R7 — 415 only when the caller forced `text`."""
    from modules.flow_gate.documents.attachments import AttachmentError, read_attachment

    upload([FakePart("pixel.bin", b"\xff\xfe\x00\x9c")])

    with pytest.raises(AttachmentError) as excinfo:
        read_attachment(DOC_ID, "pixel.bin", mode="text")
    assert (excinfo.value.status_code, excinfo.value.code) == (415, "INVALID_TEXT_ENCODING")


def test_read_over_the_limit_refuses_the_whole_file_instead_of_truncating(env):
    """L0012 §2-9 R4 — R4 runs before R5, so an oversized file is never even loaded.

    Cutting the content and answering 200 would let the caller treat a fragment as the whole
    file, which is why `truncated` is in the contract but always false.
    """
    from modules.flow_gate.documents.attachments import AttachmentError, read_attachment
    from modules.flow_gate.documents.attachments.constants import ATTACH_READ_MAX_BYTES

    upload([FakePart("big.txt", b"x" * (ATTACH_READ_MAX_BYTES + 1))])

    with pytest.raises(AttachmentError) as excinfo:
        read_attachment(DOC_ID, "big.txt")
    assert (excinfo.value.status_code, excinfo.value.code) == (413, "READ_TOO_LARGE")
    assert excinfo.value.details["limit_bytes"] == ATTACH_READ_MAX_BYTES


def test_read_at_exactly_the_limit_is_allowed(env):
    """L0012 §5 — the comparison is `>`, so the boundary itself passes."""
    from modules.flow_gate.documents.attachments import read_attachment
    from modules.flow_gate.documents.attachments.constants import ATTACH_READ_MAX_BYTES

    upload([FakePart("exact.txt", b"x" * ATTACH_READ_MAX_BYTES)])
    assert read_attachment(DOC_ID, "exact.txt", mode="base64")["kind"] == "binary"


def test_an_unknown_encoding_is_422_not_500(env):
    """L0012 §5 — codec lookup failure is a bad request, not a crash."""
    from modules.flow_gate.documents.attachments import AttachmentError, read_attachment

    upload([FakePart("notes.txt", b"hi")])
    with pytest.raises(AttachmentError) as excinfo:
        read_attachment(DOC_ID, "notes.txt", encoding="utf8x")
    assert (excinfo.value.status_code, excinfo.value.code) == (422, "INVALID_REQUEST")


# ── copy ────────────────────────────────────────────────────────────────────────

@pytest.fixture
def src_root(tmp_path, monkeypatch):
    """A stand-in for the group worktree. There is no git repo in a unit test."""
    from modules.flow_gate.services import git_service

    root = tmp_path / "worktree"
    root.mkdir()
    monkeypatch.setattr(git_service, "effective_src_root_ex", lambda *_: (root, "worktree"))
    monkeypatch.setattr(git_service, "base_src_root", lambda *_a, **_k: root)
    return root


def test_copy_into_the_group_worktree(env, src_root):
    """P0011 §7 [정상 — 그룹 작업본으로 복사]."""
    from modules.flow_gate.documents.attachments import copy_to_source

    upload([FakePart("요구사항.xlsx", b"payload")])

    result = copy_to_source(DOC_ID, "요구사항.xlsx", "imports/0060/요구사항.xlsx", GROUP_ID, ACTOR)

    assert result["destination"]["target_path"] == "imports/0060/요구사항.xlsx"
    assert result["destination"]["path_base"] == "source"
    assert result["destination"]["group_id"] == GROUP_ID
    assert (src_root / "imports" / "0060" / "요구사항.xlsx").read_bytes() == b"payload"
    assert result["content_sha256"] == hashlib.sha256(b"payload").hexdigest()


def test_copy_into_the_base_checkout(env, src_root):
    """P0011 §7 [정상 — 기준 체크아웃으로 복사]. `group_id: null` is an explicit choice."""
    from modules.flow_gate.documents.attachments import copy_to_source

    upload([FakePart("notes.txt", b"base")])
    result = copy_to_source(DOC_ID, "notes.txt", "imports/0060/notes.txt", None, ACTOR)

    assert result["destination"]["group_id"] is None
    assert (src_root / "imports" / "0060" / "notes.txt").read_bytes() == b"base"


def test_copy_refuses_to_overwrite(env, src_root):
    """L0012 §2-10 C8 — reject, never overwrite, never silently rename.

    The upload path DOES auto-rename, and the difference is deliberate: there the name came
    from a file and the result is visible in the list; here the path came from the caller and
    later edits/tests/commits key off it.
    """
    from modules.flow_gate.documents.attachments import AttachmentError, copy_to_source

    upload([FakePart("notes.txt", b"new")])
    target = src_root / "imports" / "notes.txt"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"original")

    with pytest.raises(AttachmentError) as excinfo:
        copy_to_source(DOC_ID, "notes.txt", "imports/notes.txt", GROUP_ID, ACTOR)

    assert (excinfo.value.status_code, excinfo.value.code) == (409, "TARGET_EXISTS")
    assert target.read_bytes() == b"original"      # untouched


def test_copy_refuses_a_case_only_collision_on_every_platform(env, src_root):
    """L0012 §2-10 C8 — otherwise the same request overwrites on Windows and coexists on Linux."""
    from modules.flow_gate.documents.attachments import AttachmentError, copy_to_source

    upload([FakePart("Notes.txt", b"new")])
    target = src_root / "imports" / "notes.txt"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"original")

    with pytest.raises(AttachmentError) as excinfo:
        copy_to_source(DOC_ID, "Notes.txt", "imports/Notes.txt", GROUP_ID, ACTOR)

    assert excinfo.value.code == "TARGET_EXISTS"
    assert excinfo.value.details["reason"] == "case_insensitive_match"
    assert excinfo.value.details["existing_name"] == "notes.txt"


@pytest.mark.parametrize(
    "target_path",
    ["../secrets/notes.txt", "/etc/passwd", "C:/Windows/notes.txt", "a//b.txt",
     "imports/", "imports/CON.txt", "imports/trailing./x.txt"],
)
def test_copy_refuses_a_malformed_target_path(env, src_root, target_path):
    """L0012 §2-10 C2 / P0011 §7 [실패 — 대상 경로 이탈]. Pure string checks, before any I/O."""
    from modules.flow_gate.documents.attachments import AttachmentError, copy_to_source

    upload([FakePart("notes.txt", b"x")])
    with pytest.raises(AttachmentError) as excinfo:
        copy_to_source(DOC_ID, "notes.txt", target_path, GROUP_ID, ACTOR)
    assert (excinfo.value.status_code, excinfo.value.code) == (400, "INVALID_PATH")


def test_copy_does_not_apply_the_upload_extension_denylist_to_its_target(env, src_root):
    """L0012 §2-10 C4 — `.ps1` is an ordinary file in a source tree.

    The deny-list exists to stop a receiver clicking and running something; a copy result
    never goes to a browser. Blocking it here would make "put the script I was sent into the
    source" impossible.
    """
    from modules.flow_gate.documents.attachments import copy_to_source

    upload([FakePart("script.txt", b"Write-Host hi")])
    result = copy_to_source(DOC_ID, "script.txt", "tools/deploy.ps1", GROUP_ID, ACTOR)

    assert (src_root / "tools" / "deploy.ps1").exists()
    assert result["destination"]["target_path"] == "tools/deploy.ps1"


def test_copy_refuses_when_the_group_worktree_cannot_be_resolved(env, monkeypatch):
    """L0012 §2-10 C5 — fail closed. The base checkout must NOT be substituted.

    `storage_paths.resolve_project_src_root` silently falls back to the base checkout, which
    would dirty it under a group tab and jam every other group's finalize — the same reason
    `file_transfer_routes.py:37-43` went fail-closed.
    """
    from modules.flow_gate.documents.attachments import AttachmentError, copy_to_source
    from modules.flow_gate.services import git_service

    upload([FakePart("notes.txt", b"x")])
    monkeypatch.setattr(
        git_service, "effective_src_root_ex", lambda *_: (None, "worktree_unregistered")
    )

    with pytest.raises(AttachmentError) as excinfo:
        copy_to_source(DOC_ID, "notes.txt", "imports/notes.txt", GROUP_ID, ACTOR)

    assert (excinfo.value.status_code, excinfo.value.code) == (409, "WORKTREE_UNAVAILABLE")
    assert excinfo.value.details["reason"] == "worktree_unregistered"


def test_copy_refuses_an_unknown_group(env, src_root):
    """P0011 §8 TARGET_GROUP_NOT_FOUND — the group must belong to the document's project."""
    from modules.flow_gate.documents.attachments import AttachmentError, copy_to_source

    upload([FakePart("notes.txt", b"x")])
    with pytest.raises(AttachmentError) as excinfo:
        copy_to_source(DOC_ID, "notes.txt", "imports/notes.txt", "flowgate.default.0999", ACTOR)
    assert (excinfo.value.status_code, excinfo.value.code) == (404, "TARGET_GROUP_NOT_FOUND")


# ── RPC compatibility wrapper ───────────────────────────────────────────────────

def test_the_rpc_wrapper_and_the_rest_route_produce_the_same_result(env):
    """T0016 §3-3 / NR0015 §2-3 — one shared service body, two entry points.

    The RPC used to call the REST *route function* directly. Now both call
    `upload_attachments`, so the two cannot drift; this pins that they agree field for field
    (only the generated filename differs, because the second upload dedupes).
    """
    from modules.flow_gate.documents.routers import documents as documents_router

    rest = upload([FakePart("shared.txt", b"same-bytes")])["attachments"][0]

    class _Request:
        headers = {"content-length": "10"}

    response = asyncio.run(
        documents_router.upload_attachment_rpc(
            _Request(), doc_id=DOC_ID, file=FakePart("shared.txt", b"same-bytes"),
            current_user=ACTOR,
        )
    )

    assert response.status_code == 201
    assert response.headers["Deprecation"] == "true"
    assert "rel=\"successor-version\"" in response.headers["Link"]

    import json

    rpc = json.loads(response.body)["data"]
    assert rpc["content_sha256"] == rest["content_sha256"]
    assert rpc["size"] == rest["size"]
    assert rpc["path_base"] == rest["path_base"] == "storage"
    # NR0003 G4: no absolute path in the compatibility response either
    assert not Path(rpc["path"]).is_absolute()
    assert rpc["path"].startswith(f"documents/{PROJECT_ID}/")
    # dedupe kicked in for the second upload of the same name
    assert rpc["filename"] != rest["filename"]
    assert rpc["original_filename"] == rest["original_filename"] == "shared.txt"


# ── numbering scan regression (T0016 §9-2) ──────────────────────────────────────

def test_the_numbering_check_stays_green_after_an_attachment_room_appears(env):
    """L0012 §2-12 — before this, adding one attachment turned the whole check red.

    `verify.py` reported every file under project_root that no document row pointed at, and
    flipped `ok` to False if there was even one. The attachment room lives at
    {module}/{group}/{doc_code}/, which the old top-level-only exclusion list could not reach.
    """
    from modules.flow_gate.numbering.verify import verify_id_widths

    upload([FakePart("요구사항.xlsx", b"x")])

    report = verify_id_widths(PROJECT_ID)
    assert report.ok is True
    assert report.orphan_files == []
    assert report.unregistered_attachments == []


def test_a_file_in_a_room_with_no_registry_row_is_reported_without_failing_the_check(env):
    """L0012 §2-12 W3 — the safety net for mid-migration files. Reported, but `ok` stays true."""
    from modules.flow_gate.numbering.verify import verify_id_widths

    upload([FakePart("registered.txt", b"x")])
    stray = room_of(env) / "stray.txt"
    stray.write_text("left behind", encoding="utf-8")

    report = verify_id_widths(PROJECT_ID)
    assert report.ok is True
    assert report.orphan_files == []
    assert [Path(p).name for p in report.unregistered_attachments] == ["stray.txt"]


def test_a_real_orphan_outside_any_room_still_fails_the_check(env):
    """W4 — the exclusion must not become a blanket amnesty."""
    from modules.flow_gate.numbering.verify import verify_id_widths

    (env["group_dir"] / "who_put_this_here.txt").write_text("?", encoding="utf-8")

    report = verify_id_widths(PROJECT_ID)
    assert report.ok is False
    assert [Path(p).name for p in report.orphan_files] == ["who_put_this_here.txt"]


# ── 500 diagnosability (TR0017 rev2 반려: "500 Internal Server Error" while uploading) ──
#
# The rework reason was a bare `500 (Internal Server Error)` in the browser console plus a
# generic "upload failed" message. Two things made that report undiagnosable, and both are
# pinned here:
#   1. A1~A6 (the upload preamble) ran OUTSIDE the try, so anything unexpected there escaped
#      as FastAPI's bare 500 — no P0011 envelope, no error code the card can read.
#   2. The package logged nothing at all, so `server/logs/default.log` held no trace of the
#      failed request. That is why the cause had to be hunted from disk forensics.

def test_an_unexpected_failure_in_the_preamble_answers_the_envelope_not_a_bare_500(env, caplog):
    """A1~A6 crash → 500 ATTACHMENT_STORE_FAILED with reason='storage', traceback logged."""
    import logging

    from modules.flow_gate.documents.attachments import AttachmentError
    from modules.flow_gate.documents.attachments import service as svc

    def boom(_doc_id):
        raise sqlite3.OperationalError("no such table: attachments")

    original = svc.registry_count
    svc.registry_count = boom
    try:
        with caplog.at_level(logging.ERROR, logger="flow_gate.attachments"):
            with pytest.raises(AttachmentError) as excinfo:
                upload([FakePart("보고서.xlsx", b"x")])
    finally:
        svc.registry_count = original

    error = excinfo.value
    assert (error.status_code, error.code) == (500, "ATTACHMENT_STORE_FAILED")
    assert error.details["reason"] == "storage"
    # P0011 §1-4 — the envelope still carries no path and no exception text.
    assert "no such table" not in str(error.body())
    assert "OperationalError" not in str(error.body())
    # ...but the server log carries both, with the traceback.
    logged = "\n".join(r.getMessage() for r in caplog.records)
    assert "[attachments] upload failed" in logged
    assert "OperationalError" in logged
    assert any(r.exc_info for r in caplog.records)


def test_a_registry_commit_failure_is_logged_and_leaves_no_file_behind(env, caplog):
    """A8/A9 — metadata failure keeps its own code, still logs, still rolls the file back."""
    import logging

    from modules.flow_gate.documents.attachments import AttachmentError
    from modules.flow_gate.documents.attachments import service as svc

    def boom(**kwargs):
        raise sqlite3.IntegrityError("UNIQUE constraint failed: attachments.filename")

    original = svc.registry_insert
    svc.registry_insert = boom
    try:
        with caplog.at_level(logging.ERROR, logger="flow_gate.attachments"):
            with pytest.raises(AttachmentError) as excinfo:
                upload([FakePart("메모.txt", b"x")])
    finally:
        svc.registry_insert = original

    error = excinfo.value
    assert (error.status_code, error.code) == (500, "ATTACHMENT_METADATA_FAILED")
    assert error.details["reason"] == "registry"
    assert not (room_of(env) / "메모.txt").exists()      # A9 rollback
    assert env["store"]._fetch_all("SELECT * FROM attachments") == []
    assert "[attachments] upload:registry failed" in "\n".join(
        r.getMessage() for r in caplog.records
    )


def test_a_group_lock_is_never_swallowed_into_the_500_branch(env):
    """The 423 the mutability guard raises must survive the new catch-all."""
    from fastapi import HTTPException

    from modules.flow_gate.documents.attachments import AttachmentError
    from modules.flow_gate.documents.attachments import service as svc

    def locked(doc, actor, operation):
        raise HTTPException(status_code=423, detail={"error": {"code": "GROUP_AI_RUN_LOCKED"}})

    original = svc.assert_mutable
    svc.assert_mutable = locked
    try:
        with pytest.raises(HTTPException) as excinfo:
            upload([FakePart("메모.txt", b"x")])
    finally:
        svc.assert_mutable = original

    assert excinfo.value.status_code == 423
    assert not isinstance(excinfo.value, AttachmentError)


def test_the_route_turns_an_unexpected_crash_into_the_envelope(env):
    """Route shell — a service that raises something else still answers P0011, and logs."""
    import json

    from modules.flow_gate.documents.attachments import unexpected

    error = unexpected(RuntimeError("boom"), operation="route:list", doc_id=DOC_ID)
    body = json.loads(error.response().body.decode("utf-8"))

    assert error.status_code == 500
    assert body["error"]["code"] == "ATTACHMENT_OPERATION_FAILED"
    assert body["error"]["details"] == {"reason": "unexpected", "doc_id": DOC_ID}
    assert "boom" not in json.dumps(body)


def test_every_hostile_filename_that_crashed_the_old_upload_now_lands(env):
    """The reproduced pre-0060 500 class: names Windows cannot create.

    Measured on the deployed build (8089) 2026-08-15: `보고서*.txt`, `a|b.txt`, `a<b>.txt`,
    a tab in the name and a 300-character name all answered `500 Internal Server Error`,
    because the old body handed the raw name to `dest.write_bytes` and let the OSError out.
    S4/S8 sanitize them instead, so every one of them is stored.
    """
    hostile = ["보고서*.txt", "a|b.txt", "a<b>.txt", "a\tb.txt", "가" * 300 + ".txt"]

    for name in hostile:
        item = upload([FakePart(name, b"x")])["attachments"][0]
        stored = room_of(env) / item["filename"]
        assert stored.exists(), name
        for illegal in ("*", "|", "<", ">", "\t"):
            assert illegal not in item["filename"]
        assert len(item["filename"].encode("utf-8")) <= 200


# ── deployment skew — the actual cause of the rejected 500 ──────────────────────
#
# The two tests above made the failure *readable*; these two make it not happen. The rejected
# upload did not die of a logic bug in this package but of a NEW cross-module symbol: locator
# called `storage_paths.within_allowed_roots()`, the public wrapper 0060 adds to the shared
# `storage/paths.py`, while the running deployment carried this (new) package next to an
# OLDER copy of that shared file. Every upload then died inside A1~A6 with
#
#   AttributeError: module 'modules.flow_gate.storage.paths'
#                   has no attribute 'within_allowed_roots'
#
# (dev preview, 2026-08-15 19:43:00 and 19:43:11, server/logs/default.log). The whole package
# is new, so it is always the newer half of any such skew — the jail check is therefore
# resolved at call time, not bound at import.

def test_upload_survives_a_paths_module_without_the_new_public_wrapper(env, monkeypatch):
    """The reproduced rejection: an older `paths.py` must not turn every upload into a 500."""
    from modules.flow_gate.storage import paths as storage_paths

    monkeypatch.delattr(storage_paths, "within_allowed_roots")
    assert not hasattr(storage_paths, "within_allowed_roots")   # the deployed shape

    item = upload([FakePart("요구사항_정리.xlsx", b"x")])["attachments"][0]

    assert (room_of(env) / item["filename"]).is_file()
    # and the jail is still a jail — the download path resolves through the same helper.
    from modules.flow_gate.documents.attachments import resolve_download

    path, _row = resolve_download(DOC_ID, item["filename"])
    assert path.read_bytes() == b"x"


def test_a_jail_that_cannot_be_resolved_at_all_denies_instead_of_waving_it_through(
    env, monkeypatch
):
    """Fail CLOSED. A missing guard must never read as "the path is fine".

    The skew is simulated the way it actually arrives — the module object locator holds is an
    older `paths.py` — rather than by deleting the attribute from the real module, which would
    only break `paths.py`'s own internal callers before this check is ever reached.
    """
    from modules.flow_gate.documents.attachments import AttachmentError
    from modules.flow_gate.documents.attachments import locator
    from modules.flow_gate.storage import paths as storage_paths

    class _PathsWithoutAnyJail:
        """Everything the real module has, minus both spellings of the check."""

        def __getattr__(self, name):
            if name in ("within_allowed_roots", "_within_allowed_roots"):
                raise AttributeError(name)
            return getattr(storage_paths, name)

    monkeypatch.setattr(locator, "storage_paths", _PathsWithoutAnyJail())

    assert locator.within_storage_jail(room_of(env), PROJECT_ID) is False

    with pytest.raises(AttachmentError) as excinfo:
        upload([FakePart("메모.txt", b"x")])

    assert (excinfo.value.status_code, excinfo.value.code) == (
        403,
        "STORAGE_PATH_OUTSIDE_ROOT",
    )
    assert not (room_of(env) / "메모.txt").exists()
