/**
 * flowgate.v02.0074 T#5 — 「지시 생략」 in the work plan edit window (D#1 §3.7 · §6.2), mounted by DocumentWindows with
 * `@shared/api` mocked as the server (the plan read and its PUT, the AI run, the deploy status / preview / execution):
 * - the rows that offer it (an N / T instruction, a D / P / L / DB Step; not TS, a result, a locked or hidden Step) and when
 *   it is off: text or only an attachment (내용 있음), the window's lock, unsaved edits (먼저 저장), the row's Step window open,
 *   a plan that cannot deploy or whose status read failed (전개 불가); its tooltip (「설계 지시 생략」 / 「지시 생략」);
 * - the order of one click: the text on the parent draft (origin human, no attachment), one PUT with base_revision_no, a
 *   preview of first (no deployed card) / add at the saved revision, the execution with that preview's baseline;
 * - a refused save puts the row and the window back (no text, not dirty) and shows the save's failure; no deploy call;
 * - saved but not deployed (a refusal or a blocker) is told apart from a refused save; 「전개 재시도」 sends no PUT, only a
 *   new preview and the execution; one at a time;
 * - instruction_kept / kept_type_mismatch are told as such; a deployed omission lights the row's pre-instruction mark.
 * - only the row's own card entry is the success (instruction_write): kept_duplicate · skipped_type · no entry are told
 *   as such even when other cards changed, and a preview with no change still says why for the row's card (its
 *   instruction kept, another type — as the server answers: writes false, no_change true, executable false);
 * - 「전개 재시도」 is each row's own: two rows failing in turn both keep it, each retries with a new preview and no PUT,
 *   and a deploy whose answer settles another row's card takes that row's retry away.
 */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import { DocumentWindows, closeAllDocumentAiWindows } from '@/features/documentWindow'
import { closeAllWorkPlanEditWindows, openWorkPlanEditWindow, setWorkPlanStepEditHandler } from '@/features/workPlanWindow'
import { WORK_PLAN_OMIT_TEXT, omitEligible } from '@/features/workPlanWindow/workPlanEditModel'
import { DOC, FLOW, hiddenPlanBody, planBody, readAnswer, step } from './workPlanFixtures'

const api = vi.hoisted(() => ({ getRequest: vi.fn(), putRequest: vi.fn(), postRequest: vi.fn() }))

vi.mock('@shared/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@shared/api')>()
  return { ...actual, ...api }
})

registerAppMessages(i18n)

const WP_PATH = `/api/v1/documents/${encodeURIComponent(DOC)}/work-plan`
const DEPLOY = `${WP_PATH}/deploy`
const PREVIEW = `${DEPLOY}/preview`

let answer: Record<string, unknown>
let deployStatus: Record<string, unknown> | null
let putAnswer: (payload: any) => unknown
let previewAnswer: (body: any) => unknown
let executeAnswer: (body: any) => unknown
let previews = 0
let wrapper: VueWrapper<any> | null = null

const axiosError = (status: number, data: unknown) => ({ isAxiosError: true, response: { status, data } })

function savedAnswer(payload: { base_revision_no: number; body: Record<string, any> }): Record<string, unknown> {
  const body = JSON.parse(JSON.stringify(payload.body))
  for (const s of body.steps) if (!s.card_id) s.card_id = `c_new_${s.pair_role === 'result' ? s.pair_key : s.key}`
  return { ok: true, doc_id: DOC, revision_no: payload.base_revision_no + 1, title: '작업계획', doc_review_status: 'pending_review', body, warnings: [] }
}

function statusAnswer(over: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    ok: true, doc_id: DOC, flow_id: FLOW, wp_node_key: 'gn_wp', ownership: 'graph', wp_revision_no: 2, deployable: true, reason: null,
    deployed_count: 0, target_count: 4, missing_count: 4, orphan_count: 0, mismatch_count: 0, cards: [], modes: { first: true, add: false, cleanup: false },
    ...over,
  }
}

