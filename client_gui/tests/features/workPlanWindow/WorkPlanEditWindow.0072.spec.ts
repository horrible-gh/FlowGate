/**
 * flowgate.v02.0072 T#1 — the v0.2 work plan edit window (features/workPlanWindow), mounted by DocumentWindows with
 * `@shared/api` mocked (no server):
 * - opening reads `GET …/work-plan` once and the Flow's live AI run; the window is a ModelessShell (minimize,
 *   bring back, close) — nothing opens it from the WP card yet (T#3);
 * - edits (quantities, card order, per-Step AI / note, defaults and [모두 적용]) change only the window's draft;
 *   [저장] sends one `PUT …/work-plan` with base_revision_no and the whole body (fields without a control kept);
 * - the save's answers: 409 conflict (banner, read again asks first), 422 field errors on their rows, 422 capability
 *   (confirm sends the findings' step keys), 422 not editable, 423 the AI run;
 * - locks: the server's editable verdict, the opener's read-only / run lock, the Flow's AI run — nothing is sent;
 * - locks that change while the window is open (rework rej_01M58P4CTC98S752): a read again over unsaved edits locks
 *   every input and the Step window's apply until its answer lands; a save refused as not editable (422) or as a
 *   discarded Flow (409, the read still says editable) reads the lock state again, keeps the edits and stays
 *   locked; an AI run started elsewhere after opening locks the window on the next poll, and its end unlocks it;
 * - a quantity decrease that removes Steps with values and closing with unsaved edits ask first;
 * - external changes: a clean window takes the new plan, a dirty one keeps its edits and says a newer revision exists;
 * - ✎ opens nothing inside the window: without a Step window it is disabled; with one it gets the row and a session.
 */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import { DocumentWindows, closeAllDocumentAiWindows } from '@/features/documentWindow'
import { DOCUMENT_AI_POLL_MS } from '@/features/documentWindow/documentAiWindows'
import { DOCUMENT_WINDOW_POLL_MS, DOCUMENT_WINDOW_REFRESH_COALESCE_MS } from '@/features/documentWindow/useDocumentWindowEvents'
import {
  closeAllWorkPlanEditWindows,
  openWorkPlanEditWindow,
  setWorkPlanEditWindowMinimized,
  setWorkPlanStepEditHandler,
  workPlanEditWindowList,
  workPlanGroupOf,
  type WorkPlanStepEditSession,
  type WorkPlanStepEditTarget,
} from '@/features/workPlanWindow'
import { DOC, FLOW, planBody, readAnswer } from './workPlanFixtures'

const api = vi.hoisted(() => ({ getRequest: vi.fn(), putRequest: vi.fn(), postRequest: vi.fn() }))

vi.mock('@shared/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@shared/api')>()
  return { ...actual, ...api }
})

registerAppMessages(i18n)

const WP_PATH = `/api/v1/documents/${encodeURIComponent(DOC)}/work-plan`

let answer: Record<string, unknown>
let active: Record<string, unknown>
let readFailure: unknown
let wrapper: VueWrapper<any> | null = null

/** The server's save: the body as sent, new cards named, the revision bumped. */
function savedAnswer(payload: { base_revision_no: number; body: Record<string, any> }): Record<string, unknown> {
  const body = JSON.parse(JSON.stringify(payload.body))
  for (const s of body.steps) if (!s.card_id) s.card_id = `c_new_${s.pair_role === 'result' ? s.pair_key : s.key}`
  return { ok: true, doc_id: DOC, revision_no: payload.base_revision_no + 1, title: '작업계획 (저장됨)', doc_review_status: 'pending_review', body, warnings: [] }
}

beforeEach(() => {
  i18n.global.locale.value = 'ko'
  closeAllDocumentAiWindows()
  closeAllWorkPlanEditWindows()
  setWorkPlanStepEditHandler(null)
  api.getRequest.mockReset()
  api.putRequest.mockReset()
  api.postRequest.mockReset()
  answer = readAnswer()
  active = { active: false }
  readFailure = null
  api.getRequest.mockImplementation(async (path: string) => {
    if (path === WP_PATH) {
      if (readFailure) throw readFailure
      return { data: JSON.parse(JSON.stringify(answer)) }
    }
    if (path === '/api/v1/ai-invoke/active') return { data: active }
    throw new Error(`unexpected GET ${path}`)
  })
  api.putRequest.mockImplementation(async (_path: string, payload: any) => ({ data: savedAnswer(payload) }))
  wrapper = mount(DocumentWindows, { global: { plugins: [i18n] }, attachTo: document.body })
})

afterEach(() => {
  vi.useRealTimers()
  wrapper?.unmount()
  wrapper = null
  closeAllWorkPlanEditWindows()
  closeAllDocumentAiWindows()
  setWorkPlanStepEditHandler(null)
  document.body.innerHTML = ''
})

const q = (id: string, root: ParentNode = document.body) => root.querySelector<HTMLElement>(`[data-test="${id}"]`)
const qa = (id: string, root: ParentNode = document.body) => [...root.querySelectorAll<HTMLElement>(`[data-test="${id}"]`)]
const text = (node: Element | null | undefined) => node?.textContent?.replace(/\s+/g, ' ').trim() ?? null
const row = (rowId: string) => document.body.querySelector<HTMLElement>(`[data-test="wp-edit-step"][data-row="${rowId}"]`)
const box = (type: string) => document.body.querySelector<HTMLElement>(`[data-test="wp-edit-quantity"][data-type="${type}"]`)

