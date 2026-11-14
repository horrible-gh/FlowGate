"""L#2 §2.8: fail-closed body/path gates and transaction-bound storage grants."""
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
import logging
import os
import stat
import stat as stat_module
import time

from fastapi import HTTPException
from modules.flow_gate.db import connection, documents
from modules.flow_gate.graph_workflow.services import flow_guard
from modules.flow_gate.storage import paths as storage_paths
from . import target

BODY_COLUMNS = frozenset({'file_path', 'filename', 'revision_no', 'project_id', 'branch'})
_grant = ContextVar('card_document_write_grant', default=None)
_created = ContextVar('card_document_created_rows', default=())
_identity = ContextVar('card_document_identity_transfer', default=None)
_guard_keys = ContextVar('card_document_gate_guard_keys', default=frozenset())
_project_rows = ContextVar('card_document_gate_project_rows', default=None)
_namespace_tx = ContextVar('card_document_path_namespace_transaction', default=None)
_checked_paths = ContextVar('card_document_checked_paths', default=None)
_link_listings = ContextVar('card_document_gate_link_listings', default=None)


def lock_path_namespace():
    """Serialize path writes with INSERT/path attachment until the outer transaction ends.

    A reserved row in the existing settings table is a database-wide namespace mutex.
    The upsert takes a durable write lock on SQLite/PostgreSQL/MySQL, including for
    missing paths/documents. Acquire only after the operation's Flow/document guards;
    never acquire another in-process guard while holding this mutex.
    """
    tx = transaction()
    if tx is None:
        raise RuntimeError('path namespace requires a transaction')
    if _namespace_tx.get() is tx:
        return
    connection.get_store()._execute(
        "INSERT INTO system_settings (setting_key, setting_value, value_type, updated_at) "
        "VALUES (?, '', 'string', ?) ON CONFLICT(setting_key) DO UPDATE SET "
        "setting_value = excluded.setting_value",
        ['internal.card_document_path_namespace', connection.now_iso()])
    _namespace_tx.set(tx)
    def clear():
        if _namespace_tx.get() is tx:
            _namespace_tx.set(None)
    connection.after_commit(clear)
    connection.after_rollback(clear)


def path_key(path):
    return os.path.normcase(str(Path(path).resolve()))


def file_key(path):
    try:
        stat = Path(path).stat()
        return stat.st_dev, stat.st_ino
    except FileNotFoundError:
        return None


def path_index(paths):
    names, identities = set(), set()
    for path in paths:
        names.add(path_key(path))
        identity = file_key(path)
        if identity is not None:
            identities.add(identity)
    return names, identities



def refuse():
    raise HTTPException(status_code=409, detail='card_document_edit_required')


# flowgate.v02gui.0011 (0004-CH): internal housekeeping locations under the global storage root S0.
# The delete journal G (S0/.flowgate-gc, storage/filesystem.py) and the token / AI-run scratch roots
# (S0/work/<project>/tok_*, S0/scratch/<project>/aiv*_*) hold lock files, journal records and scratch
# trees, never a document. Yet every file operation of a scratch delete (lock open/unlink, journal
# write/remove, the tree delete: 5-6 per folder) walked every document row twice, the second time under
# the database-wide path mutex (~6 s a walk on the 9k-row stg store). A 6,593-folder backlog then took
# days, kept 1-2.6 cores busy after every restart and made chat starts wait on the same mutex.
# Such a target now skips the walk, but only while no document row can point into those roots.
INTERNAL_DIRS = ('.flowgate-gc', 'work', 'scratch')
# A row whose stored path mentions none of these cannot resolve into an internal root by its string
# ('..' climbs, '~' is an 8.3 short name); a row that does is resolved in full before it is cleared.
_INTERNAL_HINTS = ('.flowgate-gc', 'work', 'scratch', '..', '~')
_log = logging.getLogger(__name__)
_internal_warned = False


def _norm(path):
    return os.path.normcase(os.path.abspath(str(path)))


