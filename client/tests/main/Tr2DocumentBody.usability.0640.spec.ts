import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import en from '../../shared/i18n/en'
import ja from '../../shared/i18n/ja'
import ko from '../../shared/i18n/ko'
import Tr2DocumentBody from '@main/components/documents/Tr2DocumentBody.vue'

const { getRequest, postRequest, putRequest, deleteRequest } = vi.hoisted(() => ({
  getRequest: vi.fn(), postRequest: vi.fn(), putRequest: vi.fn(), deleteRequest: vi.fn(),
}))
vi.mock('@shared/api', () => ({ getRequest, postRequest, putRequest, deleteRequest }))
const docId = 'flowgate.default.0640.0009-TR2'
const base = `/api/v1/documents/${docId}/tr2`
const tab = { id: docId, title: 'Apply proposal', type: 'md' as const, typeCode: 'TR2', path: 'document.json', projectId: 'flowgate' }

// 60 items across all three types, including multiple EDITs for the same file.
function fixture() {
  const edits = Array.from({ length: 40 }, (_, i) => ({ id: `e${i + 1}`, kind: 'edit', file: i < 2 ? 'src/shared.ts' : `src/module-${i + 1}.ts`, anchor_old: `before ${i + 1}`, replacement_new: `after ${i + 1}`, confidence: 'high', rationale: 'fix' }))
  const creates = Array.from({ length: 12 }, (_, i) => ({ id: `c${i + 1}`, kind: 'create_file', file: `src/new-${i + 1}.ts`, content: `new ${i + 1}`, confidence: 'medium', rationale: 'add' }))
  const deferred = Array.from({ length: 8 }, (_, i) => ({ id: `d${i + 1}`, file: `src/later-${i + 1}.ts`, reason: 'needs_runtime', rationale: 'runtime required' }))
  return {
    document: { doc_id: docId, revision_no: 2, doc_review_status: 'pending_review', editable: true },
    mutation: { allowed: true, reason: null as string | null }, readiness: { ready: true, code: null, loc: null, reason: null, edits: [] },
    gate_admission: { fingerprint: 'admission', candidate_count: 0, all_candidate: false, commands: [] },
    body: { tr2_version: 1, source_t2_doc_id: 'flowgate.default.0640.0008-T2', baseline_fingerprint: 'base', edit_spec: { termination: 'ready_to_apply', edits: [...edits, ...creates], deferred, gate: { commands: [], apply: false } } },
    derived: { files: [], live_precheck: { drift: false, baseline_fingerprint: 'base', live_fingerprint: 'base' } },
    approval: { latest_attempt: null, attempts: [], retry: { allowed: false, reason: 'no_attempt' } },
    history: { source_history_state: 'aligned', ledger: [] },
  }
}
let current: ReturnType<typeof fixture>
const mounted: ReturnType<typeof mount>[] = []
function body(readOnly = false) {
  const wrapper = mount(Tr2DocumentBody, { props: { tab, readOnly }, global: { plugins: [i18n] }, attachTo: document.body })
  mounted.push(wrapper)
  return wrapper
}
const find = (w: ReturnType<typeof mount>, id: string) => w.find(`[data-testid="${id}"]`)
const rows = (w: ReturnType<typeof mount>) => find(w, 'tr2-file-list').findAll('li')
beforeEach(() => {
  i18n.global.locale.value = 'en'
  current = fixture()
  for (const fn of [getRequest, postRequest, putRequest, deleteRequest]) fn.mockReset()
  getRequest.mockImplementation((url: string) => Promise.resolve({ data: url.includes('/files/') ? { before_text: 'live source', truncated: false } : current }))
  postRequest.mockResolvedValue({ data: { ready: true, code: null } })
})
afterEach(() => { mounted.forEach(w => w.unmount()); mounted.length = 0; i18n.global.locale.value = 'en' })

