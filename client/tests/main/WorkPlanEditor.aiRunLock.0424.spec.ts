// flowgate.default.0424 TR0005 rev2 — B0001 재반려("AI실행중에 버튼들이 안눌리게 하던가
// 없애야지 토스트 띄우면 다인가?") 대응.
//
// 반려자가 실제로 있던 화면은 WP(작업계획) 문서다. dev 8080 의 ai_invoke_runs 기록상 두 번의
// 반려 직전 실행은 모두 doc_ref 가 *-WP 인 work_plan_fill 실행이었고(18:34~18:35 test.test.0009,
// 18:59 test.test.0005), 그 실행을 시작하는 단추가 이 편집기의 [AI 제안 불러오기]다. 문서 열의
// 다른 카드는 모두 aiRunDocumentLocked 를 받는데 이 편집기만 아무 잠금도 받지 않아서, 실행 중에도
// 저장·수량·공급자·멘트·모두 적용이 그대로 눌렸고 서버 423 뒤 토스트만 떴다.
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import WorkPlanEditor from '@main/components/WorkPlanEditor.vue'
import { useAiInvokeRunsStore } from '@main/stores/aiInvokeRuns'
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

const DOC_ID = 'flowgate.default.0424.0002-WP'
const GROUP = 'flowgate.default.0424'

const TYPES = [
  { code: 'D', label: '기본설계', category: 'design', countable: true, unit: 'sheet', sort_order: 1 },
  { code: 'T', label: '작업지시', category: 'instruction', countable: true, unit: 'set', pair_code: 'TR', sort_order: 2 },
  { code: 'TR', label: '작업레포트', category: 'work', countable: false, sort_order: 3 },
]

const REGISTERED_PROVIDERS = [
  { id: 'aip_opus', name: 'Claude Opus', group_label: 'Claude · CLI' },
  { id: 'aip_sonnet', name: 'Claude Sonnet', group_label: 'Claude · CLI' },
]

const READ_RESPONSE = {
  ok: true,
  doc_id: DOC_ID,
  doc_type: 'WP',
  title: '0424 작업계획',
  group_id: GROUP,
  parent_doc_id: `${GROUP}.0001-R`,
  status: 'open',
  doc_review_status: 'pending_review',
  revision_no: 2,
  origin: 'human',
  body: {
    wp_version: 1,
    binding: 'advisory',
    counted_types: ['D', 'T'],
    quantities: { D: { unit: 'sheet', count: 1 }, T: { unit: 'set', count: 1 } },
    provider_candidates: [{ provider_id: 'aip_opus', display_name: 'Claude Opus', group_label: 'Claude · CLI' }],
    defaults: { provider_id: null, note: '' },
    steps: [
      { key: 'D#1', type: 'D', ordinal: 1, pair_key: null, pair_role: 'single', provider_id: 'aip_opus', provider_display_name: 'Claude Opus', note: '설계', locked: false, locked_reason: null, origin: 'human' },
      { key: 'T#1', type: 'T', ordinal: 1, pair_key: 'TR#1', pair_role: 'instruction', provider_id: null, provider_display_name: null, note: null, locked: false, locked_reason: null, origin: 'human' },
      { key: 'TR#1', type: 'TR', ordinal: 1, pair_key: 'T#1', pair_role: 'result', provider_id: null, provider_display_name: null, note: null, locked: false, locked_reason: null, origin: 'human' },
    ],
  },
  registered_providers: REGISTERED_PROVIDERS,
  provider_status: [],
  assignment_summary: [{ provider_id: 'aip_opus', display_name: 'Claude Opus', step_count: 1 }],
  unassigned_step_count: 2,
  totals: { design_sheets: 1, work_sets: 1, steps: 3 },
  last_application: null,
  editable: true,
  edit_locked_reason: null,
}

function mountEditor(props: Record<string, unknown> = {}) {
  return mount(WorkPlanEditor, {
    props: { docId: DOC_ID, projectId: 'flowgate', ...props },
    global: { plugins: [i18n], stubs: { AppIcon: true, teleport: true } },
  })
}

function startRun(): void {
  useAiInvokeRunsStore().trackStarted({
    run_id: 'aiv_20260816_000004',
    group_id: GROUP,
    doc_ref: DOC_ID,
    status: 'running',
    mode: 'single',
  })
}

/** 편집을 일으키는 컨트롤 전부. 하나라도 살아 있으면 그것이 반려 지점이다. */
function writeControls(wrapper: ReturnType<typeof mountEditor>) {
  return {
    save: wrapper.findAll('button').filter((b) => b.text().includes('저장')),
    aiSuggest: wrapper.findAll('button').filter((b) => b.text().includes('AI 제안 불러오기')),
    steppers: wrapper.findAll('.wp-stepper-btn'),
    applyAll: wrapper.findAll('button').filter((b) => b.text().includes('모든 단계에 적용')),
    defaultsNote: wrapper.find('.wp-defaults-note'),
    stepNotes: wrapper.findAll('.wp-step-msg'),
    providerSelects: wrapper.findAll('select'),
  }
}

beforeEach(() => {
  setActivePinia(createPinia())
  i18n.global.locale.value = 'ko'
  useProjectStore().currentProjectId = 'flowgate'
  getRequest.mockReset()
  postRequest.mockReset()
  putRequest.mockReset()
  getRequest.mockImplementation((url: string) => {
    if (url.includes('/document-types')) return Promise.resolve({ data: { data: TYPES } })
    if (url.includes('/ai-invoke/leases')) return Promise.resolve({ data: { items: [] } })
    if (url.includes('/ai-invoke/providers')) {
      return Promise.resolve({ data: { providers: structuredClone(REGISTERED_PROVIDERS), default_provider_id: 'aip_opus' } })
    }
    if (url.includes('/work-plan')) return Promise.resolve({ data: structuredClone(READ_RESPONSE) })
    return Promise.reject(new Error(`unexpected url: ${url}`))
  })
})

