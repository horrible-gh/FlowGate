// flowgate.default.0554 T0010 (T#2) — WorkPlanEditor per-step execution settings (D0007 §3.2-§3.4,
// §6; approved MirageGlass deck t17hbdfg v8). Covers exactly the five behaviours the T doc asks
// the TR to report on: new-input state preservation across quantity re-expand, the one-file
// attachment limit, non-target (report/server-assembled) step handling, the AI-run/edit lock, and
// drawer save/close behaviour.
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@shared/i18n'
import WorkPlanEditor from '@main/components/WorkPlanEditor.vue'
import { useAiInvokeRunsStore } from '@main/stores/aiInvokeRuns'
import { useProjectStore } from '@main/stores/project'

const { getRequest, postRequest, postFormRequest, putRequest } = vi.hoisted(() => ({
  getRequest: vi.fn(),
  postRequest: vi.fn(),
  postFormRequest: vi.fn(),
  putRequest: vi.fn(),
}))

vi.mock('@shared/api', () => ({
  default: { head: vi.fn(), get: vi.fn(), post: vi.fn(), patch: vi.fn(), put: vi.fn() },
  getRequest,
  postRequest,
  postFormRequest,
  putRequest,
  patchRequest: vi.fn(),
}))

// D0007 §5.5 — the quantity-decrease removal warning goes through the shared imperative
// confirm() (L0009 §2), not window.confirm. Default to "confirmed" so the existing
// re-expand/recovery tests below (which lower a quantity through steps that carry values)
// keep exercising the removal path unattended; individual tests override this to assert the
// cancel path preserves state.
const { dialogConfirm } = vi.hoisted(() => ({
  dialogConfirm: vi.fn(() => Promise.resolve(true)),
}))
vi.mock('@main/composables/useDialogStack', async (importOriginal) => ({
  ...(await importOriginal<Record<string, unknown>>()),
  confirm: dialogConfirm,
}))

const DOC_ID = 'flowgate.default.0554.0011-WP'
const GROUP = 'flowgate.default.0554'

const TYPES = [
  { code: 'D', label: '기본설계', category: 'design', countable: true, unit: 'sheet', sort_order: 1 },
  { code: 'T', label: '작업지시', category: 'instruction', countable: true, unit: 'set', pair_code: 'TR', sort_order: 2 },
  { code: 'TR', label: '작업레포트', category: 'work', countable: false, sort_order: 3 },
  { code: 'TS', label: '테스트지시', category: 'instruction', countable: true, unit: 'set', pair_code: 'TSR', sort_order: 4 },
  { code: 'TSR', label: '테스트레포트', category: 'work', countable: false, sort_order: 5 },
]

const REGISTERED_PROVIDERS = [
  { id: 'aip_opus', name: 'Claude Opus', group_label: 'Claude · CLI' },
  { id: 'aip_sonnet', name: 'Claude Sonnet', group_label: 'Claude · CLI' },
]

const ATTACHMENT_REF = {
  doc_id: DOC_ID,
  filename: '__wp_pre_instruction__T-1__0123456789abcdef.txt',
  original_filename: 'brief.txt',
  content_sha256: 'a'.repeat(64),
}

function planBody() {
  return {
    wp_version: 2,
    binding: 'advisory',
    counted_types: ['D', 'T', 'TS'],
    quantities: {
      D: { unit: 'sheet', count: 1 },
      T: { unit: 'set', count: 1 },
      TS: { unit: 'set', count: 1 },
    },
    provider_candidates: [
      { provider_id: 'aip_opus', display_name: 'Claude Opus', group_label: 'Claude · CLI' },
    ],
    defaults: { provider_id: null, note: '' },
    steps: [
      { key: 'D#1', type: 'D', ordinal: 1, pair_key: null, pair_role: 'single', provider_id: 'aip_opus', provider_display_name: 'Claude Opus', note: '설계', review_count: 0, reviewer_provider_id: null, reviewer_provider_display_name: null, pre_instruction_text: null, pre_instruction_attachment: null, locked: false, locked_reason: null, origin: 'human' },
      { key: 'T#1', type: 'T', ordinal: 1, pair_key: 'TR#1', pair_role: 'instruction', provider_id: 'aip_opus', provider_display_name: 'Claude Opus', note: '', review_count: 2, reviewer_provider_id: 'aip_sonnet', reviewer_provider_display_name: 'Claude Sonnet', pre_instruction_text: '기존 지시', pre_instruction_attachment: ATTACHMENT_REF, locked: false, locked_reason: null, origin: 'human' },
      { key: 'TR#1', type: 'TR', ordinal: 1, pair_key: 'T#1', pair_role: 'result', provider_id: null, provider_display_name: null, note: null, review_count: 1, reviewer_provider_id: null, reviewer_provider_display_name: null, pre_instruction_text: null, pre_instruction_attachment: null, locked: false, locked_reason: null, origin: 'human' },
      { key: 'TS#1', type: 'TS', ordinal: 1, pair_key: 'TSR#1', pair_role: 'instruction', provider_id: null, provider_display_name: null, note: null, review_count: 0, reviewer_provider_id: null, reviewer_provider_display_name: null, pre_instruction_text: null, pre_instruction_attachment: null, locked: false, locked_reason: null, origin: 'human' },
      { key: 'TSR#1', type: 'TSR', ordinal: 1, pair_key: 'TS#1', pair_role: 'result', provider_id: null, provider_display_name: null, note: null, review_count: 0, reviewer_provider_id: null, reviewer_provider_display_name: null, pre_instruction_text: null, pre_instruction_attachment: null, locked: true, locked_reason: 'server_assembled', origin: 'system' },
    ],
  }
}

