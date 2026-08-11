import { createApp, h, nextTick, ref } from 'vue'
import { createPinia } from 'pinia'
import i18n from '../../shared/i18n'
import api from '../../shared/api'
import WorkPlanEditor from '../../src/main/components/WorkPlanEditor.vue'
import WorkPlanCreateDialog from '../../src/main/components/WorkPlanCreateDialog.vue'
import ContinuousWorkDialog from '../../src/main/components/ContinuousWorkDialog.vue'
import { useProjectStore } from '../../src/main/stores/project'

const DOC_ID = 'flowgate.default.0395.0091-WP'
const ROOT_ID = 'flowgate.default.0395.0001-R'
const types = [
  { code: 'DS', label: '설계지시', category: 'instruction', countable: true, unit: 'sheet', sort_order: 0 },
  { code: 'D', label: '기본설계', category: 'design', countable: true, unit: 'sheet', sort_order: 1 },
  { code: 'T', label: '작업지시', category: 'instruction', countable: true, unit: 'set', pair_code: 'TR', sort_order: 2 },
  { code: 'TR', label: '작업레포트', category: 'work', countable: false, unit: null, sort_order: 3 },
]
const providers = [
  { id: 'aip_opus', name: 'Claude Opus', kind: 'claude', exec_type: 'cli' },
  { id: 'aip_fable', name: 'Fable', kind: 'openai', exec_type: 'cli' },
]
const body = {
  wp_version: 1, binding: 'advisory', counted_types: ['D', 'T'],
  quantities: { D: { unit: 'sheet', count: 1 }, T: { unit: 'set', count: 1 } },
  provider_candidates: [{ provider_id: 'aip_opus', display_name: 'Claude Opus', group_label: 'Claude · CLI' }],
  defaults: { provider_id: 'aip_opus', note: '계획 공통 멘트' },
  steps: [
    { key: 'D#1', type: 'D', ordinal: 1, pair_key: null, pair_role: 'single', provider_id: 'aip_opus', provider_display_name: 'Claude Opus', note: '화면 경계 확인', locked: false, locked_reason: null, origin: 'human' },
    { key: 'T#1', type: 'T', ordinal: 1, pair_key: 'TR#1', pair_role: 'instruction', provider_id: 'aip_opus', provider_display_name: 'Claude Opus', note: '구현', locked: false, locked_reason: null, origin: 'human' },
    { key: 'TR#1', type: 'TR', ordinal: 1, pair_key: 'T#1', pair_role: 'result', provider_id: 'aip_opus', provider_display_name: 'Claude Opus', note: '구현', locked: false, locked_reason: null, origin: 'human' },
  ],
}
const preview = {
  wp_revision_no: 2, wp_review_status: 'approved', instruction_mode: 'auto_approved',
  workflow: { owner_doc_id: ROOT_ID, workflow_tag: 's7-h0-i4' },
  comparison: { kept: { count: 4, done_count: 0 }, added: { count: 0, items: [] }, not_deleted: { count: 0, items: [] } },
  step_map: body.steps.map((step, index) => ({ key: step.key, type: step.type, matched: true, item_seq: index + 1, position_after_apply: index + 1, status: 'pending' })),
  fill_preview: { target_seq: 4, target_key: 'TR#1', target_label: '작업레포트', provider_overrides: { '1': 'aip_opus', '4': 'aip_opus' }, note_overrides: { '1': '화면 경계 확인', '4': '구현' }, folded: [{ from_key: 'T#1', to_key: 'TR#1', to_item_seq: 4 }] },
  warnings: [],
  can_apply: true,
  can_apply_without_workflow: true,
  can_apply_with_workflow: true,
  can_change_workflow: true,
  apply_blockers: { keep_workflow: null, change_workflow: null },
}
const sequence = {
  doc_id: ROOT_ID, doc_class: 'R', decided: true,
  items: [
    { id: 1, item_seq: 1, type: 'D', label: '기본설계', status: 'pending' },
    { id: 2, item_seq: 2, type: 'D', label: '기본설계', status: 'pending' },
    { id: 3, item_seq: 3, type: 'T', label: '작업지시', status: 'pending' },
    { id: 4, item_seq: 4, type: 'TR', label: '작업레포트', status: 'pending' },
  ],
  head: { id: 1, item_seq: 1, type: 'D', label: '기본설계', status: 'pending' },
}

let createPayload: any = null
let saveCalls = 0