async function click(node: HTMLElement | null) {
  if (!node) throw new Error('nothing to click')
  node.dispatchEvent(new MouseEvent('click', { bubbles: true }))
  await flushPromises()
}

async function pick(select: HTMLElement | null, value: string) {
  if (!(select instanceof HTMLSelectElement)) throw new Error('no select')
  select.value = value
  select.dispatchEvent(new Event('change', { bubbles: true }))
  await flushPromises()
}

async function type(input: HTMLElement | null, value: string) {
  if (!(input instanceof HTMLInputElement)) throw new Error('no input')
  input.value = value
  input.dispatchEvent(new Event('input', { bubbles: true }))
  await flushPromises()
}

/** The buttons of the open confirmation dialog: [cancel, confirm]. */
function confirmButtons(): HTMLButtonElement[] {
  return [...document.body.querySelectorAll<HTMLButtonElement>('.ui-confirm-actions button')]
}

async function open(extra: Partial<Parameters<typeof openWorkPlanEditWindow>[0]> = {}) {
  openWorkPlanEditWindow({ docId: DOC, projectId: 'flowgate', title: '작업계획', ...extra })
  await flushPromises()
}

const wpReads = () => api.getRequest.mock.calls.filter(([path]) => path === WP_PATH).length
const lastPut = () => api.putRequest.mock.calls.at(-1)?.[1] as { base_revision_no: number; body: Record<string, any>; capability_warning_acks?: string[] }

describe('opening (modeless, read once)', () => {
  it('reads the plan once and the Flow’s AI run; draws quantities, cards and rows', async () => {
    await open()
    expect(wpReads()).toBe(1)
    expect(api.getRequest).toHaveBeenCalledWith('/api/v1/ai-invoke/active', { group_id: FLOW })
    expect(q('wp-edit-window')?.dataset.doc).toBe(DOC)
    expect(text(q('wp-edit-heading'))).toContain('작업계획')
    expect(text(q('wp-edit-revision'))).toBe('r2')
    expect(box('T')?.dataset.count).toBe('2')
    expect(box('D')?.dataset.count).toBe('1')
    expect(qa('wp-edit-card').map((card) => card.dataset.card)).toEqual(['c_d1', 'c_t1', 'c_t2', 'c_ts1'])
    expect(qa('wp-edit-step').map((r) => r.dataset.step)).toEqual(['D#1', 'T#1', 'TR#1', 'T#2', 'TR#2', 'TS#1', 'TSR#1'])
    expect(text(q('wp-edit-step-type', row('c_t1|main')!))).toContain('작업 지시서')
    expect(text(q('wp-edit-step-review', row('c_t1|main')!))).toBe('검수 1회 · Sonnet')
    expect(q('wp-edit-step-instruction', row('c_t1|main')!)).not.toBeNull()
    expect(text(q('wp-edit-step-server', row('c_ts1|result')!))).toBe('서버가 만듭니다')
    expect(text(q('wp-edit-unassigned'))).toBe('담당 AI 미지정 2개')
    expect((q('wp-edit-save') as HTMLButtonElement).disabled).toBe(true)
    expect(workPlanGroupOf(DOC)).toBe(FLOW)
  })

  it('an AI no longer registered stays visible on the row that uses it, marked', async () => {
    await open()
    const select = q('wp-edit-step-provider', row('c_t2|main')!) as HTMLSelectElement
    expect(select.value).toBe('aip_old')
    expect([...select.options].map((o) => o.textContent)).toEqual(['담당 미지정', 'Opus', 'Sonnet', 'Old AI (사용 불가)'])
  })

  it('a second open brings the same window back (one per document); minimize and close follow the registry', async () => {
    await open()
    setWorkPlanEditWindowMinimized(DOC, true)
    await flushPromises()
    expect(workPlanEditWindowList()[0].minimized).toBe(true)
    await open()
    expect(workPlanEditWindowList()).toHaveLength(1)
    expect(workPlanEditWindowList()[0].minimized).toBe(false)
    expect(wpReads()).toBe(1)
    await click(q('wp-edit-close'))
    expect(workPlanEditWindowList()).toHaveLength(0)
    expect(q('wp-edit-window')).toBeNull()
  })
})

