"""Select prompt guidance from facts; this module never grants tool permissions."""
from __future__ import annotations

import logging
from dataclasses import dataclass

from modules.flow_gate.services.chat_help_content import help_item_url

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ChatSituation:
    can_write: bool = False
    unanswered: bool | None = None
    current_run: bool | None = None


def chat_situation(group_id, *, can_write=False):
    """Read stored state of the bound Flow only. Failed reads are not empty results."""
    unanswered = current_run = None
    if group_id:
        try:
            from modules.flow_gate.db import documents, questions
            rows = questions.list_open_doc_ids_by_group(group_id)
            if rows is not None:
                # Match the card-Q list: conversation questions are not card questions.
                docs = (documents.get_by_id(doc_id) for doc_id in rows)
                unanswered = any(doc and doc.get("type_code") != "CH" for doc in docs)
        except Exception:
            logger.warning("chat unanswered-Q guidance lookup failed", exc_info=True)
        try:
            from modules.flow_gate.graph_workflow.store import run_store
            current_run = run_store.current_run(group_id) is not None
        except Exception:
            logger.warning("chat current-run guidance lookup failed", exc_info=True)
    return ChatSituation(can_write=can_write is True, unanswered=unanswered, current_run=current_run)


_CONTROL_WRITE = {'place_flow_cards', 'start_flow_run', 'set_card_roles'}
_DOCUMENT_WRITE = {'revise_card_document', 'revise_document_by_id', 'create_new_document'}
_RECOVERY_WRITE = {'retry_flow_card', 'restart_flow_run'}
_RECOVERY = _RECOVERY_WRITE | {'read_flow_recovery_basis'}
_READ_DESCRIPTIONS = {
    'get_ai_options': 'Read available AI options and role groups for this Flow.',
    'get_card_roles': 'Read the AI roles and settings of a card owned by this chat.',
    'read_card_document': 'Read the full body, doc_id and revision_no of one card document. Report read errors honestly.',
    'read_document_by_id': 'Read an existing project document by doc_id, including content and revision_no. Never ask for card coordinates. Report read errors honestly.',
    'read_flow_recovery_basis': 'Read the Flow or card input basis. Unknown is not main/latest; only actual_basis is materialized.',
}


def _pointer(item, base):
    task = {
        'chat_control': 'placing cards, starting a Flow, or changing roles',
        'chat_documents': 'editing or creating documents',
        'chat_recovery': 'retrying a card or restarting a Flow',
        'chat_questions': 'answering card questions',
    }[item]
    return f'Before {task}, read GET {help_item_url(item, base)} (API: read_help item={item}).'


def _section(name, situation, base, token):
    root = base.rstrip('/')
    if root.endswith('/api/v1'):
        root = root[:-len('/api/v1')]
    chat = root + '/api/v2/chat'
    auth = f'Authorization: Bearer {token}'
    if name == 'get_ai_options':
        lines = ['## Card information', f'GET {chat}/flows/{{flow_id}}/ai-options',
                 f'GET {chat}/flows/{{flow_id}}/nodes/{{node_key}}/roles', auth]
        if situation.can_write:
            lines += [_pointer('chat_control', base)]
        return '\n'.join(lines)
    if name == 'read_card_document':
        lines = ['## Document read', f'GET {chat}/flows/{{flow_id}}/nodes/{{node_key}}/slots/{{slot}}/document',
                 f'GET {chat}/documents/{{doc_id}}', auth,
                 'Read the full content and revision_no. Report refusals and errors honestly.']
        if situation.can_write:
            lines += [_pointer('chat_documents', base),
                      'For edits send expected_revision from the latest read and complete content; re-read on conflict.']
        return '\n'.join(lines)
    if name == 'read_flow_recovery_basis':
        lines = ['## Flow input basis', f'GET {chat}/flows/{{flow_id}}/recovery-basis',
                 f'GET {chat}/flows/{{flow_id}}/nodes/{{node_key}}/basis', auth,
                 _READ_DESCRIPTIONS[name]]
        if situation.can_write:
            lines += [_pointer('chat_recovery', base)]
        return '\n'.join(lines)
    if name == 'list_card_questions':
        if situation.unanswered is False:
            return '\n'.join(['## Card question list', f'GET {chat}/questions', auth])
        return '\n'.join(['## Card questions (Q): list and answer', _pointer('chat_questions', base), auth,
                          'Answer BEFORE you submit your turn; submitting consumes this token. Reading mode may answer.'])
    return ''


def select_chat_guidance(tools, situation):
    """Copy descriptors, retaining handlers/schemas even when not advertised to the model."""
    selected = []
    for original in tools:
        tool = dict(original)
        name = tool['name']
        hidden = (not situation.can_write and name in (_CONTROL_WRITE | _DOCUMENT_WRITE | _RECOVERY_WRITE))
        hidden |= situation.current_run is False and name in _RECOVERY
        hidden |= situation.unanswered is False and name == 'answer_card_question'
        tool['prompt_visible'] = not hidden
        if name in _READ_DESCRIPTIONS:
            tool['description'] = _READ_DESCRIPTIONS[name]
            tool.pop('prompt_hint', None)
        item = ('chat_control' if name in _CONTROL_WRITE else
                'chat_documents' if name in _DOCUMENT_WRITE else
                'chat_recovery' if name in _RECOVERY_WRITE else None)
        if item:
            tool['description'] = f"{name}: before use, call read_help with item={item}. Report errors honestly."
            tool.pop('prompt_hint', None)
        # The old combined CLI sections live unchanged in detailed help.
        if name in (_CONTROL_WRITE | _DOCUMENT_WRITE | _RECOVERY | {'list_card_questions'}):
            tool.pop('cli_mention', None)
        if name in {'get_ai_options', 'read_card_document', 'read_flow_recovery_basis', 'list_card_questions'}:
            tool['cli_mention'] = lambda base, token, name=name: _section(name, situation, base, token)
        if name == 'list_card_questions':
            tool['description'] = 'List questions on this Flow; doc_id and include_answered can select historical questions.'
            if situation.unanswered is False:
                tool.pop('prompt_hint', None)
            else:
                tool['prompt_hint'] = ('Before answering card questions, call read_help with item=chat_questions. '
                                       'Answer before submitting the turn.')
        elif name == 'answer_card_question':
            tool['description'] = ('Answer a card question before submitting the turn. Reading mode may answer. '
                                   'Before use, call read_help with item=chat_questions; report refusals honestly.')
        selected.append(tool)
    return selected
