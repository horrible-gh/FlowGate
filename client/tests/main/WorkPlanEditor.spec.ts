// flowgate.default.0395 TR0014 (T0013) — work-plan table editor (D0007 §6.4 / P0009 §4.4-4.8).
// Covers the T/TR2 completion criteria from NR0005 §6.3: the approved-mockup table
// renders from the server body, and a save round-trip preserves the data exactly
// when nothing was touched (JSON 왕복 시 데이터 보존).
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@shared/i18n'
import WorkPlanEditor from '@main/components/WorkPlanEditor.vue'
import { useProjectStore } from '@main/stores/project'

const { getRequest, postRequest, putRequest } = vi.hoisted(() => ({
  getRequest: vi.fn(),
  postRequest: vi.fn(),
  putRequest: vi.fn(),
}))

vi.mock('@shared/api', () => ({
  default: { head: vi.fn(), get: vi.fn(), post: vi.fn(), patch: vi.fn(), put: vi.fn() },
  getRequest,
  postRequest,
  putRequest,
  patchRequest: vi.fn(),
}))

const TYPES = [
  { code: 'D', label: '기본설계', category: 'design', countable: true, unit: 'sheet', sort_order: 1 },
  { code: 'T', label: '작업지시', category: 'instruction', countable: true, unit: 'set', pair_code: 'TR', sort_order: 2 },
  { code: 'TR', label: '작업레포트', category: 'work', countable: false },
  { code: 'TSR', label: '테스트레포트', category: 'work', countable: false },
]

const EIGHT_COUNTABLE_TYPES = [
  { code: 'DS', label: '설계지시', category: 'design', countable: true, unit: 'sheet', sort_order: 0 },
  { code: 'D', label: '기본설계', category: 'design', countable: true, unit: 'sheet', sort_order: 1 },
  { code: 'P', label: '프로토콜', category: 'design', countable: true, unit: 'sheet', sort_order: 2 },
  { code: 'L', label: '로직', category: 'design', countable: true, unit: 'sheet', sort_order: 3 },
  { code: 'DB', label: '데이터베이스', category: 'design', countable: true, unit: 'sheet', sort_order: 4 },
  { code: 'N', label: '조사지시', category: 'instruction', countable: true, unit: 'set', pair_code: 'NR', sort_order: 5 },
  { code: 'NR', label: '조사레포트', category: 'work', countable: false, sort_order: 6 },
  { code: 'T', label: '작업지시', category: 'instruction', countable: true, unit: 'set', pair_code: 'TR', sort_order: 7 },
  { code: 'TR', label: '작업레포트', category: 'work', countable: false, sort_order: 8 },
  { code: 'TS', label: '테스트지시', category: 'instruction', countable: true, unit: 'set', pair_code: 'TSR', sort_order: 9 },
  { code: 'TSR', label: '테스트레포트', category: 'work', countable: false, sort_order: 10 },
]

const PLAN_BODY = {
  wp_version: 1,
  binding: 'advisory',
  counted_types: ['D', 'T'],
  quantities: {
    D: { unit: 'sheet', count: 1 },
    T: { unit: 'set', count: 1 },
  },
  provider_candidates: [
    { provider_id: 'aip_opus', display_name: 'Claude Opus', group_label: 'Claude · CLI' },
  ],
  defaults: { provider_id: null, note: '' },
  steps: [
    { key: 'D#1', type: 'D', ordinal: 1, pair_key: null, pair_role: 'single', provider_id: 'aip_opus', provider_display_name: 'Claude Opus', note: '문서 화면 설계', locked: false, locked_reason: null, origin: 'human' },
    { key: 'T#1', type: 'T', ordinal: 1, pair_key: 'TR#1', pair_role: 'instruction', provider_id: null, provider_display_name: null, note: null, locked: false, locked_reason: null, origin: 'human' },
    { key: 'TR#1', type: 'TR', ordinal: 1, pair_key: 'T#1', pair_role: 'result', provider_id: null, provider_display_name: null, note: null, locked: false, locked_reason: null, origin: 'human' },
  ],
}