function previewOf(body: any, over: Record<string, unknown> = {}): Record<string, unknown> {
  previews += 1
  return {
    ok: true, doc_id: DOC, mode: body.mode, cards: [], blockers: [], writes: true, no_change: false, executable: true,
    counts: {}, baseline: { wp_revision_no: body.wp_revision_no, plan_digest: `digest-${previews}`, draft_version: 40 + previews, base_revision_no: 1, remove_node_keys: [] },
    ...over,
  }
}

const deployCard = (cardId: string, over: Record<string, unknown> = {}) => ({
  card_id: cardId, doc_type: 'T', node_key: `gn_${cardId}`, result: 'already_deployed', reason: null, instruction: 'instruction_write', instruction_reason: null, settings_skipped: [], ...over,
})

beforeEach(() => {
  i18n.global.locale.value = 'ko'
  closeAllDocumentAiWindows()
  closeAllWorkPlanEditWindows()
  setWorkPlanStepEditHandler(null)
  for (const fn of Object.values(api)) fn.mockReset()
  answer = readAnswer()
  deployStatus = statusAnswer()
  previews = 0
  putAnswer = (payload) => savedAnswer(payload)
  previewAnswer = (body) => previewOf(body)
  executeAnswer = (body) => ({ ok: true, doc_id: DOC, mode: body.mode, cards: [deployCard('c_t2'), deployCard('c_d1', { doc_type: 'D' })], no_change: false, draft_version: 50, created_node_keys: [], removed_node_keys: [], key_map: {}, counts: {} })
  api.getRequest.mockImplementation(async (path: string) => {
    if (path === WP_PATH) return { data: JSON.parse(JSON.stringify(answer)) }
    if (path === DEPLOY) {
      if (deployStatus === null) throw axiosError(500, { ok: false, code: 'internal_error' })
      return { data: JSON.parse(JSON.stringify(deployStatus)) }
    }
    if (path === '/api/v1/ai-invoke/active') return { data: { active: false } }
    throw new Error(`unexpected GET ${path}`)
  })
  api.putRequest.mockImplementation(async (_path: string, payload: any) => ({ data: await putAnswer(payload) }))
  api.postRequest.mockImplementation(async (path: string, body: any) => {
    if (path === PREVIEW) return { data: await previewAnswer(body) }
    if (path === DEPLOY) return { data: await executeAnswer(body) }
    throw new Error(`unexpected POST ${path}`)
  })
  wrapper = mount(DocumentWindows, { global: { plugins: [i18n] }, attachTo: document.body })
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  closeAllWorkPlanEditWindows()
  closeAllDocumentAiWindows()
  setWorkPlanStepEditHandler(null)
  document.body.innerHTML = ''
})

const q = (id: string, root: ParentNode = document.body) => root.querySelector<HTMLElement>(`[data-test="${id}"]`)
const text = (node: Element | null | undefined) => node?.textContent?.replace(/\s+/g, ' ').trim() ?? null
const row = (rowId: string) => document.body.querySelector<HTMLElement>(`[data-test="wp-edit-step"][data-row="${rowId}"]`)
const omit = (rowId: string) => q('wp-edit-step-omit', row(rowId)!) as HTMLButtonElement | null
const mark = (rowId: string) => q('wp-edit-step-instruction', row(rowId)!)
const puts = () => api.putRequest.mock.calls.map(([, payload]) => payload as { base_revision_no: number; body: Record<string, any> })
const postsTo = (path: string) => api.postRequest.mock.calls.filter(([p]) => p === path).map(([, body]) => body as Record<string, unknown>)
const statusReads = () => api.getRequest.mock.calls.filter(([p]) => p === DEPLOY).length
const sentStep = (payload: { body: Record<string, any> }, key: string) => payload.body.steps.find((s: any) => s.key === key)
const notice = () => q('wp-edit-notice')

async function click(node: HTMLElement | null) {
  if (!node) throw new Error('nothing to click')
  node.dispatchEvent(new MouseEvent('click', { bubbles: true }))
  await flushPromises()
}

