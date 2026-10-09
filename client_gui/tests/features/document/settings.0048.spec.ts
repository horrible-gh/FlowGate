/**
 * flowgate.v02.0048 T#9 — the settings tab's calls in features/document/api/settings.ts (D#1 §6.3, §12.1;
 * D#2 §2.1 / §2.4): the request each one sends to the existing endpoint (the Flow's graph read for the
 * sequence root, GET / PATCH /workflow/sequence, the providers read, the active run), the answers ->
 * camelCase (a sequence row as the dev server answers it, choices from execution_policy, a run and the hop
 * of a start answer), the save payload (every row back with its item_id and its fields as read, only one
 * row's settings changed), and how a refusal is read.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest'

const getRequest = vi.fn()
const patchRequest = vi.fn()
vi.mock('@shared/api', () => ({
  getRequest: (...args: unknown[]) => getRequest(...args),
  patchRequest: (...args: unknown[]) => patchRequest(...args),
}))

import {
  SETTINGS_REPEAT_COUNT_DEFAULT_MAX,
  settingsChoicesRead,
  settingsErrorOf,
  settingsHopOf,
  settingsRead,
  settingsRootRead,
  settingsRunRead,
  settingsSave,
  settingsSavePayload,
  settingsSequenceRead,
  toSettingsChoices,
  toSettingsRoot,
  toSettingsRun,
  toSettingsSequence,
  type SettingsRow,
} from '@/features/document'

const FLOW = 'test2.default.0017'
const ROOT = 'test2.default.0017.0001-R'

/** Rows as GET /workflow/sequence answered them on the dev server (test2.default.0017), shortened notes. */
const RAW_ROWS = [
  { id: 124, item_id: 124, protected: true, source_wp_card_id: null, item_seq: 4, type: 'T', label: '작업지시', doc_class: 'R', sort_order: 3, status: 'done', note: 'T#1 지시', source_doc_id: null, source_revision_no: null, review_count: 0, reviewer_provider_id: null, reviewer_provider_display_name: null, pre_instruction_text: null, pre_instruction_attachment: null, source_freshness: null, source_current_revision_no: null, provider_id: 'aip_zclzvc', provider_display_name: 'Claude Haiku 5.5', provider_registered: true },
  { id: 125, item_id: 125, protected: true, source_wp_card_id: null, item_seq: 5, type: 'TR', label: '작업레포트', doc_class: 'R', sort_order: 4, status: 'pending', note: 'T#1 결과 보고', source_doc_id: null, source_revision_no: null, review_count: 0, reviewer_provider_id: null, reviewer_provider_display_name: null, pre_instruction_text: null, pre_instruction_attachment: null, source_freshness: null, source_current_revision_no: null, provider_id: 'aip_zclzvc', provider_display_name: 'Claude Haiku 5.5', provider_registered: true },
  { id: 126, item_id: 126, protected: false, source_wp_card_id: 'c_t2', item_seq: 6, type: 'T', label: '작업지시', doc_class: 'R', sort_order: 5, status: 'pending', note: 'T#2 지시', source_doc_id: 'test2.default.0017.0003-WP', source_revision_no: 2, review_count: 2, reviewer_provider_id: 'aip_j6bfn4', reviewer_provider_display_name: 'Claude Sonnet 5.5', pre_instruction_text: '먼저 읽을 것', pre_instruction_attachment: { name: 'spec.md', size: 12 }, source_freshness: 'current', source_current_revision_no: 2, provider_id: 'aip_gone', provider_display_name: 'Old AI', provider_registered: false },
]

const SEQUENCE = { doc_id: ROOT, doc_class: 'R', decided: true, sequence_id: 22, note_max_chars: 1000, has_stale_sources: false, stale_source_count: 0, items: RAW_ROWS }

const axiosError = (status: number, data: unknown) => ({ isAxiosError: true, response: { status, data } })

beforeEach(() => {
  getRequest.mockReset()
  patchRequest.mockReset()
})

