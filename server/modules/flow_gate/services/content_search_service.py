"""Document body full-text search (R0001 Phase 2 / group 0123 T0006).

Phase 1 (``GET /search/documents``) matched title/doc_id metadata in SQL. Phase 2
adds body (markdown) search. The body is stored on the filesystem — the
``documents`` table has no ``content`` column — and the DB is multi-dialect
(SQLite/MySQL/PostgreSQL), so native FTS (FTS5 / FULLTEXT / tsvector) cannot be
expressed in one portable query (NR0003 §2-2). Instead of mirroring bodies into a
denormalized column (which would couple to every write path) this service reads the
canonical file through the same ``resolve_storage_path`` chokepoint the read API
uses, so it is correct regardless of which writer produced the file.

Repeated searches are made cheap by a process-local cache keyed by the file's
mtime: a candidate whose file is unchanged since last read is matched from memory;
a changed file (any mtime delta, from any write path) is transparently re-read.
This is self-healing — no write-path hooks, no migration, zero dialect cost.

Limitation (honest): a facet-less search reads every candidate file on the cold
pass (then only re-reads changed files). For the per-instance document volume this
is acceptable; if corpora grow large enough to feel it, the next step is a
persistent index / dialect-specific FTS (NR0003 Option B1), tracked separately.
"""
from __future__ import annotations

import re
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Optional

from modules.flow_gate.db import conversation_turns as turn_store
from modules.flow_gate.db import documents as db_docs
from modules.flow_gate.db.connection import get_store
from modules.flow_gate.storage.paths import resolve_storage_path

# ── T4 conversation-turn search (L0004 §1-4 / §2-15) ────────────────────────────
# Single source of truth for the read-side numbers; do not inline these values.
SEARCH_TURN_LIMIT = 50
SEARCH_TURNS_PER_DOC = 3
SEARCH_SNIPPET_CHARS = 120
SEARCH_SNIPPET_LEAD = 40

_LIKE_ESCAPE_RE = re.compile(r"([\\%_])")


def _escape_like(value: str) -> str:
    """Escape a LIKE pattern's special characters so a literal ``%``/``_`` cannot act
    as a wildcard (paired with ``ESCAPE '\\'`` in the query)."""
    return _LIKE_ESCAPE_RE.sub(r"\\\1", value)

# doc_id -> (mtime, original_text, lowered_text), least-recently-used first.
#
# 0279 P3-11: this cache was an unbounded plain dict. A facet-less body search walks
# every candidate document (see the "Limitation (honest)" note above), so one such
# search admitted the *entire corpus* — and each entry holds the body twice, the
# original text plus its lowercased copy, so the resident cost is roughly 2× the
# total size of all document bodies and it never came back down. Nothing evicted:
# `reset_cache()` is only called by tests. On a long-lived server with a growing
# corpus that is a monotonic climb into memory pressure, which is exactly the kind
# of slow-onset "가끔 멈춘다" R0001 is chasing.
#
# An LRU cap makes the footprint predictable. The cache is a pure optimisation
# keyed on mtime — a miss re-reads the file and is correct, just slower — so
# eviction can never produce a wrong result, only a re-read.
_CACHE_MAX_ENTRIES = 512
_CACHE: "OrderedDict[str, tuple[float, str, str]]" = OrderedDict()
_LOCK = threading.Lock()

_SNIPPET_BEFORE = 40
_SNIPPET_AFTER = 120
_WS = re.compile(r"\s+")
# The document "header" the reviewer means is the leading YAML frontmatter block —
# the ``---`` … ``---`` fence carrying project/module/group/doc_number/title/… that
# every stored document file begins with. The detail snippet (and the body match)
# must be drawn from the *body* below that fence so the header never shows in the
# search detail row and the row starts from the real content ("내용…")
# (group 0123 rev6: "상세쪽에 문서의 헤더를 출력하지 말라 … 내용… 부터 나오게").
# This mirrors the in-app viewer's ``stripFrontmatter`` (client shared/utils/markdown.ts)
# exactly, so search and the open document agree on where the body begins.
#
# rev4 mistakenly removed whole ATX markdown heading lines (``# ``…``###### ``) from
# the snippet, reading "문서의 헤더" (the document header) as a ``#`` markdown heading.
# rev6 clarified the header is the leading YAML ``---`` frontmatter block, not a ``#``
# heading: a ``## 2. 리소스`` section title is *body content*. Dropping heading lines
# meant a search term that lives only in a section title (very common in these
# structured docs) matched the row but produced *no* snippet — the body excerpt came
# and went depending on whether the hit was prose or a heading (group 0123 rev7:
# "내용이 나왔다 안나왔다 … 본문이 있는 문서도 나오지 않는다"). So the heading *text*
# stays in the snippet; only the leading ``#`` markup glyphs are stripped so no header
# markup shows.
_FRONTMATTER = re.compile(r"\A---\r?\n.*?\r?\n---[ \t]*\r?\n?", re.DOTALL)
# Leading ATX heading markers (``# ``…``###### ``) at the start of a line — only the
# ``#`` glyphs and the gap are removed; the heading words are kept as snippet content
# (group 0123 rev7). NOT a whole-line strip (that was the rev4 bug).
_HEADING_MARK = re.compile(r"^[ \t]*#{1,6}[ \t]+", re.MULTILINE)
# Canonical storage-relative tail of a stored body path: ``documents/<project>/…``.
# Used to recover a body file whose stored ``file_path`` is a stale *absolute* path
# left over from a previous host (group 0123 rev8 — see ``_resolve_body_path``).
_DOC_TAIL = re.compile(r"(documents/[^/]+/.*)$")


