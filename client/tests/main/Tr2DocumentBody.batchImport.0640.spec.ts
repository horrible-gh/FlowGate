import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import en from '../../shared/i18n/en'
import ja from '../../shared/i18n/ja'
import ko from '../../shared/i18n/ko'
import Tr2DocumentBody from '@main/components/documents/Tr2DocumentBody.vue'
import { tr2State, type Tr2View } from '@main/components/documents/tr2State'

// 0565 T0028 — TR2 body: MirageGlass states, retry, whole/item CRUD, upload/download,
// CAS/immutable feedback, SSE refresh and ko/ja/en. The server contract these requests
// go to is exercised for real in server/tests/test_tr2_connected_e2e_0565.py.

const { getRequest, postRequest, putRequest, deleteRequest } = vi.hoisted(() => ({
  getRequest: vi.fn(), postRequest: vi.fn(), putRequest: vi.fn(), deleteRequest: vi.fn(),
}))
vi.mock('@shared/api', () => ({ getRequest, postRequest, putRequest, deleteRequest }))

const docId = 'flowgate.default.0565.0009-TR2'
const base = `/api/v1/documents/${encodeURIComponent(docId)}/tr2`
const tab = { id: docId, title: 'Connected TR2', type: 'md' as const, typeCode: 'TR2', path: 'document.json', projectId: 'flowgate' }
const EDIT = { id: 'e1', kind: 'edit', file: 'src/a.txt', anchor_old: 'before line', replacement_new: 'after line', confidence: 'high', rationale: 'fix' }
const CREATE = { id: 'c1', kind: 'create_file', file: 'src/new.txt', content: 'brand new', confidence: 'medium', rationale: 'add' }
const DEFER = { id: 'd1', reason: 'needs_runtime', rationale: 'runtime only', file: 'src/later.txt' }

function makeView(over: Record<string, any> = {}): Tr2View & Record<string, any> {
  const attempts = over.attempts ?? []
  return {
    document: { doc_id: docId, revision_no: 2, doc_review_status: 'pending_review', editable: true, ...(over.document ?? {}) },
    mutation: over.mutation ?? { allowed: true, reason: null },
    readiness: over.readiness ?? { ready: true, code: null, loc: null, reason: null, edits: [{ id: 'e1', status: 'applicable', applicable: true }, { id: 'c1', status: 'applicable', applicable: true }] },
    gate_admission: over.gateAdmission ?? {
      fingerprint: 'admission-fp', candidate_count: 0, all_candidate: false,
      commands: [
        { index: 0, command: 'pytest -q', state: 'registered', registry_row_id: 1, origin: 'manual', verified_os: 'posix', shell_complex: false },
        { index: 1, command: 'npm test', state: 'registered', registry_row_id: 2, origin: 'auto', verified_os: 'posix', shell_complex: false },
      ],
    },
    body: {
      tr2_version: 1, source_t2_doc_id: 'flowgate.default.0565.0008-T2', baseline_fingerprint: 'sha256:base',
      edit_spec: { termination: 'ready_to_apply', edits: [EDIT, CREATE], deferred: [DEFER], gate: { commands: ['pytest -q', 'npm test'], apply: false }, ...(over.spec ?? {}) },
    },
    derived: {
      files: [{ path: 'src/a.txt', kind: 'edit', exists: true, edit_ids: ['e1'] }, { path: 'src/new.txt', kind: 'create_file', exists: false, edit_ids: ['c1'] }],
      live_precheck: { drift: false, baseline_fingerprint: 'sha256:base', live_fingerprint: 'sha256:base', ...(over.live ?? {}) },
    },
    approval: { latest_attempt: attempts[0] ?? null, attempts, retry: over.retry ?? { allowed: false, reason: attempts.length ? 'in_progress' : 'no_attempt' } },
    history: { source_history_state: over.history ?? 'aligned', ledger: over.ledger ?? [] },
  }
}
function attempt(over: Record<string, any>) {
  return { attempt_id: 'a1', approval_round: 1, document_revision: 2, state: 'failed', phase: 'complete', error_code: null, retryable: null, ...over }
}