describe('settings API — reading (flowgate.v02.0048 T#9)', () => {
  it('the root comes from the Flow graph read (ownership block, D#2 §2.4 rule 1)', async () => {
    getRequest.mockResolvedValueOnce({
      data: { ok: true, flow_id: FLOW, ownership: { state: 'v01', sequence_root_doc_id: ROOT, graph_revision_no: null, assigned_at: null }, draft: null, latest_revision: null },
    })
    expect(await settingsRootRead('test2.default.0017')).toEqual({ ownership: 'v01', rootDocId: ROOT })
    expect(getRequest).toHaveBeenCalledWith('/api/v1/flows/test2.default.0017/graph')
  })

  it('a Flow id is encoded in the path; another owner has no root', () => {
    expect(toSettingsRoot({ ownership: { state: 'graph', sequence_root_doc_id: null } })).toEqual({ ownership: 'graph', rootDocId: null })
    expect(toSettingsRoot({})).toEqual({ ownership: 'unassigned', rootDocId: null })
  })

  it('the sequence of the root: every row with its id, state, settings and the fields sent back', async () => {
    getRequest.mockResolvedValueOnce({ data: SEQUENCE })
    const sequence = await settingsSequenceRead(ROOT)
    expect(getRequest).toHaveBeenCalledWith('/api/v1/workflow/sequence', { doc_id: ROOT })
    expect(sequence.rootDocId).toBe(ROOT)
    expect(sequence.sequenceId).toBe(22)
    expect(sequence.items.map((row) => [row.itemId, row.itemSeq, row.type, row.status, row.protected])).toEqual([
      [124, 4, 'T', 'done', true],
      [125, 5, 'TR', 'pending', true],
      [126, 6, 'T', 'pending', false],
    ])
    expect(sequence.items[2]).toEqual({
      itemId: 126,
      itemSeq: 6,
      type: 'T',
      label: '작업지시',
      status: 'pending',
      protected: false,
      note: 'T#2 지시',
      sourceDocId: 'test2.default.0017.0003-WP',
      sourceRevisionNo: 2,
      sourceWpCardId: 'c_t2',
      providerId: 'aip_gone',
      providerDisplayName: 'Old AI',
      providerRegistered: false,
      reviewCount: 2,
      reviewerProviderId: 'aip_j6bfn4',
      reviewerProviderDisplayName: 'Claude Sonnet 5.5',
      preInstructionText: '먼저 읽을 것',
      preInstructionAttachment: { name: 'spec.md', size: 12 },
    })
  })

  it('a row without an item id is dropped (it could not be sent back); a body without doc_id is refused', () => {
    expect(toSettingsSequence({ doc_id: ROOT, items: [{ type: 'T' }, RAW_ROWS[0]] }).items.map((row) => row.itemId)).toEqual([124])
    expect(() => toSettingsSequence({ items: [] })).toThrow()
  })

  it('settingsRead: the root first, then its sequence', async () => {
    getRequest.mockResolvedValueOnce({ data: { ownership: { state: 'v01', sequence_root_doc_id: ROOT } } }).mockResolvedValueOnce({ data: SEQUENCE })
    const sequence = await settingsRead(FLOW)
    expect(getRequest.mock.calls.map((call) => call[0])).toEqual([`/api/v1/flows/${FLOW}/graph`, '/api/v1/workflow/sequence'])
    expect(sequence.items).toHaveLength(3)
  })

  it('settingsRead on a Flow without a v0.1 sequence throws a refusal read as not_decided (no sequence read)', async () => {
    getRequest.mockResolvedValueOnce({ data: { ownership: { state: 'unassigned', sequence_root_doc_id: null } } })
    const refusal = await settingsRead(FLOW).catch((error: unknown) => error)
    expect(settingsErrorOf(refusal).kind).toBe('not_decided')
    expect(getRequest).toHaveBeenCalledTimes(1)
  })

  it('choices: the providers, the default, and the review counts -1, 0, 1..repeat_count_max (execution_policy)', async () => {
    getRequest.mockResolvedValueOnce({
      data: {
        ok: true,
        project: 'test2',
        providers: [{ id: 'aip_tyj3vy', name: 'Claude Opus 5.5', exec_type: 'cli', kind: 'claude' }, { id: 'aip_j6bfn4', name: 'Claude Sonnet 5.5' }, { name: 'no id' }],
        default_provider_id: 'aip_j6bfn4',
        execution_policy: { repeat_count_max: 4, repeat_count_min: 1, repeat_count_hard_max: 30 },
      },
    })
    const choices = await settingsChoicesRead('test2')
    expect(getRequest).toHaveBeenCalledWith('/api/v1/ai-invoke/providers', { project: 'test2' })
    expect(choices).toEqual({
      providers: [{ id: 'aip_tyj3vy', name: 'Claude Opus 5.5' }, { id: 'aip_j6bfn4', name: 'Claude Sonnet 5.5' }],
      defaultProviderId: 'aip_j6bfn4',
      reviewCounts: [-1, 0, 1, 2, 3, 4],
    })
  })

  it('choices without a policy fall back to the server default ceiling', () => {
    expect(SETTINGS_REPEAT_COUNT_DEFAULT_MAX).toBe(3)
    expect(toSettingsChoices({ providers: [] }).reviewCounts).toEqual([-1, 0, 1, 2, 3])
    expect(toSettingsChoices({ providers: [], execution_policy: { repeat_count_max: 0 } }).reviewCounts).toEqual([-1, 0, 1, 2, 3])
  })

  it('the active run and its hop; a finished or absent run is not active', async () => {
    getRequest.mockResolvedValueOnce({
      data: { ok: true, run_id: 'r1', status: 'running', mode: 'continuous', group_id: FLOW, doc_ref: ROOT, provider: { id: 'aip_tyj3vy', name: 'Claude Opus 5.5' }, hop_item_seq: 6, hop_kind: 'worker', hop_review_count: 2, hop_reviewer_provider_id: 'aip_j6bfn4', hop_reviewer_provider_name: 'Claude Sonnet 5.5', active: true },
    })
    const run = await settingsRunRead(FLOW)
    expect(getRequest).toHaveBeenCalledWith('/api/v1/ai-invoke/active', { group_id: FLOW })
    expect(run).toEqual({
      active: true,
      hop: { mode: 'continuous', itemSeq: 6, providerId: 'aip_tyj3vy', providerName: 'Claude Opus 5.5', providerSource: null, reviewCount: 2, reviewerProviderId: 'aip_j6bfn4', reviewerProviderName: 'Claude Sonnet 5.5' },
    })
    expect(toSettingsRun({ ok: true, active: false, group_id: FLOW })).toEqual({ active: false, hop: null })
    expect(toSettingsRun({ active: true, status: 'finished' })).toEqual({ active: false, hop: null })
  })

  it('a start answer carries selected_provider_source with the hop (D#2 §2.1 실행 확인 지점)', () => {
    expect(
      settingsHopOf({ run_id: 'r2', status: 'running', mode: 'continuous', hop_item_seq: 6, provider: { id: 'aip_gone', name: 'Old AI' }, selected_provider_source: 'stored_sequence', hop_review_count: 2, hop_reviewer_provider_id: 'aip_j6bfn4', hop_reviewer_provider_name: 'Claude Sonnet 5.5' }),
    ).toEqual({ mode: 'continuous', itemSeq: 6, providerId: 'aip_gone', providerName: 'Old AI', providerSource: 'stored_sequence', reviewCount: 2, reviewerProviderId: 'aip_j6bfn4', reviewerProviderName: 'Claude Sonnet 5.5' })
  })
})

