import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import AiInvokeDialog from '@main/components/AiInvokeDialog.vue'

// 0242 NR0003: the AI-invoke dialog's continuous mode used to ask for a raw `목표 seq`
// (`item_seq`) in a <input type="number"> — a DB column name the user must guess a value for,
// with no list of steps and no validation beyond min="1". The step picker the action-bar
// 연속 작업 path already used is now presented here too, so the target is always an existing,
// not-yet-done step.

const { getRequest, postRequest } = vi.hoisted(() => ({ getRequest: vi.fn(), postRequest: vi.fn() }))
vi.mock('@shared/api', () => ({ getRequest, postRequest }))

const ROOT = 'flowgate.default.0242.0001-R'
const MEMBER = 'flowgate.default.0242.0004-T'

// N/NR done, T (head) / TR / TS pending — head is idx 2 (item_seq 3).
function seqResponse() {
  return {
    data: {
      doc_id: ROOT,
      doc_class: 'R',
      decided: true,
      sequence: [
        { id: 1, item_seq: 1, type: 'N', label: '조사지시', status: 'done' },
        { id: 2, item_seq: 2, type: 'NR', label: '조사레포트', status: 'done' },
        { id: 3, item_seq: 3, type: 'T', label: '작업지시', status: 'pending' },
        { id: 4, item_seq: 4, type: 'TR', label: '작업레포트', status: 'pending' },
        { id: 5, item_seq: 5, type: 'TS', label: '테스트시나리오', status: 'pending' },
      ],
      head: { id: 3, item_seq: 3, type: 'T', label: '작업지시', status: 'pending' },
    },
  }
}

function mountDialog(props: Record<string, unknown> = {}) {
  return mount(AiInvokeDialog, {
    props: {
      visible: true,
      project: 'flowgate',
      module: 'default',
      group: '0242',
      docRef: MEMBER,
      sequenceDocRef: ROOT,
      actionScope: 'edit',
      ...props,
    },
    global: { plugins: [i18n] },
  })
}

/** Switch the mode radio to 'continuous' and let the picker's sequence fetch settle. */
async function pickContinuous() {
  const radio = document.querySelector('input[type="radio"][value="continuous"]') as HTMLInputElement
  radio.checked = true
  radio.dispatchEvent(new Event('change'))
  await flushPromises()
}

function startBody() {
  return postRequest.mock.calls[0][1] as Record<string, unknown>
}

beforeEach(() => {
  setActivePinia(createPinia())
  getRequest.mockReset()
  postRequest.mockReset()
  // aiProvider store's ensureLoaded + the picker both go through getRequest.
  getRequest.mockImplementation((url: string) =>
    url === '/api/v1/workflow/sequence'
      ? Promise.resolve(seqResponse())
      : Promise.resolve({ data: { providers: [] } }),
  )
  postRequest.mockResolvedValue({ data: { run_id: 'aiv_1', status: 'running' } })
})

afterEach(() => {
  document.body.innerHTML = ''
})

