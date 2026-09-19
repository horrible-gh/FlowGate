// flowgate.default.0576 T0004 (NR0003) — work plan download/upload round trip.
// Covers the completion criteria: download ships the server's canonical body (not the
// screen's rawJson), upload reuses the existing PUT + validator, and a failed upload
// (parse error / 422 / 409) never touches the plan already on screen.
import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@shared/i18n'
import WorkPlanEditor from '@main/components/WorkPlanEditor.vue'
import { useProjectStore } from '@main/stores/project'

const { getRequest, postRequest, putRequest, showToast } = vi.hoisted(() => ({
  getRequest: vi.fn(),
  postRequest: vi.fn(),
  putRequest: vi.fn(),
  showToast: vi.fn(),
}))

vi.mock('@shared/api', () => ({
  default: { head: vi.fn(), get: vi.fn(), post: vi.fn(), patch: vi.fn(), put: vi.fn() },
  getRequest,
  postRequest,
  putRequest,
  patchRequest: vi.fn(),
}))

vi.mock('@main/components/common/useToast', () => ({
  useToast: () => ({ showToast }),
}))

const TYPES = [
  { code: 'D', label: '기본설계', category: 'design', countable: true, unit: 'sheet', sort_order: 1 },
  { code: 'T', label: '작업지시', category: 'instruction', countable: true, unit: 'set', pair_code: 'TR', sort_order: 2 },
  { code: 'TR', label: '작업레포트', category: 'work', countable: false },
  { code: 'TSR', label: '테스트레포트', category: 'work', countable: false },
]
const TYPES_WP = [
  { code: 'D', label: '기본설계', category: 'design', unit: 'sheet' },
  { code: 'T', label: '작업지시', category: 'instruction', unit: 'set', pair_code: 'TR' },
]

const REGISTERED_PROVIDERS = [
  { id: 'aip_opus', name: 'Claude Opus', group_label: 'Claude · CLI' },
]

const DOC_ID = 'flowgate.default.0576.0002-WP'