def _under(path, roots, *, equal=False):
    return any((equal and path == root) or path.startswith(root + os.sep) for root in roots)


def internal_only(paths=(), trees=()):
    """True when every target lies strictly inside an internal root and no document row can point there.

    Fail-closed: an error, a target outside (logically or once resolved), a hard-linked target file, or a
    document row that resolves into an internal root answers False, and the caller runs the full gate.
    """
    global _internal_warned
    targets = [p for p in (*paths, *trees) if p is not None]
    if not targets:
        return False
    try:
        s0 = Path(os.path.abspath(str(storage_paths.get_storage_root())))
        roots = set()
        for name in INTERNAL_DIRS:
            roots.add(_norm(s0 / name))
            roots.add(_norm(os.path.realpath(s0 / name)))
        roots = tuple(roots)
        for target in targets:
            if not (_under(_norm(target), roots) and _under(_norm(os.path.realpath(target)), roots)):
                return False
        for target in paths:
            if target is None:
                continue
            try:
                st = os.lstat(target)
            except FileNotFoundError:
                continue
            if stat.S_ISREG(st.st_mode) and st.st_nlink > 1:
                return False  # a hard link may share a document's content
        clauses = ' OR '.join(['LOWER(file_path) LIKE ?'] * len(_INTERNAL_HINTS))
        rows = connection.get_store()._fetch_all(
            "SELECT doc_id, project_id, branch, file_path FROM documents "
            f"WHERE file_path IS NOT NULL AND TRIM(file_path) <> '' AND ({clauses})",
            [f'%{hint}%' for hint in _INTERNAL_HINTS])
        project_roots = {}
        for doc in rows:
            for resolved in candidate_paths(doc, project_roots):
                if _under(_norm(resolved), roots, equal=True):
                    if not _internal_warned:
                        _internal_warned = True
                        _log.warning('write gate: document %s resolves into an internal storage root; '
                                     'internal paths take the full gate', doc.get('doc_id'))
                    return False
        return True
    except Exception:
        return False


def transaction():
    return getattr(connection._tx_local, 'txn', None)


def same(a, b):
    a, b = Path(a), Path(b)
    if os.path.normcase(str(a.resolve())) == os.path.normcase(str(b.resolve())):
        return True
    return a.exists() and b.exists() and os.path.samefile(a, b)


def candidates(doc, reference=None):
    value = doc.get('file_path') if reference is None else reference
    if not value:
        return []
    return list(storage_paths.storage_path_candidates(value, doc['project_id'], doc.get('branch') or 'main'))


def candidate_paths(doc, roots, resolve=None):
    """The resolved candidates of one row (as ``candidates``, without the jail) for a walk of every row.

    ``roots`` is the walk's memo: each project's storage root is read and resolved once per walk (0135 T#3/T#4);
    ``resolve`` the walk's shared resolver (``storage_paths.memo_resolver``).
    """
    project_id = doc['project_id']
    if project_id not in roots:
        roots[project_id] = storage_paths.get_storage_root(project_id).resolve()
    extra = {} if resolve is None else {'resolve': resolve}
    return storage_paths.storage_candidate_paths(doc.get('file_path'), roots[project_id], doc.get('branch') or 'main',
                                                 **extra)