api.defaults.adapter = async (config) => {
  const url = config.url ?? ''
  const method = (config.method ?? 'get').toLowerCase()
  let data: any
  if (url.includes('/document-types')) data = { data: types }
  else if (url.includes('/ai-invoke/providers')) data = { ok: true, providers, default_provider_id: null }
  else if (url.includes('/workflow/sequence')) data = sequence
  else if (url.endsWith('/work-plan/apply/preview')) data = preview
  else if (url.endsWith('/api/v1/documents/work-plan') && method === 'post') {
    createPayload = typeof config.data === 'string' ? JSON.parse(config.data) : config.data
    data = {
      ok: true,
      doc_id: DOC_ID,
      title: createPayload.title,
      body,
    }
  }
  else if (url.includes('/work-plan') && method === 'put') {
    saveCalls += 1
    data = {
      ok: true,
      doc_id: DOC_ID,
      revision_no: 3,
      doc_review_status: 'pending_review',
      totals: { design_sheets: 1, work_sets: 1, steps: 3 },
      assignment_summary: [{ provider_id: 'aip_opus', display_name: 'Claude Opus', step_count: 3 }],
      unassigned_step_count: 0,
    }
  }
  else if (url.includes('/work-plan')) data = {
    ok: true, doc_id: DOC_ID, doc_type: 'WP', title: '작업계획 — 설계 2장 · 작업 1세트', group_id: 'flowgate.default.0395',
    parent_doc_id: ROOT_ID, status: 'open', doc_review_status: 'pending_review', revision_no: 2,
    body, provider_status: [{ provider_id: 'aip_opus', registered: true, current_name: 'Claude Opus', snapshot_name: 'Claude Opus', name_changed: false }],
    assignment_summary: [{ provider_id: 'aip_opus', display_name: 'Claude Opus', step_count: 3 }],
    unassigned_step_count: 0,
    totals: { design_sheets: 1, work_sets: 1, steps: 3 },
  }
  else throw new Error(`unexpected browser harness request: ${url}`)
  return { data, status: 200, statusText: 'OK', headers: {}, config }
}

const preset = {
  sourceDocId: DOC_ID, sourceRevisionNo: 2, instructionMode: 'auto_approved' as const,
  targetSeq: 4, providerOverrides: { 1: 'aip_opus', 4: 'aip_opus' },
  messageOverrides: { 4: '브라우저 회귀에서 긴 안내 문구와 채움 배지가 잘리지 않는지 확인합니다.' },
  defaultMessage: '계획 기준으로 연속 작업을 진행합니다.', filledSeqs: [1, 4], warnings: preview.warnings,
}

