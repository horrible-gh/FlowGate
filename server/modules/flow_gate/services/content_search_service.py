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

# doc_id -> (mtime, original_text, lowered_text, frontmatter_prefix), LRU first.
#
# 0370 L0003 §2-7: the cached body is frontmatter-stripped, but a match locator must be
# reported in *file* coordinates (P0002 §1-2 — one coordinate system, the stored file).
# Caching the stripped prefix is all that is needed to rebuild the file text, and it is a
# few hundred bytes rather than a third full copy of the body.
#
# 0279 P3-11: this cache was an unbounded plain dict. A facet-less body search walks
# every candidate document (see the "Limitation (honest)" note above), so one such
# search admitted the *entire corpus* — and each entry holds the body twice, the
# original text plus its lowercased copy, so the resident cost is roughly 2× the
# total size of all document bodies and it never came back down. Nothing evicted:
# `reset_cache()` is only called by tests. On a long-lived server with a growing
# corpus that is a monotonic climb into memory pressure, which is exactly the kind
# of slow-onset "it freezes sometimes" R0001 is chasing.
#
# An LRU cap makes the footprint predictable. The cache is a pure optimisation
# keyed on mtime — a miss re-reads the file and is correct, just slower — so
# eviction can never produce a wrong result, only a re-read.
_CACHE_MAX_ENTRIES = 512
_CACHE: "OrderedDict[str, tuple[float, str, str, str]]" = OrderedDict()
_LOCK = threading.Lock()

_SNIPPET_BEFORE = 40
_SNIPPET_AFTER = 120
_WS = re.compile(r"\s+")
# The document "header" the reviewer means is the leading YAML frontmatter block —
# the ``---`` … ``---`` fence carrying project/module/group/doc_number/title/… that
# every stored document file begins with. The detail snippet (and the body match)
# must be drawn from the *body* below that fence so the header never shows in the
# search detail row and the row starts from the real content
# (group 0123 rev6: "do not print the document header in the detail pane; start from the content").
# This mirrors the in-app viewer's ``stripFrontmatter`` (client shared/utils/markdown.ts)
# exactly, so search and the open document agree on where the body begins.
#
# rev4 mistakenly removed whole ATX markdown heading lines (``# ``…``###### ``) from
# the snippet, reading "the document header" as a ``#`` markdown heading.
# rev6 clarified the header is the leading YAML ``---`` frontmatter block, not a ``#``
# heading: a ``## 2. Resources`` section title is *body content*. Dropping heading lines
# meant a search term that lives only in a section title (very common in these
# structured docs) matched the row but produced *no* snippet — the body excerpt came
# and went depending on whether the hit was prose or a heading (group 0123 rev7:
# "content appears and disappears; even documents with a body do not show up"). So the heading *text*
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
    literal report: "even documents that have a body do not show up in search" (group 0123 rev8).
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


def _get_cached_ex(doc: dict) -> Optional[tuple[str, str, str]]:
    """Return ``(body_text, lowered_body, frontmatter_prefix)`` for a document, or None.

    Resolves the stored path, stats it for the mtime change-signal, and reads the
    file only when the cache is cold or stale. None when the document has no file,
    the path does not resolve inside the storage jail, or the read fails.

    ``frontmatter_prefix + body_text`` is exactly the canonical file text, so a caller
    that needs file coordinates (0370, set 2) can rebuild it without a second read.
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
            return cached[1], cached[2], cached[3]

    try:
        raw = resolved.read_text(encoding="utf-8")
    except OSError:
        return None
    # Cache the body with the leading YAML frontmatter (the document header) removed,
    # so both the body-match test and the snippet operate on the content below the
    # fence — the header never reaches the detail row and a metadata-only term in the
    # frontmatter does not masquerade as a body hit (group 0123 rev6).
    # 0370 L0003 §2-1: normalise to the same canonical text the outline/section endpoints
    # compute on (BOM dropped, newlines folded to ``\n``). Without this the search would
    # be able to disagree with /outline about a character offset on a BOM-carrying file.
    from modules.flow_gate.services import document_outline_service as _outline

    raw = _outline.canonical_text(raw)
    text = _strip_frontmatter(raw)
    frontmatter_prefix = raw[: len(raw) - len(text)]
    lowered = text.lower()
    with _LOCK:
        _CACHE[doc_id] = (mtime, text, lowered, frontmatter_prefix)
        _CACHE.move_to_end(doc_id)
        while len(_CACHE) > _CACHE_MAX_ENTRIES:
            _CACHE.popitem(last=False)  # evict least recently used
    return text, lowered, frontmatter_prefix


def _get_cached(doc: dict) -> Optional[tuple[str, str]]:
    """``(body_text, lowered_body)`` — the two-value view every pre-0370 caller uses."""
    cached = _get_cached_ex(doc)
    return None if cached is None else (cached[0], cached[1])


def _snippet(text: str, needle: str) -> Optional[str]:
    """Build a whitespace-collapsed excerpt around the first body match, with ellipses.

    ``text`` is already the document body with the YAML frontmatter header removed
    (see ``_strip_frontmatter`` in ``_get_cached``). The excerpt is drawn from the
    *whole* body — a markdown section heading (``## 2. Resources ...``) is body content, so a
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
    — the search the explorer runs when "search inside contents" is *off*) uses so that every
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