def references(paths=(), trees=()):
    """Index normalized names and file identities once: O(documents + tree files).

    Containment checks walk a candidate's ancestors rather than comparing it with
    every file. The inode index additionally catches hard links outside the tree.

    flowgate.v02.0135 T#3/T#4: this walks every document row, twice per gated write (the
    second time under the path namespace mutex). It used to read the storage settings 4-8
    times and resolve each path ~14 times per row: on the stg store (9,090 rows, PostgreSQL)
    one walk took 237 s / 54,541 queries (29 s with the request cache), so every document
    creation (a chat, a placed card's document) outlived the GUI's 30 s timeout. Now each
    project's root is resolved once per walk, each candidate once, a file identity is read
    only when a target file exists (none can match otherwise), and the jail is asked only
    for a matched row. The matches are the same.
    Review rework: a walk was still ~6 s on a 9,106-row store on Windows (~75 % of it
    Path.resolve), several walks per creation: each parent directory is now resolved once
    per walk (storage_paths.memo_resolver); a matched row is resolved in full again for its
    result and jail answer.
    """
    paths = tuple(Path(p) for p in paths if p is not None)
    trees = tuple(Path(p).resolve() for p in trees if p is not None)
    if not paths and not trees:
        return []
    try:
        names, identities = path_index(paths)
        tree_names = {path_key(t) for t in trees}
        for root in trees:
            if root.is_dir():
                for path in root.rglob('*'):
                    if path.is_file():
                        identity = file_key(path)
                        if identity is not None:
                            identities.add(identity)
        store = connection.get_store()
        roots = {}
        target_names = None if trees else _name_filter(paths)
        clock = _WalkClock()
        if target_names is None:
            rows = store._fetch_all(_ROWS_SQL)
            clock.mark('sql', rows=len(rows))
        else:
            light = store._fetch_all(_WALK_SQL)
            clock.mark('sql', rows=len(light))
            row_links = _walk_link_listings()
            listed = len(row_links)
            light = [d for d in light if not _cannot_match(d, target_names, roots, row_links)]
            clock.mark('filter', dirs_listed=len(row_links) - listed)
            rows = _full_rows(store, light)
            clock.mark('full', walked=len(rows))
        resolve = storage_paths.memo_resolver()
        # Every target exists, each its own file of one link: a row matches exactly when one of its candidates is
        # that file (a path equal to a target's names the target itself), so a stat answers without resolving.
        by_identity = target_names is not None and len(identities) == len(paths)
        result = []
        for doc in rows:
            if by_identity and _identity_row(doc):
                matched = _identity_match(doc, roots, identities)
                if matched is not None:
                    resolved = matched.resolve(strict=False)  # a match: the exact path, as before
                    result.append((doc, storage_paths.within_allowed_roots(resolved, doc['project_id'])))
                continue
            for resolved in candidate_paths(doc, roots, resolve):
                if (os.path.normcase(str(resolved)) in names or
                        (tree_names and any(os.path.normcase(str(parent)) in tree_names
                                            for parent in (resolved, *resolved.parents))) or
                        (identities and file_key(resolved) in identities)):
                    resolved = resolved.resolve(strict=False)  # a match: the exact path, as before
                    result.append((doc, storage_paths.within_allowed_roots(resolved, doc['project_id'])))
                    break
        clock.mark('match', matched=len(result))
        clock.report('references', trees=bool(trees), filtered=target_names is not None)
        return result
    except Exception:
        refuse()


# 0001-B A, second pass (flowgate.v02gui.0012 0004-CH turn 49): after the name filter a card type switch on stg still
# took ~7 s (R<->B) and a save ~4.4 s: every walk kept a fixed cost -- the whole documents table (9,454 rows, ~13 MB
# with every column) and a listing of every row's directory (1,343) to rule out links -- and a switch walks 7-9 times.
# A filtered walk now reads only the four columns it decides on and the full rows of the rows it walks; the
# directory listings are shared by every walk of one transaction; and paths_scope no longer repeats a walk that
# already ran under the path namespace mutex. The rows found are the same.
_ROWS_SQL = "SELECT * FROM documents WHERE file_path IS NOT NULL AND TRIM(file_path) <> ''"
_WALK_SQL = ("SELECT doc_id, project_id, branch, file_path FROM documents "
             "WHERE file_path IS NOT NULL AND TRIM(file_path) <> ''")
_FULL_ROWS_CHUNK = 200
# A walk slower than this writes one timing line to logs/default.log (logger flowgate.write_gate.timing): the
# breakdown (database read, directory listings, full rows, matching) that 0001-B A could only estimate from outside.
SLOW_WALK_MS = 150
_timing_log = None