const READ_RESPONSE = {
  ok: true,
  doc_id: 'flowgate.default.0402.0002-WP',
  doc_type: 'WP',
  title: '0402 작업계획',
  group_id: 'flowgate.default.0402',
  parent_doc_id: 'flowgate.default.0402.0001-R',
  status: 'open',
  doc_review_status: 'pending_review',
  revision_no: 3,
  stored_path: 'documents/flowgate/main/default/0402/0002-WP_document.json',
  origin: 'human',
  created_by: 'sjm',
  updated_by: 'sjm',
  updated_at: '2026-08-08T15:02:44+09:00',
  body: PLAN_BODY,
  provider_status: [],
  assignment_summary: [{ provider_id: 'aip_opus', display_name: 'Claude Opus', step_count: 1 }],
  unassigned_step_count: 2,
  totals: { design_sheets: 1, work_sets: 1, steps: 3 },
  last_application: null,
}

const APPLY_PREVIEW = {
  wp_revision_no: 3,
  wp_review_status: 'pending_review',
  instruction_mode: 'auto_approved',
  workflow: { owner_doc_id: READ_RESPONSE.parent_doc_id, workflow_tag: 'none' },
  comparison: {
    kept: { count: 0, done_count: 0 },
    added: { count: 3, items: [] },
    not_deleted: { count: 0, items: [] },
  },
  step_map: [],
  fill_preview: {
    target_seq: null,
    target_key: null,
    target_label: null,
    provider_overrides: {},
    note_overrides: {},
    folded: [],
  },
  warnings: [],
  can_apply: false,
  can_apply_without_workflow: false,
  can_apply_with_workflow: false,
  can_change_workflow: true,
  apply_blockers: {
    keep_workflow: 'workflow_not_decided',
    change_workflow: 'nothing_to_fill',
  },
}

function routeGet() {
  getRequest.mockImplementation((url: string) => {
    if (url.includes('/document-types')) return Promise.resolve({ data: { data: TYPES } })
    if (url.includes('/work-plan')) return Promise.resolve({ data: structuredClone(READ_RESPONSE) })
    return Promise.reject(new Error(`unexpected url: ${url}`))
  })
}

function mountEditor() {
  return mount(WorkPlanEditor, {
    props: { docId: 'flowgate.default.0402.0002-WP', projectId: 'flowgate' },
    global: { plugins: [i18n], stubs: { AppIcon: true, teleport: true } },
  })
}

beforeEach(() => {
  setActivePinia(createPinia())
  i18n.global.locale.value = 'ko'
  useProjectStore().currentProjectId = 'flowgate'
  getRequest.mockReset()
  postRequest.mockReset()
  putRequest.mockReset()
  routeGet()
})