let current: ReturnType<typeof makeView>
const mounted: Array<ReturnType<typeof mount>> = []
function mountBody(readOnly = false) {
  const wrapper = mount(Tr2DocumentBody, { props: { tab, readOnly }, global: { plugins: [i18n] }, attachTo: document.body })
  mounted.push(wrapper)
  return wrapper
}
const byTestId = (wrapper: ReturnType<typeof mount>, id: string) => wrapper.find(`[data-testid="${id}"]`)
function fileInput(wrapper: ReturnType<typeof mount>, id: string, text: string) {
  const input = byTestId(wrapper, id).element as HTMLInputElement
  Object.defineProperty(input, 'files', { value: [{ text: () => Promise.resolve(text) }], configurable: true })
  return byTestId(wrapper, id).trigger('change')
}
const reject = (status: number, data: object) => Promise.reject({ response: { status, data } })
function blobText(blob: Blob): Promise<string> {
  return new Promise((resolve) => { const reader = new FileReader(); reader.onload = () => resolve(String(reader.result)); reader.readAsText(blob) })
}
/** jsdom has no object URLs: capture what a download would have saved. */
function captureDownloads(): Blob[] {
  const blobs: Blob[] = []
  Object.assign(URL, { createObjectURL: (blob: Blob) => { blobs.push(blob); return 'blob:tr2' }, revokeObjectURL: () => {} })
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {})
  return blobs
}

beforeEach(() => {
  i18n.global.locale.value = 'en'
  current = makeView()
  for (const fn of [getRequest, postRequest, putRequest, deleteRequest]) fn.mockReset()
  getRequest.mockImplementation((url: string) => Promise.resolve({ data: url.includes('/files/')
    ? { path: 'src/a.txt', kind: 'edit', before_text: 'before line\n', truncated: false, edits: [EDIT] }
    : current }))
  postRequest.mockResolvedValue({ data: { ready: true, code: null, worktree_clean: true, new_revision: 3 } })
  putRequest.mockResolvedValue({ data: { new_revision: 3 } })
  deleteRequest.mockResolvedValue({ data: { new_revision: 3 } })
})
afterEach(() => { mounted.forEach((wrapper) => wrapper.unmount()); mounted.length = 0; i18n.global.locale.value = 'en' })

async function batchFiles(wrapper: ReturnType<typeof mount>, files: Array<{ name: string; text: () => Promise<string> }>) {
  const node = byTestId(wrapper, 'tr2-upload-batch')
  Object.defineProperty(node.element, 'files', { value: files, configurable: true })
  await node.trigger('change')
  await flushPromises()
}
const jsonFile = (name: string, value: unknown) => ({ name, text: () => Promise.resolve(JSON.stringify(value)) })
const importedEdit = { ...EDIT, id: 'e2', replacement_new: 'imported' }
const importedCreate = { ...CREATE, id: 'c2', file: 'src/new2.txt' }
const importedDeferred = { ...DEFER, id: 'd2' }
function batchServer(validity?: boolean[]) {
  postRequest.mockImplementation((url: string, payload: any) => Promise.resolve({ data: url.endsWith('/batch/preview')
    ? { revision_no: payload.expected_revision, items: payload.items.map((_entry: unknown, index: number) => ({
      index, valid: validity?.[index] ?? true, error: validity?.[index] === false
        ? { code: 'tr2_spec_invalid', details: { loc: `items[${index}].item.id`, reason: 'duplicate id' } } : null,
    })) }
    : { new_revision: 3, imported_count: payload.items.length } }))
}