def _timing_logger():
    global _timing_log
    if _timing_log is None:
        from modules.flow_gate.services.route_logging import forward_to_file_log
        logger = logging.getLogger('flowgate.write_gate.timing')
        logger.setLevel(logging.INFO)
        _timing_log = forward_to_file_log(logger, logging.INFO)
    return _timing_log


class _WalkClock:
    """Per-phase milliseconds of one walk, logged only when the whole walk is slow (``SLOW_WALK_MS``)."""

    def __init__(self):
        self.start = self.last = time.perf_counter()
        self.parts = {}

    def mark(self, phase, **facts):
        now = time.perf_counter()
        self.parts[phase + '_ms'] = round((now - self.last) * 1000)
        self.parts.update(facts)
        self.last = now

    def report(self, what, **facts):
        total = round((time.perf_counter() - self.start) * 1000)
        if total < SLOW_WALK_MS:
            return
        try:
            fields = ' '.join(f'{k}={v}' for k, v in {**self.parts, **facts}.items())
            _timing_logger().info('write gate %s slow: total_ms=%s %s', what, total, fields)
        except Exception:  # noqa: BLE001 -- timing must never break a write
            pass


def _walk_link_listings():
    """The link listings of ``_cannot_match`` (per project, branch and stored parent), shared by every walk of the
    current transaction and dropped when it ends; outside a transaction, one walk's own.

    A write in progress makes regular files and directories only (a new file, its revision snapshot, an orphan moved
    aside), never a link, so a listing taken by an earlier walk of the same transaction still names every link.
    """
    tx = transaction()
    if tx is None:
        return {}
    held = _link_listings.get()
    if held is not None and held[0] is tx:
        return held[1]
    listings = {}
    _link_listings.set((tx, listings))

    def clear():
        current = _link_listings.get()
        if current is not None and current[0] is tx:
            _link_listings.set(None)
    connection.after_commit(clear)
    connection.after_rollback(clear)
    return listings


def _full_rows(store, light):
    """The full rows of the light rows a walk goes on with, in their order.

    A row deleted since the light read is gone; a row whose path emptied since is skipped like the full read skips
    it. A path attached in between is what the second walk under the namespace mutex is there to see.
    """
    ids = [d['doc_id'] for d in light]
    full = {}
    for start in range(0, len(ids), _FULL_ROWS_CHUNK):
        chunk = ids[start:start + _FULL_ROWS_CHUNK]
        for row in store._fetch_all(
                "SELECT * FROM documents WHERE doc_id IN (" + ', '.join('?' * len(chunk)) + ")", chunk):
            full.setdefault(row['doc_id'], row)
    return [full[i] for i in ids if i in full and (full[i].get('file_path') or '').strip()]


def _plain_name(name):
    """A file name only an alias with another name can reach: links and hard links aside, no other name resolves to
    it. Anything Windows may rewrite or alias (a trailing dot / space, an 8.3 short name, a stream), a dot segment
    and a non-ASCII name (no Unicode normalization here) is not plain."""
    return (bool(name) and name.isascii() and name not in ('.', '..') and name[-1] not in '. '
            and '~' not in name and ':' not in name)


def _name_filter(paths):
    """The casefolded names of the targets when a row can match them only through its own name, else None.

    0001-B A (flowgate.v02gui.0012): a walk still cost ~0.75-1.1 s for 9,000 rows on the Windows stg host, almost
    all of it building and resolving each row's candidate paths, ~4-7 walks per document write. A row whose file
    name differs from every target name can match only if it is a link (caught by ``_cannot_match``) or a hard link
    of a target (nlink > 1: then every row is walked, as before). A target that is itself a link, or whose name is
    not plain, walks every row too.
    """
    names = set()
    for path in paths:
        if not _plain_name(path.name):
            return None
        try:
            st = os.lstat(path)
        except FileNotFoundError:
            st = None
        if st is not None and (stat_module.S_ISLNK(st.st_mode) or st.st_nlink > 1
                               or getattr(st, 'st_reparse_tag', 0)):
            return None
        names.add(path.name.casefold())
    return names