describe('WorkPlanEditor', () => {
  // 0399 M0020 — 편집기가 갖고 있던 전면 적용 미리보기 오버레이는 걷어냈다. 그것을 열던
  // 액션바 단추도 함께 없앴고, 편집기는 이제 그 창을 열 길 자체를 노출하지 않는다.
  it('적용 미리보기 오버레이도, 그것을 여는 길도 남아 있지 않다', async () => {
    const wrapper = mountEditor()
    await flushPromises()

    expect(wrapper.find('.wpa-overlay').exists()).toBe(false)
    expect((wrapper.vm as unknown as { openApplyPreview?: () => void }).openApplyPreview)
      .toBeUndefined()
    // 계획을 읽는 것 말고 다른 POST 는 나가지 않는다 — 예전엔 여기서 미리보기를 불렀다.
    expect(postRequest.mock.calls.every(([url]) => !String(url).includes('/work-plan/apply')))
      .toBe(true)
  })

  it('shows the server totals in the three summary cards of the mockup, and flags unassigned steps', async () => {
    const wrapper = mountEditor()
    await flushPromises()

    // 시안 xc32frrg 화면 1 하단 — 설계 장수 / 작업 세트 / 배정할 단계
    const cards = wrapper.findAll('.wp-sum-card')
    expect(cards).toHaveLength(3)
    expect(cards[0].text()).toContain('설계 장수')
    expect(cards[0].find('.wp-sum-value').text()).toBe('1장')
    expect(cards[1].find('.wp-sum-value').text()).toBe('1세트')
    expect(cards[2].find('.wp-sum-value').text()).toBe('3단계')
    expect(cards[2].text()).toContain('설계 장수 + 작업 세트 × 2 (= 문서 건수)')

    expect(wrapper.get('.wp-section-missing').text()).toContain('미지정 2단계')
  })

  it('draws the table-mode strip, numbered sections and AI legend of the mockup', async () => {
    const wrapper = mountEditor()
    await flushPromises()

    expect(wrapper.get('.wp-mode-pill').text()).toContain('표 편집 모드')
    expect(wrapper.get('.wp-toolbar').text()).toContain('AI 제안 불러오기')
    expect(wrapper.findAll('.wp-step-no-badge').map((n) => n.text())).toEqual(['1', '2'])
    expect(wrapper.get('.wp-step-legend').text()).toContain('보라색 칸 = AI가 제안한 값')
    expect(wrapper.get('.wp-step-row .wp-step-no').text()).toBe('단계 1')
  })

  it('shows the complete wording when the server reports zero unassigned steps', async () => {
    const complete = structuredClone(READ_RESPONSE)
    complete.body.steps = complete.body.steps.map((step: any) => ({
      ...step,
      provider_id: 'aip_opus',
      provider_display_name: 'Claude Opus',
    }))
    complete.assignment_summary = [{ provider_id: 'aip_opus', display_name: 'Claude Opus', step_count: 3 }]
    complete.unassigned_step_count = 0
    getRequest.mockImplementation((url: string) => {
      if (url.includes('/document-types')) return Promise.resolve({ data: { data: TYPES } })
      if (url.includes('/work-plan')) return Promise.resolve({ data: complete })
      return Promise.reject(new Error(`unexpected url: ${url}`))
    })

    const wrapper = mountEditor()
    await flushPromises()
    expect(wrapper.find('.wp-section-missing').exists()).toBe(false)
  })

  it('restores all eight quantity cards from the registry and saves missing types as zero', async () => {
    getRequest.mockImplementation((url: string) => {
      if (url.includes('/document-types')) return Promise.resolve({ data: { data: EIGHT_COUNTABLE_TYPES } })
      if (url.includes('/work-plan')) return Promise.resolve({ data: structuredClone(READ_RESPONSE) })
      return Promise.reject(new Error(`unexpected url: ${url}`))
    })
    putRequest.mockResolvedValue({
      data: {
        revision_no: 4,
        totals: { design_sheets: 1, work_sets: 1, steps: 3 },
        assignment_summary: [],
        unassigned_step_count: 2,
      },
    })
    const wrapper = mountEditor()
    await flushPromises()

    expect(wrapper.findAll('.wp-qty-card')).toHaveLength(8)
    expect(wrapper.findAll('.wp-qty-value').map((node) => node.text())).toEqual(['0', '1', '0', '0', '0', '0', '1', '0'])

    const saveBtn = wrapper.findAll('button').find((button) => button.text().includes('저장') && !button.text().includes('저장 중'))!
    await saveBtn.trigger('click')
    await flushPromises()

    const saved = putRequest.mock.calls[0][1].body
    expect(saved.counted_types).toEqual(['DS', 'D', 'P', 'L', 'DB', 'N', 'T', 'TS'])
    expect(saved.quantities).toEqual({
      DS: { unit: 'sheet', count: 0 },
      D: { unit: 'sheet', count: 1 },
      P: { unit: 'sheet', count: 0 },
      L: { unit: 'sheet', count: 0 },
      DB: { unit: 'sheet', count: 0 },
      N: { unit: 'set', count: 0 },
      T: { unit: 'set', count: 1 },
      TS: { unit: 'set', count: 0 },
    })
  })

  it('renders compact provider controls without the mockup-extraneous robot icon', async () => {
    const wrapper = mountEditor()
    await flushPromises()

    expect(wrapper.findAll('.wp-defaults-row .aip-select-icon')).toHaveLength(0)
    expect(wrapper.findAll('.wp-step-row .aip-select-icon')).toHaveLength(0)
    expect(wrapper.findAll('.wp-step-row .aip-select--compact')).toHaveLength(3)
  })

  it('renders quantity cards and the expanded step table from the fetched body', async () => {
    const wrapper = mountEditor()
    await flushPromises()

    expect(wrapper.text()).toContain('작업계획')
    // Quantity cards: one per counted type, showing its current count.
    const qtyCards = wrapper.findAll('.wp-qty-card')
    expect(qtyCards).toHaveLength(2)
    expect(wrapper.find('.wp-qty-grid').text()).toContain('기본설계')

    // Steps expand D#1 (single) + T#1/TR#1 (instruction/result pair) = 3 rows.
    const rows = wrapper.findAll('.wp-step-row')
    expect(rows).toHaveLength(3)
    // TSR is not part of this plan, so the server-assembly notice never appears.
    expect(wrapper.find('.wp-step-list').text()).not.toContain('서버 자동조립')
    // The note is a plain <input value="...">; textContent never carries it.
    const noteInputs = wrapper.findAll('.wp-step-msg')
    expect((noteInputs[0].element as HTMLInputElement).value).toBe('문서 화면 설계')
  })

  it('locks a TSR-shaped step from provider/note input when present', async () => {
    const withTsr = structuredClone(READ_RESPONSE)
    withTsr.body.counted_types.push('TS')
    withTsr.body.quantities.TS = { unit: 'set', count: 1 }
    withTsr.body.steps.push(
      { key: 'TS#1', type: 'TS', ordinal: 1, pair_key: 'TSR#1', pair_role: 'instruction', provider_id: 'aip_opus', provider_display_name: 'Claude Opus', note: null, locked: false, locked_reason: null, origin: 'human' },
      { key: 'TSR#1', type: 'TSR', ordinal: 1, pair_key: 'TS#1', pair_role: 'result', provider_id: null, provider_display_name: null, note: null, locked: true, locked_reason: 'server_assembled', origin: 'system' },
    )
    withTsr.totals.steps = 5
    getRequest.mockImplementation((url: string) => {
      if (url.includes('/document-types')) return Promise.resolve({ data: { data: TYPES } })
      if (url.includes('/work-plan')) return Promise.resolve({ data: withTsr })
      return Promise.reject(new Error(`unexpected url: ${url}`))
    })

    const wrapper = mountEditor()
    await flushPromises()
    expect(wrapper.text()).toContain('서버 자동조립')
  })

  it('saves the canonical body unchanged when nothing was edited (JSON round-trip preservation)', async () => {
    putRequest.mockResolvedValue({
      data: {
        ok: true,
        doc_id: 'flowgate.default.0402.0002-WP',
        revision_no: 4,
        updated_at: '2026-08-08T15:20:31+09:00',
        updated_by: 'sjm',
        doc_review_status: 'pending_review',
        unassigned_step_count: 2,
        assignment_summary: [],
        totals: { design_sheets: 1, work_sets: 1, steps: 3 },
      },
    })
    const wrapper = mountEditor()
    await flushPromises()

    const saveBtn = wrapper.findAll('button').find((b) => b.text().includes('저장') && !b.text().includes('저장 중'))!
    await saveBtn.trigger('click')
    await flushPromises()

    expect(putRequest).toHaveBeenCalledTimes(1)
    const [url, payload] = putRequest.mock.calls[0]
    expect(url).toBe('/api/v1/documents/flowgate.default.0402.0002-WP/work-plan')
    expect(payload.base_revision_no).toBe(3)
    expect(payload.body).toEqual(PLAN_BODY)
  })

  it('lowers a value-bearing quantity immediately and restores its values when raised again', async () => {
    const wrapper = mountEditor()
    await flushPromises()

    const minusButtons = wrapper.findAll('.wp-stepper-btn').filter((button) => button.text() === '−')
    await minusButtons[0].trigger('click')
    await flushPromises()

    expect(wrapper.text()).not.toContain('입력값이 있는 단계가 빠집니다')
    expect(wrapper.findAll('.wp-step-row')).toHaveLength(2)

    const plusButtons = wrapper.findAll('.wp-stepper-btn').filter((button) => button.text() === '+')
    await plusButtons[0].trigger('click')
    await flushPromises()
    const restoredRows = wrapper.findAll('.wp-step-row')
    expect(restoredRows).toHaveLength(3)
    const restoredNote = restoredRows[0].find('.wp-step-msg').element as HTMLInputElement
    expect(restoredNote.value).toBe('문서 화면 설계')
  })
})