describe('modeless behavior (ModelessShell)', () => {
  it('no backdrop; the titlebar minimizes (the registry hears it) and drags the window', async () => {
    await open()
    const panel = document.body.querySelector<HTMLElement>('.ui-modeless-panel')!
    expect(document.body.querySelector('.ui-modal-overlay')).toBeNull()
    const titlebar = panel.querySelector<HTMLElement>('.ui-modeless-titlebar')!
    const left = parseInt(panel.style.left, 10)
    titlebar.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, clientX: 300, clientY: 120 }))
    document.dispatchEvent(new MouseEvent('mousemove', { bubbles: true, clientX: 340, clientY: 150 }))
    document.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }))
    await flushPromises()
    expect(parseInt(panel.style.left, 10)).toBe(left + 40)
    await click(panel.querySelector<HTMLElement>('[aria-label="Minimize"]'))
    expect(workPlanEditWindowList()[0].minimized).toBe(true)
    expect((panel.querySelector('.ui-modeless-body') as HTMLElement).style.display).toBe('none')
    await click(panel.querySelector<HTMLElement>('[aria-label="Restore"]'))
    expect(workPlanEditWindowList()[0].minimized).toBe(false)
  })

  it('× of the titlebar closes like [닫기] (asks first when dirty)', async () => {
    await open()
    await type(q('wp-edit-step-note', row('c_d1|main')!) as HTMLInputElement, '변경')
    await click(document.body.querySelector<HTMLElement>('.ui-modeless-panel [aria-label="Close"]'))
    expect(text(document.body.querySelector('.ui-confirm-title'))).toBe('저장하지 않은 변경을 버리고 닫을까요?')
    await click(confirmButtons()[1])
    expect(workPlanEditWindowList()).toHaveLength(0)
  })
})

describe('editing and the one save', () => {
  it('per-row AI and note make the window dirty; [저장] PUTs the whole body with base_revision_no', async () => {
    await open()
    await pick(q('wp-edit-step-provider', row('c_t1|main')!), 'aip_sonnet')
    await type(q('wp-edit-step-note', row('c_t2|result')!) as HTMLInputElement, '보고서 메모')
    expect(q('wp-edit-dirty')).not.toBeNull()
    expect(api.putRequest).not.toHaveBeenCalled()
    await click(q('wp-edit-save'))
    expect(api.putRequest).toHaveBeenCalledTimes(1)
    const [path, payload] = api.putRequest.mock.calls[0]
    expect(path).toBe(WP_PATH)
    expect(payload.base_revision_no).toBe(2)
    expect(payload.capability_warning_acks).toBeUndefined()
    const expected = planBody() as Record<string, any>
    expected.steps[1] = { ...expected.steps[1], provider_id: 'aip_sonnet', provider_display_name: 'Sonnet', origin: 'human' }
    expected.steps[4] = { ...expected.steps[4], note: '보고서 메모', origin: 'human' }
    expect(payload.body).toEqual(expected)
    expect(q('wp-edit-dirty')).toBeNull()
    expect(text(q('wp-edit-revision'))).toBe('r3')
    expect(q('wp-edit-notice')?.dataset.key).toBe('saved_unassigned')
  })

  it('a new card gets no AI even with a default AI set; [모두 적용] fills every unlocked Step', async () => {
    await open()
    await click(q('wp-edit-quantity-inc', box('T')!))
    expect(box('T')?.dataset.count).toBe('3')
    expect(qa('wp-edit-step').map((r) => r.dataset.step).slice(-2)).toEqual(['T#3', 'TR#3'])
    const added = qa('wp-edit-step').slice(-2)
    expect(added.map((r) => (q('wp-edit-step-provider', r) as HTMLSelectElement).value)).toEqual(['', ''])
    await click(q('wp-edit-save'))
    const sent = lastPut().body.steps.slice(-2)
    expect(sent.map((s: any) => [s.key, s.card_id ?? null, s.provider_id])).toEqual([['T#3', null, null], ['TR#3', null, null]])
    // the server named the new card: its rows follow the new id
    expect(qa('wp-edit-card').map((card) => card.dataset.card).slice(-1)).toEqual(['c_new_T#3'])

    await click(q('wp-edit-apply-defaults'))
    expect(q('wp-edit-notice')?.dataset.key).toBe('applied_defaults')
    await click(q('wp-edit-save'))
    const body = lastPut().body
    expect(body.steps.filter((s: any) => !s.locked).every((s: any) => s.provider_id === 'aip_opus' && s.note === '기본 메모')).toBe(true)
    expect(body.steps.find((s: any) => s.key === 'T#1')).toMatchObject({ review_count: 1, pre_instruction_text: '사전 지시' })
  })

  it('card order: ▲▼ move a card and renumber; a started card cannot move and nothing goes before it', async () => {
    await open()
    const up = (cardId: string) => q('wp-edit-card-up', document.body.querySelector(`[data-test="wp-edit-card"][data-card="${cardId}"]`)!) as HTMLButtonElement
    const down = (cardId: string) => q('wp-edit-card-down', document.body.querySelector(`[data-test="wp-edit-card"][data-card="${cardId}"]`)!) as HTMLButtonElement
    expect(up('c_t1').disabled).toBe(true)
    expect(down('c_d1').disabled).toBe(true)
    expect(document.body.querySelector('[data-card="c_d1"]')?.getAttribute('data-started')).toBe('true')
    await click(down('c_t1'))
    expect(qa('wp-edit-card').map((card) => card.dataset.card)).toEqual(['c_d1', 'c_t2', 'c_t1', 'c_ts1'])
    expect(row('c_t1|main')?.dataset.step).toBe('T#2')
    expect(row('c_t2|main')?.dataset.step).toBe('T#1')
    await click(q('wp-edit-save'))
    expect(lastPut().body.steps.map((s: any) => `${s.card_id}:${s.key}`)).toEqual([
      'c_d1:D#1', 'c_t2:T#1', 'c_t2:TR#1', 'c_t1:T#2', 'c_t1:TR#2', 'c_ts1:TS#1', 'c_ts1:TSR#1',
    ])
  })

  it('a quantity decrease that removes Steps with values asks first; cancel keeps them', async () => {
    await open()
    await click(q('wp-edit-quantity-dec', box('T')!))
    expect(text(document.body.querySelector('.ui-confirm-title'))).toBe('값이 있는 단계 1개가 지워집니다')
    await click(confirmButtons()[0])
    expect(box('T')?.dataset.count).toBe('2')
    expect(q('wp-edit-dirty')).toBeNull()
    await click(q('wp-edit-quantity-dec', box('T')!))
    await click(confirmButtons()[1])
    expect(box('T')?.dataset.count).toBe('1')
    expect(row('c_t2|main')).toBeNull()
    // raised again in the session: the same card with its values
    await click(q('wp-edit-quantity-inc', box('T')!))
    expect((q('wp-edit-step-provider', row('c_t2|main')!) as HTMLSelectElement).value).toBe('aip_old')
  })

  it('a decrease that would need a started card is refused with a note', async () => {
    await open()
    await click(q('wp-edit-quantity-dec', box('D')!))
    expect(q('wp-edit-notice')?.dataset.key).toBe('quantity_blocked_started')
    expect(box('D')?.dataset.count).toBe('1')
    expect(document.body.querySelector('.ui-confirm-title')).toBeNull()
  })
})

