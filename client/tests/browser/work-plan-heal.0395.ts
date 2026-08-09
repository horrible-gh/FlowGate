import { createApp, h, nextTick, ref } from 'vue'
import { createPinia } from 'pinia'
import i18n from '../../shared/i18n'
import api from '../../shared/api'
import WorkPlanEditor from '../../src/main/components/WorkPlanEditor.vue'
import { useProjectStore } from '../../src/main/stores/project'

// flowgate.default.0395 T0026 재작업 — 반려 사유를 화면에서 확인한다.
//
//   이 작업계획을 표로 열 수 없습니다.
//   이 작업계획을 표로 열 수 없습니다. 원문 보기로 확인해 주세요.
//   Expecting value: line 1 column 1 (char 0)
//
// 앞(before)은 고치기 전 서버가 돌려주던 409 wp_unreadable 응답, 뒤(after)는 같은 문서를
// 고친 서버가 실제로 돌려준 응답이다. 뒤의 값은 지어낸 것이 아니라, 검수자가 8080 미리보기
// 에서 만든 문서(test.test.0001.0005-WP, 제목 "aa")를 이번 코드로 열어 받은 그대로다.

const DOC_ID = 'test.test.0001.0005-WP'

const UNREADABLE = {
  code: 'wp_unreadable',
  message: '이 작업계획을 표로 열 수 없습니다. 원문 보기로 확인해 주세요.',
  reason: 'not_json',
  detail: 'Expecting value: line 1 column 1 (char 0)',
  revisions: [],
  raw: '---\nproject: test\nmodule: test\ngroup: 0001\ntype: WP\ndoc_number: 0005-WP\ntitle: aa\n---\n',
}

const HEALED = {
  ok: true,
  doc_id: DOC_ID,
  doc_type: 'WP',
  title: 'aa',
  group_id: 'test.test.0001',
  parent_doc_id: 'test.test.0001.0001-R',
  status: 'draft',
  doc_review_status: 'pending_review',
  revision_no: 0,
  origin: 'human',
  body: {
    wp_version: 1,
    binding: 'advisory',
    counted_types: ['DS', 'D', 'P', 'L', 'DB', 'N', 'T', 'TS'],
    quantities: {
      DS: { unit: 'sheet', count: 0 },
      D: { unit: 'sheet', count: 0 },
      P: { unit: 'sheet', count: 0 },
      L: { unit: 'sheet', count: 0 },
      DB: { unit: 'sheet', count: 0 },
      N: { unit: 'set', count: 1 },
      T: { unit: 'set', count: 0 },
      TS: { unit: 'set', count: 1 },
    },
    provider_candidates: [
      { provider_id: 'aip_lv5tzg', display_name: 'haiku', group_label: 'Claude · CLI' },
      { provider_id: 'aip_wxwcvt', display_name: 'sonnet', group_label: 'Claude · CLI' },
    ],
    defaults: { provider_id: 'aip_lv5tzg', note: '' },
    steps: [
      { key: 'N#1', type: 'N', ordinal: 1, pair_key: 'NR#1', pair_role: 'instruction', provider_id: 'aip_lv5tzg', provider_display_name: 'haiku', note: null, locked: false, locked_reason: null, origin: 'human' },
      { key: 'NR#1', type: 'NR', ordinal: 1, pair_key: 'N#1', pair_role: 'result', provider_id: 'aip_lv5tzg', provider_display_name: 'haiku', note: null, locked: false, locked_reason: null, origin: 'human' },
      { key: 'TS#1', type: 'TS', ordinal: 1, pair_key: 'TSR#1', pair_role: 'instruction', provider_id: 'aip_lv5tzg', provider_display_name: 'haiku', note: null, locked: false, locked_reason: null, origin: 'human' },
      { key: 'TSR#1', type: 'TSR', ordinal: 1, pair_key: 'TS#1', pair_role: 'result', provider_id: null, provider_display_name: null, note: null, locked: true, locked_reason: 'server_assembled', origin: 'system' },
    ],
  },
  provider_status: [
    { provider_id: 'aip_lv5tzg', registered: true, current_name: 'haiku', snapshot_name: 'haiku', name_changed: false },
    { provider_id: 'aip_wxwcvt', registered: true, current_name: 'sonnet', snapshot_name: 'sonnet', name_changed: false },
  ],
  assignment_summary: [{ provider_id: 'aip_lv5tzg', display_name: 'haiku', step_count: 3 }],
  unassigned_step_count: 0,
  totals: { design_sheets: 0, work_sets: 2, steps: 4 },
  last_application: null,
}

