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

// ── 0446 NR0003 R5 / T0010 §4-3: 반려 재작업 실행 시간 선택 ───────────────────
//
// A rejection rework was pinned to exactly 60 minutes by the server formula
// min(3600 × max(1, docs_target), 14400) — 264 of 264 measured runs got 3600 seconds, two were
// cut at the 3603-second boundary. The picker below is the UI half of the fix; the server half
// (an in-range pick outranks the mode branch in _resolve_timeout_sec) is proven in
// server/tests/test_ai_invoke_step_timeout_0400.py. Neither half is a fix on its own: this
// spec proves what LEAVES the browser, that spec proves what is SAVED.
describe('AiInvokeDialog 실행 시간 선택 (0446 T0010 R5)', () => {
  const CWD_KEY = 'flowgate.continuousWork.stepTimeoutMinutes'
  const AIV_KEY = 'flowgate.aiInvoke.stepTimeoutMinutes'

  /** Re-query every time: a DOMWrapper captured earlier goes stale after a re-render. */
  function timeoutSelect(): HTMLSelectElement | null {
    return document.querySelector('[data-test="ai-invoke-step-timeout-select"]')
  }

  async function clickStart() {
    ;(document.querySelector('.modal-ft .btn-primary') as HTMLButtonElement).click()
    await flushPromises()
  }

  it('renders the picker on a rework, defaults to 120 minutes, and sends 7200', async () => {
    const wrapper = mountDialog({ actionScope: 'rework' })
    await flushPromises()

    const select = timeoutSelect()
    expect(select).not.toBeNull()
    // All seven options ContinuousWorkDialog offers — the shared list in
    // composables/useStepTimeout.ts, so neither dialog can quietly lose 240 minutes.
    expect(Array.from(select!.options).map((o) => Number(o.value)))
      .toEqual([30, 45, 60, 90, 120, 180, 240])
    expect(Number(select!.value)).toBe(120)

    await clickStart()
    expect(startBody()).toMatchObject({
      mode: 'single',
      action_scope: 'rework',
      continuation_step_timeout_sec: 7200,
    })

    wrapper.unmount()
  })

  it('sends 14400 when 240 minutes is chosen', async () => {
    const wrapper = mountDialog({ actionScope: 'rework' })
    await flushPromises()

    const select = timeoutSelect()!
    select.value = '240'
    select.dispatchEvent(new Event('change'))
    await flushPromises()

    await clickStart()
    expect(startBody().continuation_step_timeout_sec).toBe(14400)

    wrapper.unmount()
  })

  it.each([1800, 2700, 3600, 5400, 7200, 10800, 14400])(
    'sends %i seconds for the matching option',
    async (seconds) => {
      const wrapper = mountDialog({ actionScope: 'rework' })
      await flushPromises()

      const select = timeoutSelect()!
      select.value = String(seconds / 60)
      select.dispatchEvent(new Event('change'))
      await flushPromises()

      await clickStart()
      // The two edges (1800 / 14400) are exactly the server's STEP_TIMEOUT_MIN_SEC /
      // STEP_TIMEOUT_MAX_SEC, so no option the screen offers can be a 422.
      expect(startBody().continuation_step_timeout_sec).toBe(seconds)

      wrapper.unmount()
    },
  )

  it('attaches the picker to rework ONLY — with a positive control in the same test', async () => {
    // §4-3 #3: a bare "it is absent" assertion passes just as well when the selector is
    // misspelled or the component failed to mount. So the positive case runs first, in the
    // same test, through the same query.
    const rework = mountDialog({ actionScope: 'rework' })
    await flushPromises()
    expect(timeoutSelect()).not.toBeNull()          // positive control
    await clickStart()
    expect(startBody()).toHaveProperty('continuation_step_timeout_sec', 7200)
    rework.unmount()
    document.body.innerHTML = ''

    for (const scope of ['edit', 'review', 'chat', 'new', 'vr_correction',
                         'next_step_message', 'design_handoff'] as const) {
      postRequest.mockClear()
      const other = mountDialog({ actionScope: scope })
      await flushPromises()
      expect(timeoutSelect()).toBeNull()            // re-queried, never a stale wrapper
      await clickStart()
      expect(startBody()).not.toHaveProperty('continuation_step_timeout_sec')
      other.unmount()
      document.body.innerHTML = ''
    }
  })

  it('leaves the continuous preset path untouched (regression)', async () => {
    // §4-3 #4: ContinuousWorkDialog's own pick still travels the `mode === 'continuous'`
    // branch, on a scope that has no rework picker at all.
    const wrapper = mountDialog({
      actionScope: 'edit',
      docRef: ROOT,
      sequenceDocRef: ROOT,
      initialMode: 'continuous',
      initialTargetSeq: 4,
      autoStart: true,
      continuationStepTimeoutSec: 7200,
    })
    await flushPromises()

    expect(timeoutSelect()).toBeNull()
    expect(startBody()).toMatchObject({
      mode: 'continuous',
      continuation_step_timeout_sec: 7200,
    })

    wrapper.unmount()
  })

  it('remembers the pick under its OWN key and never touches the chain dialog key', async () => {
    // §4-3 #5. The two dialogs answer different questions ("how long may one hop of an
    // unmanned chain run?" vs "how long may this one rework run?"), so one pick must not
    // become the other's default.
    localStorage.setItem(CWD_KEY, '30')

    const first = mountDialog({ actionScope: 'rework' })
    await flushPromises()
    const select = timeoutSelect()!
    // The chain dialog's stored 30 is NOT read: this dialog opens on its own default.
    expect(Number(select.value)).toBe(120)
    select.value = '180'
    select.dispatchEvent(new Event('change'))
    await flushPromises()

    expect(localStorage.getItem(AIV_KEY)).toBe('180')
    expect(localStorage.getItem(CWD_KEY)).toBe('30')   // control: untouched
    first.unmount()
    document.body.innerHTML = ''

    // Reopened (a fresh mount, as MainPanel does) — the pick is restored, not reset.
    const second = mountDialog({ actionScope: 'rework' })
    await flushPromises()
    expect(Number(timeoutSelect()!.value)).toBe(180)
    postRequest.mockClear()
    await clickStart()
    expect(startBody().continuation_step_timeout_sec).toBe(10800)

    second.unmount()
  })

  it.each(['ko', 'en', 'ja'] as const)('renders real %s copy, not raw i18n keys', async (locale) => {
    i18n.global.locale.value = locale
    const wrapper = mountDialog({ actionScope: 'rework' })
    await flushPromises()

    const group = document.querySelector('[data-test="ai-invoke-step-timeout"]') as HTMLElement
    expect(group).not.toBeNull()
    // A missing key renders as the key path itself in vue-i18n.
    expect(group.textContent).not.toContain('main.ai_invoke_dialog.step_timeout')
    expect(group.textContent).toContain(i18n.global.t('main.ai_invoke_dialog.step_timeout_title'))
    // Not borrowed from the chain dialog: that copy is about ONE STEP of a chain and would be
    // wrong on a single-run screen (T0010 §3-6).
    expect(group.textContent)
      .not.toContain(i18n.global.t('main.continuous_work.step_timeout_desc'))
    // The option labels are localised too, not bare numbers.
    expect(timeoutSelect()!.options[2].textContent?.trim())
      .toBe(i18n.global.t('main.ai_invoke_dialog.step_timeout_option_minutes', { n: 60 }))

    wrapper.unmount()
    i18n.global.locale.value = 'ko'
  })

  function mockReworkContext(
    runs: Array<Record<string, unknown>> = [],
    timeoutKind: string | null = null,
  ) {
    getRequest.mockImplementation((url: string) => {
      if (url === '/api/v1/ai-invoke/runs') return Promise.resolve({ data: { items: runs } })
      if (url === '/api/v1/ai-invoke/rework-hint') {
        return Promise.resolve({ data: { ok: true, timeout_kind: timeoutKind } })
      }
      if (url === '/api/v1/workflow/sequence') return Promise.resolve(seqResponse())
      return Promise.resolve({ data: { providers: [] } })
    })
  }

  function finishedRun(minutes: number, mode = 'single', status = 'finished') {
    return { mode, status, duration_ms: minutes * 60_000 }
  }

  it('still applies the no-progress hint when the independent runs lookup fails', async () => {
    getRequest.mockImplementation((url: string) => {
      if (url === '/api/v1/ai-invoke/runs') return Promise.reject(new Error('runs unavailable'))
      if (url === '/api/v1/ai-invoke/rework-hint') {
        return Promise.resolve({ data: { ok: true, timeout_kind: 'no_progress' } })
      }
      return Promise.resolve({ data: { providers: [] } })
    })
    const wrapper = mountDialog({ actionScope: 'rework' })
    await flushPromises()

    expect(Number(timeoutSelect()!.value)).toBe(180)
    expect(document.querySelector('.aiv-timeout-desc')?.textContent)
      .toBe(i18n.global.t('main.ai_invoke_dialog.step_timeout_recommend', { n: 180 }))

    wrapper.unmount()
  })

  it('still renders run history when the independent hint lookup fails', async () => {
    getRequest.mockImplementation((url: string) => {
      if (url === '/api/v1/ai-invoke/runs') {
        return Promise.resolve({ data: { items: [finishedRun(42)] } })
      }
      if (url === '/api/v1/ai-invoke/rework-hint') {
        return Promise.reject(new Error('hint unavailable'))
      }
      return Promise.resolve({ data: { providers: [] } })
    })
    const wrapper = mountDialog({ actionScope: 'rework' })
    await flushPromises()

    expect(Number(timeoutSelect()!.value)).toBe(120)
    expect(document.querySelector('.aiv-timeout-desc')?.textContent)
      .toBe(i18n.global.t('main.ai_invoke_dialog.step_timeout_recent_values', {
        n: 1,
        values: '42',
      }))

    wrapper.unmount()
  })

  it('renders no-history, small-sample, and 10+ descriptions from real run data', async () => {
    i18n.global.locale.value = 'ko'

    mockReworkContext()
    const empty = mountDialog({ actionScope: 'rework' })
    await flushPromises()
    expect(document.querySelector('.aiv-timeout-desc')?.textContent)
      .toBe(i18n.global.t('main.ai_invoke_dialog.step_timeout_desc'))
    empty.unmount()
    document.body.innerHTML = ''

    mockReworkContext([
      finishedRun(18),
      finishedRun(999, 'continuous'),
      finishedRun(24),
      finishedRun(777, 'single', 'running'),
      finishedRun(59),
    ])
    const small = mountDialog({ actionScope: 'rework' })
    await flushPromises()
    expect(document.querySelector('.aiv-timeout-desc')?.textContent)
      .toBe(i18n.global.t('main.ai_invoke_dialog.step_timeout_recent_values', {
        n: 3,
        values: '18 / 24 / 59',
      }))
    small.unmount()
    document.body.innerHTML = ''

    mockReworkContext(
      [10, 20, 30, 40, 50, 60, 70, 80, 90, 100].map((minutes) => finishedRun(minutes)),
    )
    const many = mountDialog({ actionScope: 'rework' })
    await flushPromises()
    expect(document.querySelector('.aiv-timeout-desc')?.textContent)
      .toBe(i18n.global.t('main.ai_invoke_dialog.step_timeout_recent_summary', {
        n: 10,
        median: 55,
        max: 100,
      }))
    many.unmount()
  })

  it.each(['ko', 'en', 'ja'] as const)(
    'renders the new recent-run copy in %s instead of a raw key',
    async (locale) => {
      i18n.global.locale.value = locale
      mockReworkContext([finishedRun(18), finishedRun(24), finishedRun(59)])
      const wrapper = mountDialog({ actionScope: 'rework' })
      await flushPromises()

      const description = document.querySelector('.aiv-timeout-desc')?.textContent
      expect(description).toBe(i18n.global.t(
        'main.ai_invoke_dialog.step_timeout_recent_values',
        { n: 3, values: '18 / 24 / 59' },
      ))
      expect(description).not.toContain('main.ai_invoke_dialog')

      wrapper.unmount()
      i18n.global.locale.value = 'ko'
    },
  )

  it.each([
    ['no_progress', 180, true],
    ['absolute_cap', 120, false],
    [null, 120, false],
  ] as const)(
    'uses the previous-run kind %s only as a no-progress recommendation',
    async (timeoutKind, expectedMinutes, recommended) => {
      mockReworkContext([finishedRun(42)], timeoutKind)
      const wrapper = mountDialog({ actionScope: 'rework' })
      await flushPromises()

      expect(Number(timeoutSelect()!.value)).toBe(expectedMinutes)
      const description = document.querySelector('.aiv-timeout-desc')?.textContent ?? ''
      const recommendCopy = i18n.global.t('main.ai_invoke_dialog.step_timeout_recommend', {
        n: expectedMinutes,
      })
      if (recommended) {
        expect(description).toBe(recommendCopy)
      } else {
        expect(description).not.toBe(recommendCopy)
        expect(description).toBe(i18n.global.t(
          'main.ai_invoke_dialog.step_timeout_recent_values',
          { n: 1, values: '42' },
        ))
      }

      wrapper.unmount()
    },
  )

  it('keeps a user choice made before the no-progress hint arrives', async () => {
    localStorage.setItem(AIV_KEY, '120')
    let resolveHint!: (value: { data: { ok: boolean; timeout_kind: string } }) => void
    const hint = new Promise<{ data: { ok: boolean; timeout_kind: string } }>((resolve) => {
      resolveHint = resolve
    })
    getRequest.mockImplementation((url: string) => {
      if (url === '/api/v1/ai-invoke/runs') return Promise.resolve({ data: { items: [] } })
      if (url === '/api/v1/ai-invoke/rework-hint') return hint
      return Promise.resolve({ data: { providers: [] } })
    })
    const wrapper = mountDialog({ actionScope: 'rework' })
    await flushPromises()

    const select = timeoutSelect()!
    expect(Number(select.value)).toBe(120)
    select.value = '240'
    select.dispatchEvent(new Event('change'))
    await flushPromises()
    expect(localStorage.getItem(AIV_KEY)).toBe('240')

    resolveHint({ data: { ok: true, timeout_kind: 'no_progress' } })
    await flushPromises()
    expect(Number(timeoutSelect()!.value)).toBe(240)
    expect(document.querySelector('.aiv-timeout-desc')?.textContent)
      .toBe(i18n.global.t('main.ai_invoke_dialog.step_timeout_recommend', { n: 180 }))

    await clickStart()
    expect(startBody().continuation_step_timeout_sec).toBe(14400)

    wrapper.unmount()
  })

  it('does not persist a programmatic recommendation and restores the saved value next time', async () => {
    localStorage.setItem(AIV_KEY, '90')
    mockReworkContext([], 'no_progress')
    const recommended = mountDialog({ actionScope: 'rework' })
    await flushPromises()

    expect(Number(timeoutSelect()!.value)).toBe(120)
    expect(localStorage.getItem(AIV_KEY)).toBe('90')
    await clickStart()
    expect(startBody().continuation_step_timeout_sec).toBe(7200)
    recommended.unmount()
    document.body.innerHTML = ''

    mockReworkContext([], 'absolute_cap')
    const control = mountDialog({ actionScope: 'rework' })
    await flushPromises()
    expect(Number(timeoutSelect()!.value)).toBe(90)
    expect(document.querySelector('.aiv-timeout-desc')?.textContent)
      .toBe(i18n.global.t('main.ai_invoke_dialog.step_timeout_desc'))
    expect(localStorage.getItem(AIV_KEY)).toBe('90')

    control.unmount()
  })
})