describe('what a save may answer', () => {
  it('409 conflict: the banner; reading again with edits asks first and then takes the server plan', async () => {
    api.putRequest.mockRejectedValueOnce({ isAxiosError: true, response: { status: 409, data: { code: 'wp_revision_conflict', message: 'x', updated_by: 'kim', updated_at: '2026-10-19T08:00' } } })
    await open()
    await type(q('wp-edit-step-note', row('c_d1|main')!) as HTMLInputElement, '내 변경')
    await click(q('wp-edit-save'))
    expect(text(q('wp-edit-conflict'))).toContain('kim')
    expect(q('wp-edit-dirty')).not.toBeNull()
    answer = readAnswer({ revision_no: 5 })
    await click(q('wp-edit-conflict-reload'))
    expect(text(document.body.querySelector('.ui-confirm-title'))).toBe('저장하지 않은 변경을 버리고 다시 읽을까요?')
    await click(confirmButtons()[1])
    expect(text(q('wp-edit-revision'))).toBe('r5')
    expect(q('wp-edit-dirty')).toBeNull()
    expect(q('wp-edit-conflict')).toBeNull()
  })

  it('422 field errors are shown on their rows (by identity) and on top', async () => {
    api.putRequest.mockRejectedValueOnce({
      isAxiosError: true,
      response: {
        status: 422,
        data: {
          code: 'validation_failed',
          message: 'invalid',
          errors: [
            { loc: 'steps[3].note', key: 'T#2', code: 'note_too_long', params: { max: 200 }, msg: 'too long' },
            { loc: 'quantities', key: null, code: 'unknown_server_code', params: {}, msg: '서버 메시지' },
          ],
        },
      },
    })
    await open()
    await type(q('wp-edit-step-note', row('c_t2|main')!) as HTMLInputElement, 'x'.repeat(10))
    await click(q('wp-edit-save'))
    expect(q('wp-edit-failure')?.dataset.kind).toBe('validation')
    expect(q('wp-edit-step-errors', row('c_t2|main')!)).not.toBeNull()
    expect(text(q('wp-edit-top-error'))).toBe('서버 메시지')
    expect(q('wp-edit-dirty')).not.toBeNull()
  })

  it('422 capability: confirmation sends the same body with the findings acknowledged', async () => {
    api.putRequest.mockRejectedValueOnce({
      isAxiosError: true,
      response: { status: 422, data: { code: 'provider_capability_confirmation_required', findings: [{ step_key: 'T#1', step_type: 'T', provider_id: 'aip_sonnet', provider_name: 'Sonnet', missing_capabilities: ['source_write'] }] } },
    })
    await open()
    await pick(q('wp-edit-step-provider', row('c_t1|main')!), 'aip_sonnet')
    await click(q('wp-edit-save'))
    expect(text(q('wp-edit-capability-item'))).toBe('T#1 · Sonnet: source_write')
    // inputs wait while the confirmation is open
    expect((q('wp-edit-step-provider', row('c_t2|main')!) as HTMLSelectElement).disabled).toBe(true)
    const firstBody = lastPut().body
    await click(q('wp-edit-capability-confirm'))
    expect(api.putRequest).toHaveBeenCalledTimes(2)
    expect(lastPut().capability_warning_acks).toEqual(['T#1'])
    expect(lastPut().body).toEqual(firstBody)
    expect(q('wp-edit-capability')).toBeNull()
    expect(q('wp-edit-dirty')).toBeNull()
  })

  it('422 not editable and 423 AI run: the failure line; the edits stay', async () => {
    api.putRequest.mockRejectedValueOnce({ isAxiosError: true, response: { status: 423, data: { detail: { code: 'GROUP_AI_RUN_LOCKED' } } } })
    await open()
    await type(q('wp-edit-step-note', row('c_d1|main')!) as HTMLInputElement, 'a')
    await click(q('wp-edit-save'))
    expect(q('wp-edit-failure')?.dataset.kind).toBe('ai_running')
    api.putRequest.mockRejectedValueOnce({ isAxiosError: true, response: { status: 422, data: { detail: 'Modification not allowed after final approval.' } } })
    await click(q('wp-edit-save'))
    expect(q('wp-edit-failure')?.dataset.kind).toBe('not_editable')
    expect(text(q('wp-edit-failure'))).toContain('Modification not allowed after final approval.')
    expect(q('wp-edit-dirty')).not.toBeNull()
  })
})