const types = [
  { code: 'DS', label: '설계지시', category: 'instruction', countable: true, unit: 'sheet', sort_order: 0 },
  { code: 'D', label: '기본설계', category: 'design', countable: true, unit: 'sheet', sort_order: 1 },
  { code: 'P', label: '상세설계', category: 'design', countable: true, unit: 'sheet', sort_order: 2 },
  { code: 'L', label: '로직설계', category: 'design', countable: true, unit: 'sheet', sort_order: 3 },
  { code: 'DB', label: 'DB설계', category: 'design', countable: true, unit: 'sheet', sort_order: 4 },
  { code: 'N', label: '지시', category: 'instruction', countable: true, unit: 'set', pair_code: 'NR', sort_order: 5 },
  { code: 'NR', label: '지시레포트', category: 'work', countable: false, unit: null, sort_order: 6 },
  { code: 'T', label: '작업지시', category: 'instruction', countable: true, unit: 'set', pair_code: 'TR', sort_order: 7 },
  { code: 'TR', label: '작업레포트', category: 'work', countable: false, unit: null, sort_order: 8 },
  { code: 'TS', label: '시험지시', category: 'instruction', countable: true, unit: 'set', pair_code: 'TSR', sort_order: 9 },
  { code: 'TSR', label: '시험레포트', category: 'work', countable: false, unit: null, sort_order: 10 },
]
const providers = [
  { id: 'aip_lv5tzg', name: 'haiku', kind: 'claude', exec_type: 'cli' },
  { id: 'aip_wxwcvt', name: 'sonnet', kind: 'claude', exec_type: 'cli' },
]

let phase: 'before' | 'after' = 'before'

api.defaults.adapter = async (config) => {
  const url = config.url ?? ''
  if (url.includes('/document-types')) {
    return { data: { data: types }, status: 200, statusText: 'OK', headers: {}, config }
  }
  if (url.includes('/ai-invoke/providers')) {
    return { data: { ok: true, providers, default_provider_id: 'aip_lv5tzg' }, status: 200, statusText: 'OK', headers: {}, config }
  }
  if (url.includes('/work-plan')) {
    if (phase === 'before') {
      const error: any = new Error('Request failed with status code 409')
      error.response = { data: UNREADABLE, status: 409, statusText: 'Conflict', headers: {}, config }
      throw error
    }
    return { data: HEALED, status: 200, statusText: 'OK', headers: {}, config }
  }
  throw new Error(`unexpected browser harness request: ${url}`)
}

const text = (selector: string) =>
  document.querySelector(selector)?.textContent?.replace(/\s+/g, ' ').trim() ?? ''