function planBody() {
  return {
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
}

function readResponse(body: ReturnType<typeof planBody>) {
  return {
    ok: true,
    doc_id: DOC_ID,
    doc_type: 'WP',
    title: '0576 작업계획',
    group_id: 'flowgate.default.0576',
    parent_doc_id: 'flowgate.default.0576.0001-R',
    status: 'open',
    doc_review_status: 'pending_review',
    editable: true,
    edit_locked_reason: null,
    revision_no: 3,
    stored_path: 'documents/flowgate/main/default/0576/0002-WP_document.json',
    origin: 'human',
    created_by: 'sjm',
    updated_by: 'sjm',
    updated_at: '2026-09-19T10:00:00+09:00',
    body,
    registered_providers: REGISTERED_PROVIDERS,
    provider_status: [],
    assignment_summary: [{ provider_id: 'aip_opus', display_name: 'Claude Opus', step_count: 1 }],
    unassigned_step_count: 2,
    totals: { design_sheets: 1, work_sets: 1, steps: 3 },
    limits: { note_max_chars: 1000 },
    last_application: null,
  }
}

function routeGet(body: ReturnType<typeof planBody> = planBody()) {
  getRequest.mockImplementation((url: string) => {
    if (url.includes('/document-types')) return Promise.resolve({
      data: { data: TYPES, work_plan_countable_types: TYPES_WP },
    })
    if (url.includes('/ai-invoke/providers')) return Promise.resolve({
      data: { providers: structuredClone(REGISTERED_PROVIDERS), default_provider_id: 'aip_opus' },
    })
    if (url.includes('/work-plan')) return Promise.resolve({ data: structuredClone(readResponse(body)) })
    return Promise.reject(new Error(`unexpected url: ${url}`))
  })
}

function mountEditor() {
  return mount(WorkPlanEditor, {
    props: { docId: DOC_ID, projectId: 'flowgate' },
    global: { plugins: [i18n], stubs: { AppIcon: true, teleport: true } },
  })
}

function actionButton(wrapper: ReturnType<typeof mountEditor>, label: string) {
  return wrapper.findAll('.card-hd .card-actions button')
    .find((button) => button.text().includes(label))!
}

async function flushAll(times = 6) {
  for (let i = 0; i < times; i++) await flushPromises()
}

class FakeBlob {
  parts: unknown[]
  type?: string
  constructor(parts: unknown[], options?: { type?: string }) {
    this.parts = parts
    this.type = options?.type
  }
}

beforeEach(() => {
  setActivePinia(createPinia())
  i18n.global.locale.value = 'ko'
  useProjectStore().currentProjectId = 'flowgate'
  getRequest.mockReset()
  postRequest.mockReset()
  putRequest.mockReset()
  showToast.mockReset()
  routeGet()
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.restoreAllMocks()
})

describe('WorkPlanEditor — download', () => {
  it('다운로드는 화면이 보정한 값이 아니라 서버가 다시 읽은 canonical body를 그대로 파일로 만든다', async () => {
    const bodyWithExtension = { ...planBody(), x_experiment: { note: 'kept top-level' } }
    routeGet(bodyWithExtension as any)

    const createdParts: unknown[] = []
    vi.stubGlobal('Blob', class extends FakeBlob {
      constructor(parts: unknown[], options?: { type?: string }) {
        super(parts, options)
        createdParts.push(parts[0])
      }
    } as any)
    const createObjectURL = vi.fn().mockReturnValue('blob:work-plan')
    const revokeObjectURL = vi.fn()
    vi.stubGlobal('URL', { createObjectURL, revokeObjectURL })
    const realCreateElement = document.createElement.bind(document)
    const anchor = realCreateElement('a')
    vi.spyOn(document, 'createElement').mockImplementation((tag: string) => (tag === 'a' ? anchor : realCreateElement(tag)))

    const wrapper = mountEditor()
    await flushPromises()
    const getCallsBeforeDownload = getRequest.mock.calls.length

    await actionButton(wrapper, '다운로드').trigger('click')
    await flushPromises()

    // A fresh GET, not a re-serialization of the already-loaded (and type-registry-padded) plan.
    expect(getRequest.mock.calls.length).toBe(getCallsBeforeDownload + 1)
    expect(createdParts[0]).toBe(`${JSON.stringify(bodyWithExtension, null, 2)}\n`)
    expect(anchor.download).toBe(`${DOC_ID}.work-plan.json`)
    expect(createObjectURL).toHaveBeenCalled()
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:work-plan')
  })

  it('다운로드한 JSON은 그대로 다시 업로드할 수 있는 최상위 필드 구조를 유지한다', async () => {
    const body = planBody()
    routeGet(body)
    const createdParts: unknown[] = []
    vi.stubGlobal('Blob', class extends FakeBlob {
      constructor(parts: unknown[], options?: { type?: string }) {
        super(parts, options)
        createdParts.push(parts[0])
      }
    } as any)
    vi.stubGlobal('URL', { createObjectURL: vi.fn().mockReturnValue('blob:x'), revokeObjectURL: vi.fn() })
    const realCreateElement = document.createElement.bind(document)
    const anchor = realCreateElement('a')
    vi.spyOn(document, 'createElement').mockImplementation((tag: string) => (tag === 'a' ? anchor : realCreateElement(tag)))

    const wrapper = mountEditor()
    await flushPromises()
    await actionButton(wrapper, '다운로드').trigger('click')
    await flushPromises()

    const downloaded = JSON.parse(createdParts[0] as string)
    expect(downloaded).toEqual(body)

    putRequest.mockResolvedValue({
      data: { revision_no: 4, totals: { design_sheets: 1, work_sets: 1, steps: 3 }, assignment_summary: [], unassigned_step_count: 2 },
    })
    const file = new File([JSON.stringify(downloaded)], 'plan.json', { type: 'application/json' })
    const input = wrapper.find('input[type="file"]')
    Object.defineProperty(input.element, 'files', { value: [file], configurable: true })
    await input.trigger('change')
    await flushAll()

    expect(putRequest).toHaveBeenCalledTimes(1)
    expect(putRequest.mock.calls[0][1]).toEqual({ base_revision_no: 3, body: downloaded })
  })

  it('로딩 중 · 편집 중(dirty)에는 다운로드/업로드 버튼이 비활성화된다', async () => {
    const wrapper = mountEditor()
    await flushPromises()
    const plusButtons = wrapper.findAll('.wp-stepper-btn').filter((button) => button.text() === '+')
    await plusButtons[0].trigger('click')
    await flushPromises()

    expect(actionButton(wrapper, '다운로드').attributes('disabled')).toBeDefined()
    expect(actionButton(wrapper, '업로드').attributes('disabled')).toBeDefined()
  })
})

describe('WorkPlanEditor — upload', () => {
  it('정상 JSON 업로드는 기존 저장 API로 검증·저장되고, 성공 후 서버 정본을 다시 읽는다', async () => {
    const uploadedBody = { ...planBody(), defaults: { provider_id: 'aip_opus', note: 'from file' } }
    putRequest.mockResolvedValue({
      data: { revision_no: 4, totals: { design_sheets: 1, work_sets: 1, steps: 3 }, assignment_summary: [], unassigned_step_count: 2 },
    })
    const wrapper = mountEditor()
    await flushPromises()
    const getCallsBeforeUpload = getRequest.mock.calls.filter(([url]) => String(url).includes('/work-plan')).length

    const file = new File([JSON.stringify(uploadedBody)], 'plan.json', { type: 'application/json' })
    const input = wrapper.find('input[type="file"]')
    Object.defineProperty(input.element, 'files', { value: [file], configurable: true })
    await input.trigger('change')
    await flushAll()

    expect(putRequest).toHaveBeenCalledTimes(1)
    expect(putRequest.mock.calls[0][1]).toEqual({ base_revision_no: 3, body: uploadedBody })
    const getCallsAfterUpload = getRequest.mock.calls.filter(([url]) => String(url).includes('/work-plan')).length
    expect(getCallsAfterUpload).toBe(getCallsBeforeUpload + 1)
    expect(showToast).toHaveBeenCalledWith('작업계획을 업로드했습니다.', 'success')
    // Re-selecting the same file must work again — the input value is reset after read.
    expect((input.element as HTMLInputElement).value).toBe('')
  })

  it('UTF-8 BOM이 붙은 JSON도 정상 업로드된다', async () => {
    const uploadedBody = planBody()
    putRequest.mockResolvedValue({
      data: { revision_no: 4, totals: { design_sheets: 1, work_sets: 1, steps: 3 }, assignment_summary: [], unassigned_step_count: 2 },
    })
    const wrapper = mountEditor()
    await flushPromises()

    const file = new File(['﻿' + JSON.stringify(uploadedBody)], 'plan.json', { type: 'application/json' })
    const input = wrapper.find('input[type="file"]')
    Object.defineProperty(input.element, 'files', { value: [file], configurable: true })
    await input.trigger('change')
    await flushAll()

    expect(putRequest).toHaveBeenCalledTimes(1)
    expect(putRequest.mock.calls[0][1].body).toEqual(uploadedBody)
  })

  it('JSON 문법 오류가 있는 파일은 PUT을 호출하지 않고 기존 작업계획을 그대로 둔다', async () => {
    const wrapper = mountEditor()
    await flushPromises()
    const beforeSteps = wrapper.findAll('.wp-step-row').length

    const file = new File(['{ not valid json'], 'plan.json', { type: 'application/json' })
    const input = wrapper.find('input[type="file"]')
    Object.defineProperty(input.element, 'files', { value: [file], configurable: true })
    await input.trigger('change')
    await flushAll()

    expect(putRequest).not.toHaveBeenCalled()
    expect(wrapper.findAll('.wp-step-row').length).toBe(beforeSteps)
    expect(showToast).toHaveBeenCalledWith('올바른 JSON 파일이 아닙니다.', 'danger')
  })

  it('최상위가 배열이거나 객체가 아닌 JSON은 적용하지 않는다', async () => {
    const wrapper = mountEditor()
    await flushPromises()

    const file = new File(['[1, 2, 3]'], 'plan.json', { type: 'application/json' })
    const input = wrapper.find('input[type="file"]')
    Object.defineProperty(input.element, 'files', { value: [file], configurable: true })
    await input.trigger('change')
    await flushAll()

    expect(putRequest).not.toHaveBeenCalled()
    expect(showToast).toHaveBeenCalledWith('올바른 JSON 파일이 아닙니다.', 'danger')
  })

  it('서버 검증 실패(422)면 기존 작업계획이 화면에 그대로 남는다', async () => {
    putRequest.mockRejectedValueOnce({
      response: {
        status: 422,
        data: {
          message: '작업계획을 저장하지 못했습니다. 1개 항목이 규칙에 맞지 않습니다.',
          errors: [{ loc: 'wp_version', key: null, code: 'unsupported_version', msg: '지원하지 않는 버전입니다.' }],
        },
      },
    })
    const wrapper = mountEditor()
    await flushPromises()
    const beforeNote = wrapper.get('.wp-step-row .wp-step-msg').element as HTMLInputElement
    const beforeValue = beforeNote.value

    const badBody = { ...planBody(), wp_version: 999 }
    const file = new File([JSON.stringify(badBody)], 'plan.json', { type: 'application/json' })
    const input = wrapper.find('input[type="file"]')
    Object.defineProperty(input.element, 'files', { value: [file], configurable: true })
    await input.trigger('change')
    await flushAll()

    expect(putRequest).toHaveBeenCalledTimes(1)
    expect(wrapper.get('.wp-error-banner').text()).toContain('지원하지 않는 버전입니다.')
    const afterNote = wrapper.get('.wp-step-row .wp-step-msg').element as HTMLInputElement
    expect(afterNote.value).toBe(beforeValue)
    // The failed upload must not be quietly treated as a success.
    expect(showToast).not.toHaveBeenCalledWith('작업계획을 업로드했습니다.', 'success')
  })

  it('리비전 충돌(409)이면 기존 작업계획을 덮어쓰지 않고 충돌 안내를 띄운다', async () => {
    putRequest.mockRejectedValueOnce({
      response: {
        status: 409,
        data: { code: 'wp_revision_conflict', updated_by: 'other-user', updated_at: '2026-09-19T11:00:00+09:00' },
      },
    })
    const wrapper = mountEditor()
    await flushPromises()

    const file = new File([JSON.stringify(planBody())], 'plan.json', { type: 'application/json' })
    const input = wrapper.find('input[type="file"]')
    Object.defineProperty(input.element, 'files', { value: [file], configurable: true })
    await input.trigger('change')
    await flushAll()

    expect(putRequest).toHaveBeenCalledTimes(1)
    expect(wrapper.get('.wp-conflict-banner').text()).toContain('other-user')
    expect(showToast).not.toHaveBeenCalledWith('작업계획을 업로드했습니다.', 'success')
  })

  it('업로드 PUT은 성공했지만 canonical 재조회(GET)가 실패하면 업로드 성공으로 표시하지 않는다', async () => {
    const uploadedBody = { ...planBody(), defaults: { provider_id: 'aip_opus', note: 'from file' } }
    putRequest.mockResolvedValue({
      data: { revision_no: 4, totals: { design_sheets: 1, work_sets: 1, steps: 3 }, assignment_summary: [], unassigned_step_count: 2 },
    })
    const wrapper = mountEditor()
    await flushPromises()
    const beforeNote = wrapper.get('.wp-step-row .wp-step-msg').element as HTMLInputElement
    const beforeValue = beforeNote.value

    getRequest.mockImplementation((url: string) => {
      if (url.includes('/document-types')) return Promise.resolve({
        data: { data: TYPES, work_plan_countable_types: TYPES_WP },
      })
      if (url.includes('/ai-invoke/providers')) return Promise.resolve({
        data: { providers: structuredClone(REGISTERED_PROVIDERS), default_provider_id: 'aip_opus' },
      })
      if (url.includes('/work-plan')) return Promise.reject({ response: { status: 500, data: { message: '조회 실패' } } })
      return Promise.reject(new Error(`unexpected url: ${url}`))
    })

    const file = new File([JSON.stringify(uploadedBody)], 'plan.json', { type: 'application/json' })
    const input = wrapper.find('input[type="file"]')
    Object.defineProperty(input.element, 'files', { value: [file], configurable: true })
    await input.trigger('change')
    await flushAll()

    // The PUT already saved the new plan server-side — only the follow-up canonical GET failed.
    expect(putRequest).toHaveBeenCalledTimes(1)
    expect(showToast).not.toHaveBeenCalledWith('작업계획을 업로드했습니다.', 'success')
    expect(showToast).toHaveBeenCalledWith('조회 실패', 'danger')
    // Since the canonical refetch never landed, the screen keeps the last confirmed plan
    // instead of quietly treating the failed reload as a successful refresh.
    const afterNote = wrapper.get('.wp-step-row .wp-step-msg').element as HTMLInputElement
    expect(afterNote.value).toBe(beforeValue)

    // revisionNo already moved to the new (uploaded) revision even though plan.value is still
    // the pre-upload body — a save from here would carry the new revision over stale content
    // and silently overwrite the just-uploaded plan. Editing/saving must stay blocked, and a
    // reload banner with its own [reload] button must ask for an explicit reload instead.
    expect(actionButton(wrapper, '저장').attributes('disabled')).toBeDefined()
    expect(actionButton(wrapper, '업로드').attributes('disabled')).toBeDefined()
    const banner = wrapper.get('.wp-conflict-banner')
    expect(banner.text()).toContain('다시 읽')

    // A subsequent successful reload confirms the canonical body and lifts the block.
    routeGet(uploadedBody)
    await banner.get('button').trigger('click')
    await flushAll()

    expect(actionButton(wrapper, '저장').attributes('disabled')).toBeUndefined()
    expect(wrapper.find('.wp-conflict-banner').exists()).toBe(false)
  })

  it('편집 잠금(isLocked) 상태에서는 업로드 버튼이 비활성화된다', async () => {
    const wrapper = mount(WorkPlanEditor, {
      props: { docId: DOC_ID, projectId: 'flowgate', readOnly: true },
      global: { plugins: [i18n], stubs: { AppIcon: true, teleport: true } },
    })
    await flushPromises()

    expect(actionButton(wrapper, '업로드').attributes('disabled')).toBeDefined()
  })
})

// flowgate.default.0576 TR0005 rev4 — PUT /work-plan can refuse to apply ANY body (upload or
// manual save) with 422 provider_capability_confirmation_required when a T/TR step's provider
// cannot modify source or run tests, or is not registered in the target project at all (an
// import from another project's file, or a provider id the project never onboarded). The
// findings must never be auto-approved; only an explicit [확인하고 저장] click may resend them
// as capability_warning_acks.
describe('WorkPlanEditor — upload provider capability gate', () => {
  const CAPABILITY_FINDINGS = [
    {
      step_key: 'T#1',
      step_type: 'T',
      provider_id: 'aip_no_source',
      provider_name: 'ReadOnly Reviewer',
      missing_capabilities: ['source_modification', 'test_execution'],
    },
  ]

  function capabilityRejection() {
    return {
      response: {
        status: 422,
        data: {
          code: 'provider_capability_confirmation_required',
          message: 'Confirm providers that cannot modify source or run tests.',
          findings: CAPABILITY_FINDINGS,
        },
      },
    }
  }

  it('실제 T/TR 배정 계획을 업로드하면 경고 findings를 보여주고 적용하지 않는다', async () => {
    putRequest.mockRejectedValueOnce(capabilityRejection())
    const uploadedBody = { ...planBody(), defaults: { provider_id: 'aip_no_source', note: 'from file' } }
    const wrapper = mountEditor()
    await flushPromises()
    const beforeNote = wrapper.get('.wp-step-row .wp-step-msg').element as HTMLInputElement
    const beforeValue = beforeNote.value

    const file = new File([JSON.stringify(uploadedBody)], 'plan.json', { type: 'application/json' })
    const input = wrapper.find('input[type="file"]')
    Object.defineProperty(input.element, 'files', { value: [file], configurable: true })
    await input.trigger('change')
    await flushAll()

    expect(putRequest).toHaveBeenCalledTimes(1)
    expect(putRequest.mock.calls[0][1]).toEqual({ base_revision_no: 3, body: uploadedBody })
    const banner = wrapper.get('[data-test="capability-warning-banner"]')
    expect(banner.text()).toContain('T#1')
    expect(banner.text()).toContain('ReadOnly Reviewer')
    expect(banner.text()).toContain('source_modification')
    // Not applied: the on-screen plan and revision stay exactly where they were.
    const afterNote = wrapper.get('.wp-step-row .wp-step-msg').element as HTMLInputElement
    expect(afterNote.value).toBe(beforeValue)
    expect(showToast).not.toHaveBeenCalledWith('작업계획을 업로드했습니다.', 'success')
    // A pending decision blocks the other write actions until resolved.
    expect(actionButton(wrapper, '저장').attributes('disabled')).toBeDefined()
    expect(actionButton(wrapper, '업로드').attributes('disabled')).toBeDefined()
  })

  it('경고를 명시적으로 확인하면 capability_warning_acks와 함께 재요청되어 저장된다', async () => {
    putRequest.mockRejectedValueOnce(capabilityRejection())
    const uploadedBody = { ...planBody(), defaults: { provider_id: 'aip_no_source', note: 'from file' } }
    const wrapper = mountEditor()
    await flushPromises()

    const file = new File([JSON.stringify(uploadedBody)], 'plan.json', { type: 'application/json' })
    const input = wrapper.find('input[type="file"]')
    Object.defineProperty(input.element, 'files', { value: [file], configurable: true })
    await input.trigger('change')
    await flushAll()

    putRequest.mockResolvedValueOnce({
      data: { revision_no: 4, totals: { design_sheets: 1, work_sets: 1, steps: 3 }, assignment_summary: [], unassigned_step_count: 2 },
    })
    routeGet(uploadedBody)
    await wrapper.get('[data-test="capability-warning-banner"] .btn-warning').trigger('click')
    await flushAll()

    expect(putRequest).toHaveBeenCalledTimes(2)
    expect(putRequest.mock.calls[1][1]).toEqual({
      base_revision_no: 3,
      body: uploadedBody,
      capability_warning_acks: ['T#1'],
    })
    expect(wrapper.find('[data-test="capability-warning-banner"]').exists()).toBe(false)
    expect(showToast).toHaveBeenCalledWith('작업계획을 업로드했습니다.', 'success')
    expect(actionButton(wrapper, '저장').attributes('disabled')).toBeUndefined()
  })

  it('경고를 취소하면 재요청 없이 화면이 원래 계획으로 남는다', async () => {
    putRequest.mockRejectedValueOnce(capabilityRejection())
    const uploadedBody = { ...planBody(), defaults: { provider_id: 'aip_no_source', note: 'from file' } }
    const wrapper = mountEditor()
    await flushPromises()

    const file = new File([JSON.stringify(uploadedBody)], 'plan.json', { type: 'application/json' })
    const input = wrapper.find('input[type="file"]')
    Object.defineProperty(input.element, 'files', { value: [file], configurable: true })
    await input.trigger('change')
    await flushAll()

    await wrapper.get('[data-test="capability-warning-banner"] .btn-outline').trigger('click')
    await flushAll()

    expect(putRequest).toHaveBeenCalledTimes(1)
    expect(wrapper.find('[data-test="capability-warning-banner"]').exists()).toBe(false)
    expect(actionButton(wrapper, '업로드').attributes('disabled')).toBeUndefined()
  })

  it('다른 프로젝트에서 만들어졌거나 이 프로젝트에 등록되지 않은 provider를 가진 계획을 import하면 경고가 표시된다', async () => {
    const unregisteredFindings = [
      {
        step_key: 'TR#1',
        step_type: 'TR',
        provider_id: 'aip_other_project_only',
        provider_name: 'aip_other_project_only',
        missing_capabilities: ['source_modification', 'test_execution'],
      },
    ]
    putRequest.mockRejectedValueOnce({
      response: {
        status: 422,
        data: {
          code: 'provider_capability_confirmation_required',
          message: 'Confirm providers that cannot modify source or run tests.',
          findings: unregisteredFindings,
        },
      },
    })
    const importedBody = {
      ...planBody(),
      steps: planBody().steps.map((step) =>
        step.key === 'TR#1' ? { ...step, provider_id: 'aip_other_project_only', provider_display_name: 'aip_other_project_only' } : step,
      ),
    }
    const wrapper = mountEditor()
    await flushPromises()

    const file = new File([JSON.stringify(importedBody)], 'plan.json', { type: 'application/json' })
    const input = wrapper.find('input[type="file"]')
    Object.defineProperty(input.element, 'files', { value: [file], configurable: true })
    await input.trigger('change')
    await flushAll()

    expect(putRequest).toHaveBeenCalledTimes(1)
    const banner = wrapper.get('[data-test="capability-warning-banner"]')
    expect(banner.text()).toContain('TR#1')
    expect(banner.text()).toContain('aip_other_project_only')
    expect(showToast).not.toHaveBeenCalledWith('작업계획을 업로드했습니다.', 'success')
  })

  it('수동 저장에서도 동일한 capability 게이트가 적용된다', async () => {
    putRequest.mockRejectedValueOnce(capabilityRejection())
    const wrapper = mountEditor()
    await flushPromises()
    const plusButtons = wrapper.findAll('.wp-stepper-btn').filter((button) => button.text() === '+')
    await plusButtons[0].trigger('click')
    await flushPromises()

    await actionButton(wrapper, '저장').trigger('click')
    await flushAll()

    expect(putRequest).toHaveBeenCalledTimes(1)
    expect(putRequest.mock.calls[0][1]).not.toHaveProperty('capability_warning_acks')
    expect(wrapper.find('[data-test="capability-warning-banner"]').exists()).toBe(true)
    expect(showToast).not.toHaveBeenCalledWith('저장했습니다.', 'success')
  })
})