describe('locks (nothing is sent)', () => {
  it('the server’s verdict: final approval locks every control', async () => {
    answer = readAnswer({ editable: false, edit_locked_reason: 'final_approved' })
    await open()
    expect(q('wp-edit-lock')?.dataset.reason).toBe('final_approved')
    expect((q('wp-edit-quantity-inc', box('T')!) as HTMLButtonElement).disabled).toBe(true)
    expect((q('wp-edit-step-provider', row('c_t1|main')!) as HTMLSelectElement).disabled).toBe(true)
    expect((q('wp-edit-step-note', row('c_t1|main')!) as HTMLInputElement).disabled).toBe(true)
    expect((q('wp-edit-apply-defaults') as HTMLButtonElement).disabled).toBe(true)
    expect((q('wp-edit-card-down', document.body.querySelector('[data-card="c_t1"]')!) as HTMLButtonElement).disabled).toBe(true)
    await click(q('wp-edit-quantity-inc', box('T')!))
    expect(box('T')?.dataset.count).toBe('2')
    expect((q('wp-edit-save') as HTMLButtonElement).disabled).toBe(true)
  })

  it('the opener’s read-only state and run lock, and the Flow’s AI run', async () => {
    let external: 'read_only' | 'run_locked' | null = 'run_locked'
    await open({ external: () => external })
    expect(q('wp-edit-lock')?.dataset.reason).toBe('run_locked')
    closeAllWorkPlanEditWindows()
    await flushPromises()
    external = null
    active = { active: true, run_id: 'run_x', status: 'running', doc_ref: DOC }
    api.getRequest.mockImplementation(async (path: string) => {
      if (path === WP_PATH) return { data: JSON.parse(JSON.stringify(answer)) }
      if (path === '/api/v1/ai-invoke/active') return { data: active }
      if (path === '/api/v1/ai-invoke/run_x') return { data: { status: 'running' } }
      throw new Error(`unexpected GET ${path}`)
    })
    await open({ external: () => external })
    expect(q('wp-edit-lock')?.dataset.reason).toBe('ai_running')
    expect((q('wp-edit-step-provider', row('c_t1|main')!) as HTMLSelectElement).disabled).toBe(true)
  })
})