describe('0640 — TR2 change review with 60 items', () => {
  it('prioritizes status and review and puts all proposal administration inside a collapsed disclosure', async () => {
    const w = body(); await flushPromises()
    expect(rows(w)).toHaveLength(60)
    expect(find(w, 'tr2-review-summary').text()).toContain('52')
    const advanced = find(w, 'tr2-advanced')
    expect((advanced.element as HTMLDetailsElement).open).toBe(false)
    const main = find(w, 'tr2-body').element
    const before = (a: Element, b: Element) => Boolean(a.compareDocumentPosition(b) & Node.DOCUMENT_POSITION_FOLLOWING)
    expect(before(find(w, 'tr2-state-strip').element, find(w, 'tr2-file-list').element)).toBe(true)
    expect(before(find(w, 'tr2-detail').element, advanced.element)).toBe(true)
    for (const id of ['tr2-edit-whole', 'tr2-new-whole', 'tr2-reset-whole', 'tr2-download', 'tr2-upload', 'tr2-history-toggle']) {
      expect(advanced.element.contains(find(w, id).element), id).toBe(true)
    }
    expect(main.querySelectorAll('[data-testid="tr2-reset-whole"]')).toHaveLength(1)
    await w.findAll('button').find(b => b.text() === en.main.tr2_body.actions.diagnostic)!.trigger('click')
    await flushPromises()
    expect((advanced.element as HTMLDetailsElement).open).toBe(true)
    expect(postRequest).toHaveBeenCalledWith(`${base}/precheck`, {})
  })

  it('searches paths and IDs case-insensitively, combines filters, handles empty results and resets scroll', async () => {
    const w = body(); await flushPromises()
    const list = find(w, 'tr2-file-list').element as HTMLElement
    list.scrollTop = 200
    await find(w, 'tr2-file-search').setValue('  SRC/SHARED.TS  ')
    expect(rows(w)).toHaveLength(2)
    expect(list.scrollTop).toBe(0)
    await find(w, 'tr2-filter-create_file').trigger('click')
    expect(rows(w)).toHaveLength(0)
    expect(find(w, 'tr2-no-results').text()).toBe(en.main.tr2_body.workspace.no_results)
    await find(w, 'tr2-file-search').setValue('')
    expect(rows(w)).toHaveLength(12)
    await find(w, 'tr2-filter-deferred').trigger('click')
    expect(rows(w)).toHaveLength(8)
    await find(w, 'tr2-filter-edit').trigger('click')
    expect(rows(w)).toHaveLength(40)
    await find(w, 'tr2-filter-all').trigger('click')
    await find(w, 'tr2-file-search').setValue('E40')
    expect(rows(w)).toHaveLength(1)
    expect(find(w, 'tr2-visible-count').text()).toBe('1 of 60 items shown')
    expect(postRequest).not.toHaveBeenCalled()
    expect(putRequest).not.toHaveBeenCalled()
    expect(deleteRequest).not.toHaveBeenCalled()
  })

  it('retains the exact selected edit when filters hide it, across refresh, and clears it on deletion', async () => {
    const w = body(); await flushPromises()
    await find(w, 'tr2-item-e2').trigger('click'); await flushPromises()
    expect(find(w, 'tr2-detail').text()).toContain('after 2')
    expect(find(w, 'tr2-item-e1').attributes('aria-pressed')).toBe('false')
    expect(find(w, 'tr2-item-e2').attributes('aria-pressed')).toBe('true')
    await find(w, 'tr2-filter-create_file').trigger('click')
    expect(find(w, 'tr2-selection-hidden').exists()).toBe(true)
    expect(find(w, 'tr2-detail').text()).toContain('after 2')
    current = { ...fixture(), document: { ...current.document, revision_no: 3 } }
    window.dispatchEvent(new CustomEvent('fg:document_content_changed', { detail: { doc_id: docId, revision_no: 3 } })); await flushPromises()
    expect(find(w, 'tr2-detail').text()).toContain('after 2')
    await find(w, 'tr2-clear-filters').trigger('click')
    expect(rows(w)).toHaveLength(60)
    expect(find(w, 'tr2-item-e2').attributes('aria-pressed')).toBe('true')
    current = { ...current, document: { ...current.document, revision_no: 4 }, body: { ...current.body, edit_spec: { ...current.body.edit_spec, edits: current.body.edit_spec.edits.filter(item => item.id !== 'e2') } } }
    window.dispatchEvent(new CustomEvent('fg:document_content_changed', { detail: { doc_id: docId, revision_no: 4 } })); await flushPromises()
    expect(find(w, 'tr2-detail').text()).toBe(en.main.tr2_body.files.select_hint)
  })

  it('keeps CREATE after-only and DEFERRED no-diff semantics for the last items', async () => {
    const w = body(); await flushPromises()
    await find(w, 'tr2-item-c12').trigger('click'); await flushPromises()
    expect(find(w, 'tr2-detail').text()).toContain('new 12')
    expect(find(w, 'tr2-detail').findAll('.tr2-diff > div')).toHaveLength(1)
    getRequest.mockClear()
    await find(w, 'tr2-item-d8').trigger('click'); await flushPromises()
    expect(find(w, 'tr2-detail').find('.tr2-diff').exists()).toBe(false)
    expect(find(w, 'tr2-detail').text()).toContain(en.main.tr2_body.files.deferred_no_diff)
    expect(getRequest).not.toHaveBeenCalled()
  })

  it('ignores a slow previous file response, including when the next item is deferred', async () => {
    const w = body(); await flushPromises()
    let resolveOld!: (value: any) => void
    getRequest.mockImplementationOnce(() => new Promise(resolve => { resolveOld = resolve }))
    await find(w, 'tr2-item-e1').trigger('click')
    await find(w, 'tr2-item-e40').trigger('click'); await flushPromises()
    resolveOld({ data: { before_text: 'wrong old source' } }); await flushPromises()
    expect(find(w, 'tr2-detail').text()).toContain('live source')
    expect(find(w, 'tr2-detail').text()).not.toContain('wrong old source')
    getRequest.mockImplementationOnce(() => new Promise(resolve => { resolveOld = resolve }))
    await find(w, 'tr2-item-e1').trigger('click')
    await find(w, 'tr2-item-d8').trigger('click'); await flushPromises()
    resolveOld({ data: { before_text: 'wrong deferred source' } }); await flushPromises()
    expect(find(w, 'tr2-detail').text()).not.toContain('wrong deferred source')
  })

  it.each([true, false])('keeps browsing available with readOnly=%s and respects the server mutation lock', async readOnly => {
    current.mutation = { allowed: false, reason: 'recovery_required' }
    const w = body(readOnly); await flushPromises()
    await find(w, 'tr2-file-search').setValue('e40')
    await find(w, 'tr2-item-e40').trigger('click'); await flushPromises()
    expect(find(w, 'tr2-detail').text()).toContain('after 40')
    for (const id of ['tr2-add-item', 'tr2-item-edit', 'tr2-item-delete', 'tr2-upload', 'tr2-reset-whole']) expect(find(w, id).attributes('disabled')).toBeDefined()
    expect(find(w, 'tr2-item-download').attributes('disabled')).toBeUndefined()
    expect(postRequest).not.toHaveBeenCalled()
  })

  it('respects client read-only even when the server allows mutation', async () => {
    const w = body(true); await flushPromises()
    await find(w, 'tr2-filter-create_file').trigger('click')
    await find(w, 'tr2-item-c12').trigger('click'); await flushPromises()
    expect(find(w, 'tr2-detail').text()).toContain('new 12')
    expect(find(w, 'tr2-item-edit').attributes('disabled')).toBeDefined()
    expect(find(w, 'tr2-edit-whole').attributes('disabled')).toBeDefined()
    expect(find(w, 'tr2-download').attributes('disabled')).toBeUndefined()
  })

  it('clears document-specific controls and ignores pending file details when the document changes', async () => {
    const w = body(); await flushPromises()
    let resolveOld!: (value: any) => void
    getRequest.mockImplementationOnce(() => new Promise(resolve => { resolveOld = resolve }))
    await find(w, 'tr2-item-e1').trigger('click')
    await find(w, 'tr2-file-search').setValue('shared')
    await w.setProps({ tab: { ...tab, id: 'flowgate.default.0640.0010-TR2' } }); await flushPromises()
    resolveOld({ data: { before_text: 'previous document source' } }); await flushPromises()
    expect((find(w, 'tr2-file-search').element as HTMLInputElement).value).toBe('')
    expect(find(w, 'tr2-detail').text()).toBe(en.main.tr2_body.files.select_hint)
    expect((find(w, 'tr2-advanced').element as HTMLDetailsElement).open).toBe(false)
  })

  it.each([['en', en], ['ja', ja], ['ko', ko]] as const)('translates search, filters, management and hidden selection in %s', async (locale, messages) => {
    i18n.global.locale.value = locale
    const w = body(); await flushPromises()
    const strings = messages.main.tr2_body.workspace
    expect(find(w, 'tr2-file-search').attributes('aria-label')).toBe(strings.search)
    expect(find(w, 'tr2-filter-all').text()).toBe(strings.all)
    expect(find(w, 'tr2-advanced').text()).toContain(strings.advanced)
    await find(w, 'tr2-item-e40').trigger('click'); await flushPromises()
    await find(w, 'tr2-filter-deferred').trigger('click')
    expect(find(w, 'tr2-selection-hidden').text()).toContain(strings.hidden_selection)
    expect(w.text()).not.toContain('main.tr2_body.workspace.')
  })
})
