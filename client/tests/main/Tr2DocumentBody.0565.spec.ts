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

describe('tr2State — MirageGlass screens from the server read model', () => {
  const cases: Array<[string, Record<string, any>, string]> = [
    ['ready', {}, 'ready'],
    ['needs_more_work', { spec: { termination: 'needs_more_work' } }, 'needs_more_work'],
    ['drift', { live: { drift: true, live_fingerprint: 'sha256:other' } }, 'drift'],
    ['applying', { attempts: [attempt({ state: 'in_progress', phase: 'validation' })] }, 'applying'],
    ['rollback', { attempts: [attempt({ state: 'in_progress', phase: 'rollback' })] }, 'rollback'],
    ['recovery_required', { attempts: [attempt({ state: 'recovery_required', phase: 'rollback', error_code: 'tr2_recovery_required' })] }, 'recovery_required'],
    ['validation_failed', { attempts: [attempt({ error_code: 'tr2_validation_failed' })] }, 'validation_failed'],
    ['apply_failed', { attempts: [attempt({ error_code: 'tr2_apply_failed' })] }, 'apply_failed'],
    ['commit_failed', { attempts: [attempt({ error_code: 'tr2_commit_failed' })] }, 'commit_failed'],
    ['precheck drift', { attempts: [attempt({ error_code: 'tr2_source_drift' })] }, 'drift'],
    ['other failure', { attempts: [attempt({ error_code: 'tr2_worktree_dirty' })] }, 'precheck_failed'],
    ['old failure, new revision', { attempts: [attempt({ error_code: 'tr2_apply_failed', document_revision: 1 })] }, 'ready'],
    ['approved', { document: { doc_review_status: 'approved' }, attempts: [attempt({ state: 'succeeded' })] }, 'approved'],
    ['rejected', { document: { doc_review_status: 'rejected' } }, 'rejected'],
    ['time machine revert', { document: { doc_review_status: 'approved' }, history: 'restore_pending' }, 'restore_pending'],
    ['time machine conflict', { document: { doc_review_status: 'approved' }, history: 'conflict' }, 'history_conflict'],
    ['history invariant', { history: 'invariant_error' }, 'history_invariant'],
  ]
  it.each(cases)('%s', (_name, over, expected) => {
    expect(tr2State(makeView(over))).toBe(expected)
  })

  it('never renders rollback and recovery_required the same way', async () => {
    current = makeView({ attempts: [attempt({ state: 'in_progress', phase: 'rollback' })] })
    const rolling = mountBody(); await flushPromises()
    const rollingStrip = byTestId(rolling, 'tr2-state-strip')
    current = makeView({ attempts: [attempt({ state: 'recovery_required', phase: 'rollback', error_code: 'tr2_recovery_required', retryable: false })], mutation: { allowed: false, reason: 'recovery_required' }, retry: { allowed: false, reason: 'recovery_required' } })
    const stuck = mountBody(); await flushPromises()
    const stuckStrip = byTestId(stuck, 'tr2-state-strip')
    expect(rollingStrip.text()).toContain(en.main.tr2_body.state.rollback)
    expect(stuckStrip.text()).toContain(en.main.tr2_body.state.recovery_required)
    expect(rollingStrip.classes()).toContain('tr2-tone-live')
    expect(stuckStrip.classes()).toContain('tr2-tone-blocked')
    expect(byTestId(stuck, 'tr2-retry-blocked').text()).toBe(en.main.tr2_body.retry.blocked_recovery_required)
    expect(byTestId(stuck, 'tr2-lock').text()).toBe(en.main.tr2_body.lock.recovery_required)
    expect(byTestId(stuck, 'tr2-add-item').attributes('disabled')).toBeDefined()
  })
})