describe('locks that change while the window is open', () => {
  const ACTIVE_PATH = '/api/v1/ai-invoke/active'
  /** While set, a work plan read waits for it (the answer is the plan as it was when asked). */
  let gate: Promise<void> | null
  let runStatus: string

  beforeEach(() => {
    gate = null
    runStatus = 'running'
    api.getRequest.mockImplementation(async (path: string) => {
      if (path === WP_PATH) {
        const data = JSON.parse(JSON.stringify(answer))
        if (gate) await gate
        return { data }
      }
      if (path === ACTIVE_PATH) return { data: active }
      if (path === '/api/v1/ai-invoke/run_x') return { data: { run_id: 'run_x', status: runStatus } }
      throw new Error(`unexpected GET ${path}`)
    })
  })

  const note = (rowId: string) => q('wp-edit-step-note', row(rowId)!) as HTMLInputElement
  const saveButton = () => q('wp-edit-save') as HTMLButtonElement
  const activityReads = () => api.getRequest.mock.calls.filter(([path]) => path === ACTIVE_PATH).length
  const sessions: WorkPlanStepEditSession[] = []
  async function stepSessionOf(rowId: string): Promise<WorkPlanStepEditSession> {
    sessions.length = 0
    setWorkPlanStepEditHandler({ open: (_target, session) => sessions.push(session) })
    await flushPromises()
    await click(q('wp-edit-step-edit', row(rowId)!))
    return sessions[0]
  }
  const reviewTwice = { reviewCount: 2, reviewerProviderId: 'aip_opus', preInstructionText: '사전 지시', preInstructionAttachment: null }

  it('[다시 읽기] over unsaved edits locks every input and the Step window until the answer; nothing typed meanwhile is lost', async () => {
    api.putRequest.mockRejectedValueOnce({ isAxiosError: true, response: { status: 409, data: { code: 'wp_revision_conflict', updated_by: 'kim', updated_at: '2026-10-19T08:00' } } })
    await open()
    const session = await stepSessionOf('c_t1|main')
    await type(note('c_d1|main'), '버릴 변경')
    await click(q('wp-edit-save'))
    expect(q('wp-edit-conflict')).not.toBeNull()

    answer = readAnswer({ revision_no: 5 })
    let release!: () => void
    gate = new Promise<void>((resolve) => (release = resolve))
    await click(q('wp-edit-conflict-reload'))
    await click(confirmButtons()[1])

    // the answer is on its way: nothing can change the plan, the Step window's apply included
    expect(q('wp-edit-reloading')).not.toBeNull()
    expect(note('c_d1|main').disabled).toBe(true)
    expect((q('wp-edit-step-provider', row('c_t1|main')!) as HTMLSelectElement).disabled).toBe(true)
    expect((q('wp-edit-quantity-inc', box('T')!) as HTMLButtonElement).disabled).toBe(true)
    expect(saveButton().disabled).toBe(true)
    expect(session.locked()).toBe(true)
    expect(session.apply(reviewTwice)).toBe('locked')
    await type(note('c_d1|main'), '응답 전 입력')
    await click(q('wp-edit-quantity-inc', box('T')!))
    expect(box('T')?.dataset.count).toBe('2')
    expect(api.putRequest).toHaveBeenCalledTimes(1)

    release()
    gate = null
    await flushPromises()
    expect(q('wp-edit-reloading')).toBeNull()
    expect(text(q('wp-edit-revision'))).toBe('r5')
    expect(q('wp-edit-dirty')).toBeNull()
    expect(note('c_d1|main').value).toBe('설계')
    expect(session.snapshot()).toBeNull()
    expect(session.apply(reviewTwice)).toBe('gone')
    const reopened = await stepSessionOf('c_t1|main')
    expect(reopened.snapshot()).toMatchObject({ reviewCount: 1, reviewerProviderId: 'aip_sonnet' })

    // Reload discarded the old child. A newly opened session can edit the new revision.
    expect(note('c_d1|main').disabled).toBe(false)
    await type(note('c_d1|main'), '응답 후 입력')
    expect(reopened.apply(reviewTwice)).toBe('applied')
    await click(q('wp-edit-save'))
    expect(api.putRequest).toHaveBeenCalledTimes(2)
    expect(lastPut().base_revision_no).toBe(5)
    expect(lastPut().body.steps[0].note).toBe('응답 후 입력')
    expect(lastPut().body.steps[1]).toMatchObject({ review_count: 2, reviewer_provider_id: 'aip_opus' })
  })

  it('422 not editable: the lock state is read again after the save; the edits stay, the window locks, nothing more is sent', async () => {
    await open()
    const session = await stepSessionOf('c_t1|main')
    await type(note('c_d1|main'), '남길 변경')
    answer = readAnswer({ editable: false, edit_locked_reason: 'final_approved' })
    api.putRequest.mockRejectedValueOnce({ isAxiosError: true, response: { status: 422, data: { detail: 'Modification not allowed after final approval.' } } })
    await click(q('wp-edit-save'))

    expect(wpReads()).toBe(2)
    expect(q('wp-edit-failure')?.dataset.kind).toBe('not_editable')
    expect(q('wp-edit-lock')?.dataset.reason).toBe('final_approved')
    expect(q('wp-edit-dirty')).not.toBeNull()
    expect(note('c_d1|main').value).toBe('남길 변경')
    expect(note('c_d1|main').disabled).toBe(true)
    expect(saveButton().disabled).toBe(true)
    expect(session.apply(reviewTwice)).toBe('locked')
    await click(q('wp-edit-save'))
    expect(api.putRequest).toHaveBeenCalledTimes(1)
  })

  it('422 not editable while the read still says editable: locked as refused until the plan is read again', async () => {
    await open()
    await type(note('c_d1|main'), '남길 변경')
    api.putRequest.mockRejectedValueOnce({ isAxiosError: true, response: { status: 422, data: { detail: 'Modification not allowed.' } } })
    await click(q('wp-edit-save'))
    expect(wpReads()).toBe(2)
    expect(q('wp-edit-lock')?.dataset.reason).toBe('not_editable')
    expect(note('c_d1|main').value).toBe('남길 변경')
    expect(saveButton().disabled).toBe(true)
    await click(q('wp-edit-save'))
    expect(api.putRequest).toHaveBeenCalledTimes(1)
  })

  it('409 the Flow discarded: the read still says editable, yet the window stays locked; the edits stay, nothing more is sent', async () => {
    await open()
    const session = await stepSessionOf('c_t1|main')
    await type(note('c_d1|main'), '폐기 전 변경')
    api.putRequest.mockRejectedValueOnce({ isAxiosError: true, response: { status: 409, data: { detail: 'The flow was discarded.' } } })
    await click(q('wp-edit-save'))

    expect(wpReads()).toBe(2)
    expect(q('wp-edit-failure')?.dataset.kind).toBe('disposed')
    expect(q('wp-edit-lock')?.dataset.reason).toBe('disposed')
    expect(q('wp-edit-dirty')).not.toBeNull()
    expect(note('c_d1|main').value).toBe('폐기 전 변경')
    expect(note('c_d1|main').disabled).toBe(true)
    expect(saveButton().disabled).toBe(true)
    expect(session.apply(reviewTwice)).toBe('locked')

    // a later external signal reads again (editable=true): the discard is not in the read, the lock stays
    window.dispatchEvent(new CustomEvent('fg:document_content_changed', { detail: { doc_id: DOC } }))
    await flushPromises()
    expect(wpReads()).toBe(3)
    expect(q('wp-edit-lock')?.dataset.reason).toBe('disposed')
    expect(note('c_d1|main').value).toBe('폐기 전 변경')
    await click(q('wp-edit-save'))
    expect(api.putRequest).toHaveBeenCalledTimes(1)
  })

  it('an AI run started elsewhere after opening locks the window on the next poll (no save needed); its end unlocks it', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'setInterval', 'clearInterval', 'Date'] })
    await open()
    const session = await stepSessionOf('c_t1|main')
    expect(activityReads()).toBe(1)
    expect(q('wp-edit-lock')).toBeNull()
    await type(note('c_d1|main'), '실행 전 변경')

    // another tab starts the Flow's AI run
    active = { active: true, run_id: 'run_x', status: 'running', doc_ref: `${FLOW}.0007-T` }
    await vi.advanceTimersByTimeAsync(DOCUMENT_WINDOW_POLL_MS + DOCUMENT_WINDOW_REFRESH_COALESCE_MS)
    await flushPromises()
    expect(activityReads()).toBeGreaterThan(1)
    expect(q('wp-edit-lock')?.dataset.reason).toBe('ai_running')
    expect(note('c_d1|main').disabled).toBe(true)
    expect(note('c_d1|main').value).toBe('실행 전 변경')
    expect(q('wp-edit-dirty')).not.toBeNull()
    expect(saveButton().disabled).toBe(true)
    expect(session.locked()).toBe(true)
    expect(session.apply(reviewTwice)).toBe('locked')
    await click(q('wp-edit-save'))
    expect(api.putRequest).not.toHaveBeenCalled()

    // the run ends: the window unlocks with the edits as they were, and the save goes
    runStatus = 'finished'
    active = { active: false }
    await vi.advanceTimersByTimeAsync(DOCUMENT_WINDOW_POLL_MS + DOCUMENT_AI_POLL_MS)
    await flushPromises()
    expect(q('wp-edit-lock')).toBeNull()
    expect(note('c_d1|main').disabled).toBe(false)
    expect(saveButton().disabled).toBe(false)
    expect(session.locked()).toBe(false)
    await click(q('wp-edit-save'))
    expect(api.putRequest).toHaveBeenCalledTimes(1)
    expect(lastPut().body.steps[0].note).toBe('실행 전 변경')
  })

  it('a run started elsewhere locks a clean window too; an external change signal reads the run as well', async () => {
    await open()
    expect(q('wp-edit-lock')).toBeNull()
    active = { active: true, run_id: 'run_x', status: 'running', doc_ref: DOC }
    window.dispatchEvent(new CustomEvent('fg:document_content_changed', { detail: { doc_id: DOC } }))
    await flushPromises()
    expect(activityReads()).toBe(2)
    expect(q('wp-edit-lock')?.dataset.reason).toBe('ai_running')
    expect((q('wp-edit-quantity-inc', box('T')!) as HTMLButtonElement).disabled).toBe(true)
  })
})