function readResponse() {
  return {
    ok: true,
    doc_id: DOC_ID,
    doc_type: 'WP',
    title: '0554 작업계획',
    group_id: GROUP,
    parent_doc_id: `${GROUP}.0001-R`,
    status: 'open',
    doc_review_status: 'pending_review',
    revision_no: 5,
    origin: 'human',
    body: planBody(),
    registered_providers: REGISTERED_PROVIDERS,
    provider_status: [],
    assignment_summary: [{ provider_id: 'aip_opus', display_name: 'Claude Opus', step_count: 2 }],
    unassigned_step_count: 2,
    totals: { design_sheets: 1, work_sets: 2, steps: 5 },
    last_application: null,
    editable: true,
    edit_locked_reason: null,
    review_count_choices: [0, 1, 2, 3, -1],
    limits: { note_max_chars: 200, pre_instruction_text_max_chars: 20000, pre_instruction_attachment_max_bytes: 20971520 },
  }
}

function mountEditor(props: Record<string, unknown> = {}) {
  return mount(WorkPlanEditor, {
    props: { docId: DOC_ID, projectId: 'flowgate', ...props },
    global: { plugins: [i18n], stubs: { AppIcon: true, teleport: true } },
  })
}

function startRun(): void {
  useAiInvokeRunsStore().trackStarted({
    run_id: 'aiv_20260921_000123',
    group_id: GROUP,
    doc_ref: DOC_ID,
    status: 'running',
    mode: 'single',
  })
}

beforeEach(() => {
  setActivePinia(createPinia())
  i18n.global.locale.value = 'ko'
  useProjectStore().currentProjectId = 'flowgate'
  getRequest.mockReset()
  postRequest.mockReset()
  postFormRequest.mockReset()
  putRequest.mockReset()
  dialogConfirm.mockReset()
  dialogConfirm.mockImplementation(() => Promise.resolve(true))
  getRequest.mockImplementation((url: string) => {
    if (url.includes('/document-types')) return Promise.resolve({ data: { data: TYPES } })
    if (url.includes('/ai-invoke/leases')) return Promise.resolve({ data: { items: [] } })
    if (url.includes('/ai-invoke/providers')) {
      return Promise.resolve({ data: { providers: structuredClone(REGISTERED_PROVIDERS), default_provider_id: 'aip_opus' } })
    }
    if (url.includes('/work-plan')) return Promise.resolve({ data: structuredClone(readResponse()) })
    return Promise.reject(new Error(`unexpected url: ${url}`))
  })
  putRequest.mockImplementation((_url: string, body: any) => Promise.resolve({
    data: { body: body.body, revision_no: body.base_revision_no + 1, totals: { design_sheets: 1, work_sets: 2, steps: 5 }, assignment_summary: [], unassigned_step_count: 0 },
  }))
})

