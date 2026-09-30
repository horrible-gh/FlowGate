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

  it('shows admission separately from runtime state and keeps the full normalized command', async () => {
    const full = 'cd client && npm test -- --reporter=verbose'
    current = makeView({
      spec: { gate: { commands: [full, 'blocked command'], apply: false } },
      gateAdmission: {
        fingerprint: 'candidate-fp', candidate_count: 1, all_candidate: false,
        commands: [
          { index: 0, command: full, state: 'candidate', registry_row_id: null, origin: null, verified_os: null, shell_complex: true },
          { index: 1, command: 'blocked command', state: 'suppressed', registry_row_id: 9, origin: 'manual', verified_os: null, shell_complex: false },
        ],
      },
    })
    const wrapper = mountBody(); await flushPromises()
    const rows = wrapper.findAll('.tr2-gate li')
    expect(rows[0].text()).toContain(full)
    expect(byTestId(wrapper, 'tr2-admission-0').text()).toBe(en.main.tr2_body.gate.admission_candidate)
    expect(byTestId(wrapper, 'tr2-admission-1').text()).toBe(en.main.tr2_body.gate.admission_suppressed)
    expect(byTestId(wrapper, 'tr2-shell-complex-warning').text()).toBe(en.main.tr2_body.gate.shell_complex)
    expect(rows[0].text()).toContain(en.main.tr2_body.gate.waiting)
  })

  it('shows all-candidate warning without inventing a client-side deny', async () => {
    current = makeView({
      gateAdmission: {
        fingerprint: 'all-fp', candidate_count: 2, all_candidate: true,
        commands: [
          { index: 0, command: 'pytest -q', state: 'candidate', registry_row_id: null, origin: null, verified_os: null, shell_complex: false },
          { index: 1, command: 'npm test', state: 'candidate', registry_row_id: null, origin: null, verified_os: null, shell_complex: false },
        ],
      },
    })
    const wrapper = mountBody(); await flushPromises()
    expect(byTestId(wrapper, 'tr2-all-candidate-warning').text()).toBe(en.main.tr2_body.gate.all_candidate)
    expect(byTestId(wrapper, 'tr2-state-strip').text()).toContain(en.main.tr2_body.state.ready)
  })

  it('keeps suppressed admission visibly blocked by server readiness', async () => {
    current = makeView({
      readiness: {
        ready: false,
        code: 'tr2_validation_command_unapproved',
        loc: 'edit_spec.gate.commands[0]',
        reason: 'suppressed',
        edits: [],
      },
      spec: { gate: { commands: ['blocked command'], apply: false } },
      gateAdmission: {
        fingerprint: 'suppressed-fp',
        candidate_count: 0,
        all_candidate: false,
        commands: [
          { index: 0, command: 'blocked command', state: 'suppressed', registry_row_id: 9, origin: 'manual', verified_os: null, shell_complex: false },
        ],
      },
    })
    const wrapper = mountBody(); await flushPromises()
    expect(byTestId(wrapper, 'tr2-admission-0').text()).toBe(en.main.tr2_body.gate.admission_suppressed)
    expect(byTestId(wrapper, 'tr2-state-strip').text()).toContain(en.main.tr2_body.state.not_ready)
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

// ── 0565 T0030 — authoritative readiness, error meaning, proposal recovery ─────────

describe('T0030 — ready only on the server word', () => {
  it('is not ready when the server readiness says an anchor is missing, even without drift', () => {
    const view = makeView({ readiness: { ready: false, code: 'tr2_edit_not_applicable', loc: 'e1', reason: 'anchor_missing', edits: [{ id: 'e1', status: 'anchor_missing', applicable: false }] } })
    expect(view.derived.live_precheck.drift).toBe(false)
    expect(tr2State(view)).toBe('not_ready')
  })

  it('never calls a view ready without a server readiness', () => {
    const view = makeView()
    delete (view as any).readiness
    expect(tr2State(view)).toBe('not_ready')
  })

  it('names the blocking reason in the strip and on the item', async () => {
    current = makeView({ readiness: { ready: false, code: 'tr2_edit_not_applicable', loc: 'e1', reason: 'anchor_missing', edits: [{ id: 'e1', status: 'anchor_missing', applicable: false }] } })
    const wrapper = mountBody(); await flushPromises()
    const strip = byTestId(wrapper, 'tr2-state-strip')
    expect(strip.classes()).toContain('tr2-tone-danger')
    expect(strip.text()).toContain(en.main.tr2_body.state.not_ready)
    expect(strip.text()).toContain('e1: the BEFORE text (anchor) is not in the target file.')
    await byTestId(wrapper, 'tr2-item-e1').trigger('click'); await flushPromises()
    expect(byTestId(wrapper, 'tr2-item-status').text()).toContain('e1: the BEFORE text (anchor) is not in the target file.')
  })

  it('shows a stored proposal whose worktree is unavailable as not ready, never as matching', async () => {
    current = makeView({
      readiness: { ready: false, code: 'tr2_git_unavailable', loc: 'source_root', reason: 'worktree_unregistered', edits: [] },
      live: { source_available: false, drift: null, live_fingerprint: null, anchors: null },
    })
    const wrapper = mountBody(); await flushPromises()
    expect(tr2State(current)).toBe('not_ready')
    const strip = byTestId(wrapper, 'tr2-state-strip')
    expect(strip.classes()).toContain('tr2-tone-danger')
    expect(strip.text()).toContain(en.main.tr2_body.state.not_ready)
    expect(byTestId(wrapper, 'tr2-diag-strip').text()).toContain(en.main.tr2_body.diag.unavailable)
    expect(byTestId(wrapper, 'tr2-diag-strip').text()).not.toContain(en.main.tr2_body.diag.match + '')
    expect(wrapper.text()).toContain('e1')
  })

  it.each([
    ['tr2_worktree_dirty', null, 'The working tree has uncommitted changes.'],
    ['tr2_validation_command_unapproved', null, 'A validation command is not registered.'],
    ['tr2_edit_not_applicable', 'file_exists', 'c1: the file to create already exists.'],
  ])('explains %s', async (code, reason, text) => {
    current = makeView({ readiness: { ready: false, code, loc: 'c1', reason, edits: [] } })
    const wrapper = mountBody(); await flushPromises()
    expect(byTestId(wrapper, 'tr2-state-strip').text()).toContain(text)
  })
})

describe('T0030 — every failure says what it means, never how the server broke', () => {
  it.each([
    [500, { code: 'tr2_internal_error', message: 'Internal server error' }, en.main.tr2_body.errors.internal],
    [500, { detail: 'Traceback (most recent call last): FileNotFoundError C:\\storage\\x.json' }, en.main.tr2_body.errors.internal],
    [409, { code: 'tr2_spec_immutable', details: { reason: 'approved' } }, 'It cannot be changed in its current state: approved'],
    [409, { code: 'tr2_worktree_dirty' }, 'Pre-approval check failed: The working tree has uncommitted changes.'],
    [422, { code: 'tr2_validation_failed' }, 'Applying to the actual work failed: A validation command failed.'],
    [422, { detail: 'Document is not editable' }, en.main.tr2_body.errors.not_editable],
  ])('%s %j', async (status, data, expected) => {
    putRequest.mockImplementationOnce(() => reject(status, data))
    const wrapper = mountBody(); await flushPromises()
    await byTestId(wrapper, 'tr2-edit-whole').trigger('click')
    await byTestId(wrapper, 'tr2-save-whole').trigger('click'); await flushPromises()
    const alert = wrapper.find('[role=alert]').text()
    expect(alert).toContain(expected)
    expect(alert).not.toMatch(/Traceback|[A-Za-z]:\\|rejected the proposal/)
  })

  it('reports a network failure while loading as a load failure', async () => {
    getRequest.mockImplementation(() => Promise.reject(new Error('socket hang up')))
    const wrapper = mountBody(); await flushPromises()
    expect(wrapper.find('[role=alert]').text()).toBe('Could not load the proposal: Cannot reach the server.')
  })
})

function recoveryState(over: Record<string, any> = {}) {
  return {
    doc_id: docId, revision_no: 3, state: 'tr2_body_missing',
    error: { code: 'tr2_body_missing', loc: 'document.json', reason: 'proposal file does not exist' },
    approval_blocked: true,
    revisions: [
      { revision_no: 3, created_at: '2026-09-28T10:00:00+09:00', origin: 'human', size: 900, sha256: 'a', usable: true, problem: null, is_current: true },
      { revision_no: 2, created_at: '2026-09-28T09:00:00+09:00', origin: 'human', size: 800, sha256: 'b', usable: true, problem: null, is_current: false },
      { revision_no: 1, created_at: '2026-09-28T08:00:00+09:00', origin: 'ai', size: 700, sha256: 'c', usable: false, problem: 'tr2_body_corrupt', is_current: false },
    ],
    recommended_revision_no: 3, recoverable: true,
    raw: { available: false, size: null, filename: null },
    mutation: { allowed: true, reason: null },
    ...over,
  }
}
function brokenServer(recovery: Record<string, any>, code = 'tr2_body_missing') {
  getRequest.mockImplementation((url: string) => {
    if (url === base) return reject(409, { code, recovery: true, details: { loc: 'document.json' } })
    if (url === `${base}/recovery`) return Promise.resolve({ data: recovery })
    if (url === `${base}/revisions/2`) return Promise.resolve({ data: { revision_no: 2, content: '{"edit_spec":{"edits":[]}}', usable: true } })
    if (url === `${base}/raw`) return Promise.resolve({ data: { filename: '0009-TR2_document.json', content: '{"broken": ', size: 11 } })
    return Promise.resolve({ data: current })
  })
}

describe('T0030 — 반영안 복구 (proposal recovery), apart from the apply rollback', () => {
  it('replaces the dead end with the recovery surface and restores a revision as a new one', async () => {
    brokenServer(recoveryState())
    const wrapper = mountBody(); await flushPromises()
    expect(byTestId(wrapper, 'tr2-state-strip').exists()).toBe(false)
    const panel = byTestId(wrapper, 'tr2-recovery')
    expect(panel.text()).toContain(en.main.tr2_body.errors.body_missing)
    expect(panel.text()).toContain(en.main.tr2_body.recovery.state.tr2_body_missing)
    expect(panel.text()).toContain(en.main.tr2_body.recovery.blocked)
    expect(panel.text()).toContain(en.main.tr2_body.recovery.recoverable)
    expect(byTestId(wrapper, 'tr2-revision-1').text()).toContain('unusable: Proposal file damaged')
    expect(byTestId(wrapper, 'tr2-revision-restore-1').attributes('disabled')).toBeDefined()
    for (const id of ['tr2-edit-whole', 'tr2-reset-whole', 'tr2-upload']) expect(byTestId(wrapper, id).attributes('disabled'), id).toBeDefined()

    await byTestId(wrapper, 'tr2-revision-view-2').trigger('click'); await flushPromises()
    expect(byTestId(wrapper, 'tr2-revision-content-2').text()).toContain('"edit_spec"')
    const blobs = captureDownloads()
    await byTestId(wrapper, 'tr2-revision-download-2').trigger('click'); await flushPromises()
    expect(await blobText(blobs[0])).toBe('{"edit_spec":{"edits":[]}}') // exact saved bytes

    postRequest.mockResolvedValueOnce({ data: { new_revision: 4, restored_from_revision: 2 } })
    await byTestId(wrapper, 'tr2-revision-restore-2').trigger('click')
    expect(postRequest).not.toHaveBeenCalled()
    getRequest.mockImplementation((url: string) => Promise.resolve({ data: url === `${base}/recovery` ? recoveryState({ state: 'ok', error: null }) : makeView({ document: { revision_no: 4 } }) }))
    await byTestId(wrapper, 'tr2-recovery-confirm-yes').trigger('click'); await flushPromises()
    expect(postRequest).toHaveBeenCalledWith(`${base}/recovery/restore`, { expected_revision: 3, revision_no: 2 })
    expect(byTestId(wrapper, 'tr2-recovery').exists()).toBe(false)
    expect(byTestId(wrapper, 'tr2-revision').text()).toBe('4')
    expect(wrapper.text()).toContain('Created revision 4 from revision 2.')
  })

  it('fails closed when nothing can be recovered: shows the remaining content and offers a new proposal', async () => {
    brokenServer(recoveryState({ state: 'tr2_body_corrupt', error: { code: 'tr2_body_corrupt', loc: 'document.json', reason: 'invalid JSON at line 1' },
      revisions: [], recommended_revision_no: null, recoverable: false, raw: { available: true, size: 11, filename: '0009-TR2_document.json' } }), 'tr2_body_corrupt')
    const wrapper = mountBody(); await flushPromises()
    expect(byTestId(wrapper, 'tr2-recovery-verdict').text()).toBe(en.main.tr2_body.recovery.unrecoverable)
    expect(byTestId(wrapper, 'tr2-recovery-state').text()).toContain('Location: document.json · invalid JSON at line 1')
    await byTestId(wrapper, 'tr2-raw-view').trigger('click'); await flushPromises()
    expect(byTestId(wrapper, 'tr2-raw-content').text()).toBe('{"broken":')
    postRequest.mockResolvedValueOnce({ data: { new_revision: 4 } })
    await byTestId(wrapper, 'tr2-new-proposal').trigger('click')
    expect(byTestId(wrapper, 'tr2-recovery-confirm').text()).toContain(en.main.tr2_body.confirm.new_proposal)
    await byTestId(wrapper, 'tr2-recovery-confirm-yes').trigger('click'); await flushPromises()
    expect(postRequest).toHaveBeenCalledWith(`${base}/recovery/new`, { expected_revision: 3 })
  })

  it('keeps the recovery surface read-only while the group is locked', async () => {
    brokenServer(recoveryState({ mutation: { allowed: false, reason: 'not_editable' } }))
    const wrapper = mountBody(); await flushPromises()
    expect(byTestId(wrapper, 'tr2-revision-restore-3').attributes('disabled')).toBeDefined()
    expect(byTestId(wrapper, 'tr2-new-proposal').attributes('disabled')).toBeDefined()
  })

  it('offers the revision history on a healthy proposal', async () => {
    const wrapper = mountBody(); await flushPromises()
    getRequest.mockImplementation((url: string) => Promise.resolve({ data: url === `${base}/recovery` ? recoveryState({ state: 'ok', error: null, revision_no: 2,
      revisions: recoveryState().revisions.slice(1).map((r) => ({ ...r, is_current: r.revision_no === 2 })) }) : current }))
    const toggle = byTestId(wrapper, 'tr2-history-toggle')
    ;(toggle.element as HTMLDetailsElement).open = true
    await toggle.trigger('toggle'); await flushPromises()
    expect(byTestId(wrapper, 'tr2-revision-history').text()).toContain(en.main.tr2_body.recovery.history_hint)
    expect(byTestId(wrapper, 'tr2-revision-restore-2').attributes('disabled')).toBeDefined() // current: nothing to restore
    expect(byTestId(wrapper, 'tr2-new-proposal').exists()).toBe(false)
  })
})

describe('T0030 — terms', () => {
  it.each([['ko', ko, '반영안'], ['ja', ja, '反映案'], ['en', en, 'Apply proposal']] as const)('%s names TR2 %s and says approval applies it', async (locale, messages, name) => {
    i18n.global.locale.value = locale
    const wrapper = mountBody(); await flushPromises()
    expect(byTestId(wrapper, 'tr2-chip').text()).toBe(`TR2 · ${name}`)
    expect(byTestId(wrapper, 'tr2-apply-notice').text()).toBe((messages as any).main.tr2_body.apply_notice)
    expect(wrapper.text()).not.toMatch(/edit-spec|edit spec|변경제안|変更提案|Change Proposal/)
  })

  it('retires the old names and the flattened "rejected" message in every locale', () => {
    expect([(ko as any).main.doc_types?.T2 ?? (ko as any).doc_types?.T2].filter(Boolean)).not.toContain('변경제안 지시')
    expect(JSON.stringify([ko, ja, en])).not.toMatch(/변경제안|変更提案|Change Proposal|rejected the proposal|提案を拒否|제안을 거절/)
  })
})