describe('TR2 body — detail, gate, attempts', () => {
  it('shows EDIT before/after, CREATE after-only and DEFERRED reason without a diff', async () => {
    const wrapper = mountBody(); await flushPromises()
    expect(wrapper.text()).toContain('EDIT 1 · CREATE 1 · DEFERRED 1')
    await byTestId(wrapper, 'tr2-item-e1').trigger('click'); await flushPromises()
    expect(getRequest).toHaveBeenCalledWith(`${base}/files/src/a.txt`)
    const detail = () => byTestId(wrapper, 'tr2-detail').text()
    expect(detail()).toContain('BEFORE'); expect(detail()).toContain('before line'); expect(detail()).toContain('after line')
    await byTestId(wrapper, 'tr2-item-c1').trigger('click'); await flushPromises()
    expect(detail()).toContain(en.main.tr2_body.files.after_only)
    expect(detail()).not.toContain(`${en.main.tr2_body.files.before}\n`)
    expect(detail()).toContain('brand new')
    await byTestId(wrapper, 'tr2-item-d1').trigger('click'); await flushPromises()
    expect(detail()).toContain(en.main.tr2_body.deferred_reason.needs_runtime)
    expect(detail()).toContain(en.main.tr2_body.files.deferred_no_diff)
  })

  it('shows gate results recorded by the attempt for this revision and the attempt history', async () => {
    current = makeView({ attempts: [attempt({ error_code: 'tr2_validation_failed', retryable: true, validation_json: JSON.stringify({ status: 'failed', commands: [{ exit_code: 0, timed_out: false }, { exit_code: 2, timed_out: false }] }) })] })
    const wrapper = mountBody(); await flushPromises()
    const gate = wrapper.findAll('.tr2-gate li')
    expect(gate[0].classes()).toContain('is-passed')
    expect(gate[1].classes()).toContain('is-failed')
    expect(byTestId(wrapper, 'tr2-attempts').text()).toContain(en.main.tr2_body.attempts.retryable)
    expect(byTestId(wrapper, 'tr2-state-strip').text()).toContain(en.main.tr2_body.state.validation_failed)
  })
})

describe('TR2 body — individual CRUD through the canonical writer', () => {
  it('adds an item with the current revision', async () => {
    const wrapper = mountBody(); await flushPromises()
    await byTestId(wrapper, 'tr2-add-item').trigger('click')
    const form = byTestId(wrapper, 'tr2-item-editor')
    await form.find('select[name=op]').setValue('create_file')
    await form.find('input[name=id]').setValue('c2')
    await form.find('input[name=file]').setValue('src/c2.txt')
    await form.find('textarea[name=content]').setValue('hello')
    await form.find('textarea[name=rationale]').setValue('why')
    await form.trigger('submit'); await flushPromises()
    expect(postRequest).toHaveBeenCalledWith(`${base}/items`, {
      expected_revision: 2, collection: 'edits',
      item: { id: 'c2', rationale: 'why', file: 'src/c2.txt', confidence: 'medium', kind: 'create_file', content: 'hello' },
    })
    expect(getRequest.mock.calls.filter(([url]) => url === base)).toHaveLength(2)
    expect(wrapper.text()).toContain('Saved revision 3.')
  })

  it('edits an item and can turn an edit into a deferred item', async () => {
    const wrapper = mountBody(); await flushPromises()
    await byTestId(wrapper, 'tr2-item-e1').trigger('click'); await flushPromises()
    await byTestId(wrapper, 'tr2-item-edit').trigger('click')
    const form = byTestId(wrapper, 'tr2-item-editor')
    await form.find('select[name=op]').setValue('deferred')
    await form.find('select[name=reason]').setValue('policy_direction')
    await form.trigger('submit'); await flushPromises()
    expect(putRequest).toHaveBeenCalledWith(`${base}/items/e1`, {
      expected_revision: 2, collection: 'deferred',
      item: { id: 'e1', file: 'src/a.txt', rationale: 'fix', reason: 'policy_direction' },
    })
  })

  it('deletes an item only after confirmation', async () => {
    const wrapper = mountBody(); await flushPromises()
    await byTestId(wrapper, 'tr2-item-d1').trigger('click'); await flushPromises()
    await byTestId(wrapper, 'tr2-item-delete').trigger('click')
    expect(deleteRequest).not.toHaveBeenCalled()
    expect(byTestId(wrapper, 'tr2-confirm').text()).toContain('Delete item d1?')
    await byTestId(wrapper, 'tr2-confirm-yes').trigger('click'); await flushPromises()
    expect(deleteRequest).toHaveBeenCalledWith(`${base}/items/d1?expected_revision=2`)
  })

  it('uploads an item to replace it and a new item to add it; downloads one item', async () => {
    const wrapper = mountBody(); await flushPromises()
    await byTestId(wrapper, 'tr2-item-e1').trigger('click'); await flushPromises()
    await fileInput(wrapper, 'tr2-item-upload', JSON.stringify({ collection: 'edits', item: { ...EDIT, replacement_new: 'uploaded' } })); await flushPromises()
    expect(putRequest).toHaveBeenCalledWith(`${base}/items/e1`, { expected_revision: 2, collection: 'edits', item: { ...EDIT, replacement_new: 'uploaded' } })
    await fileInput(wrapper, 'tr2-upload-new-item', JSON.stringify({ id: 'd2', reason: 'needs_runtime', rationale: 'later' })); await flushPromises()
    expect(postRequest).toHaveBeenCalledWith(`${base}/items`, { expected_revision: 2, collection: 'deferred', item: { id: 'd2', reason: 'needs_runtime', rationale: 'later' } })

    const blobs = captureDownloads()
    await byTestId(wrapper, 'tr2-item-e1').trigger('click'); await flushPromises()
    await byTestId(wrapper, 'tr2-item-download').trigger('click')
    expect(JSON.parse(await blobText(blobs[0]))).toEqual({ collection: 'edits', item: EDIT })
  })
})