async function open(extra: Partial<Parameters<typeof openWorkPlanEditWindow>[0]> = {}) {
  openWorkPlanEditWindow({ docId: DOC, projectId: 'flowgate', title: '작업계획', ...extra })
  await flushPromises()
}

// --- 11. Where and when ------------------------------------------------------------------------------------

describe('「지시 생략」: the rows that offer it and when it is off', () => {
  it('the rule: N / T instruction and D / P / L / DB Steps; not TS, a result, a locked Step, DS or T2', () => {
    const of = (type: string, pair_role: string, locked = false) => omitEligible({ type, pair_role: pair_role as any, locked })
    expect([of('N', 'instruction'), of('T', 'instruction'), of('D', 'single'), of('P', 'single'), of('L', 'single'), of('DB', 'single')]).toEqual([true, true, true, true, true, true])
    expect([of('TS', 'instruction'), of('TR', 'result'), of('NR', 'result'), of('TR2', 'result'), of('TSR', 'result', true), of('T', 'instruction', true), of('DS', 'single'), of('T2', 'instruction')]).toEqual([false, false, false, false, false, false, false, false])
  })

  it('an icon beside ✎ on the eligible rows only; text set → 「내용 있음」; the tooltips name it by the row', async () => {
    await open()
    expect(statusReads()).toBe(1)
    for (const id of ['c_t1|result', 'c_t2|result', 'c_ts1|main', 'c_ts1|result']) expect(omit(id)).toBeNull()
    expect(omit('c_d1|main')!.disabled).toBe(false)
    expect(omit('c_d1|main')!.title).toBe('설계 지시 생략')
    expect(omit('c_t2|main')!.disabled).toBe(false)
    expect(omit('c_t2|main')!.title).toBe('지시 생략')
    expect(omit('c_t1|main')!.disabled).toBe(true)
    expect(omit('c_t1|main')!.title).toBe('지시 생략 — 내용 있음')
    // Beside ✎, in the same last cell.
    expect(omit('c_t2|main')!.parentElement?.classList.contains('wpe-pencil')).toBe(true)
    expect(q('wp-edit-step-edit', omit('c_t2|main')!.parentElement!)).not.toBeNull()
  })

  it('a hidden DS / T2 row draws no icon (it is not drawn at all)', async () => {
    answer = readAnswer({ body: hiddenPlanBody() })
    await open()
    expect(row('c_ds1|main')).toBeNull()
    expect(row('c_t21|main')).toBeNull()
    expect(omit('c_d1|main')).not.toBeNull()
  })

  it('only an attachment is content too', async () => {
    const body = planBody() as Record<string, any>
    body.steps[3] = { ...body.steps[3], pre_instruction_attachment: { doc_id: DOC, filename: 'r_2.txt', original_filename: 'b.txt', content_sha256: 'b'.repeat(64) } }
    answer = readAnswer({ body })
    await open()
    expect(omit('c_t2|main')!.disabled).toBe(true)
    expect(omit('c_t2|main')!.title).toBe('지시 생략 — 내용 있음')
  })

  it('the window locked (read only, final approval): off with the lock’s own words', async () => {
    await open({ external: () => 'read_only' })
    expect(omit('c_t2|main')!.disabled).toBe(true)
    expect(omit('c_t2|main')!.title).toBe('지시 생략 — 읽기 전용으로 열린 작업계획입니다.')
    closeAllWorkPlanEditWindows()
    await flushPromises()
    answer = readAnswer({ editable: false, edit_locked_reason: 'final_approved' })
    await open()
    expect(omit('c_d1|main')!.title).toBe('설계 지시 생략 — 최종 승인된 작업계획은 편집할 수 없습니다.')
  })

  it('unsaved edits: 「먼저 저장」', async () => {
    await open()
    const note = q('wp-edit-step-note', row('c_t2|main')!) as HTMLInputElement
    note.value = '바뀐 메모'
    note.dispatchEvent(new Event('input', { bubbles: true }))
    await flushPromises()
    expect(omit('c_t2|main')!.disabled).toBe(true)
    expect(omit('c_t2|main')!.title).toBe('지시 생략 — 먼저 저장')
  })

  it('the row’s Step window open: off for that row only', async () => {
    setWorkPlanStepEditHandler({ open: () => undefined, openRow: (docId, rowId) => docId === DOC && rowId === 'c_t2|main' })
    await open()
    expect(omit('c_t2|main')!.disabled).toBe(true)
    expect(omit('c_t2|main')!.title).toBe('지시 생략 — 단계 편집 창이 열려 있음')
    expect(omit('c_d1|main')!.disabled).toBe(false)
  })

  it.each([
    ['not deployable (a v0.1 Flow)', () => statusAnswer({ deployable: false, reason: 'flow_owned_by_v01', ownership: 'v01' })],
    ['not deployable (not a card plan)', () => statusAnswer({ deployable: false, reason: 'wp_not_on_card', wp_node_key: null })],
    ['not deployable (the run lock)', () => statusAnswer({ deployable: false, reason: 'graph_run_locked' })],
    ['the status read failed', () => null],
  ])('%s: 「전개 불가」', async (_name, status) => {
    deployStatus = status()
    await open()
    expect(omit('c_t2|main')!.disabled).toBe(true)
    expect(omit('c_t2|main')!.title).toBe('지시 생략 — 전개 불가')
    expect(omit('c_d1|main')!.title).toBe('설계 지시 생략 — 전개 불가')
  })
})