# ── 0370 set 2: search-hit positions and context lines (P0002 scenarios 9-11 / L0003 §2-7, §2-8) ──
#
# The existing `snippet`, `matched_in` and `match_kind` computations are untouched. `matches` is
# added alongside them (P0002 §3) — screens using this response today need no change.


def _find_matches(body_text: str, query: str, scan_max: int) -> list[tuple[int, int]]:
    """Locate every occurrence of ``query`` in ``body_text`` (case-insensitive).

    Deliberately NOT ``lowered.find(needle)`` the way ``_snippet`` does. The snippet only
    slices text around the hit so a drift never shows, but a locator publishes the number:
    Unicode has characters whose lowercase form is *longer* (``İ`` → ``i̇``, 1 char → 2), so
    one such character earlier in the document shifts every subsequent offset. Searching the
    original text keeps the returned positions true to the file (L0003 §2-7).

    The query is literal, not a regex — ``re.escape`` neuters the metacharacters, mirroring
    the LIKE-escaping the SQL side already does. Overlapping hits are not counted twice
    (``finditer`` resumes after each match), and scanning stops at ``scan_max`` so one very
    common word in one very large document cannot make a single search walk forever.
    """
    if not query:
        return []
    pattern = re.compile(re.escape(query), re.IGNORECASE)
    out: list[tuple[int, int]] = []
    for m in pattern.finditer(body_text):
        out.append((m.start(), m.end()))
        if len(out) >= scan_max:
            break
    return out


def _match_entry(fdoc, doc_id: str, revision_no: int, char_start: int, char_end: int,
                 context_lines: int) -> dict:
    """One entry of ``matches``: where the hit is, plus the line it is on and its neighbours.

    ``fdoc`` is the *file* text (frontmatter included) so the locator agrees with
    ``/outline`` and ``/section``. Missing lines at the top/bottom of a document are simply
    absent — they are never padded with empty strings (P0002 scenario 9).
    """
    from modules.flow_gate.services import document_outline_service as _outline

    line_start = fdoc.line_containing(char_start)
    line_end = fdoc.line_containing(max(char_start, char_end - 1))
    enclosing = _outline.enclosing_section(fdoc.items, line_start)
    before = fdoc.lines[max(0, line_start - 1 - context_lines): line_start - 1]
    after = fdoc.lines[line_end: min(fdoc.document_lines, line_end + context_lines)]
    text = fdoc.lines[line_start - 1] if 1 <= line_start <= fdoc.document_lines else ""
    return {
        "locator": _outline.build_locator(
            fdoc, doc_id, revision_no, line_start, line_end, enclosing
        ),
        "match_char_start": char_start,
        "match_char_end": char_end,
        "before": [_outline.clip_line(line) for line in before],
        "text": _outline.clip_line(text),
        "after": [_outline.clip_line(line) for line in after],
    }


def _document_matches(doc: dict, needle: str, matched_in: str,
                      context_lines: int, hits_per_doc: int) -> tuple[list[dict], int]:
    """``(matches, match_total)`` for one document-body result row.

    A hit that came from the title or the doc_id has no place in the body, so no place is
    invented: both values come back empty. Forcing it to point at line 1 would open the
    wrong spot when someone clicks the result (P0002 scenario 10).
    """
    from modules.flow_gate.services import document_outline_service as _outline

    if matched_in != "body":
        return [], 0
    cached = _get_cached_ex(doc)
    if cached is None:
        return [], 0
    body_text, _lowered, frontmatter_prefix = cached
    spans = _find_matches(body_text, needle, _outline.MATCH_SCAN_MAX)
    if not spans:
        return [], 0
    fdoc = _outline.DocumentText(frontmatter_prefix + body_text)
    doc_id = doc.get("doc_id") or ""
    revision_no = int(doc.get("revision_no", 0) or 0)
    entries = [
        _match_entry(
            fdoc, doc_id, revision_no,
            fdoc.body_char_to_file(start), fdoc.body_char_to_file(end), context_lines,
        )
        for start, end in spans[:hits_per_doc]
    ]
    return entries, len(spans)


def _format_turn(row: dict) -> str:
    """``(turn number/speaker) body`` — one neighbouring turn, collapsed and clipped."""
    from modules.flow_gate.services import document_outline_service as _outline

    who = (row.get("display_name") or "").strip() or (row.get("speaker") or "")
    body = _WS.sub(" ", row.get("body") or "").strip()
    if len(body) > _outline.TURN_CONTEXT_CHARS:
        body = body[: _outline.TURN_CONTEXT_CHARS] + "…"
    return f"({row.get('seq')}/{who}) {body}"