describe('TR2 body — whole edit-spec CRUD, upload and download', () => {
  it('edits and saves the whole spec with revision CAS', async () => {
    const wrapper = mountBody(); await flushPromises()
    await byTestId(wrapper, 'tr2-edit-whole').trigger('click')
    const spec = { ...current.body.edit_spec, edits: [EDIT] }
    await byTestId(wrapper, 'tr2-whole-editor').find('textarea').setValue(JSON.stringify(spec))
    await byTestId(wrapper, 'tr2-save-whole').trigger('click'); await flushPromises()
    expect(putRequest).toHaveBeenCalledWith(base, { expected_revision: 2, body: { tr2_version: 1, source_t2_doc_id: current.body.source_t2_doc_id, edit_spec: spec } })
  })

  it('creates a new empty spec and clears the whole spec after confirmation', async () => {
    const wrapper = mountBody(); await flushPromises()
    await byTestId(wrapper, 'tr2-new-whole').trigger('click')
    expect(JSON.parse((byTestId(wrapper, 'tr2-whole-editor').find('textarea').element as HTMLTextAreaElement).value)).toEqual({ termination: 'needs_more_work', edits: [], deferred: [], gate: { commands: [], apply: false } })
    await byTestId(wrapper, 'tr2-reset-whole').trigger('click')
    expect(deleteRequest).not.toHaveBeenCalled()
    await byTestId(wrapper, 'tr2-confirm-yes').trigger('click'); await flushPromises()
    expect(deleteRequest).toHaveBeenCalledWith(`${base}/spec?expected_revision=2`)
  })

  it('uploads only edit_spec, never server-managed fields, and downloads without them', async () => {
    const wrapper = mountBody(); await flushPromises()
    const spec = { ...current.body.edit_spec, deferred: [] }
    await fileInput(wrapper, 'tr2-upload', JSON.stringify({ edit_spec: spec, baseline_fingerprint: 'sha256:forged', attempts: [{}], revision_no: 99 })); await flushPromises()
    expect(putRequest).toHaveBeenCalledWith(base, { expected_revision: 2, body: { tr2_version: 1, source_t2_doc_id: current.body.source_t2_doc_id, edit_spec: spec } })

    const blobs = captureDownloads()
    await byTestId(wrapper, 'tr2-download').trigger('click')
    expect(Object.keys(JSON.parse(await blobText(blobs[0])))).toEqual(['tr2_version', 'source_t2_doc_id', 'edit_spec'])
  })

  it('rejects invalid JSON before any request', async () => {
    const wrapper = mountBody(); await flushPromises()
    await fileInput(wrapper, 'tr2-upload', '{not json'); await flushPromises()
    expect(putRequest).not.toHaveBeenCalled()
    expect(wrapper.find('[role=alert]').text()).toContain('Invalid JSON')
  })
})