// 0414 T0012 / P0007: ContinuousWorkDialog 의 [검수] 탭 값이 여기까지 내려와
// continuation_review_count_overrides / continuation_reviewer_overrides 라는 정확한
// snake_case 키로 시작 요청에 실려야 한다. 위 전달멘트 블록과 같은 이유로 이 한 홉은
// 클라이언트 스펙과 서버 스펙 어느 쪽도 혼자서는 지키지 못한다.
describe('AiInvokeDialog 검수 전달 (0414 T0012)', () => {
  function mountAutoStart(props: Record<string, unknown> = {}) {
    return mountDialog({
      actionScope: 'new',
      docRef: ROOT,
      sequenceDocRef: ROOT,
      initialMode: 'continuous',
      initialTargetSeq: 5,
      autoStart: true,
      ...props,
    })
  }

  it('비어 있지 않은 두 맵을 snake_case 키로 시작 요청에 싣는다', async () => {
    const wrapper = mountAutoStart({
      reviewCountOverrides: { 3: -1, 4: 2, 5: 3 },
      reviewerOverrides: { 3: 'aip_codex', 4: 'aip_sonnet', 5: 'aip_codex' },
    })
    await flushPromises()

    expect(startBody()).toMatchObject({
      mode: 'continuous',
      continuation_review_count_overrides: { 3: -1, 4: 2, 5: 3 },
      continuation_reviewer_overrides: { 3: 'aip_codex', 4: 'aip_sonnet', 5: 'aip_codex' },
    })
    // 값은 횟수 정수와 provider id 문자열 그대로다.
    const body = startBody() as any
    expect(Object.values(body.continuation_review_count_overrides).every((v) => typeof v === 'number')).toBe(true)
    expect(Object.values(body.continuation_reviewer_overrides).every((v) => typeof v === 'string')).toBe(true)

    wrapper.unmount()
  })

  it('횟수만 있고 검수자가 비면 검수자 키만 생략한다', async () => {
    // P0007: 검수자 맵이 비어도 422 가 아니다 — 서버가 프로젝트 유효 체인의 첫 프로바이더로
    // 해석한다. 빈 맵을 `{}` 로 실어 보내지 않는다.
    const wrapper = mountAutoStart({
      reviewCountOverrides: { 4: 2 },
      reviewerOverrides: {},
    })
    await flushPromises()

    expect(startBody().continuation_review_count_overrides).toEqual({ 4: 2 })
    expect(startBody()).not.toHaveProperty('continuation_reviewer_overrides')

    wrapper.unmount()
  })

  it('두 맵이 비어 있으면 두 키를 모두 생략한다', async () => {
    const wrapper = mountAutoStart({ reviewCountOverrides: {}, reviewerOverrides: {} })
    await flushPromises()

    const body = startBody()
    expect(body).not.toHaveProperty('continuation_review_count_overrides')
    expect(body).not.toHaveProperty('continuation_reviewer_overrides')
    // 검수 없는 요청은 이 기능이 생기기 전과 한 글자도 다르지 않아야 한다.
    expect(body).toMatchObject({ mode: 'continuous', continuation_target_seq: 5 })

    wrapper.unmount()
  })

  it('props 자체가 없어도 두 키를 싣지 않는다', async () => {
    const wrapper = mountAutoStart()
    await flushPromises()

    const body = startBody()
    expect(body).not.toHaveProperty('continuation_review_count_overrides')
    expect(body).not.toHaveProperty('continuation_reviewer_overrides')

    wrapper.unmount()
  })

  it('single 요청에는 값이 들어와도 두 키를 절대 싣지 않는다', async () => {
    const wrapper = mountDialog({
      reviewCountOverrides: { 4: 2 },
      reviewerOverrides: { 4: 'aip_codex' },
    })
    await flushPromises()
    ;(document.querySelector('.modal-ft .btn-primary') as HTMLButtonElement).click()
    await flushPromises()

    const body = startBody()
    expect(body.mode).toBe('single')
    expect(body).not.toHaveProperty('continuation_review_count_overrides')
    expect(body).not.toHaveProperty('continuation_reviewer_overrides')

    wrapper.unmount()
  })

  it('pre-decision(workflow_decide) 요청에는 값이 들어와도 두 키를 싣지 않는다', async () => {
    // 워크플로가 결정되기 전에는 단계별 item_seq 가 없다 — 검수 키를 매길 좌표가 없다.
    getRequest.mockImplementation((url: string) =>
      url === '/api/v1/workflow/sequence'
        ? Promise.reject({ response: { status: 400, data: { error: 'sequence_not_decided', doc_id: ROOT } } })
        : Promise.resolve({ data: { providers: [] } }),
    )
    const wrapper = mountDialog({
      reviewCountOverrides: { 4: 2 },
      reviewerOverrides: { 4: 'aip_codex' },
    })
    await pickContinuous()
    ;(document.querySelector('.modal-ft .btn-primary') as HTMLButtonElement).click()
    await flushPromises()

    const body = startBody()
    expect(body).toMatchObject({ action_scope: 'workflow_decide', continuation_target_seq: -1 })
    expect(body).not.toHaveProperty('continuation_review_count_overrides')
    expect(body).not.toHaveProperty('continuation_reviewer_overrides')

    wrapper.unmount()
  })
})
