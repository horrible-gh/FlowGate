// flowgate.v02.0064 T-B1d — body diagnosis / recovery client contract (P0006 §2.6~§2.8, §6.2) and the
// MdViewer [복구] flow. The server is mocked at @shared/api.
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import en from '@/app/i18n/en'
import ja from '@/app/i18n/ja'
import ko from '@/app/i18n/ko'
import {
  bodyRecoveryBulk,
  bodyRecoveryChunks,
  bodyRecoveryDiagnose,
  bodyRecoveryFlowOf,
  bodyRecoveryRecover,
  bodyRecoveryRefusalOf,
  toBodyRecoveryDiagnosis,
} from '@/features/document'
import MdViewer from '@/features/documentWindow/MdViewer.vue'

const api = vi.hoisted(() => ({ getRequest: vi.fn(), postRequest: vi.fn(), patchRequest: vi.fn() }))
vi.mock('@shared/api', async (importOriginal) => ({ ...await importOriginal<typeof import('@shared/api')>(), ...api }))
registerAppMessages(i18n)

const FLOW = 'test2.default.0052'
const DOC = `${FLOW}.0002-N`
const failure = (status: number, data: object) => ({ isAxiosError: true, response: { status, data } })
const wrappers: VueWrapper<any>[] = []
const el = (w: VueWrapper<any>, id: string) => w.find(`[data-test="${id}"]`)

const item = (over: Record<string, unknown> = {}) => ({
  doc_id: DOC, code: '0002-N', title: '조사 · 지시서', type: 'N', status: 'draft',
  file_path: 'documents/test2/main/default/0052/0002-N_instruction.md', reason: 'file_not_found',
  method: 'regenerate_card_draft', recoverable: true, body_loss: 'possible',
  origin: { card: true, node_key: 'n_req', slot: 'instruction', unsubmitted: true, untouched_proven: false, inherited_body: false },
  candidate: null, evidence: null, ...over,
})
const diagnosis = (items: object[]) => ({
  ok: true, scope: { kind: 'group', id: FLOW, project_id: 'test2' },
  storage_root: { effective: 'D:/flowgate/storage', source: 'project_setting',
    configured: { env: null, project: 'D:/flowgate/storage', system: 'D:/flowgate/storage_old', default: 'C:/s' } },
  summary: { checked: 7, unreadable: items.length, recoverable: items.length, storage_root_suspect: 0, suspect_roots: [] },
  items, fingerprint: 'f'.repeat(64),
})

beforeEach(() => {
  api.getRequest.mockReset()
  api.postRequest.mockReset()
  api.patchRequest.mockReset()
  i18n.global.locale.value = 'ko'
})
afterEach(() => {
  wrappers.splice(0).forEach((w) => w.unmount())
})

describe('0064 body diagnosis and recovery API', () => {
  it('reads a diagnosis with its scope, root, summary and items', async () => {
    api.getRequest.mockResolvedValue({ data: diagnosis([item(), item({ doc_id: `${FLOW}.0015-D`, method: 'fix_storage_root',
      recoverable: false, body_loss: null, origin: { card: false }, evidence: { root: 'D:/old', source: 'system_setting' } })]) })
    const read = await bodyRecoveryDiagnose({ groupId: FLOW })
    expect(api.getRequest).toHaveBeenCalledWith('/api/v1/documents/body-diagnosis', { group_id: FLOW })
    expect(read.scope).toEqual({ kind: 'group', id: FLOW, projectId: 'test2' })
    expect(read.storageRoot.configured.system).toBe('D:/flowgate/storage_old')
    expect(read.items.map((i) => [i.method, i.recoverable, i.bodyLoss, i.card, i.evidenceRoot])).toEqual([
      ['regenerate_card_draft', true, 'possible', true, null], ['fix_storage_root', false, null, false, 'D:/old']])
    await bodyRecoveryDiagnose({ projectId: 'test2' })
    expect(api.getRequest).toHaveBeenLastCalledWith('/api/v1/documents/body-diagnosis', { project_id: 'test2' })
    expect(toBodyRecoveryDiagnosis(null).items).toEqual([])
  })

  it('sends the diagnosed method and reads the loss', async () => {
    api.postRequest.mockResolvedValue({ data: { method: 'regenerate_card_draft', body_loss: 'possible', body_lost: true } })
    expect(await bodyRecoveryRecover(DOC, 'regenerate_card_draft')).toEqual(
      { docId: DOC, status: 'recovered', method: 'regenerate_card_draft', bodyLoss: 'possible', refusal: null })
    expect(api.postRequest).toHaveBeenCalledWith(`/api/v1/documents/${encodeURIComponent(DOC)}/regenerate`,
      { expected_method: 'regenerate_card_draft' })
  })

  it.each([
    [failure(404, { detail: 'Document not found: x' }), 'not_found'],
    [failure(409, { detail: '…', error: 'diagnosis_changed', diagnosis: {} }), 'diagnosis_changed'],
    [failure(409, { detail: '…', error: 'target_in_use', evidence: {} }), 'target_in_use'],
    [failure(409, { detail: 'TR2 recovery requires its canonical JSON document.' }), 'tr2_not_supported'],
    [failure(409, { detail: 'Modification not allowed: the group has been disposed.' }), 'group_disposed'],
    [failure(423, { error: { code: 'GROUP_AI_RUN_LOCKED' } }), 'group_ai_running'],
    [failure(500, {}), null],
  ])('maps a single refusal to its code (%#)', (error, code) => {
    expect(bodyRecoveryRefusalOf(error)).toBe(code)
  })

  it('sends more than 50 selections in chunks and never counts a refusal as recovered', async () => {
    const selections = Array.from({ length: 51 }, (_, i) => ({ docId: `${FLOW}.${String(i + 1).padStart(4, '0')}-D`,
      expectedMethod: 'regenerate_stub' as const }))
    expect(bodyRecoveryChunks(selections).map((c) => c.length)).toEqual([50, 1])
    api.postRequest.mockImplementation(async (_url: string, body: { selections: { doc_id: string }[] }) => ({
      data: { ok: true, results: body.selections.map((s, i) => ({ doc_id: s.doc_id,
        status: i === 0 ? 'refused' : 'recovered', method: i === 0 ? null : 'regenerate_stub', body_loss: i === 0 ? null : 'lost',
        refusal: i === 0 ? { error: 'target_in_use', detail: '…', evidence: {} } : null })) } }))
    const out = await bodyRecoveryBulk(selections)
    expect(api.postRequest).toHaveBeenCalledTimes(2)
    expect(api.postRequest.mock.calls[0][0]).toBe('/api/v1/documents/body-recovery')
    expect(api.postRequest.mock.calls[0][1].selections).toHaveLength(50)
    expect(api.postRequest.mock.calls[1][1].selections).toEqual([{ doc_id: selections[50].docId, expected_method: 'regenerate_stub' }])
    expect([out.recovered, out.refused, out.failed]).toEqual([49, 2, 0])
    expect(out.results.map((r) => r.docId)).toEqual(selections.map((s) => s.docId))
    expect(out.results[0].refusal).toBe('target_in_use')
  })

  it('finds a document\'s Flow from its id', () => {
    expect(bodyRecoveryFlowOf(DOC)).toBe(FLOW)
    expect(bodyRecoveryFlowOf('nodot')).toBeNull()
  })

  it('has every recovery string in ko, en and ja', () => {
    const keys = (tree: Record<string, unknown>, prefix = ''): string[] => Object.entries(tree).flatMap(([key, value]) =>
      value && typeof value === 'object' ? keys(value as Record<string, unknown>, `${prefix}${key}.`) : [`${prefix}${key}`])
    const recover = (messages: { document: { recover: Record<string, unknown> } }) => keys(messages.document.recover).sort()
    expect(recover(en)).toEqual(recover(ko))
    expect(recover(ja)).toEqual(recover(ko))
    for (const code of ['not_found', 'file_exists', 'read_error', 'diagnosis_changed', 'storage_root_mismatch', 'group_disposed',
      'group_ai_running', 'target_in_use', 'tr2_not_supported', 'internal_error']) {
      expect(recover(ko)).toContain(`refusal.${code}`)
    }
  })
})

