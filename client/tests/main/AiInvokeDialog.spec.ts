import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import AiInvokeDialog from '@main/components/AiInvokeDialog.vue'
import { useAiProviderStore } from '@main/stores/aiProvider'

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
      items: [
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
      // 0406 T0022 작업 2: the mode is a required prop now — the component no longer
      // falls back to 'auto_approved' on its own, so every mount states it.
      continuationInstructionMode: 'auto_approved',
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
  localStorage.clear()
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
    // 0337 R0001-1: mounted in auto_approved mode, so the T step (idx 2) is server-approved
    // rather than AI-run and is not a stop point here either — both continuous entry points
    // must agree on what can be selected.
    expect((steps[2] as HTMLButtonElement).disabled).toBe(true)
    expect((steps[3] as HTMLButtonElement).disabled).toBe(false)

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
        // 0406 T0013: the canonical query-form handler reports an undecided sequence as a 400,
        // not as 200 + decided:false. Mock what production actually answers.
        ? Promise.reject({ response: { status: 400, data: { error: 'sequence_not_decided', doc_id: ROOT } } })
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

  it('ignores a legacy sequence-shaped body: only `items` is a sequence', async () => {
    // 0406 T0013: the duplicate route that answered `sequence` is gone. Reading that key again
    // would let a fixture keep a dead contract alive — the exact blind spot NR0003 Q7 named.
    getRequest.mockImplementation((url: string) =>
      url === '/api/v1/workflow/sequence'
        ? Promise.resolve({
            data: {
              doc_id: ROOT, doc_class: 'R', decided: true, head: null,
              sequence: [
                { id: 1, item_seq: 1, type: 'T', label: '작업지시', status: 'pending' },
              ],
            },
          })
        : Promise.resolve({ data: { providers: [] } }),
    )
    const wrapper = mountDialog()
    await pickContinuous()

    // No real step is read from the dead key; the picker falls back to the decision start.
    expect(document.querySelectorAll('.wsp-step--pending')).toHaveLength(0)
    expect(document.body.textContent).toContain(i18n.global.t('main.continuous_work.from_decision_title'))

    wrapper.unmount()
  })

  it('blocks the start when every step is already done', async () => {
    getRequest.mockImplementation((url: string) =>
      url === '/api/v1/workflow/sequence'
        ? Promise.resolve({
            data: {
              doc_id: ROOT, doc_class: 'R', decided: true, head: null,
              items: [
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

  // 0448 T0005 §6. The old single test called ONLY selectProvider('aip_one') and then
  // expected provider_pinned:true — it had frozen NR0003 §6-1's defect (an ordinary pick
  // silently becoming a force-all) into a contract. The two request states are asserted
  // separately now, each through the API that is allowed to produce it.
  async function loadProviders() {
    getRequest.mockResolvedValueOnce({
      data: {
        ok: true,
        project: 'flowgate',
        default_provider_id: 'aip_two',
        providers: [
          { id: 'aip_one', name: 'One', exec_type: 'cli', kind: 'codex' },
          { id: 'aip_two', name: 'Two', exec_type: 'cli', kind: 'codex' },
        ],
      },
    })
    const providerStore = useAiProviderStore()
    await providerStore.loadForProject('flowgate')
    return providerStore
  }

  function mountContinuousAutoStart() {
    return mountDialog({
      actionScope: 'new',
      docRef: ROOT,
      sequenceDocRef: ROOT,
      initialMode: 'continuous',
      initialTargetSeq: 4,
      autoStart: true,
    })
  }

  it('forwards an ordinary provider selection without a pin on the autoStart request', async () => {
    const providerStore = await loadProviders()
    providerStore.selectProvider('aip_one')
    postRequest.mockClear()

    const wrapper = mountContinuousAutoStart()
    await flushPromises()

    expect(startBody()).toMatchObject({ provider_id: 'aip_one', mode: 'continuous' })
    expect(startBody()).not.toHaveProperty('provider_pinned')
    wrapper.unmount()
  })

  it('forwards an explicit force-all as a provider pin on the autoStart request', async () => {
    const providerStore = await loadProviders()
    providerStore.forceProviderForAllSteps('aip_one')
    postRequest.mockClear()

    const wrapper = mountContinuousAutoStart()
    await flushPromises()

    expect(startBody()).toMatchObject({
      provider_id: 'aip_one',
      provider_pinned: true,
      mode: 'continuous',
    })
    wrapper.unmount()
  })
})

// flowgate.default.0346 T0005 §2-3: the [전달멘트] tab's values arrive here as props (via
// MainPanel's openAiInvokeDialog preset) and must land on the /ai-invoke/start body as
// continuation_default_note / continuation_note_overrides. This is the one hop the dialog
// spec and the server spec each half-cover: ContinuousWorkDialog.spec.ts proves the confirm
// payload, test_ai_invoke_continuation_note_0346.py proves start_run's injection — a props
// name typo between them would drop every note silently, with both suites still green.
describe('AiInvokeDialog 전달멘트 forwarding (0346 T0005)', () => {
  function mountAutoStart(props: Record<string, unknown> = {}) {
    return mountDialog({
      actionScope: 'new',
      docRef: ROOT,
      sequenceDocRef: ROOT,
      initialMode: 'continuous',
      initialTargetSeq: 4,
      autoStart: true,
      ...props,
    })
  }

  it('puts the common note and the per-step notes on the start body', async () => {
    const wrapper = mountAutoStart({
      defaultMessage: '이 그룹은 결제 모듈 리팩터링입니다',
      messageOverrides: { 4: 'TR: 결제 실패 케이스도 문서화해줘' },
    })
    await flushPromises()

    expect(startBody()).toMatchObject({
      mode: 'continuous',
      continuation_default_note: '이 그룹은 결제 모듈 리팩터링입니다',
      continuation_note_overrides: { 4: 'TR: 결제 실패 케이스도 문서화해줘' },
    })

    wrapper.unmount()
  })

  it('carries a common note with no per-step notes, and vice versa', async () => {
    const commonOnly = mountAutoStart({ defaultMessage: '공통 멘트', messageOverrides: {} })
    await flushPromises()
    expect(startBody().continuation_default_note).toBe('공통 멘트')
    expect(startBody()).not.toHaveProperty('continuation_note_overrides')
    commonOnly.unmount()

    postRequest.mockClear()
    const perStepOnly = mountAutoStart({ defaultMessage: '', messageOverrides: { 4: '개별 멘트' } })
    await flushPromises()
    expect(startBody().continuation_note_overrides).toEqual({ 4: '개별 멘트' })
    expect(startBody()).not.toHaveProperty('continuation_default_note')
    perStepOnly.unmount()
  })

  it('omits both keys entirely when the user typed no note', async () => {
    // T0005 §3 제약 5: an un-noted run must reach the server exactly as it did before this
    // feature — not with empty-string / empty-object fields the server would have to ignore.
    const wrapper = mountAutoStart()
    await flushPromises()

    const body = startBody()
    expect(body).not.toHaveProperty('continuation_default_note')
    expect(body).not.toHaveProperty('continuation_note_overrides')

    wrapper.unmount()
  })

  it('never sends notes on a single run', async () => {
    // The note fields live inside the mode === 'continuous' branch; a single run must not
    // carry them even if a caller passes the props.
    const wrapper = mountDialog({
      defaultMessage: '공통 멘트',
      messageOverrides: { 4: '개별 멘트' },
    })
    await flushPromises()
    ;(document.querySelector('.modal-ft .btn-primary') as HTMLButtonElement).click()
    await flushPromises()

    const body = startBody()
    expect(body.mode).toBe('single')
    expect(body).not.toHaveProperty('continuation_default_note')
    expect(body).not.toHaveProperty('continuation_note_overrides')

    wrapper.unmount()
  })
})
