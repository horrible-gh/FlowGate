"""Pure text contract for the last instruction in a FlowGate invocation.

Builders carry a small JSON record, not a rendered block. The record survives
ordinary string joins and persisted retry/carryover prompts. Only copy/provider
handoffs consume it. Already delivered blocks are recognized by the full owned
five-line grammar (never by a heading alone), so reusing a copied prompt is safe.
No provider, database, token or permission decisions belong in this module.
"""
from __future__ import annotations

import json

_MARKER = '<!-- flowgate-terminal-task:v1 '
_END = ' -->'

# Each row has the same semantic fields in ko/en/ja. Only this module owns them.
_WORDS = {
    'header': ('## 지금 할 일', '## What to do now', '## 今やること'),
    'task': ('할 일: ', 'Task: ', '作業: '),
    'before': ('완료 전: ', 'Before completion: ', '完了前: '),
    'once': ('제출·완료: 필요한 일을 마친 뒤 마지막에 한 번만 한다.', 'Submit/complete: once, at the end, after the required work.', '提出・完了: 必要な作業を終えた後、最後に一度だけ行う。'),
    'format': ('마지막 형식: ', 'Final format: ', '最後の形式: '),
    'none': ('추가 선행 작업 없음', 'No additional prerequisites', '追加の前提作業なし'),
    'chat': ('마지막 사용자 메시지에 답한다', 'Answer the last user message', '最後のユーザーメッセージに答える'),
    'new': ('이 단계의 새 카드 결과물을 작성한다', 'Write the new result for this card stage', 'この段階の新しいカード成果物を書く'),
    'first_write': ('기존 빈 카드의 본문을 처음 작성한다', 'Write the first body of the existing empty card', '既存の空カードの本文を初めて書く'),
    'edit': ('현재 카드 문서를 요청대로 수정한다', 'Revise the current card document as requested', '現在のカード文書を依頼どおり修正する'),
    'rework': ('반려 지적을 반영해 카드 문서를 수정한다', 'Rework the card document from the rejection findings', '差し戻し指摘を反映してカード文書を修正する'),
    'review': ('이 카드 결과물을 검수한다', 'Review this card result', 'このカード成果物をレビューする'),
    'preflight': ('다음 단계의 진행 조건을 검토한다', 'Review the prerequisites of the next stage', '次の段階の進行条件を確認する'),
    'q_answer': ('지정된 카드 질문 하나에 답한다', 'Answer the one assigned card question', '指定されたカードの質問一件に答える'),
    'workflow': ('이 문서의 작업 순서를 결정한다', 'Decide the workflow for this document', 'この文書の作業順序を決める'),
    'sequence': ('대기 중인 작업 순서를 수정한다', 'Edit the pending workflow sequence', '待機中の作業順序を修正する'),
    'work_plan': ('WP의 작업 범위를 채운다', 'Fill the WP work scope', 'WPの作業範囲を埋める'),
    'merge_review': ('병합 검토 질문에 근거를 들어 답한다', 'Answer the merge review question with evidence', 'マージレビューの質問に根拠を示して答える'),
    'conflict': ('지정된 병합 충돌을 해결한다', 'Resolve the assigned merge conflict', '指定されたマージ競合を解決する'),
    'repair': ('테스트 실행 준비 실패를 고친다', 'Repair the failed test-run setup', 'テスト実行の準備失敗を修正する'),
    'failure_origin': ('시험 실패의 원인을 분류한다', 'Classify the origin of the test failure', 'テスト失敗の原因を分類する'),
    'test_run': ('승인된 TS의 테스트 실행을 요청한다', 'Request execution of the approved TS', '承認済みTSのテスト実行を要求する'),
    'test_spec': ('승인된 TS의 실제 검증 결과를 등록한다', 'Register actual verification results for the approved TS', '承認済みTSの実際の検証結果を登録する'),
    'next_step': ('위에 지정된 다음 단계의 결과물을 만든다', 'Produce the next-stage result specified above', '上記で指定された次の段階の成果物を作る'),
    'generic': ('위에서 지정한 현재 작업을 수행한다', 'Perform the current task specified above', '上記で指定された現在の作業を行う'),
    'references': ('필요한 참조와 실제 근거 확인', 'Check required references and actual evidence', '必要な参照と実際の根拠を確認'),
    'body': ('전체 본문 준비와 저장 내용 확인', 'Prepare the complete body and verify what will be saved', '完全な本文を準備し保存内容を確認'),
    'header_keep': ('기존 문서 헤더 유지', 'Preserve the existing document header', '既存の文書ヘッダーを維持'),
    'rejection': ('반려 항목별 응답', 'Address each rejection finding', '差し戻し項目への応答'),
    'q_required': ('이번 요청에 필요한 미답 Q를 먼저 답변', 'Answer outstanding Q needed for this request first', '今回の依頼に必要な未回答Qに先に回答'),
    'q_unknown': ('미답 Q를 조회하고 이번 요청에 필요한 답변 먼저 처리', 'Check outstanding Q and handle answers needed for this request first', '未回答Qを確認し今回の依頼に必要な回答を先に処理'),
    'q_basis': ('과거 답변·근거·사용자 질의 방침 확인', 'Check past answers, evidence and the user question policy', '過去の回答・根拠・ユーザー質問方針を確認'),
    'review_basis': ('요구사항·실제 변경 점검', 'Check requirements and actual changes', '要件・実際の変更を確認'),
    'review_answers': ('전달된 Q 답변의 반영 여부 점검', 'Check whether the supplied Q answers are reflected', '渡されたQ回答が反映されているか確認'),
    'rework_answers': ('전달된 Q 답변 반영', 'Apply the supplied Q answers', '渡されたQ回答を反映'),
    'locked': ('보호된 항목과 기존 완료 계약 확인', 'Check protected items and the existing completion contract', '保護項目と既存の完了契約を確認'),
    'results': ('검증을 끝까지 실행하고 실제 결과 확인', 'Finish verification and inspect the actual results', '検証を最後まで実行し実際の結果を確認'),
    'turn_format': ('턴 body·based_on_seq 계약 준수', 'Follow the turn body/based_on_seq contract', 'ターンbody・based_on_seq契約を守る'),
    'probe': ('제출 body/content 마지막 줄에 지정된 probe 유지', 'Keep the assigned probe on the last line of submitted body/content', '提出body/contentの最終行に指定probeを維持'),
    'commit': ('소스 변경 시 commit_message 포함', 'Include commit_message for source changes', 'ソース変更時はcommit_messageを含める'),
    'new_format': ('new 제출 계약과 필수 변경 파일 정보 준수', 'Follow the new submission contract and required changed-file reporting', 'new提出契約と必須の変更ファイル報告を守る'),
    'edit_format': ('edit·expected_revision·edit_reason 계약 준수', 'Follow the edit/expected_revision/edit_reason contract', 'edit・expected_revision・edit_reason契約を守る'),
    'rework_format': ('rejection_response·반려 식별자·expected_revision 포함', 'Include rejection_response, rejection identity and expected_revision', 'rejection_response・差し戻し識別子・expected_revisionを含める'),
    'review_format': ('verdict/findings/comment 검수 JSON 제출', 'Submit review JSON with verdict/findings/comment', 'verdict/findings/commentのレビューJSONを提出'),
    'preflight_format': ('검토 결과에 따라 필요한 질의 등록', 'Register questions required by the review findings', '確認結果に応じて必要な質問を登録'),
    'q_format': ('answers 또는 허용된 escalate POST 성공 확인', 'Confirm successful answers or permitted escalate POST', 'answersまたは許可されたescalate POSTの成功を確認'),
    'workflow_format': ('workflow 결정 JSON 계약 준수', 'Follow the workflow decision JSON contract', 'workflow決定JSON契約を守る'),
    'sequence_format': ('workflow/sequence PATCH 계약 준수', 'Follow the workflow/sequence PATCH contract', 'workflow/sequence PATCH契約を守る'),
    'wp_format': ('canonical WP JSON과 기존 저장 계약 유지', 'Preserve canonical WP JSON and its save contract', 'canonical WP JSONと既存の保存契約を維持'),
    'merge_format': ('merge review conversation 응답 계약 준수', 'Follow the merge review conversation response contract', 'merge review conversation応答契約を守る'),
    'conflict_format': ('지정된 충돌 해결 JSON 계약 준수', 'Follow the assigned conflict-resolution JSON contract', '指定された競合解決JSON契約を守る'),
    'repair_format': ('TS 준비 수정·재실행 계약 준수', 'Follow the TS setup repair/rerun contract', 'TS準備修正・再実行契約を守る'),
    'origin_format': ('product_defect/test_defect/hold 분류 JSON 제출', 'Submit product_defect/test_defect/hold classification JSON', 'product_defect/test_defect/hold分類JSONを提出'),
    'test_format': ('test-run 요청·완료 확인 계약 준수', 'Follow the test-run request/completion contract', 'test-run要求・完了確認契約を守る'),
    'spec_format': ('실제 증거가 있는 case 결과 JSON 제출', 'Submit case-result JSON supported by actual evidence', '実際の証拠があるcase結果JSONを提出'),
    'existing_format': ('위에 지정된 제출 형식 준수', 'Follow the submission format specified above', '上記で指定された提出形式を守る'),
}
_TASKS = {
    'chat': ((), ('turn_format', 'probe')),
    'new': (('references', 'body'), ('new_format',)),
    'first_write': (('header_keep', 'body'), ('edit_format',)),
    'edit': (('references', 'body'), ('edit_format',)),
    'rework': (('rejection', 'body'), ('rework_format',)),
    'review': (('review_basis',), ('review_format',)),
    'preflight': (('references',), ('preflight_format',)),
    'q_answer': (('q_basis',), ('q_format',)),
    'workflow': (('references',), ('workflow_format',)),
    'sequence': (('locked',), ('sequence_format',)),
    'work_plan': (('locked',), ('wp_format',)),
    'merge_review': (('references',), ('merge_format',)),
    'conflict': (('references',), ('conflict_format',)),
    'repair': (('results',), ('repair_format',)),
    'failure_origin': (('references',), ('origin_format',)),
    'test_run': (('references',), ('test_format',)),
    'test_spec': (('results',), ('spec_format',)),
    'next_step': (('references',), ('existing_format',)),
    'generic': ((), ('existing_format',)),
}