describe('TR2 body — CAS, validation and immutable-state feedback', () => {
  it('reports a stale revision and reloads on demand', async () => {
    putRequest.mockImplementationOnce(() => reject(409, { code: 'tr2_spec_changed', details: { current_revision_no: 5 } }))
    const wrapper = mountBody(); await flushPromises()
    await byTestId(wrapper, 'tr2-edit-whole').trigger('click')
    await byTestId(wrapper, 'tr2-save-whole').trigger('click'); await flushPromises()
    expect(wrapper.find('[role=alert]').text()).toContain('now revision 5')
    current = makeView({ document: { revision_no: 5 } })
    await byTestId(wrapper, 'tr2-reload').trigger('click'); await flushPromises()
    expect(wrapper.find('[role=alert]').exists()).toBe(false)
    expect(wrapper.text()).toContain('5')
  })

  it('shows the validator location and reason', async () => {
    postRequest.mockImplementationOnce(() => reject(422, { code: 'tr2_spec_invalid', details: { loc: 'edit_spec.edits[2].anchor_old', reason: 'non-empty string required' } }))
    const wrapper = mountBody(); await flushPromises()
    await byTestId(wrapper, 'tr2-add-item').trigger('click')
    const form = byTestId(wrapper, 'tr2-item-editor')
    await form.find('input[name=id]').setValue('e9'); await form.find('input[name=file]').setValue('x')
    await form.trigger('submit'); await flushPromises()
    expect(wrapper.find('[role=alert]').text()).toContain('edit_spec.edits[2].anchor_old: non-empty string required')
    expect(byTestId(wrapper, 'tr2-item-editor').exists()).toBe(true) // the draft is kept
  })

  it.each([
    ['approved', { document: { doc_review_status: 'approved' }, mutation: { allowed: false, reason: 'approved' } }],
    ['applying', { attempts: [attempt({ state: 'in_progress', phase: 'apply' })], mutation: { allowed: false, reason: 'applying' } }],
  ])('locks every mutation entry point while %s', async (reason, over) => {
    current = makeView(over)
    const wrapper = mountBody(); await flushPromises()
    for (const id of ['tr2-edit-whole', 'tr2-new-whole', 'tr2-reset-whole', 'tr2-add-item', 'tr2-upload', 'tr2-upload-new-item']) {
      expect(byTestId(wrapper, id).attributes('disabled'), id).toBeDefined()
    }
    expect(byTestId(wrapper, 'tr2-lock').text()).toBe((en.main.tr2_body.lock as any)[reason])
    expect(byTestId(wrapper, 'tr2-download').attributes('disabled')).toBeUndefined()
  })

  it('honours the AI-run read-only lock', async () => {
    const wrapper = mountBody(true); await flushPromises()
    expect(byTestId(wrapper, 'tr2-add-item').attributes('disabled')).toBeDefined()
    expect(byTestId(wrapper, 'tr2-lock').text()).toBe(en.main.tr2_body.lock.read_only)
  })
})