const App = {
  setup() {
    const metrics = ref('waiting')
    const createVisible = ref(true)
    const editorVisible = ref(false)
    const createdTitle = ref('')
    const continuousVisible = ref(false)
    const onCreated = (payload: { title: string }) => {
      createdTitle.value = payload.title
      editorVisible.value = true
    }
    setTimeout(async () => {
      await nextTick()
      const selectAllTypesButton = document.querySelector('.wpc-section .wpc-section-hd .btn-ghost') as HTMLButtonElement | null
      selectAllTypesButton?.click()
      await nextTick()
      ;(document.querySelector('.wpc-provider-list .wpc-check-item') as HTMLElement | null)?.click()
      await nextTick()
      const firstCreatePlus = document.querySelectorAll<HTMLButtonElement>('.wpc-type-qty .wpc-qty-btn')[1] ?? null
      firstCreatePlus?.click()
      await nextTick()
      const createButton = document.querySelector('.modal-wpc .modal-ft .btn-primary') as HTMLButtonElement | null
      createButton?.click()
      await new Promise(resolve => setTimeout(resolve, 0))
      await nextTick()

      const rect = (selector: string) => document.querySelector(selector)?.getBoundingClientRect()
      const hitCenter = (element: HTMLElement | null) => {
        const value = element?.getBoundingClientRect()
        if (!element || !value) return null
        return document.elementFromPoint(value.left + value.width / 2, value.top + value.height / 2)
      }
      const createdUnassignedBadgeCount = document.querySelectorAll('.wp-unassigned-badge').length
      const createdSummaryText = document.querySelector('.wp-summary-strip')?.textContent?.replace(/\s+/g, ' ').trim() ?? ''
      const providerSelect = document.querySelector('.wp-step-table .aip-select-input') as HTMLSelectElement | null
      const noteInput = document.querySelector('.wp-step-table .wp-note-input') as HTMLInputElement | null
      const saveButton = [...document.querySelectorAll('.card-actions button')]
        .find((button) => button.textContent?.includes('저장')) as HTMLButtonElement | undefined
      providerSelect?.scrollIntoView({ block: 'center' })
      const providerHit = hitCenter(providerSelect)
      if (providerHit instanceof HTMLElement) providerHit.click()
      const providerSelectFocused = document.activeElement === providerSelect
      noteInput?.scrollIntoView({ block: 'center' })
      const noteHit = hitCenter(noteInput)
      if (noteHit instanceof HTMLElement) noteHit.click()
      const noteInputFocused = document.activeElement === noteInput
      if (noteInput) {
        noteInput.value = '좌표 입력 확인'
        noteInput.dispatchEvent(new Event('input', { bubbles: true }))
      }
      const noteValueAfterCoordinateInput = noteInput?.value ?? ''
      saveButton?.scrollIntoView({ block: 'center' })
      const saveHit = hitCenter(saveButton ?? null)
      if (saveHit instanceof HTMLElement) saveHit.click()
      await new Promise(resolve => setTimeout(resolve, 0))
      await nextTick()
      const initialOverlayAbsent = document.querySelector('.wpa-overlay') === null
      const plusButton = [...document.querySelectorAll('.wp-stepper-btn')]
        .find((button) => button.textContent?.trim() === '+') as HTMLButtonElement | undefined
      plusButton?.scrollIntoView({ block: 'center' })
      const plusRect = plusButton?.getBoundingClientRect()
      const quantityBefore = Number(plusButton?.previousElementSibling?.textContent ?? NaN)
      const hitAtPlusCenter = plusRect
        ? document.elementFromPoint(plusRect.left + plusRect.width / 2, plusRect.top + plusRect.height / 2)
        : null
      if (hitAtPlusCenter instanceof HTMLElement) hitAtPlusCenter.click()
      await nextTick()
      const currentPlusButton = [...document.querySelectorAll('.wp-stepper-btn')]
        .find((button) => button.textContent?.trim() === '+') as HTMLButtonElement | undefined
      const quantityAfter = Number(currentPlusButton?.previousElementSibling?.textContent ?? NaN)

      // 0399 M0020 — 전면 적용 미리보기 오버레이는 없앴다. 여기서 재던 것도 함께 없어진다.
      continuousVisible.value = true
      await nextTick()
      ;(document.querySelectorAll('.cwd-tab')[1] as HTMLButtonElement | undefined)?.click()
      await nextTick()

      const area = rect('#document-area')
      const editor = rect('.wp-editor')
      const header = rect('#document-header')
      const action = rect('#document-actionbar')
      const modal = rect('.modal-cwd')
      const banner = document.querySelector('.cwd-preset-banner') as HTMLElement | null
      const badges = [...document.querySelectorAll('.cwd-filled-badge')] as HTMLElement[]
      const result = {
        viewport: { width: window.innerWidth, height: window.innerHeight },
        documentAreaWidth: Math.round(area?.width ?? 0),
        editorWidth: Math.round(editor?.width ?? 0),
        editorLayoutClass: [...(document.querySelector('.wp-editor')?.classList ?? [])].find(value => value.startsWith('wp-layout-')) ?? null,
        tableColumnCount: document.querySelectorAll('.wp-step-table thead th').length,
        createPayload,
        createSelectedAtLeastTwoTypes: (createPayload?.counted_types?.length ?? 0) >= 2,
        createSelectedAtLeastOneProvider: (createPayload?.provider_candidates?.length ?? 0) >= 1,
        createdTitle: createdTitle.value,
        createdTitleIsDescriptive: !!createdTitle.value && createdTitle.value !== DOC_ID,
        createdUnassignedBadgeCount,
        createdPlanHasNoUnassignedBadges: createdUnassignedBadgeCount === 0,
        createdSummaryText,
        createdSummaryHasTotalsAndAssignments: createdSummaryText.includes('전체 3단계') && createdSummaryText.includes('Claude Opus 3') && createdSummaryText.includes('빠진 칸 없음'),
        initialOverlayAbsent,
        providerSelectHitByCoordinates: !!providerSelect && (providerHit === providerSelect || !!(providerHit && providerSelect.contains(providerHit))),
        providerSelectFocused,
        noteInputHitByCoordinates: !!noteInput && (noteHit === noteInput || !!(noteHit && noteInput.contains(noteHit))),
        noteInputFocused,
        noteValueAfterCoordinateInput,
        noteInputAcceptedText: noteValueAfterCoordinateInput === '좌표 입력 확인',
        saveButtonHitByCoordinates: !!saveButton && (saveHit === saveButton || !!(saveHit && saveButton.contains(saveHit))),
        saveRequestCount: saveCalls,
        saveTriggeredByCoordinateClick: saveCalls > 0,
        quantityButtonCenter: plusRect ? {
          x: Math.round(plusRect.left + plusRect.width / 2),
          y: Math.round(plusRect.top + plusRect.height / 2),
        } : null,
        quantityButtonHitByCoordinates: !!plusButton && (
          hitAtPlusCenter === plusButton || !!(hitAtPlusCenter && plusButton.contains(hitAtPlusCenter))
        ),
        quantityBeforeCoordinateClick: quantityBefore,
        quantityAfterCoordinateClick: quantityAfter,
        quantityRaisedByCoordinateClick: quantityAfter === quantityBefore + 1,
        headerBottom: Math.round(header?.bottom ?? 0),
        actionbarBottom: Math.round(action?.bottom ?? 0),
        modalWidth: Math.round(modal?.width ?? 0),
        presetBannerNotClipped: !!banner && banner.scrollWidth <= banner.clientWidth && banner.scrollHeight <= banner.clientHeight,
        filledBadgeCount: badges.length,
        filledBadgesInsideModal: !!modal && badges.every((badge) => {
          const value = badge.getBoundingClientRect()
          return value.left >= modal.left && value.right <= modal.right && value.top >= modal.top && value.bottom <= modal.bottom
        }),
      }
      metrics.value = JSON.stringify(result)
      document.body.dataset.regressionReady = 'true'
    }, 1400)
    return () => [
      h(WorkPlanCreateDialog, {
        visible: createVisible.value,
        parentDocId: ROOT_ID,
        projectId: 'flowgate',
        groupId: 'flowgate.default.0395',
        'onUpdate:visible': (value: boolean) => { createVisible.value = value },
        onCreated,
      }),
      h('div', { class: 'reg-shell' }, [
        h('aside', { class: 'reg-side' }, '문서'),
        h('main', { id: 'document-area' }, [
          h('header', { id: 'document-header' }, '작업계획 문서 헤더'),
          h('div', { id: 'document-actionbar' }, '검토 · 승인 · 적용 액션바'),
          h('section', { id: 'editor-host' }, [editorVisible.value ? h(WorkPlanEditor, { docId: DOC_ID, projectId: 'flowgate' }) : null]),
        ]),
        h('aside', { class: 'reg-info' }, '정보'),
      ]),
      h(ContinuousWorkDialog, {
        visible: continuousVisible.value, docRef: ROOT_ID, providers, selectedProvider: 'aip_fable', preset,
      }),
      h('pre', { id: 'regression-metrics' }, metrics.value),
    ]
  },
}

const style = document.createElement('style')
style.textContent = `
  *{box-sizing:border-box} html,body,#app{margin:0;width:100%;height:100%;font-family:Arial,sans-serif}
  .reg-shell{display:grid;grid-template-columns:110px minmax(0,1fr) 90px;width:100vw;height:100vh;background:#eef2f7}
  .reg-side,.reg-info{padding:12px;background:#e2e8f0;color:#475569}
  #document-area{position:relative;min-width:0;height:100vh;background:#fff;overflow:hidden}
  #document-header{height:48px;padding:14px;border-bottom:1px solid #cbd5e1;font-weight:700}
  #document-actionbar{height:40px;padding:10px 14px;border-bottom:1px solid #cbd5e1;color:#475569}
  #editor-host{position:absolute;inset:88px 0 0;overflow:auto}
  #regression-metrics{position:fixed;left:4px;bottom:4px;z-index:9999;max-width:calc(100vw - 8px);margin:0;padding:4px;background:#111;color:#0f0;font-size:10px;white-space:pre-wrap}
`
document.head.appendChild(style)

const pinia = createPinia()
i18n.global.locale.value = 'ko'
useProjectStore(pinia).currentProjectId = 'flowgate'
createApp(App).use(pinia).use(i18n).mount('#app')