describe('WorkPlanEditor — 7열 표 / drawer 진입점 (D0007 §6.1)', () => {
  it('단계 목록 표가 7열이고, 세 진입점이 같은 drawer를 연다', async () => {
    const wrapper = mountEditor()
    await flushPromises()

    const head = wrapper.get('.wp-step-head')
    expect(head.findAll('span').length).toBeGreaterThanOrEqual(7)

    const nameToggles = wrapper.findAll('[data-test="step-name-toggle"]')
    // D#1·T#1·TR#1·TS#1 — TSR#1 is locked and gets a plain span instead of a button.
    expect(nameToggles).toHaveLength(4)

    await nameToggles[1].trigger('click') // T#1
    await flushPromises()
    expect(wrapper.findAll('[data-test="step-drawer"]')).toHaveLength(1)

    // A second entry point (review pill) for the SAME step toggles the SAME drawer closed.
    const reviewPills = wrapper.findAll('[data-test="review-pill-toggle"]')
    await reviewPills[1].trigger('click')
    await flushPromises()
    expect(wrapper.findAll('[data-test="step-drawer"]')).toHaveLength(0)

    // The instruction icon (third entry point) opens it again.
    const instrIcons = wrapper.findAll('[data-test="instr-icon-toggle"]')
    await instrIcons[1].trigger('click')
    await flushPromises()
    expect(wrapper.findAll('[data-test="step-drawer"]')).toHaveLength(1)

    // Opening a different step's drawer closes the first — only one open at a time.
    await nameToggles[0].trigger('click') // D#1
    await flushPromises()
    const drawers = wrapper.findAll('[data-test="step-drawer"]')
    expect(drawers).toHaveLength(1)
  })
})

describe('WorkPlanEditor — 비대상 단계 (D0007 §3.2)', () => {
  it('TSR(서버 조립)은 drawer가 열리지 않고 검수·지시가 모두 비활성이다', async () => {
    const wrapper = mountEditor()
    await flushPromises()

    // No step-name button for the locked row at all.
    expect(wrapper.findAll('[data-test="step-name-toggle"]')).toHaveLength(4)

    const reviewPills = wrapper.findAll('[data-test="review-pill-toggle"]')
    const tsrPill = reviewPills[4] // D,T,TR,TS,TSR order
    expect(tsrPill.attributes('disabled')).toBeDefined()
    await tsrPill.trigger('click')
    await flushPromises()
    expect(wrapper.findAll('[data-test="step-drawer"]')).toHaveLength(0)

    const instrIcons = wrapper.findAll('[data-test="instr-icon-toggle"]')
    expect(instrIcons[4].attributes('disabled')).toBeDefined()
  })

  it('레포트 단계(TR)는 검수는 가능하고 사전지시는 대상이 아니라는 문구만 보인다', async () => {
    const wrapper = mountEditor()
    await flushPromises()

    const nameToggles = wrapper.findAll('[data-test="step-name-toggle"]')
    await nameToggles[2].trigger('click') // TR#1
    await flushPromises()

    const drawer = wrapper.get('[data-test="step-drawer"]')
    // Review section is present and editable for a report step.
    expect(drawer.find('[data-test="drawer-review-count"]').exists()).toBe(true)
    // No textarea / file control — pre-instruction is not eligible here.
    expect(drawer.find('[data-test="drawer-instr-text"]').exists()).toBe(false)
    expect(drawer.find('[data-test="drawer-instr-file-input"]').exists()).toBe(false)
    expect(drawer.find('.wp-drawer-excluded').exists()).toBe(true)
    expect(drawer.find('.wp-drawer-excluded').text()).toContain('레포트 문서')
  })
})

describe('WorkPlanEditor — 파일 1개 제한 (D0007 §3.7)', () => {
  it('선택→붙음, 교체는 참조를 바꿀 뿐 두 번째 슬롯을 만들지 않는다, 제거로 비운다', async () => {
    postFormRequest.mockResolvedValueOnce({
      data: { ok: true, reference: { ...ATTACHMENT_REF, original_filename: 'first.txt' }, size: 10, content_type: 'text/plain' },
    })

    const wrapper = mountEditor()
    await flushPromises()
    await wrapper.findAll('[data-test="step-name-toggle"]')[1].trigger('click') // T#1 already has ATTACHMENT_REF ('brief.txt')
    await flushPromises()

    let drawer = wrapper.get('[data-test="step-drawer"]')
    expect(drawer.find('[data-test="drawer-instr-file-name"]').text()).toBe('brief.txt')
    // Exactly one file input in the whole drawer — there is no structural slot for a second file.
    expect(drawer.findAll('[data-test="drawer-instr-file-input"]')).toHaveLength(1)

    // Replace: selecting a new file re-uses the same reference field.
    const fileInput = drawer.get('[data-test="drawer-instr-file-input"]')
    const file = new File(['hello'], 'second.txt', { type: 'text/plain' })
    Object.defineProperty(fileInput.element, 'files', { value: [file], configurable: true })
    await fileInput.trigger('change')
    await flushPromises()

    expect(postFormRequest).toHaveBeenCalledTimes(1)
    const [uploadUrl, form] = postFormRequest.mock.calls[0]
    expect(uploadUrl).toContain('/pre-instruction-attachments')
    expect(form.get('step_key')).toBe('T#1')

    drawer = wrapper.get('[data-test="step-drawer"]')
    expect(drawer.find('[data-test="drawer-instr-file-name"]').text()).toBe('first.txt')
    expect(drawer.findAll('[data-test="drawer-instr-file-input"]')).toHaveLength(1)

    // Remove clears the reference without a network call.
    await drawer.get('[data-test="drawer-instr-file-remove"]').trigger('click')
    await flushPromises()
    drawer = wrapper.get('[data-test="step-drawer"]')
    expect(drawer.find('[data-test="drawer-instr-file-remove"]').exists()).toBe(false)
    expect(drawer.find('[data-test="drawer-instr-file-name"]').text()).not.toBe('first.txt')
  })
})

