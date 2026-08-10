// flowgate.default.0395 T0017 — connected create/edit/continuous-work acceptance flow.
//
// 0399 M0020: 이 흐름의 가운데에 있던 전면 [작업계획 적용] 미리보기 오버레이는
// 사라졌다 — 눌러만 놓으면 저장 없이 워크플로를 바꿔 버렸기 때문이다. 이 파일은
// 남은 두 끝(생성 → 편집 → 저장, 그리고 연속 작업 창의 프리셋 처리)을 그대로 지킨다.
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import WorkPlanCreateDialog from '@main/components/WorkPlanCreateDialog.vue'
import WorkPlanEditor from '@main/components/WorkPlanEditor.vue'
import ContinuousWorkDialog from '@main/components/ContinuousWorkDialog.vue'
import { useProjectStore } from '@main/stores/project'

const { getRequest, postRequest, putRequest } = vi.hoisted(() => ({
  getRequest: vi.fn(), postRequest: vi.fn(), putRequest: vi.fn(),
}))

vi.mock('@shared/api', () => ({
  default: { head: vi.fn(), get: vi.fn(), post: vi.fn(), patch: vi.fn(), put: vi.fn() },
  getRequest,
  postRequest,
  putRequest,
  patchRequest: vi.fn(),
}))

const DOC_ID = 'flowgate.default.0395.0091-WP'
const ROOT_ID = 'flowgate.default.0395.0001-R'
const TYPES = [
  { code: 'T', label: '작업지시', category: 'instruction', countable: true, unit: 'set', pair_code: 'TR', sort_order: 1 },
  { code: 'TR', label: '작업레포트', category: 'work', countable: false, unit: null, sort_order: 2 },
]
const PROVIDERS = [
  { id: 'aip_opus', name: 'Claude Opus', kind: 'claude', exec_type: 'cli' },
  { id: 'aip_fable', name: 'Fable', kind: 'openai', exec_type: 'cli' },
]
const PLAN_BODY = {
  wp_version: 1,
  binding: 'advisory',
  counted_types: ['T'],
  quantities: { T: { unit: 'set', count: 1 } },
  provider_candidates: [
    { provider_id: 'aip_opus', display_name: 'Claude Opus', group_label: 'Claude · CLI' },
  ],
  defaults: { provider_id: null, note: '' },
  steps: [
    { key: 'T#1', type: 'T', ordinal: 1, pair_key: 'TR#1', pair_role: 'instruction', provider_id: 'aip_opus', provider_display_name: 'Claude Opus', note: 'from plan', locked: false, locked_reason: null, origin: 'human' },
    { key: 'TR#1', type: 'TR', ordinal: 1, pair_key: 'T#1', pair_role: 'result', provider_id: null, provider_display_name: null, note: null, locked: false, locked_reason: null, origin: 'human' },
  ],
}
const WARNING_CODES = [
  'workflow_not_decided', 'steps_added', 'extra_workflow_steps',
  'steps_already_done', 'type_not_placeable', 'order_differs',
  'provider_unset', 'provider_not_registered', 'provider_renamed',
  'note_empty', 'nothing_to_fill', 'locked_step_has_value',
  'instructions_folded', 'wp_not_approved', 'unmatched_plan_steps',
]
const WARNINGS = WARNING_CODES.map((code) => ({
  code, severity: 'warning', count: 1, keys: [], item_seqs: [], message: `server-sentinel-${code}`,
}))
const READ_RESPONSE = {
  ok: true, doc_id: DOC_ID, doc_type: 'WP', title: '0395 작업계획',
  group_id: 'flowgate.default.0395', parent_doc_id: ROOT_ID, status: 'open',
  doc_review_status: 'pending_review', revision_no: 1, origin: 'human',
  body: PLAN_BODY,
  provider_status: [{ provider_id: 'aip_opus', registered: false, current_name: null, snapshot_name: 'Claude Opus', name_changed: false }],
  totals: { design_sheets: 0, work_sets: 1, steps: 2 },
}
const PREVIEW = {
  wp_revision_no: 2, wp_review_status: 'approved', instruction_mode: 'auto_approved',
  workflow: { owner_doc_id: ROOT_ID, workflow_tag: 's7-h0-i2' },
  comparison: { kept: { count: 2, done_count: 0 }, added: { count: 0, items: [] }, not_deleted: { count: 0, items: [] } },
  step_map: [
    { key: 'T#1', type: 'T', matched: true, item_seq: 1, position_after_apply: 1, status: 'pending' },
    { key: 'TR#1', type: 'TR', matched: true, item_seq: 2, position_after_apply: 2, status: 'pending' },
  ],
  fill_preview: {
    target_seq: 2, target_key: 'TR#1', target_label: '작업레포트',
    provider_overrides: { '2': 'aip_opus' }, note_overrides: { '2': 'from plan' },
    folded: [{ from_key: 'T#1', to_key: 'TR#1', to_item_seq: 2 }],
  },
  warnings: WARNINGS,
  can_apply: true,
  can_apply_without_workflow: true,
  can_apply_with_workflow: true,
  can_change_workflow: true,
  apply_blockers: { keep_workflow: null, change_workflow: null },
}
const APPLY = {
  ok: true, workflow: { owner_doc_id: ROOT_ID }, warnings: WARNINGS,
  fill: {
    source_doc_id: DOC_ID, source_revision_no: 2, instruction_mode: 'auto_approved',
    target_seq: 2, provider_overrides: { '2': 'aip_opus' }, note_overrides: { '2': 'from plan' },
    default_note: 'common plan note', filled_item_seqs: [2], folded: PREVIEW.fill_preview.folded,
  },
}
const SEQUENCE = {
  data: {
    doc_id: ROOT_ID, doc_class: 'R', decided: true,
    sequence: [
      { id: 1, item_seq: 1, type: 'T', label: '작업지시', status: 'pending' },
      { id: 2, item_seq: 2, type: 'TR', label: '작업레포트', status: 'pending' },
    ],
    head: { id: 1, item_seq: 1, type: 'T', label: '작업지시', status: 'pending' },
  },
}