const App = {
  setup() {
    const metrics = ref('waiting')
    const editorKey = ref('before')
    setTimeout(async () => {
      await nextTick()
      await new Promise(resolve => setTimeout(resolve, 60))

      // ── 고치기 전 화면: 검수자가 본 그대로 ──────────────────────────────
      const beforeUnreadableVisible = !!document.querySelector('.wp-unreadable')
      const beforeTitle = text('.wp-unreadable-title')
      const beforeMessage = text('.wp-unreadable-desc')
      const beforeDetail = text('.wp-unreadable-detail')
      const beforeTableRows = document.querySelectorAll('.wp-step-table tbody tr').length

      // ── 고친 뒤 화면: 같은 문서를 이번 코드로 연 응답 ────────────────────
      phase = 'after'
      editorKey.value = 'after'
      await nextTick()
      await new Promise(resolve => setTimeout(resolve, 60))
      await nextTick()

      const afterUnreadableVisible = !!document.querySelector('.wp-unreadable')
      const rows = [...document.querySelectorAll('.wp-step-table tbody tr')] as HTMLElement[]
      const summaryText = text('.wp-summary-strip')
      const unassignedBadges = document.querySelectorAll('.wp-unassigned-badge').length
      const overlayAbsent = document.querySelector('.wpa-overlay') === null
      const editorRect = document.querySelector('.wp-editor')?.getBoundingClientRect()

      const plusButton = [...document.querySelectorAll('.wp-stepper-btn')]
        .find(button => button.textContent?.trim() === '+') as HTMLButtonElement | undefined
      plusButton?.scrollIntoView({ block: 'center' })
      const plusRect = plusButton?.getBoundingClientRect()
      const quantityBefore = Number(plusButton?.previousElementSibling?.textContent ?? NaN)
      const hitAtPlusCenter = plusRect
        ? document.elementFromPoint(plusRect.left + plusRect.width / 2, plusRect.top + plusRect.height / 2)
        : null
      if (hitAtPlusCenter instanceof HTMLElement) hitAtPlusCenter.click()
      await nextTick()
      const currentPlus = [...document.querySelectorAll('.wp-stepper-btn')]
        .find(button => button.textContent?.trim() === '+') as HTMLButtonElement | undefined
      const quantityAfter = Number(currentPlus?.previousElementSibling?.textContent ?? NaN)

      metrics.value = JSON.stringify({
        docId: DOC_ID,
        before: {
          unreadableScreenShown: beforeUnreadableVisible,
          title: beforeTitle,
          message: beforeMessage,
          detail: beforeDetail,
          stepTableRowCount: beforeTableRows,
        },
        after: {
          unreadableScreenShown: afterUnreadableVisible,
          stepTableRowCount: rows.length,
          stepTypes: rows.map(row => row.querySelector('.wp-col-type .doc-tag')?.textContent?.trim() ?? ''),
          summaryText,
          summaryShowsTotals: summaryText.includes('전체 4단계'),
          summaryShowsAssignments: summaryText.includes('haiku 3'),
          summaryShowsComplete: summaryText.includes('빠진 칸 없음'),
          unassignedBadgeCount: unassignedBadges,
          overlayAbsent,
          editorWidth: Math.round(editorRect?.width ?? 0),
          quantityButtonCenter: plusRect
            ? { x: Math.round(plusRect.left + plusRect.width / 2), y: Math.round(plusRect.top + plusRect.height / 2) }
            : null,
          quantityButtonHitByCoordinates: !!plusButton && (
            hitAtPlusCenter === plusButton || !!(hitAtPlusCenter && plusButton.contains(hitAtPlusCenter))
          ),
          quantityBeforeCoordinateClick: quantityBefore,
          quantityAfterCoordinateClick: quantityAfter,
          quantityRaisedByCoordinateClick: quantityAfter === quantityBefore + 1,
        },
      })
      document.body.dataset.healReady = 'true'
    }, 400)

    return () => [
      h('div', { class: 'heal-shell' }, [
        h('main', { id: 'document-area' }, [
          h('header', { id: 'document-header' }, `작업계획 · ${DOC_ID}`),
          h('section', { id: 'editor-host' }, [
            h(WorkPlanEditor, { key: editorKey.value, docId: DOC_ID, projectId: 'test' }),
          ]),
        ]),
      ]),
      h('pre', { id: 'heal-metrics' }, metrics.value),
    ]
  },
}

const style = document.createElement('style')
style.textContent = `
  *{box-sizing:border-box} html,body,#app{margin:0;width:100%;height:100%;font-family:Arial,sans-serif}
  .heal-shell{display:grid;grid-template-columns:minmax(0,1fr);width:100vw;height:100vh;background:#eef2f7}
  #document-area{position:relative;min-width:0;height:100vh;background:#fff;overflow:hidden}
  #document-header{height:48px;padding:14px;border-bottom:1px solid #cbd5e1;font-weight:700}
  #editor-host{position:absolute;inset:48px 0 0;overflow:auto}
  #heal-metrics{position:fixed;left:4px;bottom:4px;z-index:9999;max-width:calc(100vw - 8px);margin:0;padding:4px;background:#111;color:#0f0;font-size:10px;white-space:pre-wrap}
`
document.head.appendChild(style)

const pinia = createPinia()
i18n.global.locale.value = 'ko'
useProjectStore(pinia).currentProjectId = 'test'
createApp(App).use(pinia).use(i18n).mount('#app')