describe('WorkPlanEditor — lock (D0007 §6.4 / T0010 §10)', () => {
  it('AI 실행 중에는 검수·사전지시·첨부·저장이 모두 disabled로 남는다(숨기지 않는다)', async () => {
    const wrapper = mountEditor()
    await flushPromises()
    await wrapper.findAll('[data-test="step-name-toggle"]')[1].trigger('click') // T#1
    await flushPromises()

    startRun()
    await flushPromises()

    const drawer = wrapper.get('[data-test="step-drawer"]')
    expect(drawer.get('[data-test="drawer-review-count"]').attributes('disabled')).toBeDefined()
    expect(drawer.get('[data-test="drawer-instr-text"]').attributes('disabled')).toBeDefined()
    expect(drawer.get('[data-test="drawer-instr-file-input"]').attributes('disabled')).toBeDefined()
    expect(drawer.get('[data-test="drawer-instr-file-remove"]').attributes('disabled')).toBeDefined()
    expect(drawer.get('[data-test="drawer-save"]').attributes('disabled')).toBeDefined()
    // The controls are still rendered — locked means disabled, not removed.
    expect(drawer.find('[data-test="drawer-review-count"]').exists()).toBe(true)
  })

  it('부트스트랩 read-only(props.readOnly)만으로도 새 컨트롤이 잠긴다', async () => {
    const wrapper = mountEditor({ readOnly: true })
    await flushPromises()
    await wrapper.findAll('[data-test="step-name-toggle"]')[1].trigger('click')
    await flushPromises()
    const drawer = wrapper.get('[data-test="step-drawer"]')
    expect(drawer.get('[data-test="drawer-review-count"]').attributes('disabled')).toBeDefined()
  })
})

describe('WorkPlanEditor — 저장 동작 (D0007 §6.2)', () => {
  it('[저장]은 전체 WP 저장을 실행하고 성공하면 drawer가 닫힌다', async () => {
    const wrapper = mountEditor()
    await flushPromises()
    await wrapper.findAll('[data-test="step-name-toggle"]')[0].trigger('click') // D#1
    await flushPromises()

    let drawer = wrapper.get('[data-test="step-drawer"]')
    await drawer.get('[data-test="drawer-review-count"]').setValue('2')
    await flushPromises()

    await wrapper.get('[data-test="drawer-save"]').trigger('click')
    await flushPromises()

    expect(putRequest).toHaveBeenCalledTimes(1)
    const [, payload] = putRequest.mock.calls[0]
    const savedStep = payload.body.steps.find((s: any) => s.key === 'D#1')
    expect(savedStep.review_count).toBe(2)
    expect(wrapper.findAll('[data-test="step-drawer"]')).toHaveLength(0)
  })

  it('[닫기]는 저장 없이 drawer만 닫고 입력값은 유지된다', async () => {
    const wrapper = mountEditor()
    await flushPromises()
    await wrapper.findAll('[data-test="step-name-toggle"]')[0].trigger('click')
    await flushPromises()
    const drawer = wrapper.get('[data-test="step-drawer"]')
    await drawer.get('[data-test="drawer-review-count"]').setValue('3')
    await flushPromises()

    await wrapper.get('[data-test="drawer-close"]').trigger('click')
    await flushPromises()

    expect(putRequest).not.toHaveBeenCalled()
    expect(wrapper.findAll('[data-test="step-drawer"]')).toHaveLength(0)
    // The edit is not discarded — dirty state persists until an explicit save.
    expect(wrapper.find('.wp-dirty-banner').exists()).toBe(true)
  })

  it('저장 실패 시 drawer와 입력값이 그대로 남는다', async () => {
    putRequest.mockRejectedValueOnce({ response: { status: 422, data: { message: '실패', errors: [] } } })
    const wrapper = mountEditor()
    await flushPromises()
    await wrapper.findAll('[data-test="step-name-toggle"]')[0].trigger('click')
    await flushPromises()
    await wrapper.get('[data-test="drawer-review-count"]').setValue('1')
    await flushPromises()

    await wrapper.get('[data-test="drawer-save"]').trigger('click')
    await flushPromises()

    expect(wrapper.findAll('[data-test="step-drawer"]')).toHaveLength(1)
    expect((wrapper.get('[data-test="drawer-review-count"]').element as HTMLSelectElement).value).toBe('1')
  })

  it('신규 필드만 바꿔도 dirty=true가 되어 저장이 생략되지 않는다', async () => {
    const wrapper = mountEditor()
    await flushPromises()
    await wrapper.findAll('[data-test="step-name-toggle"]')[0].trigger('click')
    await flushPromises()
    expect(wrapper.find('.wp-dirty-banner').exists()).toBe(false)

    await wrapper.get('[data-test="drawer-instr-text"]').setValue('새 지시')
    await flushPromises()
    expect(wrapper.find('.wp-dirty-banner').exists()).toBe(true)
  })
})

