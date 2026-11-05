"""Current-revision author evidence, L#2 §2.10 (including the Q552 chat decision)."""
import json
import re
from .contracts import EVENT_SCHEMA

UNKNOWN = {'author': 'unknown', 'provider_id': None}
PERSON = {'author': 'person', 'provider_id': None}


def current(doc, rows, token_author):
    """None means legacy schema only. A malformed new event is authoritative unknown."""
    selected = []
    for row in rows:
        if row.get('event_type') != 'doc_edited':
            continue
        raw = row.get('metadata')
        try:
            meta = raw if isinstance(raw, dict) else json.loads(raw or '{}')
        except (TypeError, ValueError):
            if (str(row.get('document_id')) == str(doc.get('id'))
                    and isinstance(raw, str) and re.search(r'"schema"\s*:\s*"card_document_edit/v1"', raw)):
                selected.append(None)
            continue
        if not isinstance(meta, dict):
            continue
        belongs = meta.get('doc_id') == doc['doc_id'] or str(row.get('document_id')) == str(doc.get('id'))
        if not belongs or meta.get('schema') != EVENT_SCHEMA:
            continue
        revision = meta.get('revision_no')
        if type(revision) is int and revision != doc.get('revision_no'):
            continue
        if (meta.get('schema') != EVENT_SCHEMA or meta.get('doc_id') != doc['doc_id']
                or type(revision) is not int or revision != doc.get('revision_no')
                or str(row.get('document_id')) != str(doc.get('id'))):
            selected.append(None)
        else:
            selected.append(meta)
    if not selected:
        return None
    if len(selected) != 1 or selected[0] is None:
        return dict(UNKNOWN)
    meta = selected[0]
    origin, actor = meta.get('origin'), meta.get('actor_kind')
    if origin in {'designer', 'wp_window', 'wp_restore', 'tr2_window'} and actor == 'person':
        return dict(PERSON)
    if origin == 'agent' and actor == 'worker' and isinstance(meta.get('token_id'), str) and meta['token_id']:
        return token_author(meta['token_id']) or dict(UNKNOWN)
    return dict(UNKNOWN)