function globalOptions() {
  return { plugins: [i18n], stubs: { teleport: true, AppIcon: true } }
}

beforeEach(() => {
  setActivePinia(createPinia())
  i18n.global.locale.value = 'ko'
  useProjectStore().currentProjectId = 'flowgate'
  vi.spyOn(window, 'confirm').mockReturnValue(true)
  getRequest.mockReset()
  postRequest.mockReset()
  putRequest.mockReset()
  getRequest.mockImplementation((url: string) => {
    if (url.includes('/document-types')) return Promise.resolve({ data: { data: TYPES } })
    if (url.includes('/ai-invoke/providers')) return Promise.resolve({ data: { ok: true, providers: PROVIDERS } })
    if (url.includes('/workflow/sequence')) return Promise.resolve(SEQUENCE)
    if (url.includes('/work-plan')) return Promise.resolve({ data: structuredClone(READ_RESPONSE) })
    return Promise.reject(new Error(`unexpected GET ${url}`))
  })
  postRequest.mockImplementation((url: string) => {
    if (url === '/api/v1/documents/work-plan') {
      return Promise.resolve({ data: { ok: true, doc_id: DOC_ID, title: '0395 작업계획', body: structuredClone(PLAN_BODY) } })
    }
    if (url.endsWith('/work-plan/apply/preview')) return Promise.resolve({ data: structuredClone(PREVIEW) })
    if (url.endsWith('/work-plan/apply')) return Promise.resolve({ data: structuredClone(APPLY) })
    return Promise.reject(new Error(`unexpected POST ${url}`))
  })
  putRequest.mockResolvedValue({ data: {
    ok: true, doc_id: DOC_ID, revision_no: 2, doc_review_status: 'pending_review',
    totals: { design_sheets: 0, work_sets: 1, steps: 2 }, assignment_summary: [], unassigned_step_count: 1,
  } })
})