describe('WorkPlanEditor — 신규 입력 상태 보존 (D0007 §3.6 / §5.5)', () => {
  it('수량을 0으로 줄였다 다시 올리면 검수·검수담당·사전지시·첨부가 복원된다', async () => {
    const wrapper = mountEditor()
    await flushPromises()

    // T#1's review pill starts reflecting the fixture's review_count=2 / Claude Sonnet.
    const reviewPillsBefore = wrapper.findAll('[data-test="review-pill-toggle"]')
    expect(reviewPillsBefore[1].text()).toContain('2회')
    expect(reviewPillsBefore[1].text()).toContain('Claude Sonnet')

    // Lower the T (instruction/result set) quantity to 0 — this drops T#1/TR#1 from the table.
    const steppers = wrapper.findAll('.wp-stepper-btn')
    // Quantity cards render in registry order: D, T, TS (per TYPES above) → T is the second card.
    const tMinus = steppers[2] // [D-, D+, T-, T+, TS-, TS+]
    await tMinus.trigger('click')
    await flushPromises()
    expect(wrapper.findAll('[data-test="step-name-toggle"]').length).toBe(2) // D#1, TS#1 only

    // Raise it back to 1 — the step re-expands and must recover the buffered values.
    const steppersAfterDrop = wrapper.findAll('.wp-stepper-btn')
    await steppersAfterDrop[3].trigger('click') // T+
    await flushPromises()

    const reviewPillsAfter = wrapper.findAll('[data-test="review-pill-toggle"]')
    const restoredTPill = reviewPillsAfter[1]
    expect(restoredTPill.text()).toContain('2회')
    expect(restoredTPill.text()).toContain('Claude Sonnet')

    await wrapper.findAll('[data-test="step-name-toggle"]')[1].trigger('click')
    await flushPromises()
    const drawer = wrapper.get('[data-test="step-drawer"]')
    expect((drawer.get('[data-test="drawer-instr-text"]').element as HTMLTextAreaElement).value).toBe('기존 지시')
    expect(drawer.get('[data-test="drawer-instr-file-name"]').text()).toBe('brief.txt')
  })
})