def _turn_matches(row: dict, needle: str, context_lines: int,
                  hits_per_doc: int) -> tuple[list[dict], int]:
    """``(matches, match_total)`` for one conversation-turn result row (L0003 §2-8).

    A turn has no lines that mean anything, so ``unit`` is ``turn`` and the context is
    neighbouring *turns*, not neighbouring lines. Neighbours are the nearest sequence
    numbers that actually exist rather than ``seq ± 1`` — turns are append-only, but a gap
    would otherwise silently produce a blank line.
    """
    from modules.flow_gate.services import document_outline_service as _outline

    body = row.get("body") or ""
    spans = _find_matches(body, needle, _outline.MATCH_SCAN_MAX)
    if not spans:
        return [], 0
    doc_id = row.get("doc_id") or ""
    seq = int(row.get("seq") or 0)
    revision_no = int(row.get("doc_revision_no", 0) or 0)
    neighbours = min(max(0, context_lines), _outline.TURN_CONTEXT_TURNS_MAX)
    before: list[str] = []
    after: list[str] = []
    if neighbours:
        try:
            prevs = turn_store.fetch_turns_before(doc_id, seq, neighbours)
            before = [_format_turn(t) for t in reversed(prevs)]
            after = [_format_turn(t) for t in turn_store.fetch_turns_after(doc_id, seq, neighbours)]
        except Exception:  # noqa: BLE001 — unreadable neighbouring turns must not kill the search result
            before, after = [], []
    locator = _outline.build_turn_locator(doc_id, revision_no, seq, 0, len(body))
    entries = [
        {
            "locator": locator,
            "match_char_start": start,
            "match_char_end": end,
            "before": before,
            "text": _outline.clip_line(body),
            "after": after,
        }
        for start, end in spans[:hits_per_doc]
    ]
    return entries, len(spans)


def search_document_bodies(
    q: str,
    project: str = None,
    doc_type: str = None,
    status: str = None,
    limit: int = 50,
    offset: int = 0,
    include_matches: bool = True,
    context_lines: int = None,
    hits_per_doc: int = None,
) -> tuple[list[dict], int]:
    """Full document search (body + title + doc_id) with optional metadata facets.

    A document matches when the query appears in its body, title, or doc_id (a
    superset of the Phase 1 metadata search). Body matches carry a ``snippet`` and
    ``matched_in="body"``; otherwise the snippet is None and ``matched_in`` names the
    field that matched. Returns ``(page_items, total_matches)`` where total ignores
    limit/offset for paging. Ordering follows the DB helper (updated_at DESC).

    0370 set 2: when ``include_matches`` is on, every row of the *returned page* also
    carries ``match_total`` and ``matches`` (P0002 scenario 9). The locators are computed
    for the page only — the match scan re-walks each body, and doing that for every
    candidate rather than the ~50 rows actually returned would make a facet-less search
    pay it corpus-wide. ``include_matches=False`` omits **both keys entirely**, giving
    byte-identical output to the pre-0370 response.
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
        matches.append((doc, matched_in, _item(doc, snippet, matched_in)))

    total = len(matches)
    page = matches[offset:offset + limit] if offset < total else []
    if not include_matches:
        return [item for _doc, _matched_in, item in page], total

    from modules.flow_gate.services import document_outline_service as _outline

    ctx = _outline.CONTEXT_LINES_DEFAULT if context_lines is None else context_lines
    ctx = max(0, min(int(ctx), _outline.CONTEXT_LINES_MAX))
    per_doc = _outline.HITS_PER_DOC_DEFAULT if hits_per_doc is None else hits_per_doc
    per_doc = max(1, min(int(per_doc), _outline.HITS_PER_DOC_MAX))
    items: list[dict] = []
    for doc, matched_in, item in page:
        entries, match_total = _document_matches(doc, needle, matched_in, ctx, per_doc)
        item["match_total"] = match_total
        item["matches"] = entries
        items.append(item)
    return items, total


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
    include_matches: bool = True,
    context_lines: int = None,
    hits_per_doc: int = None,
) -> list[dict]:
    """Conversation-turn body search (T4, L0004 §2-15 / P0003 scenario 16).

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
        "d.group_id AS group_id, d.revision_no AS doc_revision_no "
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
        item = {
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
        }
        if include_matches:
            from modules.flow_gate.services import document_outline_service as _outline

            ctx = _outline.CONTEXT_LINES_DEFAULT if context_lines is None else context_lines
            ctx = max(0, min(int(ctx), _outline.CONTEXT_LINES_MAX))
            hits = _outline.HITS_PER_DOC_DEFAULT if hits_per_doc is None else hits_per_doc
            hits = max(1, min(int(hits), _outline.HITS_PER_DOC_MAX))
            entries, match_total = _turn_matches(row, needle, ctx, hits)
            item["match_total"] = match_total
            item["matches"] = entries
        items.append(item)
    return items