describe('0064 MdViewer [복구]', () => {
  function unreadable(items: object[]) {
    api.getRequest.mockImplementation(async (url: string) => {
      if (url.includes('/body-diagnosis')) return { data: diagnosis(items) }
      throw failure(404, { reason: 'file_not_found' })
    })
  }

  async function viewer(canReenter = true) {
    const w = mount(MdViewer, { props: { docId: DOC, canReenter }, global: { plugins: [i18n] } })
    wrappers.push(w)
    await flushPromises()
    return w
  }

  it('recovers with the diagnosed method, reloads and says the body may be lost', async () => {
    unreadable([item()])
    const w = await viewer()
    expect(api.getRequest).toHaveBeenCalledWith('/api/v1/documents/body-diagnosis', { group_id: FLOW })
    api.postRequest.mockResolvedValue({ data: { method: 'regenerate_card_draft', body_loss: 'possible', body_lost: true } })
    api.getRequest.mockResolvedValue({ data: { content: '---\ntitle: 조사 · 지시서\n---\n' } })
    await el(w, 'md-viewer-recover').trigger('click')
    await flushPromises()
    expect(api.postRequest).toHaveBeenCalledWith(`/api/v1/documents/${encodeURIComponent(DOC)}/regenerate`,
      { expected_method: 'regenerate_card_draft' })
    expect(el(w, 'md-viewer-unreadable').exists()).toBe(false)
    expect(el(w, 'md-viewer-recover-notice').text()).toBe(i18n.global.t('gui.document.recover.loss.possible'))
  })

  it.each([
    ['fix_storage_root', 'gui.document.recover.guide.fix_storage_root'],
    ['read_error_manual', 'gui.document.recover.guide.read_error_manual'],
  ])('shows only guidance for %s', async (method, key) => {
    unreadable([item({ method, recoverable: false, body_loss: null })])
    const w = await viewer()
    expect(el(w, 'md-viewer-recover').exists()).toBe(false)
    expect(el(w, 'md-viewer-recover-guide').text()).toBe(i18n.global.t(key))
    expect(el(w, 'md-viewer-reenter').exists()).toBe(true)
  })

  it('shows a refusal and diagnoses again', async () => {
    unreadable([item()])
    const w = await viewer()
    api.postRequest.mockRejectedValue(failure(409, { detail: '…', error: 'diagnosis_changed', diagnosis: {} }))
    await el(w, 'md-viewer-recover').trigger('click')
    await flushPromises()
    expect(el(w, 'md-viewer-recover-notice').text()).toBe(i18n.global.t('gui.document.recover.refusal.diagnosis_changed'))
    expect(api.getRequest.mock.calls.filter(([url]) => String(url).includes('/body-diagnosis'))).toHaveLength(2)
    expect(el(w, 'md-viewer-unreadable').exists()).toBe(true)
  })

  it('offers nothing and reads no diagnosis without permission', async () => {
    unreadable([item()])
    const w = await viewer(false)
    expect(el(w, 'md-viewer-recover').exists()).toBe(false)
    expect(api.getRequest.mock.calls.some(([url]) => String(url).includes('/body-diagnosis'))).toBe(false)
  })
})
