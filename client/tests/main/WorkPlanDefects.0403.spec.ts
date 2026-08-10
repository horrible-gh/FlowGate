/**
 * flowgate.default.0403 T0005 — NR0004 가 화면 쪽에 남긴 결함들의 회귀 테스트.
 *
 *   F2  붓기 저장은 계획 리비전을 함께 보낸다
 *   F3  계획이 워크플로에 부어진 적이 있는지 화면이 말한다
 *   F4  워크플로가 없는 그룹의 작업계획에도 [작업계획 적용] 이 있다
 *   F5  저장하지 않은 편집이 있으면 AI 에 맡기지 못한다
 *   F7  편집 잠금은 서버 판정을 그대로 따른다
 */
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'

const { getRequest, postRequest, putRequest, patchRequest } = vi.hoisted(() => ({
  getRequest: vi.fn(),
  postRequest: vi.fn(),
  putRequest: vi.fn(),
  patchRequest: vi.fn(),
}))
vi.mock('@shared/api', () => ({
  default: { head: vi.fn(), get: vi.fn(), post: vi.fn(), patch: vi.fn(), put: vi.fn() },
  getRequest: (...a: unknown[]) => getRequest(...a),
  postRequest: (...a: unknown[]) => postRequest(...a),
  putRequest: (...a: unknown[]) => putRequest(...a),
  patchRequest: (...a: unknown[]) => patchRequest(...a),
  deleteRequest: vi.fn(),
}))

import DocWorkflow from '@main/components/DocWorkflow.vue'
import WorkPlanEditor from '@main/components/WorkPlanEditor.vue'
import WorkflowDecisionModal, { type PourPayload } from '@main/components/WorkflowDecisionModal.vue'

const WP_DOC_ID = 'flowgate.default.0403.0009-WP'
const OWNER_DOC_ID = 'flowgate.default.0403.0001-B'

const TYPES = [
  { code: 'D', label: '기본설계', category: 'design', countable: true, unit: 'sheet', sort_order: 1 },
  { code: 'T', label: '작업지시', category: 'instruction', countable: true, unit: 'set', pair_code: 'TR', sort_order: 2 },
  { code: 'TR', label: '작업레포트', category: 'work', countable: false, sort_order: 3 },
]