describe('WorkPlanEditor — 수량 감소 삭제 경고 (D0007 §5.5)', () => {
  // Rejection rej_01M31T8VNBANKX38: hasValue() judges a step with only review_count/
  // reviewer/pre_instruction set (no provider, no note) as non-empty, but setQuantity() used
  // to discard reexpand()'s removalCandidates and drop the step without ever asking. This
  // fixture strips T#1's provider/note so only the new execution-setting fields carry a
  // value, reproducing exactly that regression.
  function mockWorkPlanWithoutProviderOnT1() {
    getRequest.mockImplementation((url: string) => {
      if (url.includes('/document-types')) return Promise.resolve({ data: { data: TYPES } })
      if (url.includes('/ai-invoke/leases')) return Promise.resolve({ data: { items: [] } })
      if (url.includes('/ai-invoke/providers')) {
        return Promise.resolve({ data: { providers: structuredClone(REGISTERED_PROVIDERS), default_provider_id: 'aip_opus' } })
      }
      if (url.includes('/work-plan')) {
        const res = structuredClone(readResponse())
        const t1 = res.body.steps.find((s: any) => s.key === 'T#1')
        t1.provider_id = null
        t1.provider_display_name = null
        t1.note = ''
        return Promise.resolve({ data: res })
      }
      return Promise.reject(new Error(`unexpected url: ${url}`))
    })
  }

  it('검수·사전지시만 설정되고 provider·멘트는 없는 단계를 수량 감소로 지우기 전 확인을 요구한다', async () => {
    mockWorkPlanWithoutProviderOnT1()
    const wrapper = mountEditor()
    await flushPromises()

    const steppers = wrapper.findAll('.wp-stepper-btn')
    const tMinus = steppers[2] // [D-, D+, T-, T+, TS-, TS+]

    dialogConfirm.mockResolvedValueOnce(false)
    await tMinus.trigger('click')
    await flushPromises()

    expect(dialogConfirm).toHaveBeenCalledTimes(1)
    expect(dialogConfirm.mock.calls[0][0]).toMatchObject({ danger: true })
    // Cancelled — T#1/TR#1 (values-only, no provider) must still be in the table.
    expect(wrapper.findAll('[data-test="step-name-toggle"]').length).toBe(4)

    dialogConfirm.mockResolvedValueOnce(true)
    await tMinus.trigger('click')
    await flushPromises()

    expect(dialogConfirm).toHaveBeenCalledTimes(2)
    // Confirmed this time — the pair is dropped.
    expect(wrapper.findAll('[data-test="step-name-toggle"]').length).toBe(2)
  })
})

describe('WorkPlanEditor — 검수 -1(통과할 때까지) 표시 (T0010 §2/§4/§14)', () => {
  // Rejection rej_01M31V2XP9D6FXC5: reviewSummaryText()/has-review used `count <= 0` / `count > 0`,
  // which folded -1 (review until it passes) into the "no review" state instead of treating it
  // as an active, non-zero review setting distinct from 0.
  function mockWorkPlanWithUnlimitedReviewOnD1() {
    getRequest.mockImplementation((url: string) => {
      if (url.includes('/document-types')) return Promise.resolve({ data: { data: TYPES } })
      if (url.includes('/ai-invoke/leases')) return Promise.resolve({ data: { items: [] } })
      if (url.includes('/ai-invoke/providers')) {
        return Promise.resolve({ data: { providers: structuredClone(REGISTERED_PROVIDERS), default_provider_id: 'aip_opus' } })
      }
      if (url.includes('/work-plan')) {
        const res = structuredClone(readResponse())
        const d1 = res.body.steps.find((s: any) => s.key === 'D#1')
        d1.review_count = -1
        d1.reviewer_provider_id = null
        d1.reviewer_provider_display_name = null
        return Promise.resolve({ data: res })
      }
      return Promise.reject(new Error(`unexpected url: ${url}`))
    })
  }

  it('review_count=-1은 표에서 검수 안 함이 아니라 통과할 때까지로 표시되고 활성 스타일이 붙는다', async () => {
    mockWorkPlanWithUnlimitedReviewOnD1()
    const wrapper = mountEditor()
    await flushPromises()

    const reviewPills = wrapper.findAll('[data-test="review-pill-toggle"]')
    const d1Pill = reviewPills[0] // D,T,TR,TS,TSR order
    expect(d1Pill.text()).toContain('통과할 때까지')
    expect(d1Pill.text()).not.toContain('검수 안 함')
    expect(d1Pill.classes()).toContain('has-review')
  })

  it('drawer에서 검수 횟수를 -1로 바꾸면 select가 활성 스타일이 되고 표 요약도 즉시 갱신된다', async () => {
    const wrapper = mountEditor()
    await flushPromises()

    // T#1 starts at review_count=2 (already "active"); switching to -1 must stay active,
    // not fall back to "no review" the way count<=0 used to.
    await wrapper.findAll('[data-test="step-name-toggle"]')[1].trigger('click') // T#1
    await flushPromises()

    const drawer = wrapper.get('[data-test="step-drawer"]')
    const select = drawer.get('[data-test="drawer-review-count"]')
    await select.setValue('-1')
    await flushPromises()

    expect(select.classes()).toContain('is-active')
    const reviewPills = wrapper.findAll('[data-test="review-pill-toggle"]')
    expect(reviewPills[1].text()).toContain('통과할 때까지')
    expect(reviewPills[1].classes()).toContain('has-review')
  })
})