describe('0640 T#2 JSON batch import', () => {
  it('previews mixed JSON files, selects only valid rows and imports checked rows once', async () => {
    batchServer()
    const wrapper = mountBody(); await flushPromises()
    expect(byTestId(wrapper, 'tr2-upload-batch').attributes('multiple')).toBeDefined()
    const changed = vi.fn()
    window.addEventListener('fg:document_content_changed', changed)
    try {
      await batchFiles(wrapper, [
        jsonFile('edit.json', importedEdit),
        jsonFile('create.json', { collection: 'edits', item: importedCreate }),
        jsonFile('deferred.json', importedDeferred),
        { name: 'broken.json', text: () => Promise.resolve('{not json') },
        jsonFile('array.json', []),
        jsonFile('collection.json', { collection: 'unknown', item: importedEdit }),
      ])
      expect(postRequest).toHaveBeenCalledTimes(1)
      expect(postRequest).toHaveBeenCalledWith(`${base}/items/batch/preview`, {
        expected_revision: 2, items: [
          { collection: 'edits', item: importedEdit }, { collection: 'edits', item: importedCreate },
          { collection: 'deferred', item: importedDeferred },
        ],
      })
      expect(putRequest).not.toHaveBeenCalled()
      expect(changed).not.toHaveBeenCalled()
      expect(byTestId(wrapper, 'tr2-batch-count').text()).toContain('6 files · 3 valid · 3 selected')
      expect(byTestId(wrapper, 'tr2-batch-row-0').text()).toContain('src/a.txt')
      expect(byTestId(wrapper, 'tr2-batch-row-1').find('pre').text()).toContain('brand new')
      expect(byTestId(wrapper, 'tr2-batch-row-2').text()).toContain('DEFERRED')
      for (const index of [3, 4, 5]) expect(byTestId(wrapper, `tr2-batch-select-${index}`).attributes('disabled')).toBeDefined()
      await byTestId(wrapper, 'tr2-batch-select-1').setValue(false)
      await byTestId(wrapper, 'tr2-batch-import').trigger('click'); await flushPromises()
      expect(postRequest).toHaveBeenCalledTimes(2)
      expect(postRequest).toHaveBeenLastCalledWith(`${base}/items/batch`, { expected_revision: 2, items: [
        { collection: 'edits', item: importedEdit }, { collection: 'deferred', item: importedDeferred },
      ] })
      expect(byTestId(wrapper, 'tr2-batch-preview').exists()).toBe(false)
      expect(wrapper.text()).toContain('Saved revision 3.')
      expect(changed).toHaveBeenCalledTimes(1)
    } finally { window.removeEventListener('fg:document_content_changed', changed) }
  })

  it('shows server schema and duplicate errors per file without importing invalid rows', async () => {
    batchServer([false, true, false])
    const wrapper = mountBody(); await flushPromises()
    await batchFiles(wrapper, [jsonFile('existing.json', EDIT), jsonFile('good.json', importedEdit), jsonFile('bad.json', { id: 'bad' })])
    expect(byTestId(wrapper, 'tr2-batch-row-0').text()).toContain('duplicate id')
    expect(byTestId(wrapper, 'tr2-batch-select-0').attributes('disabled')).toBeDefined()
    await byTestId(wrapper, 'tr2-batch-import').trigger('click'); await flushPromises()
    expect(postRequest).toHaveBeenLastCalledWith(`${base}/items/batch`, {
      expected_revision: 2, items: [{ collection: 'edits', item: importedEdit }],
    })
  })

  it('allows cancelling and reselecting the same files without saving', async () => {
    batchServer()
    const wrapper = mountBody(); await flushPromises()
    await batchFiles(wrapper, [jsonFile('same.json', importedEdit)])
    await byTestId(wrapper, 'tr2-batch-cancel').trigger('click')
    expect(byTestId(wrapper, 'tr2-batch-preview').exists()).toBe(false)
    expect(postRequest).toHaveBeenCalledTimes(1)
    await batchFiles(wrapper, [jsonFile('same.json', importedEdit)])
    expect(postRequest).toHaveBeenCalledTimes(2)
    expect((byTestId(wrapper, 'tr2-upload-batch').element as HTMLInputElement).value).toBe('')
    await byTestId(wrapper, 'tr2-batch-select-0').setValue(false)
    expect(byTestId(wrapper, 'tr2-batch-import').attributes('disabled')).toBeDefined()
  })

  it('keeps read errors local and refuses an all-invalid batch', async () => {
    batchServer()
    const wrapper = mountBody(); await flushPromises()
    await batchFiles(wrapper, [{ name: 'unreadable.json', text: () => Promise.reject(new Error('read failed')) }, jsonFile('null.json', null)])
    expect(postRequest).not.toHaveBeenCalled()
    expect(byTestId(wrapper, 'tr2-batch-row-0').text()).toContain('read failed')
    expect(byTestId(wrapper, 'tr2-batch-import').attributes('disabled')).toBeDefined()
  })

  it('reports preview revision conflicts and reload clears the draft', async () => {
    postRequest.mockImplementation(() => reject(409, { code: 'tr2_spec_changed', details: { current_revision_no: 3 } }))
    const wrapper = mountBody(); await flushPromises()
    await batchFiles(wrapper, [jsonFile('new.json', importedEdit)])
    expect(byTestId(wrapper, 'tr2-reload').exists()).toBe(true)
    expect(byTestId(wrapper, 'tr2-batch-import').attributes('disabled')).toBeDefined()
    current = makeView({ document: { revision_no: 3 } })
    await byTestId(wrapper, 'tr2-reload').trigger('click'); await flushPromises()
    expect(byTestId(wrapper, 'tr2-batch-preview').exists()).toBe(false)
    batchServer()
    await batchFiles(wrapper, [jsonFile('new.json', importedEdit)])
    expect(postRequest).toHaveBeenLastCalledWith(`${base}/items/batch/preview`, {
      expected_revision: 3, items: [{ collection: 'edits', item: importedEdit }],
    })
  })

  it('uses the preview revision for save and preserves the draft on CAS rejection', async () => {
    batchServer()
    const wrapper = mountBody(); await flushPromises()
    await batchFiles(wrapper, [jsonFile('new.json', importedEdit)])
    postRequest.mockImplementationOnce(() => reject(409, { code: 'tr2_spec_changed' }))
    await byTestId(wrapper, 'tr2-batch-import').trigger('click'); await flushPromises()
    expect(postRequest).toHaveBeenLastCalledWith(`${base}/items/batch`, { expected_revision: 2, items: [{ collection: 'edits', item: importedEdit }] })
    expect(byTestId(wrapper, 'tr2-batch-preview').exists()).toBe(true)
    expect(byTestId(wrapper, 'tr2-batch-stale').exists()).toBe(true)
    expect(byTestId(wrapper, 'tr2-batch-import').attributes('disabled')).toBeDefined()
    expect(wrapper.find('[role=status]').text()).not.toContain('Saved')
  })

  it('blocks import after an SSE revision change without losing the draft', async () => {
    batchServer()
    const wrapper = mountBody(); await flushPromises()
    await batchFiles(wrapper, [jsonFile('new.json', importedEdit)])
    const reads = getRequest.mock.calls.length
    window.dispatchEvent(new CustomEvent('fg:document_content_changed', { detail: { doc_id: docId, revision_no: 3 } }))
    await flushPromises()
    expect(getRequest).toHaveBeenCalledTimes(reads)
    expect(byTestId(wrapper, 'tr2-batch-stale').exists()).toBe(true)
    expect(byTestId(wrapper, 'tr2-batch-import').attributes('disabled')).toBeDefined()
    current = makeView({ document: { revision_no: 3 } })
    await byTestId(wrapper, 'tr2-batch-reload').trigger('click'); await flushPromises()
    expect(byTestId(wrapper, 'tr2-batch-preview').exists()).toBe(false)
    expect(byTestId(wrapper, 'tr2-revision').text()).toBe('3')
  })

  it('blocks a draft whose captured revision differs after manual refresh', async () => {
    batchServer()
    const wrapper = mountBody(); await flushPromises()
    await batchFiles(wrapper, [jsonFile('new.json', importedEdit)])
    current = makeView({ document: { revision_no: 3 } })
    await (wrapper.vm as any).refresh(); await flushPromises()
    expect(byTestId(wrapper, 'tr2-batch-stale').exists()).toBe(true)
    expect(byTestId(wrapper, 'tr2-batch-import').attributes('disabled')).toBeDefined()
  })

  it.each(['read', 'preview'])('discards a delayed %s after document switch', async (phase) => {
    let finish!: (value: any) => void
    const delayed = new Promise<any>(resolve => { finish = resolve })
    batchServer()
    if (phase === 'preview') postRequest.mockImplementation(() => delayed)
    const wrapper = mountBody(); await flushPromises()
    const files = phase === 'read' ? [{ name: 'delayed.json', text: () => delayed }] : [jsonFile('new.json', importedEdit)]
    await batchFiles(wrapper, files)
    await wrapper.setProps({ tab: { ...tab, id: 'flowgate.default.0640.0010-TR2' } }); await flushPromises()
    finish(phase === 'read' ? JSON.stringify(importedEdit) : { data: { items: [{ index: 0, valid: true, error: null }] } })
    await flushPromises()
    expect(byTestId(wrapper, 'tr2-batch-preview').exists()).toBe(false)
    expect(postRequest).toHaveBeenCalledTimes(phase === 'read' ? 0 : 1)
  })

  it.each([409, 500])('keeps the draft and reports save failure %s', async status => {
    batchServer()
    const wrapper = mountBody(); await flushPromises()
    await batchFiles(wrapper, [jsonFile('new.json', importedEdit)])
    postRequest.mockImplementationOnce(() => reject(status, { code: status === 409 ? 'tr2_spec_immutable' : 'tr2_internal_error' }))
    await byTestId(wrapper, 'tr2-batch-import').trigger('click'); await flushPromises()
    expect(wrapper.find('[role=alert]').exists()).toBe(true)
    expect(byTestId(wrapper, 'tr2-batch-preview').exists()).toBe(true)
    expect(putRequest).not.toHaveBeenCalled()
  })

  it.each([true, false])('respects client/server read-only admission (%s)', async readOnly => {
    if (!readOnly) current = makeView({ mutation: { allowed: false, reason: 'applying' } })
    const wrapper = mountBody(readOnly); await flushPromises()
    expect(byTestId(wrapper, 'tr2-upload-batch').attributes('disabled')).toBeDefined()
    await batchFiles(wrapper, [jsonFile('new.json', importedEdit)])
    expect(postRequest).not.toHaveBeenCalled()
  })

  it('rejects more than 200 files before reading them', async () => {
    const text = vi.fn(() => Promise.resolve(JSON.stringify(importedEdit)))
    const wrapper = mountBody(); await flushPromises()
    await batchFiles(wrapper, Array.from({ length: 201 }, (_, index) => ({ name: `${index}.json`, text })))
    expect(text).not.toHaveBeenCalled()
    expect(postRequest).not.toHaveBeenCalled()
    expect(wrapper.find('[role=alert]').text()).toContain('200')
  })

  it('preserves raw and wrapped single-item upload endpoints and replacement', async () => {
    const wrapper = mountBody(); await flushPromises()
    await fileInput(wrapper, 'tr2-upload-new-item', JSON.stringify(importedEdit)); await flushPromises()
    expect(postRequest).toHaveBeenCalledWith(`${base}/items`, { expected_revision: 2, collection: 'edits', item: importedEdit })
    await byTestId(wrapper, 'tr2-item-e1').trigger('click'); await flushPromises()
    await fileInput(wrapper, 'tr2-item-upload', JSON.stringify({ collection: 'edits', item: { ...EDIT, replacement_new: 'single replace' } })); await flushPromises()
    expect(putRequest).toHaveBeenCalledWith(`${base}/items/e1`, { expected_revision: 2, collection: 'edits', item: { ...EDIT, replacement_new: 'single replace' } })
  })

  it.each([['en', en], ['ko', ko], ['ja', ja]] as const)('renders batch actions and hint in %s', async (locale, messages) => {
    i18n.global.locale.value = locale
    batchServer()
    const wrapper = mountBody(); await flushPromises()
    expect(byTestId(wrapper, 'tr2-upload-batch').element.parentElement!.textContent).toContain(messages.main.tr2_body.batch.upload)
    await batchFiles(wrapper, [jsonFile('new.json', importedEdit)])
    expect(byTestId(wrapper, 'tr2-batch-preview').text()).toContain(messages.main.tr2_body.batch.hint)
    expect(wrapper.text()).not.toContain('main.tr2_body.batch.')
  })
})