describe('closing and external changes', () => {
  it('closing with unsaved edits asks first: keep editing, or discard and close', async () => {
    await open()
    await type(q('wp-edit-step-note', row('c_d1|main')!) as HTMLInputElement, '변경')
    await click(q('wp-edit-close'))
    expect(text(document.body.querySelector('.ui-confirm-title'))).toBe('저장하지 않은 변경을 버리고 닫을까요?')
    await click(confirmButtons()[0])
    expect(workPlanEditWindowList()).toHaveLength(1)
    await click(q('wp-edit-close'))
    await click(confirmButtons()[1])
    expect(workPlanEditWindowList()).toHaveLength(0)
    expect(api.putRequest).not.toHaveBeenCalled()
  })

  it('a clean window takes a plan changed elsewhere; a dirty one keeps its edits and says a newer revision exists', async () => {
    await open()
    answer = readAnswer({ revision_no: 3, body: { ...planBody(), defaults: { provider_id: null, note: '바뀜' } } })
    window.dispatchEvent(new CustomEvent('fg:document_content_changed', { detail: { doc_id: DOC } }))
    await flushPromises()
    expect(text(q('wp-edit-revision'))).toBe('r3')
    expect((q('wp-edit-default-note') as HTMLInputElement).value).toBe('바뀜')

    await type(q('wp-edit-step-note', row('c_d1|main')!) as HTMLInputElement, '내 변경')
    answer = readAnswer({ revision_no: 4 })
    window.dispatchEvent(new CustomEvent('fg:document_content_changed', { detail: { doc_id: DOC } }))
    await flushPromises()
    expect(text(q('wp-edit-revision'))).toBe('r3')
    expect(text(q('wp-edit-external'))).toContain('r4')
    expect((q('wp-edit-step-note', row('c_d1|main')!) as HTMLInputElement).value).toBe('내 변경')
    await click(q('wp-edit-save'))
    expect(lastPut().base_revision_no).toBe(3)
  })

  it('another document’s change is not this window’s', async () => {
    await open()
    window.dispatchEvent(new CustomEvent('fg:document_content_changed', { detail: { doc_id: `${FLOW}.0001-R` } }))
    await flushPromises()
    expect(wpReads()).toBe(1)
  })
})