describe('WorkPlanEditor AI-run lock (0424 B0001 rev2)', () => {
  it('대조군: 실행이 없으면 편집 컨트롤이 모두 살아 있다', async () => {
    const wrapper = mountEditor()
    await flushPromises()

    const c = writeControls(wrapper)
    expect(c.save.length).toBeGreaterThan(0)
    expect(c.save.every((b) => b.attributes('disabled') === undefined)).toBe(true)
    expect(c.aiSuggest[0].attributes('disabled')).toBeUndefined()
    expect(c.steppers.every((b) => b.attributes('disabled') === undefined)).toBe(true)
    expect(c.applyAll[0].attributes('disabled')).toBeUndefined()
    expect(c.defaultsNote.attributes('disabled')).toBeUndefined()
    expect(wrapper.find('.wp-locked-hint').exists()).toBe(false)
  })

  it('AI 실행 중에는 저장·AI 제안·수량·모두 적용·멘트·공급자가 전부 눌리지 않는다', async () => {
    const wrapper = mountEditor()
    await flushPromises()
    startRun()
    await flushPromises()

    const c = writeControls(wrapper)
    expect(c.save.length).toBeGreaterThan(0)
    for (const button of c.save) expect(button.attributes('disabled')).toBeDefined()
    expect(c.aiSuggest[0].attributes('disabled')).toBeDefined()
    expect(c.steppers.length).toBeGreaterThan(0)
    for (const button of c.steppers) expect(button.attributes('disabled')).toBeDefined()
    expect(c.applyAll[0].attributes('disabled')).toBeDefined()
    expect(c.defaultsNote.attributes('disabled')).toBeDefined()
    expect(c.stepNotes.length).toBeGreaterThan(0)
    for (const input of c.stepNotes) expect(input.attributes('disabled')).toBeDefined()
    expect(c.providerSelects.length).toBeGreaterThan(0)
    for (const select of c.providerSelects) expect(select.attributes('disabled')).toBeDefined()
  })

  it('왜 안 눌리는지 화면에 적는다 — 잠금 안내가 AI 실행 문구로 바뀐다', async () => {
    const wrapper = mountEditor()
    await flushPromises()
    startRun()
    await flushPromises()

    const hint = wrapper.get('.wp-locked-hint')
    expect(hint.text()).toBe(i18n.global.t('main.review_action_bar.ai_running_hint'))
  })

  it('실행 중 저장을 눌러도 PUT 이 나가지 않는다 (토스트로 때우지 않는다)', async () => {
    const wrapper = mountEditor()
    await flushPromises()
    startRun()
    await flushPromises()

    const save = wrapper.findAll('button').filter((b) => b.text().includes('저장'))[0]
    await save.trigger('click')
    await flushPromises()
    expect(putRequest).not.toHaveBeenCalled()

    // 스테퍼와 모두 적용도 마찬가지로 값이 움직이지 않는다.
    const before = wrapper.get('.wp-qty-value').text()
    await wrapper.findAll('.wp-stepper-btn')[1].trigger('click')
    await flushPromises()
    expect(wrapper.get('.wp-qty-value').text()).toBe(before)
  })

  it('실행 중에는 AI 제안 창을 열 수 없고, 열려 있던 창은 닫힌다', async () => {
    const wrapper = mountEditor()
    await flushPromises()

    const vm = wrapper.vm as unknown as { aiScopeOpen: boolean }
    vm.aiScopeOpen = true
    await flushPromises()
    expect(vm.aiScopeOpen).toBe(true)

    startRun()
    await flushPromises()
    expect(vm.aiScopeOpen).toBe(false)

    const aiButton = wrapper.findAll('button').filter((b) => b.text().includes('AI 제안 불러오기'))[0]
    await aiButton.trigger('click')
    await flushPromises()
    expect(vm.aiScopeOpen).toBe(false)
    expect(postRequest).not.toHaveBeenCalled()
  })

  it('문서 화면이 넘겨주는 read-only 만으로도 잠긴다 (부트스트랩 중 fail-closed)', async () => {
    const wrapper = mountEditor({ readOnly: true })
    await flushPromises()

    const c = writeControls(wrapper)
    for (const button of c.save) expect(button.attributes('disabled')).toBeDefined()
    expect(c.aiSuggest[0].attributes('disabled')).toBeDefined()
    expect(c.defaultsNote.attributes('disabled')).toBeDefined()
  })

  it('실행이 끝나면 다시 열린다 — 잠금이 눌러앉지 않는다', async () => {
    const wrapper = mountEditor()
    await flushPromises()
    startRun()
    await flushPromises()
    expect(wrapper.findAll('button').filter((b) => b.text().includes('저장'))[0].attributes('disabled'))
      .toBeDefined()

    useAiInvokeRunsStore().trackFinished({
      run_id: 'aiv_20260816_000004',
      group_id: GROUP,
      doc_ref: DOC_ID,
      status: 'finished',
      outcome: 'complete',
      end_reason: 'exited',
    })
    await flushPromises()

    expect(wrapper.findAll('button').filter((b) => b.text().includes('저장'))[0].attributes('disabled'))
      .toBeUndefined()
  })
})
