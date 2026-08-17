// flowgate.default.0395 — 작업계획 생성 대화상자 (시안 xc32frrg 화면 2).
//
// 0032-M 반려("시안대로 하라") 이후, 이 스펙은 시안이 그린 형태를 못박는다.
//   · 좌우 두 칸(수량을 확인할 타입 | 투입할 프로바이더)과 각 칸의 ①② 번호·개수 알약
//   · 작업 타입은 쌍 태그(T+TR)와 쌍 이름(작업)으로 보인다
//   · 이 화면에서는 장수·세트수도, 단계별 프로바이더도, 문서 제목도 고르지 않는다
//     — 시안 문구 그대로 "문서를 연 뒤 표에서" 정한다
//   · 빈 타입/빈 프로바이더 차단(NR0005 §6.3 T/TR2)은 그대로 유지된다
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@shared/i18n'
import WorkPlanCreateDialog from '@main/components/WorkPlanCreateDialog.vue'
import { useProjectStore } from '@main/stores/project'

const { getRequest, postRequest } = vi.hoisted(() => ({ getRequest: vi.fn(), postRequest: vi.fn() }))

vi.mock('@shared/api', () => ({
  default: { head: vi.fn(), get: vi.fn(), post: vi.fn(), patch: vi.fn() },
  getRequest,
  postRequest,
  patchRequest: vi.fn(),
  extractApiErrorMessage: (error: any, fallback: string) =>
    error?.response?.data?.detail ?? error?.response?.data?.error?.message ?? fallback,
}))

// 0429 T0004 (NR0003): `data` mirrors the real document-types order — design series
// sorts before instruction (alphabetically), so D precedes DS here. This used to list
// DS first, which quietly assumed the very ordering contract the bug broke.
const TYPES = [
  { code: 'D', label: '기본설계', category: 'design', countable: true, unit: 'sheet' },
  { code: 'DS', label: '설계지시', category: 'instruction', countable: true, unit: 'sheet' },
  { code: 'T', label: '작업지시', category: 'instruction', countable: true, unit: 'set', pair_code: 'TR' },
  { code: 'TR', label: '작업레포트', category: 'work', countable: false, unit: null },
  { code: 'WP', label: '작업계획', category: 'general', countable: false, unit: null },
]

// The work-plan registry's own canonical order (DS leads) — the additive
// `work_plan_countable_types` field, separate from the raw `data` order above.
const TYPES_WP = [
  { code: 'DS', label: '설계지시', category: 'instruction', unit: 'sheet' },
  { code: 'D', label: '기본설계', category: 'design', unit: 'sheet' },
  { code: 'T', label: '작업지시', category: 'instruction', unit: 'set', pair_code: 'TR' },
]

const PROVIDERS = [
  { id: 'aip_opus', name: 'Claude Opus', kind: 'claude', exec_type: 'cli' },
  { id: 'aip_codex', name: 'GPT (codex)', kind: 'openai', exec_type: 'cli' },
  { id: 'aip_gpt_api', name: 'GPT (api)', kind: 'openai', exec_type: 'api' },
]

function routeGet() {
  getRequest.mockImplementation((url: string) => {
    if (url.includes('/document-types')) {
      return Promise.resolve({ data: { data: TYPES, work_plan_countable_types: TYPES_WP } })
    }
    if (url.includes('/ai-invoke/providers')) {
      return Promise.resolve({ data: { ok: true, project: 'flowgate', providers: PROVIDERS, default_provider_id: null } })
    }
    return Promise.reject(new Error(`unexpected url: ${url}`))
  })
}

function mountDialog() {
  return mount(WorkPlanCreateDialog, {
    props: {
      visible: true,
      parentDocId: 'flowgate.default.0402.0001-R',
      projectId: 'flowgate',
      groupId: 'flowgate.default.0402',
    },
    global: { plugins: [i18n], stubs: { teleport: true, AppIcon: true } },
  })
}

function createButton(wrapper: ReturnType<typeof mountDialog>) {
  return wrapper.findAll('button').find((b) => b.text().includes('생성') && !b.text().includes('생성 중'))!
}
function check(wrapper: ReturnType<typeof mountDialog>, text: string) {
  return wrapper.findAll('.wpc-check').find((l) => l.text().includes(text))!
}

beforeEach(() => {
  setActivePinia(createPinia())
  i18n.global.locale.value = 'ko'
  useProjectStore().currentProjectId = 'flowgate'
  getRequest.mockReset()
  postRequest.mockReset()
  routeGet()
})