def _cannot_match(doc, names, roots, row_links):
    """True when the row's file name is plain, is none of the target names and is no link in its directory.

    The link check lists each of the row's candidate directories once per walk (``row_links``, per project,
    branch and stored parent), so a link with another name towards a target (``links/link.md`` -> the target)
    is still walked and resolved as before.
    """
    stored = doc['file_path'].strip().replace('\\', '/').rstrip('/')
    head, _, name = stored.rpartition('/')
    branch = doc.get('branch') or 'main'
    if not _plain_name(name) or name.casefold() in names or name == branch:
        return False
    key = (doc['project_id'], branch, head)
    links = row_links.get(key)
    if links is None:
        project_id = doc['project_id']
        if project_id not in roots:
            roots[project_id] = storage_paths.get_storage_root(project_id).resolve()
        links = set()
        for candidate in storage_paths.storage_candidate_paths(stored, roots[project_id], branch,
                                                               resolve=lambda p: p):
            links |= _links_in(candidate.parent)
        row_links[key] = links
    return os.path.normcase(name) not in links


def _identity_row(doc):
    """A row whose candidates a stat can follow as resolve does: a plain name and no dot segment."""
    segments = doc['file_path'].strip().replace('\\', '/').rstrip('/').split('/')
    return _plain_name(segments[-1]) and not any(s in ('.', '..') for s in segments)


def _identity_match(doc, roots, identities):
    """The row's first candidate (in the walk's order) that is one of the target files, else None."""
    project_id = doc['project_id']
    if project_id not in roots:
        roots[project_id] = storage_paths.get_storage_root(project_id).resolve()
    for candidate in storage_paths.storage_candidate_paths(doc['file_path'], roots[project_id],
                                                           doc.get('branch') or 'main', resolve=lambda p: p):
        if file_key(candidate) in identities:
            return candidate
    return None


def _links_in(directory):
    """The normcased names of the links (symbolic links, junctions, other reparse points) in one directory."""
    found = set()
    try:
        with os.scandir(directory) as entries:
            for entry in entries:
                if entry.is_symlink() or getattr(entry, 'is_junction', lambda: False)():
                    found.add(os.path.normcase(entry.name))
                    continue
                if os.name == 'nt':
                    try:
                        if getattr(entry.stat(follow_symlinks=False), 'st_reparse_tag', 0):
                            found.add(os.path.normcase(entry.name))
                    except OSError:
                        found.add(os.path.normcase(entry.name))
    except OSError:
        pass  # a missing / unreadable directory holds no link we could follow
    return found


@dataclass
class Creation:
    doc_id: str
    tx: object
    path: object
    initialized: bool
    staged: bool = False


def creation(doc_id):
    tx = transaction()
    return next((c for c in _created.get() if tx is not None and c.tx is tx and c.doc_id == doc_id), None)


def create(data, call):
    with connection.get_store().transaction():
        lock_path_namespace()
        _checked_paths.set(None)  # a newly attached path invalidates a batch preflight
        return _create(data, call)


def _create(data, call):
    """Only a successful INSERT can mint CREATE; r0 and caller flags confer nothing."""
    if documents.get_by_id(data['doc_id']) is not None:
        refuse()
    locations = [p for p, inside in candidates(data) if inside]
    transfer = _identity.get()
    transferred_from = None
    if transfer is not None:
        old, new, tx, key = transfer
        if (new is not data or tx is not transaction() or key != flow_guard.held_guard()
                or documents.get_by_id(old['doc_id']) != old):
            refuse()
        transferred_from = old['doc_id']
    for path in locations:
        if any(d['doc_id'] != transferred_from for d, _ in references([path])):
            refuse()
    result = call()
    tx = transaction()
    if tx is not None and transferred_from is None:
        path = next((p for p in locations if p.is_file()), locations[0] if locations else None)
        proof = Creation(data['doc_id'], tx, path, bool(path and path.is_file()))
        _created.set((*[c for c in _created.get() if c.tx is tx], proof))
        def clear():
            _created.set(tuple(c for c in _created.get() if c is not proof))
        connection.after_commit(clear)
        connection.after_rollback(clear)
    return result