def normalize_locale(locale):
    value = str(locale or 'ko').lower()
    return value if value in ('ko', 'en', 'ja') else 'ko'


def _word(key, locale):
    return _WORDS[key][('ko', 'en', 'ja').index(normalize_locale(locale))]


def specification(kind='generic', locale='ko', *, unanswered=False, can_write=False):
    kind = kind if kind in _TASKS else 'generic'
    before, form = _TASKS[kind]
    before, form = list(before), list(form)
    if kind == 'chat':
        if unanswered is not False:
            before.append('q_required' if unanswered is True else 'q_unknown')
        if can_write:
            form.append('commit')
    return dict(kind=kind, locale=normalize_locale(locale), before=before, form=form)


def render(spec):
    """Exactly five physical lines; bounded catalog inputs are never truncated."""
    loc = spec['locale']
    before = ' / '.join(_word(key, loc) for key in spec['before']) or _word('none', loc)
    form = ' / '.join(_word(key, loc) for key in spec['form'])
    return '\n'.join((_word('header', loc), _word('task', loc) + _word(spec['kind'], loc),
                      _word('before', loc) + before, _word('once', loc), _word('format', loc) + form))


def _decode_block(lines):
    if len(lines) != 5:
        return None
    for loc in ('ko', 'en', 'ja'):
        if lines[0] != _word('header', loc) or lines[3] != _word('once', loc):
            continue
        if not all(lines[i].startswith(_word(key, loc)) for i, key in ((1, 'task'), (2, 'before'), (4, 'format'))):
            continue
        reverse = {_word(key, loc): key for key in _WORDS}
        try:
            kind = reverse[lines[1][len(_word('task', loc)):]]
            if kind not in _TASKS:
                continue
            before_text = lines[2][len(_word('before', loc)):]
            before = [] if before_text == _word('none', loc) else [reverse[v] for v in before_text.split(' / ')]
            form = [reverse[v] for v in lines[4][len(_word('format', loc)):].split(' / ')]
            return dict(kind=kind, locale=loc, before=before, form=form)
        except KeyError:
            continue
    return None