describe('WorkPlanCreateDialog', () => {
  it('blocks [생성] until a type AND a provider are both checked, and never calls the API meanwhile', async () => {
    const wrapper = mountDialog()
    await flushPromises()

    expect(createButton(wrapper).attributes('disabled')).toBeDefined()

    await check(wrapper, 'DS').trigger('click')
    expect(createButton(wrapper).attributes('disabled')).toBeDefined()
    expect(wrapper.text()).toContain('투입할 프로바이더를 하나 이상 체크해 주세요.')

    await check(wrapper, 'Claude Opus').trigger('click')
    expect(createButton(wrapper).attributes('disabled')).toBeUndefined()
    expect(postRequest).not.toHaveBeenCalled()
  })

  it('lays the mockup out: two numbered columns, count pills, and pair tags on the work rows', async () => {
    const wrapper = mountDialog()
    await flushPromises()

    // 좌우 두 칸 + ①② 번호 배지
    expect(wrapper.findAll('.wpc-cols .wpc-sec')).toHaveLength(2)
    expect(wrapper.findAll('.wpc-sec-no').map((n) => n.text())).toEqual(['1', '2'])

    // 개수 알약 — 고른 수 / 전체 수
    expect(wrapper.findAll('.wpc-count-pill').map((n) => n.text())).toEqual(['0 / 3', '0 / 3'])
    await wrapper.findAll('.wpc-sec-hd .wpc-mini-btn')[0].trigger('click')
    expect(wrapper.findAll('.wpc-count-pill')[0].text()).toBe('3 / 3')

    // 작업 줄은 쌍 태그(T+TR)와 쌍 이름(작업)으로 보인다 — '작업지시'가 아니다
    const workRow = check(wrapper, '작업')
    expect(workRow.findAll('.wpc-pair .doc-tag').map((n) => n.text())).toEqual(['T', 'TR'])
    expect(workRow.find('.wpc-check-name').text()).toBe('작업')

    // 프로바이더는 회사별로 묶이고(OpenAI 는 CLI/API 가 한 상자), 줄마다 부제가 붙는다
    expect(wrapper.findAll('.wpc-subhead-prov').map((n) => n.text())).toEqual(['Claude · CLI', 'Openai'])
    expect(check(wrapper, 'GPT (api)').find('.wpc-check-sub').text()).toBe('openai · API')
  })

  it('never asks for counts, per-type providers or a title — the mockup defers those to the table', async () => {
    const wrapper = mountDialog()
    await flushPromises()
    await check(wrapper, 'DS').trigger('click')
    await check(wrapper, 'Claude Opus').trigger('click')

    expect(wrapper.find('.wpc-type-qty').exists()).toBe(false)
    expect(wrapper.find('.wpc-assignment-row').exists()).toBe(false)
    expect(wrapper.find('.wpc-title-field').exists()).toBe(false)
    expect(wrapper.findAll('select')).toHaveLength(0)
    expect(wrapper.find('.wpc-intro').text()).toContain('문서를 연 뒤 표에서 정합니다')
  })

  it('submits every countable type, using zero for unchecked types', async () => {
    postRequest.mockResolvedValue({
      data: { ok: true, doc_id: 'flowgate.default.0402.0002-WP', title: '0402 작업계획', body: { wp_version: 1 } },
    })
    const wrapper = mountDialog()
    await flushPromises()

    await check(wrapper, 'DS').trigger('click')
    await check(wrapper, '작업').trigger('click')
    await check(wrapper, 'Claude Opus').trigger('click')

    await createButton(wrapper).trigger('click')
    await flushPromises()

    expect(postRequest).toHaveBeenCalledWith('/api/v1/documents/work-plan', {
      parent_doc_id: 'flowgate.default.0402.0001-R',
      title: '작업계획 — 설계 1종 · 작업 1종',
      counted_types: ['DS', 'D', 'T'],
      provider_candidates: ['aip_opus'],
      quantities: { DS: 1, D: 0, T: 1 },
      defaults: { provider_id: null, note: '' },
      type_providers: {},
    })
    const emitted = wrapper.emitted('created')
    expect(emitted?.[0][0]).toMatchObject({ docId: 'flowgate.default.0402.0002-WP', title: '0402 작업계획' })
    expect(wrapper.emitted('update:visible')?.[0]).toEqual([false])
  })

  it('previews what will be created, and drops the preview for the block reason when nothing is checked', async () => {
    const wrapper = mountDialog()
    await flushPromises()
    expect(wrapper.find('.wpc-preview').text()).toContain('수량을 확인할 타입을 하나 이상 체크해 주세요.')

    await check(wrapper, 'DS').trigger('click')
    await check(wrapper, 'Claude Opus').trigger('click')
    const preview = wrapper.find('.wpc-preview').text()
    expect(preview).toContain('설계 1개')
    expect(preview).toContain('Claude Opus')
    expect(wrapper.find('.wpc-usage-note').text()).toContain('작업계획은 기록·제안용입니다.')
  })

  it('rejects a server-side empty-selection response without crashing (P0009 §4.3 defense-in-depth)', async () => {
    postRequest.mockRejectedValue({
      response: { status: 422, data: { code: 'wp_validation_failed', message: '작업계획을 만들지 못했습니다. 2개 항목이 규칙에 맞지 않습니다.' } },
    })
    const wrapper = mountDialog()
    await flushPromises()
    await check(wrapper, 'DS').trigger('click')
    await check(wrapper, 'Claude Opus').trigger('click')

    await createButton(wrapper).trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('작업계획을 만들지 못했습니다')
    expect(wrapper.emitted('created')).toBeUndefined()
  })
})