describe('TR2 body — retry', () => {
  it('retries through the approve route with the server-issued request key', async () => {
    current = makeView({ attempts: [attempt({ error_code: 'tr2_validation_failed', retryable: true })], retry: { allowed: true, mode: 'new_attempt', reason: null, request_key: 'retry:a1', attempt_id: 'a1' } })
    const openDocs = vi.fn(); window.addEventListener('fg:open_docs_refresh', openDocs)
    const wrapper = mountBody(); await flushPromises()
    expect(byTestId(wrapper, 'tr2-retry-block').text()).toContain(en.main.tr2_body.retry.available)
    await byTestId(wrapper, 'tr2-retry').trigger('click')
    expect(postRequest).not.toHaveBeenCalledWith('/api/v1/documents/review_transitions/approve', expect.anything())
    await byTestId(wrapper, 'tr2-confirm-yes').trigger('click'); await flushPromises()
    expect(postRequest).toHaveBeenCalledWith('/api/v1/documents/review_transitions/approve', {
      doc_id: docId, comment: null, expected_revision: 2, request_key: 'retry:a1',
    })
    expect(openDocs).toHaveBeenCalled()
    window.removeEventListener('fg:open_docs_refresh', openDocs)
  })

  it('offers no retry for a non-retryable failure and says why', async () => {
    current = makeView({ attempts: [attempt({ error_code: 'tr2_apply_failed', retryable: false })], retry: { allowed: false, reason: 'not_retryable' } })
    const wrapper = mountBody(); await flushPromises()
    expect(byTestId(wrapper, 'tr2-retry').exists()).toBe(false)
    expect(byTestId(wrapper, 'tr2-retry-blocked').text()).toBe(en.main.tr2_body.retry.blocked_not_retryable)
  })

  it('names stale-attempt recovery separately', async () => {
    current = makeView({ attempts: [attempt({ state: 'in_progress', phase: 'validation' })], retry: { allowed: true, mode: 'recover_stale', request_key: 'retry:a1' } })
    const wrapper = mountBody(); await flushPromises()
    expect(byTestId(wrapper, 'tr2-retry-block').text()).toContain(en.main.tr2_body.retry.stale)
  })
})

describe('TR2 body — SSE refresh', () => {
  const reads = () => getRequest.mock.calls.filter(([url]) => url === base).length

  it('re-reads on a newer revision, a review status change and a doc-scoped screen refresh', async () => {
    mountBody(); await flushPromises()
    window.dispatchEvent(new CustomEvent('fg:document_content_changed', { detail: { doc_id: docId, revision_no: 3 } })); await flushPromises()
    expect(reads()).toBe(2)
    window.dispatchEvent(new CustomEvent('fg:doc_review_status_changed', { detail: { doc_id: docId, next_status: 'approved' } })); await flushPromises()
    expect(reads()).toBe(3)
    window.dispatchEvent(new CustomEvent('fg:open_docs_refresh', { detail: { project: 'flowgate', doc_id: docId } })); await flushPromises()
    expect(reads()).toBe(4)
    window.dispatchEvent(new CustomEvent('fg:open_docs_refresh', { detail: { project: 'flowgate', doc_id: null } })); await flushPromises()
    expect(reads()).toBe(5)
  })

  it('ignores other documents, other projects and revisions it already has', async () => {
    mountBody(); await flushPromises()
    window.dispatchEvent(new CustomEvent('fg:document_content_changed', { detail: { doc_id: docId, revision_no: 2 } }))
    window.dispatchEvent(new CustomEvent('fg:open_docs_refresh', { detail: { project: 'flowgate', doc_id: 'other' } }))
    window.dispatchEvent(new CustomEvent('fg:open_docs_refresh', { detail: { project: 'elsewhere', doc_id: null } }))
    await flushPromises()
    expect(reads()).toBe(1)
  })

  it('does not overwrite an open editor; it warns instead', async () => {
    const wrapper = mountBody(); await flushPromises()
    await byTestId(wrapper, 'tr2-edit-whole').trigger('click')
    window.dispatchEvent(new CustomEvent('fg:document_content_changed', { detail: { doc_id: docId, revision_no: 7 } })); await flushPromises()
    expect(reads()).toBe(1)
    expect(wrapper.text()).toContain(en.main.tr2_body.errors.stale_while_editing)
  })
})

describe('TR2 body — ko / ja / en', () => {
  it.each([['ko', ko], ['ja', ja], ['en', en]] as const)('renders %s strings from the locale file', async (locale, messages) => {
    i18n.global.locale.value = locale
    current = makeView({ attempts: [attempt({ state: 'in_progress', phase: 'rollback' })] })
    const wrapper = mountBody(); await flushPromises()
    const tr2 = (messages as any).main.tr2_body
    const text = wrapper.text()
    for (const label of [tr2.actions.refresh, tr2.actions.diagnostic, tr2.actions.download, tr2.actions.upload,
      tr2.actions.add_item, tr2.files.heading, tr2.gate.heading, tr2.attempts.heading, tr2.state.rollback]) {
      expect(text).toContain(label)
    }
    if (locale !== 'en') expect(text).not.toContain(en.main.tr2_body.actions.diagnostic)
  })
})