describe('settings API — saving (flowgate.v02.0048 T#9)', () => {
  const rows = (): SettingsRow[] => toSettingsSequence(SEQUENCE).items.slice()

  it('the payload holds every row with its item_id and its fields as read (D#2 §2.4 rule 2)', () => {
    const payload = settingsSavePayload(rows())
    expect(payload.map((item) => item.item_id)).toEqual([124, 125, 126])
    expect(payload[2]).toEqual({
      item_id: 126,
      type: 'T',
      label: '작업지시',
      note: 'T#2 지시',
      source_doc_id: 'test2.default.0017.0003-WP',
      source_revision_no: 2,
      source_wp_card_id: 'c_t2',
      provider_id: 'aip_gone',
      provider_display_name: 'Old AI',
      review_count: 2,
      reviewer_provider_id: 'aip_j6bfn4',
      reviewer_provider_display_name: 'Claude Sonnet 5.5',
      pre_instruction_text: '먼저 읽을 것',
      pre_instruction_attachment: { name: 'spec.md', size: 12 },
    })
    // Only fields the PATCH row knows go back (no status / freshness / registered flags).
    expect(Object.keys(payload[0]).sort()).toEqual(
      ['item_id', 'type', 'label', 'note', 'source_doc_id', 'source_revision_no', 'source_wp_card_id', 'provider_id', 'provider_display_name', 'review_count', 'reviewer_provider_id', 'reviewer_provider_display_name', 'pre_instruction_text', 'pre_instruction_attachment'].sort(),
    )
  })

  it('one row takes the new settings; every other row and every other field of that row stays as read', () => {
    const before = settingsSavePayload(rows())
    const after = settingsSavePayload(rows(), {
      itemId: 125,
      values: { providerId: 'aip_tyj3vy', providerDisplayName: 'Claude Opus 5.5', reviewCount: -1, reviewerProviderId: null, reviewerProviderDisplayName: 'stale name' },
    })
    expect(after[0]).toEqual(before[0])
    expect(after[2]).toEqual(before[2])
    expect(after[1]).toEqual({
      ...before[1],
      provider_id: 'aip_tyj3vy',
      provider_display_name: 'Claude Opus 5.5',
      review_count: -1,
      reviewer_provider_id: null,
      // A display name never goes without its AI (the server refuses that pair).
      reviewer_provider_display_name: null,
    })
  })

  it('a default run AI goes without a name, whatever name the values hold', () => {
    const payload = settingsSavePayload(rows(), {
      itemId: 126,
      values: { providerId: null, providerDisplayName: 'stale run AI', reviewCount: 1, reviewerProviderId: 'aip_tyj3vy', reviewerProviderDisplayName: 'Claude Opus 5.5' },
    })
    expect(payload[2]).toMatchObject({ provider_id: null, provider_display_name: null, reviewer_provider_id: 'aip_tyj3vy', reviewer_provider_display_name: 'Claude Opus 5.5' })
  })

  it('settingsSave PATCHes the root with that payload', async () => {
    patchRequest.mockResolvedValueOnce({ data: { doc_id: ROOT, updated: 1 } })
    const result = await settingsSave(ROOT, rows(), {
      itemId: 126,
      values: { providerId: null, providerDisplayName: null, reviewCount: 0, reviewerProviderId: null, reviewerProviderDisplayName: null },
    })
    expect(patchRequest).toHaveBeenCalledTimes(1)
    const [path, body] = patchRequest.mock.calls[0]
    expect(path).toBe('/api/v1/workflow/sequence')
    expect(body.doc_id).toBe(ROOT)
    expect(body.items).toHaveLength(3)
    expect(body.items[2]).toMatchObject({ item_id: 126, provider_id: null, provider_display_name: null, review_count: 0, note: 'T#2 지시', pre_instruction_text: '먼저 읽을 것' })
    expect(result.rootDocId).toBe(ROOT)
  })

  it('a refusal is thrown as it is', async () => {
    const refusal = axiosError(409, { error: 'sequence_item_stale', doc_id: ROOT, item_index: 2, item_id: 126 })
    patchRequest.mockRejectedValueOnce(refusal)
    await expect(settingsSave(ROOT, rows(), { itemId: 126, values: rows()[2] })).rejects.toBe(refusal)
  })
})