def _resolve_body_path(file_path: str, project_id, branch: str) -> Optional[Path]:
    """Resolve a stored body path to a real file, recovering host-migrated paths.

    First tries the canonical jailed resolver. When that fails AND the stored value
    still carries a foreign *absolute* prefix from a previous host — the B0054
    host-migration class; the ``documents`` table still holds rows like
    ``/home/<olduser>/.../storage/documents/flowgate/main/0123/0007-TR_….md`` — the
    dead prefix is dropped and the canonical ``documents/<project>/…`` tail is
    re-resolved under the *current* storage root through the same jailed resolver.

    Without this the file is never read, so a document that genuinely *has* matching
    body content is silently skipped and never appears in search — the reviewer's
    literal report: "본문이 있는 문서의 경우도 [검색에] 나오지 않는다" (group 0123 rev8).
    Purely additive: it only runs when the normal resolve already returned None, so a
    working (relative or current-host) path is never re-routed.
    """
    resolved = resolve_storage_path(file_path, project_id, branch=branch)
    if resolved is not None:
        return resolved
    norm = (file_path or "").replace("\\", "/")
    m = _DOC_TAIL.search(norm)
    if m and m.group(1) != norm:  # a foreign prefix preceded the canonical tail
        return resolve_storage_path(m.group(1), project_id, branch=branch)
    return None


def _strip_frontmatter(text: str) -> str:
    """Drop a leading YAML frontmatter fence (the document header) from a body.

    No-op when the text does not begin with a ``---`` fence (e.g. a raw source file
    or a body that was already header-stripped). Mirrors the client viewer so the
    search body and the opened document share one definition of "body".
    """
    return _FRONTMATTER.sub("", text, count=1)


def reset_cache() -> None:
    """Clear the in-process body cache (used by tests; harmless in production)."""
    with _LOCK:
        _CACHE.clear()


def _get_cached(doc: dict) -> Optional[tuple[str, str]]:
    """Return ``(original_text, lowered_text)`` for a document body, or None.

    Resolves the stored path, stats it for the mtime change-signal, and reads the
    file only when the cache is cold or stale. None when the document has no file,
    the path does not resolve inside the storage jail, or the read fails.
    """
    file_path = (doc.get("file_path") or "").strip()
    if not file_path:
        return None
    branch = doc.get("branch") or "main"
    resolved = _resolve_body_path(file_path, doc.get("project_id"), branch)
    if resolved is None:
        return None
    try:
        mtime = resolved.stat().st_mtime
    except OSError:
        return None

    doc_id = doc.get("doc_id") or ""
    with _LOCK:
        cached = _CACHE.get(doc_id)
        if cached is not None and cached[0] == mtime:
            _CACHE.move_to_end(doc_id)  # mark as recently used
            return cached[1], cached[2]

    try:
        raw = resolved.read_text(encoding="utf-8")
    except OSError:
        return None
    # Cache the body with the leading YAML frontmatter (the document header) removed,
    # so both the body-match test and the snippet operate on the content below the
    # fence — the header never reaches the detail row and a metadata-only term in the
    # frontmatter does not masquerade as a body hit (group 0123 rev6).
    text = _strip_frontmatter(raw)
    lowered = text.lower()
    with _LOCK:
        _CACHE[doc_id] = (mtime, text, lowered)
        _CACHE.move_to_end(doc_id)
        while len(_CACHE) > _CACHE_MAX_ENTRIES:
            _CACHE.popitem(last=False)  # evict least recently used
    return text, lowered