afterEach(() => {
  vi.restoreAllMocks()
  document.body.innerHTML = ''
})

describe('WorkPlan connected flow', () => {
  it('passes live IDs and values from create through preset edit/revert', async () => {
    const create = mount(WorkPlanCreateDialog, {
      props: { visible: true, parentDocId: ROOT_ID, projectId: 'flowgate', groupId: 'flowgate.default.0395' },
      global: globalOptions(),
    })
    await flushPromises()
    await create.findAll('.wpc-check').find((node) => node.text().includes('T'))!.trigger('click')
    await create.findAll('.wpc-check').find((node) => node.text().includes('Claude Opus'))!.trigger('click')
    await create.findAll('button').find((node) => node.text().includes('생성') && !node.text().includes('생성 중'))!.trigger('click')
    await flushPromises()
    const created = create.emitted('created')![0][0] as { docId: string; title: string }
    expect(created.docId).toBe(DOC_ID)
    create.unmount()

    const editor = mount(WorkPlanEditor, {
      props: { docId: created.docId, projectId: 'flowgate' }, global: globalOptions(),
    })
    await flushPromises()
    expect(editor.find('.wp-step-row select').text()).toContain(i18n.global.t('main.work_plan.unavailable_provider'))
    await editor.findAll('.wp-step-msg')[0].setValue('edited in the connected flow')
    await editor.findAll('button').find((node) => node.text().includes('저장') && !node.text().includes('저장 중'))!.trigger('click')
    await flushPromises()
    expect(putRequest.mock.calls[0][0]).toContain(encodeURIComponent(created.docId))
    expect(putRequest.mock.calls[0][1].body.steps[0].note).toBe('edited in the connected flow')
    editor.unmount()

    // 예전에는 미리보기 오버레이가 /work-plan/apply 를 불러 여기서 바로 적용해 버리고
    // 그 응답으로 프리셋을 만들었다. 그 오버레이를 걷어냈으므로, 연속 작업 창이 프리셋을
    // 받았을 때 어떻게 하는지만 그대로 남기고 입력은 같은 모양으로 직접 만든다.
    const applied = {
      ownerDocId: ROOT_ID,
      preset: {
        sourceDocId: created.docId,
        sourceRevisionNo: APPLY.fill.source_revision_no,
        instructionMode: APPLY.fill.instruction_mode as 'auto_approved',
        targetSeq: APPLY.fill.target_seq,
        providerOverrides: { 2: 'aip_opus' },
        messageOverrides: { 2: 'from plan' },
        defaultMessage: APPLY.fill.default_note,
        filledSeqs: APPLY.fill.filled_item_seqs,
        warnings: [],
      },
    }
    expect(applied.preset.sourceDocId).toBe(created.docId)

    const continuous = mount(ContinuousWorkDialog, {
      attachTo: document.body,
      props: {
        visible: true, docRef: applied.ownerDocId, providers: PROVIDERS,
        selectedProvider: 'aip_fable', preset: applied.preset,
      },
      global: { plugins: [i18n] },
    })
    await flushPromises()
    expect(document.querySelector('.cwd-preset-banner')).not.toBeNull()
    ;(document.querySelectorAll('.cwd-tab')[1] as HTMLButtonElement).click()
    await flushPromises()
    expect(document.querySelectorAll('.cwd-filled-badge')).toHaveLength(1)
    const select = document.querySelector('.cwd-override-select .aip-select-input') as HTMLSelectElement
    expect(select.value).toBe('aip_opus')
    select.value = 'aip_fable'
    select.dispatchEvent(new Event('change'))
    await flushPromises()
    expect(document.querySelectorAll('.cwd-filled-badge')).toHaveLength(0)

    const revert = document.querySelector('.cwd-preset-banner button') as HTMLButtonElement
    revert.click()
    await flushPromises()
    expect(window.confirm).toHaveBeenCalled()
    expect(document.querySelector('.cwd-preset-banner')).toBeNull()
    continuous.unmount()
  })
})