describe('AiInvokeDialog continuous target', () => {
  it('shows the step picker instead of a raw seq number input', async () => {
    const wrapper = mountDialog()
    await pickContinuous()

    // The whole point of 0242: no number box, no `목표 seq` / `item_seq` label.
    expect(document.querySelector('#aiv-target-seq')).toBeNull()
    expect(document.querySelector('input[type="number"]')).toBeNull()
    // The real steps are listed, with their human labels.
    const steps = document.querySelectorAll('.wsp-step')
    expect(steps).toHaveLength(5)
    expect(steps[2].textContent).toContain('작업지시')

    wrapper.unmount()
  })

  it('reads the sequence by the root R, not the acted-on member doc', async () => {
    // 권고 2: /workflow/sequence only answers for the sequence root. docRef here is a T
    // (the doc the run edits); passing it to the picker would find no sequence and silently
    // degrade to the "start from the workflow decision" branch.
    const wrapper = mountDialog()
    await pickContinuous()

    expect(getRequest).toHaveBeenCalledWith('/api/v1/workflow/sequence', { doc_id: ROOT })

    wrapper.unmount()
  })

  it('disables completed steps and defaults the target to the whole remaining sequence', async () => {
    const wrapper = mountDialog()
    await pickContinuous()

    const steps = document.querySelectorAll('.wsp-step')
    // Done steps cannot be targeted — no skipping / no mid-start.
    expect((steps[0] as HTMLButtonElement).disabled).toBe(true)
    expect((steps[1] as HTMLButtonElement).disabled).toBe(true)
    expect((steps[2] as HTMLButtonElement).disabled).toBe(false)

    // A user who knows nothing about the sequence can just press [Start]: the default target
    // is the last step, i.e. the whole remaining sequence.
    const start = document.querySelector('.modal-ft .btn-primary') as HTMLButtonElement
    expect(start.disabled).toBe(false)
    start.click()
    await flushPromises()

    expect(startBody()).toMatchObject({
      doc_ref: MEMBER,
      action_scope: 'edit',
      mode: 'continuous',
      continuation_target_seq: 5,
    })

    wrapper.unmount()
  })

  it('starts the chain at the step the user picked', async () => {
    const wrapper = mountDialog()
    await pickContinuous()

    // Stop at TR (item_seq 4) instead of the default last step.
    ;(document.querySelectorAll('.wsp-step')[3] as HTMLButtonElement).click()
    await flushPromises()
    ;(document.querySelector('.modal-ft .btn-primary') as HTMLButtonElement).click()
    await flushPromises()

    expect(startBody().continuation_target_seq).toBe(4)

    wrapper.unmount()
  })

  it('switches to a pre-decision run when the workflow is not decided yet', async () => {
    // The picker reports fromDecision; the dialog must mirror MainPanel.onContinuousWarnConfirm
    // — scope workflow_decide, the run-to-end sentinel, and the sequence ROOT as doc_ref.
    getRequest.mockImplementation((url: string) =>
      url === '/api/v1/workflow/sequence'
        ? Promise.resolve({ data: { doc_id: ROOT, doc_class: 'R', decided: false, sequence: [], head: null } })
        : Promise.resolve({ data: { providers: [] } }),
    )
    const wrapper = mountDialog()
    await pickContinuous()

    const start = document.querySelector('.modal-ft .btn-primary') as HTMLButtonElement
    expect(start.disabled).toBe(false)
    start.click()
    await flushPromises()

    expect(startBody()).toMatchObject({
      doc_ref: ROOT,
      action_scope: 'workflow_decide',
      continuation_target_seq: -1,
    })

    wrapper.unmount()
  })

  it('blocks the start when every step is already done', async () => {
    getRequest.mockImplementation((url: string) =>
      url === '/api/v1/workflow/sequence'
        ? Promise.resolve({
            data: {
              doc_id: ROOT, doc_class: 'R', decided: true, head: null,
              sequence: [
                { id: 1, item_seq: 1, type: 'T', label: '작업지시', status: 'done' },
                { id: 2, item_seq: 2, type: 'TR', label: '작업레포트', status: 'done' },
              ],
            },
          })
        : Promise.resolve({ data: { providers: [] } }),
    )
    const wrapper = mountDialog()
    await pickContinuous()

    // Nothing left to chain — there is no target to express, so [Start] stays disabled
    // instead of posting a seq the server would have silently stopped on.
    expect((document.querySelector('.modal-ft .btn-primary') as HTMLButtonElement).disabled).toBe(true)
    expect(document.querySelectorAll('.wsp-step--done')).toHaveLength(2)

    wrapper.unmount()
  })

  it('does not load or show the picker in single mode', async () => {
    const wrapper = mountDialog()
    await flushPromises()

    expect(document.querySelector('.wsp-steps')).toBeNull()
    expect(getRequest).not.toHaveBeenCalledWith('/api/v1/workflow/sequence', expect.anything())

    wrapper.unmount()
  })

  it('keeps the autoStart path on its preset target without re-picking (regression)', async () => {
    // The action-bar 연속 작업 flow already chose a target in ContinuousWorkDialog and opens
    // this dialog with autoStart. Mounting the picker there would refetch the sequence and
    // overwrite that choice with the default (whole remaining sequence).
    const wrapper = mountDialog({
      actionScope: 'new',
      docRef: ROOT,
      sequenceDocRef: ROOT,
      initialMode: 'continuous',
      initialTargetSeq: 3,
      autoStart: true,
    })
    await flushPromises()

    expect(document.querySelector('.wsp-steps')).toBeNull()
    expect(startBody()).toMatchObject({ mode: 'continuous', continuation_target_seq: 3 })

    wrapper.unmount()
  })
})