describe('0640 corrective import dialog and drop handling', () => {
  it('opens the tabbed dialog and discards all pending files on Escape', async () => {
    batchServer()
    const wrapper = mountBody(); await flushPromises()
    const backdrop = byTestId(wrapper, 'tr2-import-dialog')
    expect(backdrop.attributes('style')).toContain('display: none')
    await byTestId(wrapper, 'tr2-open-import').trigger('click'); await flushPromises()
    expect(backdrop.attributes('style') ?? '').not.toContain('display: none')
    expect(backdrop.find('[role="dialog"]').attributes('aria-modal')).toBe('true')
    await byTestId(wrapper, 'tr2-import-tab-json').trigger('click')
    await batchFiles(wrapper, [jsonFile('a.json', importedCreate)])
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }))
    await flushPromises()
    expect(byTestId(wrapper, 'tr2-batch-preview').exists()).toBe(false)
    expect(backdrop.attributes('style')).toContain('display: none')
    expect(postRequest.mock.calls.filter(([url]) => String(url).endsWith('/batch'))).toHaveLength(0)
  })

  it('drops 50 JSON files, preserves valid file order, saves once and reloads the new revision', async () => {
    batchServer([...Array(48).fill(true), false])
    const wrapper = mountBody(); await flushPromises()
    await byTestId(wrapper, 'tr2-open-import').trigger('click')
    await byTestId(wrapper, 'tr2-import-tab-json').trigger('click')
    const files = Array.from({ length: 50 }, (_, index) => jsonFile(`${index}.json`, { ...CREATE, id: `drop-${index}`, file: `src/drop-${index}.txt` }))
    files[47] = { name: 'malformed.json', text: () => Promise.resolve('{invalid') }
    const event = new Event('drop', { bubbles: true, cancelable: true })
    Object.defineProperty(event, 'dataTransfer', { value: { files } })
    byTestId(wrapper, 'tr2-json-dropzone').element.dispatchEvent(event); await flushPromises()
    expect(byTestId(wrapper, 'tr2-batch-count').text()).toBe('50 files · 48 valid · 48 selected')
    const selected = postRequest.mock.calls[0][1].items.filter((_item: unknown, index: number) => index < 48)
    postRequest.mockImplementation(async () => {
      current.document.revision_no = 3
      current.body.edit_spec.edits.push(...selected.map((entry: any) => entry.item))
      return { data: { new_revision: 3 } }
    })
    await byTestId(wrapper, 'tr2-batch-import').trigger('click'); await flushPromises()
    expect(postRequest).toHaveBeenCalledTimes(2)
    expect(postRequest.mock.calls[1]).toEqual([`${base}/items/batch`, { expected_revision: 2, items: selected }])
    expect(byTestId(wrapper, 'tr2-file-list').findAll('li')).toHaveLength(51)
    expect(byTestId(wrapper, 'tr2-revision').text()).toBe('3')
    expect(byTestId(wrapper, 'tr2-import-dialog').attributes('style')).toContain('display: none')
  })

  it('rejects oversized dropped JSON before reading and ignores drops while read-only', async () => {
    const text = vi.fn(() => Promise.resolve('{}'))
    const wrapper = mountBody(); await flushPromises()
    const event = () => { const e = new Event('drop', { bubbles: true, cancelable: true }); Object.defineProperty(e, 'dataTransfer', { value: { files: [{ name: 'large.json', size: 1024 * 1024 + 1, text }] } }); return e }
    byTestId(wrapper, 'tr2-json-dropzone').element.dispatchEvent(event()); await flushPromises()
    expect(text).not.toHaveBeenCalled()
    expect(byTestId(wrapper, 'tr2-batch-row-0').text()).toContain('1 MiB')
    expect(postRequest).not.toHaveBeenCalled()
    await wrapper.setProps({ readOnly: true })
    byTestId(wrapper, 'tr2-json-dropzone').element.dispatchEvent(event()); await flushPromises()
    expect(text).not.toHaveBeenCalled(); expect(postRequest).not.toHaveBeenCalled()
  })
})
