"""Flow-scoped Markdown attachment links; no download authorization is granted here."""
from __future__ import annotations

import base64
import binascii
import re

from modules.flow_gate.documents import document_service
from modules.flow_gate.documents.attachments import flow


def link_keys(body: str) -> list[str]:
    """Extract inline Markdown destinations, excluding fenced and inline code.

    Keep code labels intact: [`file`](fg-attachment:key) is still a link.
    Plain scheme text, image syntax and escaped link openers are not links.
    """
    if 'fg-attachment:' not in (body or ''):
        return []
    lines = []
    fence = None
    for line in (body or '').splitlines(keepends=True):
        match = re.match(r'^ {0,3}(`{3,}|~{3,})(.*)$', line.rstrip('\r\n'))
        if fence:
            if match and match[1][0] == fence[0] and len(match[1]) >= len(fence) and not match[2].strip():
                fence = None
            lines.append('\n')
        elif match and (match[1][0] != '`' or '`' not in match[2]):
            fence = match[1]
            lines.append('\n')
        else:
            lines.append(line)
    text = ''.join(lines)
    # Code spans must close with a run of exactly the same length.
    span = re.compile(r'(?<!`)(`+)(?!`)(.*?)(?<!`)\1(?!`)', re.S)
    text = span.sub(lambda m: ' ' * len(m[0]), text)
    destination = re.compile(r'\(\s*(?:<fg-attachment:([^\s<>]*)>|fg-attachment:([^\s<>)]*))(?:\s+["\'][^\n]*?["\'])?\s*\)')
    stack, keys = [], []
    i = 0
    while i < len(text):
        if text[i] == '\\':
            i += 2
            continue
        if text[i] == '[':
            stack.append(i > 0 and text[i - 1] == '!')
        elif text[i] == ']' and stack:
            is_image = stack.pop()
            match = destination.match(text, i + 1)
            if match:
                if not is_image:
                    key = match[1] if match[1] is not None else match[2]
                    if key not in keys:
                        keys.append(key)
                i = match.end()
                continue
        i += 1
    return keys


def unknown_keys(doc: dict, body: str) -> list[str]:
    keys = link_keys(body)
    if not keys:
        return []
    present = {r['key'] for r in flow.rows(doc.get('group_id'))} if doc.get('group_id') else set()
    return [key for key in keys if key not in present]


def statuses(doc_id: str, body: str) -> list[dict]:
    keys = link_keys(body)
    if not keys:
        return []
    doc = document_service.get_document(doc_id)
    if not doc or not doc.get('group_id'):
        return []
    rows = flow.rows(doc['group_id'])
    by_key = {r['key']: r for r in rows}
    result = []
    for key in keys:
        row = by_key.get(key)
        name = None
        replacement = None
        if not row:
            # An old namespace key carries the original name. Never use it until
            # its document has been checked against the conversation's scope.
            try:
                source_id, name = base64.b64decode(key + '=' * (-len(key) % 4), altchars=b'-_', validate=True).decode().split('\n', 1)
                source = document_service.get_document(source_id)
                if not source or (source.get('group_id'), source.get('project_id')) != (doc.get('group_id'), doc.get('project_id')):
                    name = None
                if name:
                    candidates = [r for r in rows if r['filename'] == name]
                    if len(candidates) == 1:
                        replacement = candidates[0]
            except (ValueError, UnicodeError, binascii.Error):
                name = None
        current = row or replacement
        result.append({
            'key': key,
            'status': 'available' if row else ('replaced' if replacement else 'deleted'),
            'name': current['display_name'] if current else name,
            'size': current['size'] if current else None,
            'replacement_key': replacement['key'] if replacement else None,
        })
    return result