def _snippet(text: str, needle: str) -> Optional[str]:
    """Build a whitespace-collapsed excerpt around the first body match, with ellipses.

    ``text`` is already the document body with the YAML frontmatter header removed
    (see ``_strip_frontmatter`` in ``_get_cached``). The excerpt is drawn from the
    *whole* body — a markdown section heading (``## 2. 리소스 …``) is body content, so a
    term that lives only in a heading still yields a snippet (group 0123 rev7: the body
    excerpt was missing whenever the hit was a heading instead of prose). Only the
    leading ``#`` heading markers inside the excerpt are dropped, so the heading text
    shows but the ``#`` markup never does.
    """
    lowered = text.lower()
    idx = lowered.find(needle)
    if idx < 0:
        return None
    start = max(0, idx - _SNIPPET_BEFORE)
    end = min(len(text), idx + len(needle) + _SNIPPET_AFTER)
    excerpt = _HEADING_MARK.sub("", text[start:end])
    frag = _WS.sub(" ", excerpt).strip()
    if start > 0:
        frag = "…" + frag
    if end < len(text):
        frag = frag + "…"
    return frag


def _body_preview(text: str) -> Optional[str]:
    """Return the beginning of the document body for metadata-only matches.

    Content search is the mode where the explorer is allowed to show body detail.
    Even when the query matched title/doc_id instead of the body text, the result row
    should still show the document's simplified body when a readable body exists
    (group 0123 rev9: concrete doc_id searches showed rows with no body excerpt).
    """
    frag = _HEADING_MARK.sub("", text[:_SNIPPET_AFTER])
    frag = _WS.sub(" ", frag).strip()
    if not frag:
        return None
    if len(text) > _SNIPPET_AFTER:
        frag = frag + "…"
    return frag


def body_preview_for_doc(doc: dict) -> Optional[str]:
    """Public: simplified body preview for one document row, or None.

    This is what the **default metadata search** (Phase 1, ``GET /search/documents``
    — the search the explorer runs when "내용까지 검색" is *off*) uses so that every
    result row shows the document's brief body, not just its id and title.

    Root cause of group 0123 rev1–rev9: the body preview was only ever attached on
    the *content* endpoint (the checkbox-on path), but the reviewer was searching in
    the default metadata mode (e.g. by doc_id ``flowgate.default.0094.0001-R``). In
    that mode the body was never read, so the brief body (``test 1234``) never showed
    — every prior fix patched the wrong endpoint. Attaching the preview here, on the
    default search, is the fix (group 0123 rev10).

    Reuses ``_get_cached`` (mtime cache + frontmatter strip + host-migrated path
    recovery) and ``_body_preview`` so the preview is byte-for-byte the same one the
    content endpoint produces. Only the paged result rows are read, so the default
    search stays cheap.
    """
    cached = _get_cached(doc)
    if cached is None:
        return None
    return _body_preview(cached[0])


def _item(doc: dict, snippet: Optional[str], matched_in: str) -> dict:
    return {
        "doc_id": doc.get("doc_id"),
        "type": doc.get("type_code"),
        "title": doc.get("title"),
        "status": doc.get("status"),
        "project_id": doc.get("project_id"),
        "group_id": doc.get("group_id"),
        "revision_no": doc.get("revision_no", 0),
        "owner_id": doc.get("owner_id"),
        "created_at": doc.get("created_at"),
        "updated_at": doc.get("updated_at"),
        "snippet": snippet,
        "matched_in": matched_in,
        # T4: distinguishes a document-body hit from a conversation-turn hit once the
        # two are merged into one result list (list_routes.search_documents_content).
        # ``matched_in`` is kept as-is above — existing clients still read it.
        "match_kind": "document_body",
    }


def _is_migrated_conversation(doc: dict) -> bool:
    """True for a CH document whose body of record has moved to the turn store.

    Its file stops changing the moment migration completes (T1), so matching that
    frozen file's text would surface stale content AND duplicate the same
    conversation's turn-search hit (T4 §5). Title/doc_id matching is unaffected —
    those come from document metadata, not the file body.
    """
    if (doc.get("type_code") or "").upper() != "CH":
        return False
    return turn_store.migration_state(doc.get("doc_id") or "") == "migrated"


def search_document_bodies(
    q: str,
    project: str = None,
    doc_type: str = None,
    status: str = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[dict], int]:
    """Full document search (body + title + doc_id) with optional metadata facets.

    A document matches when the query appears in its body, title, or doc_id (a
    superset of the Phase 1 metadata search). Body matches carry a ``snippet`` and
    ``matched_in="body"``; otherwise the snippet is None and ``matched_in`` names the
    field that matched. Returns ``(page_items, total_matches)`` where total ignores
    limit/offset for paging. Ordering follows the DB helper (updated_at DESC).
    """
    needle = (q or "").strip().lower()
    if not needle:
        return [], 0

    rows = db_docs.list_documents_for_fulltext(
        project=project, doc_type=doc_type, status=status
    )
    matches: list[dict] = []
    for doc in rows:
        matched_in: Optional[str] = None
        snippet: Optional[str] = None
        body_preview: Optional[str] = None

        cached = None if _is_migrated_conversation(doc) else _get_cached(doc)
        if cached is not None:
            text, lowered = cached
            body_preview = _body_preview(text)
            if needle in lowered:
                matched_in = "body"
                snippet = _snippet(text, needle)

        if matched_in is None and needle in (doc.get("title") or "").lower():
            matched_in = "title"
        if matched_in is None and needle in (doc.get("doc_id") or "").lower():
            matched_in = "doc_id"
        if matched_in is None:
            continue
        if snippet is None and matched_in in {"title", "doc_id"}:
            snippet = body_preview
        matches.append(_item(doc, snippet, matched_in))

    total = len(matches)
    page = matches[offset:offset + limit] if offset < total else []
    return page, total