@dataclass(frozen=True)
class Grant:
    ctx: object
    tx: object
    doc_id: str
    target_path: object
    revision: int

    def valid(self, doc_id):
        if self.doc_id != doc_id or self.tx is not transaction() or self.tx is None:
            return False
        self.ctx.assert_held()
        return True


@contextmanager
def revision(ctx, inp):
    """Minted at the store, after its validated plan; never accepted from a transport."""
    ctx.assert_held()
    if ctx.doc_id != inp.current['doc_id']:
        refuse()
    if ctx.mode == 'CREATE':
        proof = creation(ctx.doc_id)
        if proof is None or proof.initialized:
            refuse()
    lock_path_namespace()  # includes the store CAS that attaches/repoints file_path
    _checked_paths.set(None)
    path = inp.built.target_path or inp.body.path
    token = _grant.set(Grant(ctx, transaction(), ctx.doc_id, path, inp.current['revision_no']))
    try:
        yield
    finally:
        _grant.reset(token)


def permitted(doc, path=None):
    grant = _grant.get()
    if grant is not None and grant.valid(doc['doc_id']):
        return path is None or (grant.target_path is not None and same(path, grant.target_path))
    proof = creation(doc['doc_id'])
    return proof is not None and (path is None or (
        not proof.initialized and proof.path is not None and same(path, proof.path) and not Path(path).exists()))


def require_none(doc):
    if target.classify_card_slot(doc) != target.NON_CARD:
        refuse()


@contextmanager
def identity_transfer(doc, new_row):
    key = flow_guard.document_guard_key(doc, doc['doc_id'])
    if transaction() is None or flow_guard.held_guard() != key:
        refuse()
    if new_row.get('file_path') != doc.get('file_path') or new_row.get('revision_no') != doc.get('revision_no'):
        refuse()
    token = _identity.set((doc, new_row, transaction(), key))
    try:
        yield
    finally:
        _identity.reset(token)


@contextmanager
def guard_scope(docs):
    if not docs:
        with connection.get_store().transaction():
            yield
        return
    keys = frozenset(flow_guard.document_guard_key(d, d['doc_id']) for d in docs)
    if transaction() is not None:
        held = _guard_keys.get() | ({flow_guard.held_guard()} if flow_guard.held_guard() else set())
        if not keys.issubset(held):
            project_rows = _project_rows.get()
            if not (project_rows and project_rows[0] is transaction()
                    and all(d.get('id') in project_rows[1] for d in docs)):
                refuse()
        for d in sorted(docs, key=lambda d: d['doc_id']):
            flow_guard.lock_document_row(d['doc_id'])
        yield
        return
    locks = []
    try:
        for key in sorted(keys):
            lock = (flow_guard._flow_lock(key[1]) if key[0] == 'flow' else
                    flow_guard._named_lock(flow_guard._doc_locks, key[1]))
            if not lock.acquire(timeout=flow_guard.FLOW_LOCK_WAIT_SEC):
                raise flow_guard.FlowLockTimeout(key[1])
            locks.append(lock)
            if key[0] == 'flow':
                flow_guard.layout_store.ensure_guard_row(key[1])
        with connection.get_store().transaction():
            for key in sorted(keys):
                if key[0] == 'flow' and flow_guard.layout_store.bump_guard(key[1]) == 0:
                    refuse()
            for doc in sorted(docs, key=lambda d: d['doc_id']):
                flow_guard.lock_document_row(doc['doc_id'])
            token = _guard_keys.set(keys)
            try:
                with flow_guard._holding(next(iter(keys)) if len(keys) == 1 else None):
                    yield
            finally:
                _guard_keys.reset(token)
    finally:
        for lock in reversed(locks):
            lock.release()
        from modules.flow_gate.graph_workflow.services.card_document_ports import document_write_released
        document_write_released()