def _records(prompt):
    """Internal builder records take precedence over any quoted example block."""
    kept, spec = [], None
    for line in str(prompt or '').splitlines(keepends=True):
        value = line.rstrip('\r\n')
        if value.startswith(_MARKER) and value.endswith(_END):
            candidate = json.loads(value[len(_MARKER):-len(_END)])
            render(candidate)
            spec = candidate
        else:
            kept.append(line)
    return ''.join(kept).rstrip(), spec


def split(prompt):
    """Consume records, or relocate the last delivered block on a copy/retry.

    Explicit builder metadata means other blocks are quoted input, not generated
    instructions. Without a record, only the last complete owned grammar is the
    delivered block; earlier examples are preserved byte-for-byte.
    """
    body, spec = _records(prompt)
    if spec is not None:
        return body, spec
    lines = body.splitlines(keepends=True)
    for i in range(len(lines) - 5, -1, -1):
        candidate = _decode_block([v.rstrip('\r\n') for v in lines[i:i + 5]])
        if candidate is not None:
            return ''.join(lines[:i] + lines[i + 5:]).rstrip(), candidate
    return body, None


def prepare(prompt, *, kind, locale='ko', unanswered=False, can_write=False):
    body, _ = _records(prompt)
    spec = specification(kind, locale, unanswered=unanswered, can_write=can_write)
    return _with_spec(body, spec)


