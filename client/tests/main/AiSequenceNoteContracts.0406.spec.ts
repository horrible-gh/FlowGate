import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import AiInvokeDialog from '@main/components/AiInvokeDialog.vue'
import ContinuousWorkDialog from '@main/components/ContinuousWorkDialog.vue'
import WorkflowDecisionModal from '@main/components/WorkflowDecisionModal.vue'

const { getRequest, postRequest, patchRequest, putRequest } = vi.hoisted(() => ({
  getRequest: vi.fn(), postRequest: vi.fn(), patchRequest: vi.fn(), putRequest: vi.fn(),
}))
vi.mock('@shared/api', () => ({
  default: { head: vi.fn(), get: vi.fn(), post: vi.fn(), patch: vi.fn() },
  getRequest, postRequest, patchRequest, putRequest,
}))
vi.mock('@main/components/common/useToast', () => ({
  useToast: () => ({ showToast: vi.fn() }),
}))

const ROOT = 'flowgate.default.0406.0001-B'
const MEMBER = 'flowgate.default.0406.0011-T'

function sequence(note = '이번 단계 저장 전달멘트') {
  return { data: { doc_id: ROOT, decided: true, items: [
    { id: 1, item_seq: 1, type: 'N', label: 'Done', status: 'done', note: 'old', source_doc_id: null, source_revision_no: null },
    { id: 2, item_seq: 2, type: 'T', label: 'Current task', status: 'pending', note, source_doc_id: 'flowgate.default.0406.0004-WP', source_revision_no: 3 },
    { id: 3, item_seq: 3, type: 'TR', label: 'Report', status: 'pending', note: '', source_doc_id: null, source_revision_no: null },
  ] } }
}

function installGet(note = '이번 단계 저장 전달멘트') {
  getRequest.mockImplementation((url: string) => url === '/api/v1/workflow/sequence'
    ? Promise.resolve(sequence(note))
    : Promise.resolve({ data: { providers: [], default_provider_id: null } }))
}

beforeEach(() => {
  setActivePinia(createPinia())
  i18n.global.locale.value = 'ko'
  getRequest.mockReset()
  postRequest.mockReset().mockResolvedValue({ data: { run_id: 'run-0406', status: 'running' } })
  patchRequest.mockReset().mockResolvedValue({ data: { status: 'updated' } })
  putRequest.mockReset()
  installGet()
})

afterEach(() => { document.body.innerHTML = '' })

describe('single stored handoff display (0406 T0011)', () => {
  function mountSingle(actionScope: 'new' | 'review' | 'rework' = 'new') {
    return mount(AiInvokeDialog, {
      props: { visible: true, project: 'flowgate', module: 'default', group: '0406', docRef: MEMBER, sequenceDocRef: ROOT, actionScope },
      global: { plugins: [i18n] },
    })
  }

  it('shows the saved head note but never sends it in the single start body', async () => {
    const wrapper = mountSingle('new')
    await flushPromises()
    const box = document.querySelector('[data-test="single-step-note"]')!
    expect(box.textContent).toContain('이번 단계 저장 전달멘트')
    expect(box.textContent).toContain(i18n.global.t('main.ai_invoke_dialog.step_note_auto'))
    ;(document.querySelector('.modal-ft .btn-primary') as HTMLButtonElement).click()
    await flushPromises()
    const body = postRequest.mock.calls[0][1] as Record<string, unknown>
    expect(body).toMatchObject({ mode: 'single', action_scope: 'new' })
    expect(body).not.toHaveProperty('continuation_note_overrides')
    expect(body).not.toHaveProperty('continuation_default_note')
    console.log('SINGLE_NOTE_UI=' + JSON.stringify({ text: box.textContent, body }))
    wrapper.unmount()
  })

  it('renders the explicit empty state when the current row has no note', async () => {
    installGet('')
    const wrapper = mountSingle('new')
    await flushPromises()
    const box = document.querySelector('[data-test="single-step-note"]')!
    expect(box.textContent).toContain(i18n.global.t('main.ai_invoke_dialog.step_note_empty'))
    console.log('SINGLE_NOTE_EMPTY_UI=' + JSON.stringify({ text: box.textContent }))
    wrapper.unmount()
  })

  it.each(['review', 'rework'] as const)('does not render the field for unrelated %s scope', async (scope) => {
    const wrapper = mountSingle(scope)
    await flushPromises()
    expect(document.querySelector('[data-test="single-step-note"]')).toBeNull()
    wrapper.unmount()
  })
})

describe('continuous tombstone and sequence-edit identity (0406 T0011)', () => {
  it('sends an emptied stored prefill as a blank key and omits an originally empty row', async () => {
    getRequest.mockImplementation((url: string) => url !== '/api/v1/workflow/sequence'
      ? Promise.resolve({ data: { providers: [], default_provider_id: null } })
      : Promise.resolve({ data: { doc_id: ROOT, decided: true, items: [
        { id: 1, item_seq: 1, type: 'T', label: 'Task', status: 'pending', note: '' },
        { id: 2, item_seq: 2, type: 'TR', label: 'Report', status: 'pending', note: 'prefilled handoff', source_doc_id: 'flowgate.default.0406.0004-WP', source_revision_no: 3 },
        { id: 3, item_seq: 3, type: 'TS', label: 'Test', status: 'pending', note: '' },
      ] } }))
    const wrapper = mount(ContinuousWorkDialog, {
      props: { visible: true, docRef: ROOT }, global: { plugins: [i18n] },
    })
    await flushPromises()
    ;(document.querySelectorAll('.cwd-tab')[2] as HTMLButtonElement).click()
    await flushPromises()
    const inputs = document.querySelectorAll('.cwd-override-message-input') as NodeListOf<HTMLInputElement>
    expect(inputs).toHaveLength(2)
    inputs[0].value = ''
    inputs[0].dispatchEvent(new Event('input'))
    await flushPromises()
    ;(document.querySelector('.modal-ft .btn-primary') as HTMLButtonElement).click()
    await flushPromises()
    const payload = wrapper.emitted('confirm')![0][0] as any
    expect(payload.messageOverrides).toEqual({ 2: '' })
    expect(payload.messageOverrides).not.toHaveProperty('3')
    console.log('TOMBSTONE_PAYLOAD=' + JSON.stringify(payload.messageOverrides))
    wrapper.unmount()
  })

  it('names sequence editing and keeps the existing single workflow_sequence_edit POST', async () => {
    const wrapper = mount(WorkflowDecisionModal, {
      props: { visible: true, mode: 'edit', docId: ROOT },
      global: { plugins: [i18n], stubs: { teleport: true } },
    })
    await flushPromises()
    const label = i18n.global.t('main.workflow_edit_modal.invoke_ai')
    expect(label).toContain('시퀀스 수정')
    const button = wrapper.findAll('.modal-ft button').find(candidate => candidate.text().trim() === label)
    expect(button).toBeTruthy()
    await button!.trigger('click')
    await flushPromises()
    const call = postRequest.mock.calls.find(call => call[0] === '/api/v1/ai-invoke/start')
    expect(call).toBeTruthy()
    expect(call![1]).toMatchObject({ action_scope: 'workflow_sequence_edit', mode: 'single', doc_ref: ROOT })
  })
})