const PLAN_BODY = {
  wp_version: 1,
  binding: 'advisory',
  counted_types: ['D', 'T'],
  quantities: { D: { unit: 'sheet', count: 1 }, T: { unit: 'set', count: 1 } },
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

function readResponse(over: Record<string, unknown> = {}) {
  return {
    ok: true,
    doc_id: WP_DOC_ID,
    doc_type: 'WP',
    title: '0403 작업계획',
    group_id: 'flowgate.default.0403',
    parent_doc_id: OWNER_DOC_ID,
    status: 'open',
    doc_review_status: 'pending_review',
    editable: true,
    edit_locked_reason: null,
    revision_no: 2,
    stored_path: 'documents/flowgate/main/default/0403/0009-WP_document.json',
    origin: 'human',
    created_by: 'sjm',
    updated_by: 'sjm',
    updated_at: '2026-08-10T15:02:44+09:00',
    body: structuredClone(PLAN_BODY),
    provider_status: [],
    assignment_summary: [],
    unassigned_step_count: 2,
    totals: { design_sheets: 1, work_sets: 1, steps: 3 },
    last_application: null,
    ...over,
  }
}

function routeGet(view = readResponse()) {
  getRequest.mockImplementation((url: string) => {
    if (String(url).includes('/document-types')) return Promise.resolve({ data: { data: TYPES } })
    if (String(url).includes('/work-plan')) return Promise.resolve({ data: structuredClone(view) })
    return Promise.resolve({ data: { items: [] } })
  })
}

function mountEditor() {
  return mount(WorkPlanEditor, {
    props: { docId: WP_DOC_ID, projectId: 'flowgate' },
    global: { plugins: [i18n], stubs: { AppIcon: true, teleport: true } },
  })
}

beforeEach(() => {
  setActivePinia(createPinia())
  i18n.global.locale.value = 'ko'
  vi.clearAllMocks()
  routeGet()
})

// ── F7 — 편집 잠금은 서버가 정한다 ───────────────────────────────────────────

describe('작업계획 편집 잠금 (NR0004 F7)', () => {
  it('승인만으로는 잠그지 않는다 — 서버가 편집을 받아 주는 동안 표도 열려 있다', async () => {
    routeGet(readResponse({ doc_review_status: 'approved', editable: true }))
    const wrapper = mountEditor()
    await flushPromises()

    expect(wrapper.find('.wp-locked-hint').exists()).toBe(false)
    const save = wrapper.findAll('button').find(b => b.text().includes('저장'))!
    expect(save.attributes('disabled')).toBeUndefined()
    expect(wrapper.findAll('.wp-step-msg')[0].attributes('disabled')).toBeUndefined()
  })

  it('서버가 잠갔다고 하면 그 사유를 그대로 적고 표를 잠근다', async () => {
    routeGet(readResponse({
      doc_review_status: 'approved', editable: false, edit_locked_reason: 'final_approved',
    }))
    const wrapper = mountEditor()
    await flushPromises()

    expect(wrapper.get('.wp-locked-hint').text()).toContain('그룹 최종 승인이 끝나')
    const save = wrapper.findAll('button').find(b => b.text().includes('저장'))!
    expect(save.attributes('disabled')).toBeDefined()
  })

  it('상태 때문에 잠긴 경우는 최종승인과 다른 문구를 쓴다', async () => {
    routeGet(readResponse({ editable: false, edit_locked_reason: 'status' }))
    const wrapper = mountEditor()
    await flushPromises()

    expect(wrapper.get('.wp-locked-hint').text()).toContain('이 문서 상태에서는')
  })
})

// ── F5 — 저장하지 않은 편집 보호 ─────────────────────────────────────────────

describe('저장하지 않은 편집과 AI 채우기 (NR0004 F5)', () => {
  it('값을 고치면 저장하지 않았다고 알리고, 그동안 AI 에 맡기지 못한다', async () => {
    const wrapper = mountEditor()
    await flushPromises()

    const aiButton = () => wrapper.findAll('button').find(b => b.text().includes('AI 제안 불러오기'))!
    expect(wrapper.find('.wp-dirty-banner').exists()).toBe(false)
    expect(aiButton().attributes('disabled')).toBeUndefined()

    await wrapper.findAll('.wp-step-msg')[1].setValue('사람이 방금 적은 멘트')
    await flushPromises()

    expect(wrapper.get('.wp-dirty-banner').text()).toContain('저장하지 않은 변경이 있습니다')
    expect(aiButton().attributes('disabled')).toBeDefined()
  })

  it('저장하고 나면 다시 AI 에 맡길 수 있다', async () => {
    putRequest.mockResolvedValue({
      data: {
        ok: true, doc_id: WP_DOC_ID, revision_no: 3,
        totals: { design_sheets: 1, work_sets: 1, steps: 3 },
        assignment_summary: [], unassigned_step_count: 0,
      },
    })
    const wrapper = mountEditor()
    await flushPromises()

    await wrapper.findAll('.wp-step-msg')[1].setValue('사람이 방금 적은 멘트')
    await flushPromises()
    await wrapper.get('.wp-dirty-banner button').trigger('click')
    await flushPromises()

    expect(putRequest).toHaveBeenCalledTimes(1)
    expect(wrapper.find('.wp-dirty-banner').exists()).toBe(false)
    const aiButton = wrapper.findAll('button').find(b => b.text().includes('AI 제안 불러오기'))!
    expect(aiButton.attributes('disabled')).toBeUndefined()
  })
})

// ── F3 — 마지막 적용을 화면이 말한다 ─────────────────────────────────────────

describe('마지막 적용 (NR0004 F3)', () => {
  it('한 번도 부은 적이 없으면 그 사실을 그린다', async () => {
    const wrapper = mountEditor()
    await flushPromises()

    expect(wrapper.get('.wp-last-apply').text()).toContain('워크플로에 부은 적이 없습니다')
  })

  it('부어 넣은 기록이 있으면 누가 · 언제 · 어느 리비전인지 적는다', async () => {
    routeGet(readResponse({
      last_application: {
        applied_at: '2026-08-10T18:00:00+09:00', applied_by: 'sjm',
        wp_revision_no: 2, via: 'sequence_edit',
      },
    }))
    const wrapper = mountEditor()
    await flushPromises()

    const line = wrapper.get('.wp-last-apply').text()
    expect(line).toContain('sjm')
    expect(line).toContain('r2')
  })
})

// ── F4 — 워크플로가 없어도 부을 길이 있다 ────────────────────────────────────

describe('워크플로가 없는 그룹의 작업계획 (NR0004 F4)', () => {
  const WP_TAB = {
    id: WP_DOC_ID, title: '0403 작업계획', path: 'x.json', type: 'json',
    typeCode: 'WP', projectId: 'flowgate',
  }

  function candidates(mode: 'append' | 'replace_after') {
    return {
      data: {
        wp_doc_id: WP_DOC_ID,
        wp_revision_no: 2,
        workflow_doc_id: OWNER_DOC_ID,
        mode,
        plan_step_count: 2,
        rows: [],
        row_count_change: { before: 0, after: 2, deleted: 0, added: 2 },
        notifications: [],
        workflow_tag: 'none',
      },
    }
  }

  it('시퀀스가 하나도 없어도 [작업계획 적용] 이 화면에 있다', async () => {
    postRequest.mockImplementation((url: string, body: { mode: 'append' | 'replace_after' }) =>
      String(url).endsWith('/work-plan/sequence-candidates')
        ? Promise.resolve(candidates(body.mode))
        : Promise.resolve({ data: {} }))

    const wrapper = mount(DocWorkflow, {
      props: {
        tab: WP_TAB as never,
        workflowDecided: false,
        parentRDocId: null,
        stepStates: [] as never,
        canNextAction: false,
      },
      global: { plugins: [i18n], stubs: { teleport: true } },
    })
    await flushPromises()

    expect(wrapper.find('.wf-apply-btn').exists()).toBe(true)
    expect(wrapper.get('.wf-flow').text()).toContain('워크플로가 아직 없습니다')
  })
})

// ── F2 — 붓기 저장은 계획 리비전을 함께 보낸다 ───────────────────────────────

describe('붓기 저장이 보내는 것 (NR0004 F2)', () => {
  const ROWS = [
    {
      type: 'T', label: '작업지시', status: 'pending', locked: false, poured: true,
      note: '테스트 포함', origin: 'plan', plan_key: 'T#1',
      source_doc_id: WP_DOC_ID, source_revision_no: 2,
    },
  ]

  function pourPayload(over: Partial<PourPayload> = {}): PourPayload {
    return {
      wpDocId: WP_DOC_ID,
      wpRevisionNo: 2,
      wpShortCode: 'WP0009',
      workflowDocId: OWNER_DOC_ID,
      mode: 'append',
      planStepCount: 1,
      rows: ROWS as never,
      rowCountChange: { before: 0, after: 1, deleted: 0, added: 1 },
      notifications: [],
      workflowTag: 'none',
      ...over,
    }
  }

  async function mountModal(poured: PourPayload | null) {
    const wrapper = mount(WorkflowDecisionModal, {
      props: { visible: false, mode: 'edit' as const, docId: OWNER_DOC_ID, poured },
      global: { plugins: [i18n], stubs: { teleport: true } },
    })
    await wrapper.setProps({ visible: true })
    await flushPromises()
    return wrapper
  }

  it('부어 넣은 저장은 어느 계획을 어느 리비전으로 부었는지 함께 보낸다', async () => {
    patchRequest.mockResolvedValue({ data: { status: 'updated' } })
    getRequest.mockResolvedValue({ data: { items: [] } })
    const wrapper = await mountModal(pourPayload())

    await wrapper.findAll('button').find(b => b.text().includes('저장'))!.trigger('click')
    await flushPromises()

    const [url, body] = patchRequest.mock.calls[0]
    expect(url).toBe('/api/v1/workflow/sequence')
    expect(body.expected_plan).toEqual({
      wp_doc_id: WP_DOC_ID, wp_revision_no: 2, mode: 'append',
    })
  })

  it('계획을 거치지 않은 평범한 저장은 그것을 보내지 않는다', async () => {
    patchRequest.mockResolvedValue({ data: { status: 'updated' } })
    getRequest.mockResolvedValue({
      data: {
        items: [{
          id: 1, item_seq: 5, type: 'P', label: '프로토콜설계', doc_class: 'R',
          sort_order: 0, status: 'pending', note: '남아 있던 멘트',
          source_doc_id: WP_DOC_ID, source_revision_no: 1,
        }],
      },
    })
    const wrapper = await mountModal(null)

    await wrapper.findAll('button').find(b => b.text().includes('저장'))!.trigger('click')
    await flushPromises()

    const [, body] = patchRequest.mock.calls[0]
    expect(body.expected_plan).toBeUndefined()
    // 낡은 출처가 실려 나가도 그 자체로는 판정 근거가 아니다 — 서버가 무엇을 보고
    // 판정하는지는 expected_plan 하나로 정해진다.
    expect(body.items[0].source_revision_no).toBe(1)
  })
})