def guarded(docs, body):
    with guard_scope(docs):
        return body()


@contextmanager
def paths_scope(paths, *, trees=()):
    paths, trees = tuple(paths), tuple(trees)
    if internal_only(paths, trees):
        yield  # an internal housekeeping file/tree, no document can be there (see internal_only)
        return
    names = frozenset(path_key(p) for p in paths)
    covered = _checked_paths.get()
    if covered and covered[0] is transaction() and not trees and names.issubset(covered[1]):
        yield
        return
    # Already under the path namespace mutex (a revision, a creation, an earlier gated write of this transaction):
    # no path can be attached until it ends, so the walk under the mutex below would find exactly these rows.
    locked = transaction() is not None and _namespace_tx.get() is transaction()
    initial = references(paths, trees)
    if len({d.get('group_id') for d, _ in initial if d.get('group_id')}) > 1:
        refuse()
    ungranted = [d for d, _ in initial if not permitted(d)]
    for d in ungranted:
        require_none(d)
    with guard_scope(ungranted):
        lock_path_namespace()
        fresh = initial if locked else references(paths, trees)
        if {d['doc_id'] for d, _ in fresh} - {d['doc_id'] for d, _ in initial}:
            refuse()
        for doc, inside in fresh:
            if not inside:
                refuse()
            if permitted(doc) and not trees:
                matching = [p for p in paths if any(same(p, c) for c, _ in candidates(doc))]
                if matching and all(permitted(doc, p) for p in matching):
                    continue
            require_none(doc)
        token = _checked_paths.set((transaction(), names))
        try:
            yield
        finally:
            _checked_paths.reset(token)
        for doc, _ in fresh:
            proof = creation(doc['doc_id'])
            if proof is not None and any(proof.path is not None and same(proof.path, p) for p in paths):
                proof.initialized = True


def paths(paths, call, *, trees=()):
    with paths_scope(paths, trees=trees):
        return call()


def row(doc_id, updates, call):
    if not BODY_COLUMNS.intersection(updates):
        return call()  # status/review/relations/meta are deliberately outside this gate
    doc = documents.get_by_id(doc_id)
    if doc is None:
        return call()
    def checked():
        lock_path_namespace()
        _checked_paths.set(None)
        fresh = documents.get_by_id(doc_id)
        if fresh is None:
            return call()
        grant = _grant.get()
        if grant is not None and grant.valid(doc_id):
            # Generic UPDATE is not the common store's CAS.
            refuse()
        destination_doc = {**fresh, **updates}
        proof = creation(doc_id)
        if proof is not None:
            if 'revision_no' in updates and updates['revision_no'] != 0:
                refuse()
            value = updates.get('file_path', fresh.get('file_path'))
            resolved = [p for p, inside in candidates(destination_doc, value) if inside]
            if proof.path is not None and not any(same(proof.path, p) for p in resolved):
                refuse()
            if any(d['doc_id'] != doc_id for p in resolved for d, _ in references([p])):
                refuse()
            if proof.path is None and resolved:
                proof.path = resolved[0]
        else:
            require_none(fresh)
        if {'file_path', 'project_id', 'branch'}.intersection(updates) and destination_doc.get('file_path'):
            for p, inside in candidates(destination_doc):
                if not inside:
                    refuse()
                for other, _ in references([p]):
                    if other['doc_id'] != doc_id:
                        require_none(other)
        return call()
    if creation(doc_id) is not None:
        return checked()
    require_none(doc)
    return guarded([doc], checked)