describe('settings API — refusals (flowgate.v02.0048 T#9)', () => {
  it.each([
    ['409 group_disposed', axiosError(409, { error: 'group_disposed', doc_id: ROOT }), 'disposed'],
    ['423 GROUP_AI_RUN_LOCKED', axiosError(423, { error: { code: 'GROUP_AI_RUN_LOCKED', message: 'm', run_live: true } }), 'running'],
    ['409 run_in_progress', axiosError(409, { code: 'run_in_progress', message: 'm' }), 'running'],
    ['409 flow_owned_by_graph', axiosError(409, { error: 'flow_owned_by_graph', doc_id: ROOT, group_id: FLOW, graph_revision_no: 2 }), 'owner'],
    ['409 flow_ownership_conflict', axiosError(409, { error: 'flow_ownership_conflict', doc_id: ROOT }), 'owner'],
    ['409 sequence_item_stale', axiosError(409, { error: 'sequence_item_stale', item_id: 126 }), 'stale'],
    ['409 protected_row_modified', axiosError(409, { error: 'protected_row_modified', item_id: 125, fields: ['type'] }), 'stale'],
    ['409 protected_row_echo_ambiguous', axiosError(409, { error: 'protected_row_echo_ambiguous' }), 'stale'],
    ['409 sequence_changed', axiosError(409, { error: 'sequence_changed' }), 'stale'],
    ['400 sequence_not_decided', axiosError(400, { error: 'sequence_not_decided', doc_id: ROOT }), 'not_decided'],
    ['422 note_too_long', axiosError(422, { error: 'note_too_long', max: 1000, length: 1200 }), 'invalid'],
    ['422 invalid_sequence_item', axiosError(422, { error: 'invalid_sequence_item', detail: 'provider_id_format_invalid', item_index: 1 }), 'invalid'],
    ['403', axiosError(403, { error: 'workflow_sequence_edit_token_mismatch' }), 'forbidden'],
    ['404 doc_not_found', axiosError(404, { error: 'doc_not_found', doc_id: ROOT }), 'not_found'],
    ['500', axiosError(500, { error: 'internal_error', detail: 'x' }), 'other'],
    ['no answer', { isAxiosError: true, message: 'Network Error' }, 'network'],
  ])('%s -> %s', (_label, error, kind) => {
    expect(settingsErrorOf(error).kind).toBe(kind)
  })

  it('reads the code and the status only', () => {
    expect(settingsErrorOf(axiosError(409, { error: 'sequence_item_stale', message: '다른 문구' }))).toEqual({ kind: 'stale', status: 409, code: 'sequence_item_stale' })
  })
})