// --- 12-15. One click -----------------------------------------------------------------------------------------

describe('「지시 생략」: one click — write, save once, deploy', () => {
  it('no deployed card: the text on the draft (origin human), one PUT, a first preview at the saved revision, its baseline executed', async () => {
    await open()
    await click(omit('c_t2|main'))
    expect(puts()).toHaveLength(1)
    const put = puts()[0]
    expect(put.base_revision_no).toBe(2)
    expect(sentStep(put, 'T#2')).toMatchObject({ pre_instruction_text: WORK_PLAN_OMIT_TEXT, pre_instruction_attachment: null, origin: 'human' })
    expect(WORK_PLAN_OMIT_TEXT).toBe('승인 되었습니다')
    // Only that row changed.
    expect(sentStep(put, 'T#1').pre_instruction_text).toBe('사전 지시')
    expect(sentStep(put, 'D#1').pre_instruction_text).toBeNull()
    expect(postsTo(PREVIEW)).toEqual([{ mode: 'first', wp_revision_no: 3 }])
    expect(postsTo(DEPLOY)).toEqual([{ mode: 'first', wp_revision_no: 3, plan_digest: 'digest-1', draft_version: 41, base_revision_no: 1, remove_node_keys: [] }])
    // The PUT went first, then the preview, then the execution (in that call order).
    const order = [...api.putRequest.mock.invocationCallOrder, ...api.postRequest.mock.invocationCallOrder].sort((a, b) => a - b)
    expect(order[0]).toBe(api.putRequest.mock.invocationCallOrder[0])
    expect(notice()?.dataset.key).toBe('omitted')
    expect(text(notice())).toBe('지시를 생략하고 전개했습니다.')
    expect(mark('c_t2|main')).not.toBeNull()
    expect(q('wp-edit-unsaved')).toBeNull()
    expect(omit('c_t2|main')!.title).toBe('지시 생략 — 내용 있음')
  })

  it('a design row: the D Step’s own pre-instruction (the card’s 설계 지시); deployed cards → add', async () => {
    deployStatus = statusAnswer({ deployed_count: 2, modes: { first: false, add: true, cleanup: true } })
    await open()
    await click(omit('c_d1|main'))
    expect(sentStep(puts()[0], 'D#1')).toMatchObject({ pre_instruction_text: '승인 되었습니다', origin: 'human' })
    expect(postsTo(PREVIEW)).toEqual([{ mode: 'add', wp_revision_no: 3 }])
    expect(postsTo(DEPLOY)[0]).toMatchObject({ mode: 'add', plan_digest: 'digest-1', draft_version: 41 })
    expect(notice()?.dataset.key).toBe('omitted')
  })

  it.each([
    ['409 conflict', axiosError(409, { code: 'wp_revision_conflict', updated_by: 'kim', updated_at: 't' }), 'wp-edit-conflict', null],
    ['422 validation', axiosError(422, { code: 'validation_failed', errors: [{ loc: 'steps', key: null, code: 'x', params: {}, msg: 'bad' }] }), 'wp-edit-failure', 'validation'],
    ['422 capability (not confirmed for the person)', axiosError(422, { code: 'provider_capability_confirmation_required', findings: [{ step_key: 'T#2', step_type: 'T', provider_id: 'aip_old', provider_name: 'Old AI', missing_capabilities: ['source_write'] }] }), 'wp-edit-failure', 'capability'],
    ['423 the AI run', axiosError(423, { detail: 'running' }), 'wp-edit-failure', 'ai_running'],
    ['403', axiosError(403, { detail: 'no' }), 'wp-edit-failure', 'forbidden'],
  ])('a refused save (%s): the row and the window back as they were, the save’s failure shown, no deploy call', async (_name, error, banner, kind) => {
    putAnswer = () => {
      throw error
    }
    await open()
    await click(omit('c_t2|main'))
    expect(puts()).toHaveLength(1)
    expect(api.postRequest).not.toHaveBeenCalled()
    expect(mark('c_t2|main')).toBeNull()
    expect(q('wp-edit-unsaved')).toBeNull()
    expect(q('wp-edit-dirty')).toBeNull()
    expect(q(banner)).not.toBeNull()
    if (kind) expect(q('wp-edit-failure')?.dataset.kind).toBe(kind)
    expect(q('wp-edit-capability')).toBeNull()
    expect(q('wp-edit-capability-confirm')).toBeNull()
    expect(notice()?.dataset.key).not.toBe('omit_deploy_failed')
  })

  it('saved, the deploy refused: 「저장됨 · 전개 실패」, told apart from a refused save; 「전개 재시도」 sends no PUT, a new preview each time', async () => {
    let refuse = 2
    executeAnswer = (body) => {
      if (refuse > 0) {
        refuse -= 1
        throw axiosError(409, { ok: false, code: 'draft_version_conflict', message: 'm' })
      }
      return { ok: true, mode: body.mode, cards: [deployCard('c_t2')], no_change: false, draft_version: 60, created_node_keys: [], removed_node_keys: [], key_map: {}, counts: {} }
    }
    await open()
    await click(omit('c_t2|main'))
    expect(puts()).toHaveLength(1)
    expect(notice()?.dataset.key).toBe('omit_deploy_failed')
    expect(text(notice())).toBe('저장됨 · 전개 실패 — 그래프가 먼저 저장됨')
    expect(q('wp-edit-failure')).toBeNull()
    expect(mark('c_t2|main')).not.toBeNull()
    const retry = omit('c_t2|main')!
    expect(retry.dataset.action).toBe('retry')
    expect(retry.disabled).toBe(false)
    expect(retry.title).toBe('저장됨 · 전개 실패 — 전개 재시도')
    await click(retry)
    await click(omit('c_t2|main'))
    expect(puts()).toHaveLength(1)
    expect(postsTo(PREVIEW)).toHaveLength(3)
    expect(postsTo(DEPLOY).map((body) => [body.plan_digest, body.draft_version])).toEqual([['digest-1', 41], ['digest-2', 42], ['digest-3', 43]])
    expect(postsTo(PREVIEW).every((body) => body.wp_revision_no === 3)).toBe(true)
    expect(notice()?.dataset.key).toBe('omitted')
    expect(omit('c_t2|main')!.dataset.action).toBe('omit')
  })

  it('a blocker after the save: nothing executed, 「저장됨 · 전개 실패」 (카드 연결) with the retry', async () => {
    previewAnswer = (body) => previewOf(body, { blockers: [{ code: 'deploy_chain_nonlinear', node_keys: ['gn_x'], edge_ids: ['e1'] }], executable: false })
    await open()
    await click(omit('c_d1|main'))
    expect(puts()).toHaveLength(1)
    expect(postsTo(DEPLOY)).toEqual([])
    expect(text(notice())).toBe('저장됨 · 전개 실패 — 카드 연결')
    expect(omit('c_d1|main')!.dataset.action).toBe('retry')
  })

  it('a preview with no change: nothing executed, said in one line', async () => {
    previewAnswer = (body) => previewOf(body, { writes: false, no_change: true, executable: false })
    await open()
    await click(omit('c_t2|main'))
    expect(postsTo(DEPLOY)).toEqual([])
    expect(notice()?.dataset.key).toBe('omit_no_change')
  })

  it('one at a time: busy while it runs (aria-busy), the other rows off; a second click sends nothing', async () => {
    let release!: () => void
    const held = new Promise<void>((resolve) => (release = resolve))
    previewAnswer = async (body) => {
      await held
      return previewOf(body)
    }
    await open()
    omit('c_t2|main')!.dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await flushPromises()
    expect(omit('c_t2|main')!.getAttribute('aria-busy')).toBe('true')
    expect(omit('c_t2|main')!.disabled).toBe(true)
    expect(omit('c_d1|main')!.disabled).toBe(true)
    expect(omit('c_d1|main')!.title).toBe('설계 지시 생략 — 처리 중')
    await click(omit('c_t2|main'))
    await click(omit('c_d1|main'))
    expect(puts()).toHaveLength(1)
    expect(postsTo(PREVIEW)).toHaveLength(1)
    release()
    await flushPromises()
    expect(postsTo(DEPLOY)).toHaveLength(1)
    expect(omit('c_t2|main')!.getAttribute('aria-busy')).toBeNull()
  })

  it.each([
    ['instruction_kept', { instruction: 'instruction_kept', instruction_reason: 'has_body' }, 'omit_kept', '저장했지만 카드의 지시 문서는 그대로 두었습니다.'],
    ['kept_type_mismatch', { result: 'kept_type_mismatch', instruction: null }, 'omit_mismatch', '저장했지만 카드 종류가 달라 지시를 쓰지 않았습니다.'],
  ])('a %s result is told as such (not as a success)', async (_name, over, key, words) => {
    executeAnswer = (body) => ({ ok: true, mode: body.mode, cards: [deployCard('c_t2', over)], no_change: false, draft_version: 50, created_node_keys: [], removed_node_keys: [], key_map: {}, counts: {} })
    await open()
    await click(omit('c_t2|main'))
    expect(notice()?.dataset.key).toBe(key)
    expect(text(notice())).toBe(words)
    expect(notice()?.classList.contains('is-warning')).toBe(true)
  })

  const executed = (cards: unknown[]) => (body: any) => ({ ok: true, mode: body.mode, cards, no_change: false, draft_version: 50, created_node_keys: [], removed_node_keys: [], key_map: {}, counts: {} })

  it.each([
    ['kept_duplicate (the card placed twice)', [deployCard('c_t2', { result: 'kept_duplicate', instruction: null }), deployCard('c_t2', { result: 'kept_duplicate', instruction: null, node_key: 'gn_c_t2_b' })], 'omit_duplicate', '저장했지만 같은 카드가 여럿이라 지시를 쓰지 않았습니다.', 'omit'],
    ['skipped_type (an unregistered type)', [deployCard('c_t2', { result: 'skipped_type', reason: 'doc_type_not_registered', node_key: null, instruction: null })], 'omit_unregistered', '저장했지만 등록되지 않은 문서 종류라 카드를 만들지 않았습니다.', 'omit'],
    ['no entry for the card', [], 'omit_unconfirmed', '저장했지만 전개 결과에서 이 카드의 지시 기록을 확인하지 못했습니다.', 'retry'],
    ['its entry without an instruction write', [deployCard('c_t2', { instruction: null })], 'omit_unconfirmed', '저장했지만 전개 결과에서 이 카드의 지시 기록을 확인하지 못했습니다.', 'retry'],
  ])('the execution ran but the row’s card is %s while other cards changed: told as such, not as a success', async (_name, own, key, words, action) => {
    executeAnswer = executed([...(own as unknown[]), deployCard('c_d1', { doc_type: 'D', result: 'create', node_key: 'gn_new_d1' }), deployCard('c_t1')])
    await open()
    await click(omit('c_t2|main'))
    expect(postsTo(DEPLOY)).toHaveLength(1)
    expect(notice()?.dataset.key).toBe(key)
    expect(text(notice())).toBe(words)
    expect(notice()?.classList.contains('is-warning')).toBe(true)
    expect(omit('c_t2|main')!.dataset.action).toBe(action)
    if (action === 'retry') {
      // Not settled: the retry sends no PUT, a new preview and its execution.
      executeAnswer = executed([deployCard('c_t2')])
      await click(omit('c_t2|main'))
      expect(puts()).toHaveLength(1)
      expect(postsTo(PREVIEW)).toHaveLength(2)
      expect(notice()?.dataset.key).toBe('omitted')
      expect(omit('c_t2|main')!.dataset.action).toBe('omit')
    }
  })

  it.each([
    ['its instruction document kept (instruction_kept)', deployCard('c_t2', { instruction: 'instruction_kept', instruction_reason: 'has_body' }), 'omit_kept', '저장했지만 카드의 지시 문서는 그대로 두었습니다.'],
    ['the card of another type (kept_type_mismatch)', deployCard('c_t2', { result: 'kept_type_mismatch', reason: 'N', instruction: null }), 'omit_mismatch', '저장했지만 카드 종류가 달라 지시를 쓰지 않았습니다.'],
  ])('an already deployed card, a preview with no change and %s: nothing executed, the reason told (not 「변경 없음」)', async (_name, own, key, words) => {
    deployStatus = statusAnswer({ deployed_count: 4, missing_count: 0, modes: { first: false, add: true, cleanup: true } })
    previewAnswer = (body) => previewOf(body, { cards: [own, deployCard('c_t1', { instruction: 'instruction_kept', instruction_reason: 'has_body' }), deployCard('c_d1', { doc_type: 'D', instruction: null })], writes: false, no_change: true, executable: false })
    await open()
    await click(omit('c_t2|main'))
    expect(puts()).toHaveLength(1)
    expect(postsTo(PREVIEW)).toEqual([{ mode: 'add', wp_revision_no: 3 }])
    expect(postsTo(DEPLOY)).toEqual([])
    expect(notice()?.dataset.key).toBe(key)
    expect(text(notice())).toBe(words)
    expect(notice()?.classList.contains('is-warning')).toBe(true)
    expect(omit('c_t2|main')!.dataset.action).toBe('omit')
  })

  it('two rows saved but not deployed in turn: each keeps its own 「전개 재시도」 and retries with a new preview, no PUT', async () => {
    let refuse = 2
    let answerCards: unknown[] = []
    executeAnswer = (body) => {
      if (refuse > 0) {
        refuse -= 1
        throw axiosError(409, { ok: false, code: 'draft_version_conflict', message: 'm' })
      }
      return executed(answerCards)(body)
    }
    await open()
    await click(omit('c_t2|main'))
    expect(omit('c_t2|main')!.dataset.action).toBe('retry')
    await click(omit('c_d1|main'))
    expect(puts()).toHaveLength(2)
    expect(sentStep(puts()[1], 'D#1').pre_instruction_text).toBe(WORK_PLAN_OMIT_TEXT)
    expect(notice()?.dataset.key).toBe('omit_deploy_failed')
    // Both rows: the first one's retry is not overwritten by the second.
    for (const id of ['c_t2|main', 'c_d1|main']) {
      expect(omit(id)!.dataset.action).toBe('retry')
      expect(omit(id)!.disabled).toBe(false)
      expect(omit(id)!.title).toBe('저장됨 · 전개 실패 — 전개 재시도')
    }
    // The first row again: its card written; the second row still offers its retry.
    answerCards = [deployCard('c_t2')]
    await click(omit('c_t2|main'))
    expect(notice()?.dataset.key).toBe('omitted')
    expect(omit('c_t2|main')!.dataset.action).toBe('omit')
    expect(omit('c_t2|main')!.title).toBe('지시 생략 — 내용 있음')
    expect(omit('c_d1|main')!.dataset.action).toBe('retry')
    // Then the second row.
    answerCards = [deployCard('c_d1', { doc_type: 'D' })]
    await click(omit('c_d1|main'))
    expect(notice()?.dataset.key).toBe('omitted')
    expect(omit('c_d1|main')!.dataset.action).toBe('omit')
    expect(puts()).toHaveLength(2)
    expect(postsTo(PREVIEW)).toHaveLength(4)
    expect(postsTo(DEPLOY).map((body) => [body.plan_digest, body.draft_version])).toEqual([['digest-1', 41], ['digest-2', 42], ['digest-3', 43], ['digest-4', 44]])
    expect(postsTo(PREVIEW).map((body) => body.wp_revision_no)).toEqual([3, 4, 4, 4])
  })

  it('a deploy of another row whose answer settles the failed row’s card takes that row’s retry away; one it does not show stays', async () => {
    let refuse = 1
    executeAnswer = (body) => {
      if (refuse > 0) {
        refuse -= 1
        throw axiosError(409, { ok: false, code: 'deploy_preview_stale', message: 'm' })
      }
      return executed([deployCard('c_t2'), deployCard('c_d1', { doc_type: 'D' })])(body)
    }
    await open()
    await click(omit('c_t2|main'))
    expect(omit('c_t2|main')!.dataset.action).toBe('retry')
    await click(omit('c_d1|main'))
    expect(notice()?.dataset.key).toBe('omitted')
    // c_t2's instruction was written by this deploy (its own entry says so): no retry left.
    expect(omit('c_t2|main')!.dataset.action).toBe('omit')
    expect(omit('c_t2|main')!.title).toBe('지시 생략 — 내용 있음')
    expect(omit('c_d1|main')!.dataset.action).toBe('omit')
  })

  it('the status is read when the window opens and again after a plain [저장]', async () => {
    await open()
    expect(statusReads()).toBe(1)
    const note = q('wp-edit-step-note', row('c_t2|main')!) as HTMLInputElement
    note.value = '메모'
    note.dispatchEvent(new Event('input', { bubbles: true }))
    await flushPromises()
    await click(q('wp-edit-save'))
    expect(puts()).toHaveLength(1)
    expect(statusReads()).toBe(2)
    expect(api.postRequest).not.toHaveBeenCalled()
  })

  it('an eligible N row of a plan with a research set works the same way', async () => {
    const body = planBody() as Record<string, any>
    body.quantities.N = { unit: 'set', count: 1 }
    body.steps.push(
      step({ key: 'N#1', card_id: 'c_n1', type: 'N', ordinal: 1, pair_key: 'NR#1', pair_role: 'instruction' }),
      step({ key: 'NR#1', card_id: 'c_n1', type: 'NR', ordinal: 1, pair_key: 'N#1', pair_role: 'result' }),
    )
    answer = readAnswer({ body })
    await open()
    expect(omit('c_n1|result')).toBeNull()
    expect(omit('c_n1|main')!.title).toBe('지시 생략')
    await click(omit('c_n1|main'))
    expect(sentStep(puts()[0], 'N#1').pre_instruction_text).toBe('승인 되었습니다')
    expect(postsTo(DEPLOY)).toHaveLength(1)
    // The default answer has no entry for c_n1: not called a success.
    expect(notice()?.dataset.key).toBe('omit_unconfirmed')
  })
})