def _turn_snippet(body: str, needle_lower: str) -> Optional[str]:
    """Excerpt around the first match in a turn body (mirrors ``_snippet`` above, with
    the turn-search-specific lead/tail sizes)."""
    lowered = body.lower()
    idx = lowered.find(needle_lower)
    if idx < 0:
        return None
    start = max(0, idx - SEARCH_SNIPPET_LEAD)
    end = min(len(body), start + SEARCH_SNIPPET_CHARS)
    frag = _WS.sub(" ", body[start:end]).strip()
    if start > 0:
        frag = "…" + frag
    if end < len(body):
        frag = frag + "…"
    return frag


def search_conversation_turns(
    q: str,
    project: str = None,
    status: str = None,
    limit: int = SEARCH_TURN_LIMIT,
    per_doc: int = SEARCH_TURNS_PER_DOC,
) -> list[dict]:
    """Conversation-turn body search (T4, L0004 §2-15 / P0003 시나리오 16).

    Case-insensitive (``LOWER`` on both sides); a literal ``%``/``_``/``\\`` in the
    query is escaped so it cannot act as a wildcard. At most ``per_doc`` turns are
    returned per document (a very active chat must not crowd out every other result),
    and at most ``SEARCH_TURN_LIMIT`` turns are scanned/returned overall. Only fully
    migrated conversations participate, so an in-progress migration can never leak a
    partial turn set. Ordering is the L0004 contract: newest ``created_at`` first,
    then document id and descending sequence as deterministic tie-breakers.
    """
    needle = (q or "").strip()
    effective_limit = max(0, min(int(limit), SEARCH_TURN_LIMIT))
    if not needle or effective_limit == 0:
        return []
    store = get_store()
    from modules.flow_gate.db import dialect as _dialect_mod
    # A raw single backslash is a valid one-character ESCAPE literal in SQLite and
    # PostgreSQL, but MySQL's default (backslash-escaping) string-literal parsing
    # would read '\' as an escaped quote and never close the string; '\\' escapes
    # down to the same one-character backslash there. Same effective ESCAPE
    # character on every dialect, different literal spelling.
    escape_literal = "'\\\\'" if store.dialect == _dialect_mod.MYSQL else "'\\'"
    like_pattern = "%" + _escape_like(needle) + "%"
    clauses = [f"LOWER(t.body) LIKE LOWER(?) ESCAPE {escape_literal}"]
    params: list = [like_pattern]
    if project:
        clauses.append("d.project_id = ?")
        params.append(project)
    if status:
        clauses.append("d.status = ?")
        params.append(status)
    where_sql = " AND ".join(clauses)
    rows = get_store()._fetch_all(
        "SELECT t.doc_id AS doc_id, t.seq AS seq, t.speaker AS speaker, "
        "t.display_name AS display_name, t.body AS body, t.created_at AS created_at, "
        "d.title AS title, d.status AS status, d.project_id AS project_id, "
        "d.group_id AS group_id "
        "FROM conversation_turns t JOIN documents d ON d.doc_id = t.doc_id "
        "JOIN conversation_docs c ON c.doc_id = t.doc_id "
        f"WHERE c.migration_state = 'migrated' AND {where_sql} "
        "ORDER BY t.created_at DESC, t.doc_id ASC, t.seq DESC LIMIT ?",
        params + [effective_limit],
    )
    needle_lower = needle.lower()
    per_doc_count: dict[str, int] = {}
    items: list[dict] = []
    for row in rows:
        if len(items) >= effective_limit:
            break
        doc_id = row["doc_id"]
        seen = per_doc_count.get(doc_id, 0)
        if seen >= per_doc:
            continue
        snippet = _turn_snippet(row.get("body") or "", needle_lower)
        if snippet is None:
            continue
        per_doc_count[doc_id] = seen + 1
        items.append({
            "doc_id": doc_id,
            "type": "CH",
            "title": row.get("title"),
            "status": row.get("status"),
            "project_id": row.get("project_id"),
            "group_id": row.get("group_id"),
            "snippet": snippet,
            "matched_in": "body",
            "match_kind": "conversation_turn",
            "seq": int(row["seq"]),
            "speaker": row.get("speaker"),
            "display_name": row.get("display_name"),
            "created_at": row.get("created_at"),
        })
    return items