def stage(doc, call, *, relative=None):
    fresh = documents.get_by_id(doc['doc_id']) or doc
    proof = creation(doc['doc_id'])
    if proof is not None:
        locations = [p for p, inside in candidates(fresh, relative) if inside]
        if (proof.staged or proof.initialized or proof.path is None
                or not any(same(proof.path, p) for p in locations)):
            refuse()
        result = call()
        proof.staged = True
        return result
    require_none(fresh)
    def checked():
        require_none(documents.get_by_id(doc['doc_id']) or doc)
        return call()
    return guarded([fresh], checked)


def project(project_id, call):
    root = storage_paths.project_root(project_id)
    references_before = [doc for doc, _ in references(trees=[root])]
    docs = connection.get_store()._fetch_all('SELECT * FROM documents WHERE project_id = ?', [project_id])
    docs = list({d['doc_id']: d for d in [*docs, *references_before]}.values())
    for doc in docs:
        require_none(doc)
    with guard_scope(docs):
        lock_path_namespace()
        fresh = [doc for doc, _ in references(trees=[root])]
        if {d['doc_id'] for d in fresh} - {d['doc_id'] for d in docs}:
            refuse()
        for doc in fresh:
            require_none(doc)
        for doc in connection.get_store()._fetch_all('SELECT * FROM documents WHERE project_id = ?', [project_id]):
            require_none(doc)
        token = _project_rows.set((transaction(), frozenset(d['id'] for d in docs)))
        try:
            return call()
        finally:
            _project_rows.reset(token)


def module_rename(group_ids, docs, call):
    """Hold every Flow of a module (and its non-card documents) for a module key rename (v02gui.0012).

    The rename moves every document of the module — card documents included, which no other gate lets a
    non-card writer touch — so it takes what each of those writers takes, all at once: the Flow lock and guard
    row of every group (no run, card work or card document save starts meanwhile), the document lock of each
    non-card document, the row locks, then the path namespace. ``call`` runs inside that one transaction.
    Locks are taken in sorted order like ``guard_scope``; in-process locks first, never inside a transaction.
    """
    if transaction() is not None:
        refuse()
    flows = sorted(set(group_ids) | {d['group_id'] for d in docs
                                     if flow_guard.document_guard_key(d, d['doc_id'])[0] == 'flow'})
    loose = sorted(d['doc_id'] for d in docs if flow_guard.document_guard_key(d, d['doc_id'])[0] != 'flow')
    locks = []
    try:
        for flow_id in flows:
            lock = flow_guard._flow_lock(flow_id)
            if not lock.acquire(timeout=flow_guard.FLOW_LOCK_WAIT_SEC):
                raise flow_guard.FlowLockTimeout(flow_id)
            locks.append(lock)
            flow_guard.layout_store.ensure_guard_row(flow_id)
        for doc_id in loose:
            lock = flow_guard._named_lock(flow_guard._doc_locks, doc_id)
            if not lock.acquire(timeout=flow_guard.FLOW_LOCK_WAIT_SEC):
                raise flow_guard.FlowLockTimeout(doc_id)
            locks.append(lock)
        with connection.get_store().transaction():
            for flow_id in flows:
                if flow_guard.layout_store.bump_guard(flow_id) == 0:
                    refuse()
            for doc in sorted(docs, key=lambda d: d['doc_id']):
                flow_guard.lock_document_row(doc['doc_id'])
            token = _guard_keys.set(frozenset([('flow', f) for f in flows] + [('document', d) for d in loose]))
            try:
                lock_path_namespace()
                return call()
            finally:
                _guard_keys.reset(token)
    finally:
        for lock in reversed(locks):
            lock.release()
        from modules.flow_gate.graph_workflow.services.card_document_ports import document_write_released
        document_write_released()


def dispatch(operation, *args, **kwargs):
    if operation == "inventory":
        from .inventory_gate import dispatch as inventory
        return inventory(*args, **kwargs)
    return {'paths': paths, 'row': row, 'stage': stage, 'none': require_none,
            'create': create, 'project': project, 'paths_scope': paths_scope,
            'module_rename': module_rename}[operation](*args, **kwargs)