describe('empty, unreadable, missing', () => {
  it('a plan with every quantity 0 shows the empty line; raising a quantity adds Steps', async () => {
    const empty = planBody() as Record<string, any>
    for (const box of Object.values(empty.quantities) as Array<{ count: number }>) box.count = 0
    empty.steps = []
    answer = readAnswer({ body: empty, step_execution_status: [] })
    await open()
    expect(q('wp-edit-empty')).not.toBeNull()
    await click(q('wp-edit-quantity-inc', box('N')!))
    expect(qa('wp-edit-step').map((r) => r.dataset.step)).toEqual(['N#1', 'NR#1'])
  })

  it('409 wp_unreadable shows the stored text; 404 the missing line with [다시 읽기]', async () => {
    readFailure = { isAxiosError: true, response: { status: 409, data: { code: 'wp_unreadable', raw: '{broken', revision_no: 3 } } }
    await open()
    expect(text(q('wp-edit-raw'))).toBe('{broken')
    closeAllWorkPlanEditWindows()
    await flushPromises()
    readFailure = { isAxiosError: true, response: { status: 404, data: { detail: 'Document not found' } } }
    await open()
    expect(q('wp-edit-error')?.dataset.kind).toBe('not_found')
    readFailure = null
    await click(q('wp-edit-retry'))
    expect(qa('wp-edit-step')).toHaveLength(7)
  })
})

describe('the Step window boundary (✎, T#2)', () => {
  it('without a Step handler ✎ is disabled with its reason; it never opens a drawer here', async () => {
    setWorkPlanStepEditHandler(null)
    await open()
    const pencil = q('wp-edit-step-edit', row('c_t1|main')!) as HTMLButtonElement
    expect(pencil.disabled).toBe(true)
    expect(pencil.title).toBe('단계 상세 편집 창은 아직 연결되지 않았습니다.')
    expect((q('wp-edit-step-edit', row('c_ts1|result')!) as HTMLButtonElement).title).toBe('서버가 만드는 단계는 편집할 수 없습니다.')
  })

  it('with one, ✎ hands the row and a session: apply changes that row only; it follows a new card’s id', async () => {
    const opened: Array<{ target: WorkPlanStepEditTarget; session: WorkPlanStepEditSession }> = []
    setWorkPlanStepEditHandler({ open: (target, session) => opened.push({ target, session }) })
    await open()
    await click(q('wp-edit-step-edit', row('c_t1|main')!))
    expect(document.body.querySelectorAll('.ui-modeless-panel')).toHaveLength(1)
    expect(opened).toHaveLength(1)
    const { target, session } = opened[0]
    expect(target).toEqual({ docId: DOC, rowId: 'c_t1|main', stepKey: 'T#1', stepNo: 2, type: 'T', pairRole: 'instruction' })
    expect(session.snapshot()).toMatchObject({ reviewCount: 1, reviewerProviderId: 'aip_sonnet', preInstructionText: '사전 지시' })
    expect(session.reviewCountChoices()).toEqual([0, 1, 2, 3, -1])
    expect(session.reviewerOptions('프로젝트 기본', (n) => n).map((o) => o.value)).toEqual(['', 'aip_opus', 'aip_sonnet'])
    expect(session.apply({ reviewCount: 2, reviewerProviderId: 'aip_opus', preInstructionText: '바꾼 지시', preInstructionAttachment: null })).toBe('applied')
    await flushPromises()
    expect(text(q('wp-edit-step-review', row('c_t1|main')!))).toBe('검수 2회 · Opus')
    expect(q('wp-edit-dirty')).not.toBeNull()
    expect(api.putRequest).not.toHaveBeenCalled()
    await click(q('wp-edit-save'))
    const sent = lastPut().body.steps
    expect(sent[1]).toMatchObject({ review_count: 2, reviewer_provider_id: 'aip_opus', reviewer_provider_display_name: 'Opus', pre_instruction_text: '바꾼 지시', pre_instruction_attachment: null })
    expect(sent.filter((_: unknown, i: number) => i !== 1)).toEqual((planBody().steps as unknown[]).filter((_, i) => i !== 1))

    // a new card's row: the session keeps working after the save names it
    await click(q('wp-edit-quantity-inc', box('N')!))
    const newRow = qa('wp-edit-step').find((r) => r.dataset.step === 'N#1')!
    await click(q('wp-edit-step-edit', newRow))
    const fresh = opened[1].session
    expect(opened[1].target.rowId.startsWith('tmp:')).toBe(true)
    await click(q('wp-edit-save'))
    expect(fresh.target()?.rowId).toBe('c_new_N#1|main')
    expect(fresh.apply({ reviewCount: 1, reviewerProviderId: null, preInstructionText: null, preInstructionAttachment: null })).toBe('applied')
    expect(session.locked()).toBe(false)
    await click(q('wp-edit-close'))
    await click(confirmButtons()[1])
    expect(session.alive()).toBe(false)
    expect(session.apply({ reviewCount: 0, reviewerProviderId: null, preInstructionText: null, preInstructionAttachment: null })).toBe('gone')
  })

  it('the parent closing tells the Step windows; their pending edits are asked about', async () => {
    const closed: string[] = []
    let pending = true
    setWorkPlanStepEditHandler({ open: () => {}, pending: () => pending, parentClosed: (docId) => closed.push(docId) })
    await open()
    await click(q('wp-edit-close'))
    expect(text(document.body.querySelector('.ui-confirm-message'))).toBe('단계 상세 편집 창에서 아직 반영하지 않은 내용도 함께 사라집니다.')
    pending = false
    await click(confirmButtons()[1])
    expect(closed).toEqual([DOC])
  })
})