def _with_spec(body, spec):
    return body + '\n' + _MARKER + json.dumps(spec, ensure_ascii=True, separators=(',', ':')) + _END


def with_answered_questions(prompt, *, review):
    """Called only when the engine actually delivers answer records to this hop."""
    body, spec = split(prompt)
    spec = spec or specification('review' if review else 'rework')
    key = 'review_answers' if review else 'rework_answers'
    if key not in spec['before']:
        spec['before'].append(key)
    return _with_spec(body, spec)


def task_kind(prompt):
    return (split(prompt)[1] or {}).get('kind')


def finalize(prompt, *, locale=None, fallback_kind='generic'):
    body, spec = split(prompt)
    spec = spec or specification(fallback_kind, locale)
    if locale is not None:
        spec['locale'] = normalize_locale(locale)
    return body + '\n\n' + render(spec)


def finalize_payload(payload):
    result = dict(payload)
    if result.get('mention'):
        result['mention'] = finalize(result['mention'])
    return result


def finalize_messages(messages, *, locale=None, fallback_kind='generic'):
    """Called once before the first API model request, never on tool replies.

    Initial context and hint messages stay separate and keep their original text.
    Only the server's first instruction carries a task record/block to extract.
    The actual conversation JSON must not be scanned as generated instructions.
    Returns the settled spec so a server-written follow-up user message (see
    :func:`finalize_nudge`) can end with the very same block.
    """
    first = next(m for m in messages if m['role'] == 'user')
    body, spec = split(first['content'])
    first['content'] = body
    spec = spec or specification(fallback_kind, locale)
    if locale is not None:
        spec['locale'] = normalize_locale(locale)
    last = next(m for m in reversed(messages) if m['role'] == 'user')
    last['content'] = last['content'].rstrip() + '\n\n' + render(spec)
    return spec


def finalize_nudge(text, spec):
    """End a server-written follow-up user message (API re-request) with the block.

    ``spec`` is the value :func:`finalize_messages` returned for this run. The block
    wording stays owned by :func:`render`; callers never assemble it themselves.
    """
    return str(text).rstrip() + '\n\n' + render(spec)


def q_transport(locale):
    from modules.flow_gate.services.prompt_blocks.blocks.submit import REMOTE_CLI_Q_ANSWER_TRANSPORT
    return REMOTE_CLI_Q_ANSWER_TRANSPORT[normalize_locale(locale)]
