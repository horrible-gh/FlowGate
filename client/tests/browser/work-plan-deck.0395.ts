/**
 * flowgate.default.0395 — 시안(xc32frrg) 대조용 렌더 하네스.
 *
 * 시안과 같은 데이터(타입 8종 · 프로바이더 14개 · 15단계)를 넣고 실제 컴포넌트를
 * 그대로 띄운다. 시안 HTML과 나란히 캡처해 요소 유무를 눈과 수치로 대조하기 위한
 * 화면이며, 제품 코드는 건드리지 않는다.
 *
 *   /tests/browser/work-plan-deck.0395.html?view=dialog  → 작업계획 생성 다이얼로그
 *   /tests/browser/work-plan-deck.0395.html?view=doc     → 작업계획 문서 화면
 */
import { createApp, h, nextTick, ref } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '../../shared/i18n'
import api from '../../shared/api'
import '../../shared/variables.css'
import '../../shared/app.css'
import WorkPlanEditor from '../../src/main/components/WorkPlanEditor.vue'
import WorkPlanCreateDialog from '../../src/main/components/WorkPlanCreateDialog.vue'
import { useProjectStore } from '../../src/main/stores/project'

const DOC_ID = 'flowgate.default.0395.0003-WP'
const ROOT_ID = 'flowgate.default.0395.0001-R'

/* 시안 좌측 칸과 같은 8종 — 설계 5종(장) + 작업 3종(세트) */
const types = [
  { id: 1, code: 'DS', label: '설계지시', category: 'design', countable: true, unit: 'sheet', sort_order: 0 },
  { id: 2, code: 'D', label: '기본설계', category: 'design', countable: true, unit: 'sheet', sort_order: 1 },
  { id: 3, code: 'P', label: '프로토콜', category: 'design', countable: true, unit: 'sheet', sort_order: 2 },
  { id: 4, code: 'L', label: '로직', category: 'design', countable: true, unit: 'sheet', sort_order: 3 },
  { id: 5, code: 'DB', label: '데이터베이스', category: 'design', countable: true, unit: 'sheet', sort_order: 4 },
  { id: 6, code: 'N', label: '조사지시', category: 'instruction', countable: true, unit: 'set', pair_code: 'NR', sort_order: 5 },
  { id: 7, code: 'NR', label: '조사레포트', category: 'work', countable: false, unit: null, sort_order: 6 },
  { id: 8, code: 'T', label: '작업지시', category: 'instruction', countable: true, unit: 'set', pair_code: 'TR', sort_order: 7 },
  { id: 9, code: 'TR', label: '작업레포트', category: 'work', countable: false, unit: null, sort_order: 8 },
  { id: 10, code: 'TS', label: '테스트지시', category: 'instruction', countable: true, unit: 'set', pair_code: 'TSR', sort_order: 9 },
  { id: 11, code: 'TSR', label: '테스트레포트', category: 'work', countable: false, unit: null, sort_order: 10 },
]

/* 시안 우측 칸과 같은 14개 — Claude 6 / OpenAI 3 / Google 2 / 로컬 3 */
const providers = [
  { id: 'p_opus', name: 'Claude Opus', kind: 'claude', exec_type: 'cli' },
  { id: 'p_sonnet', name: 'Claude Sonnet', kind: 'claude', exec_type: 'cli' },
  { id: 'p_fable', name: 'Claude Fable', kind: 'claude', exec_type: 'cli' },
  { id: 'p_haiku', name: 'Claude Haiku', kind: 'claude', exec_type: 'cli' },
  { id: 'p_opus_fast', name: 'Claude Opus (fast)', kind: 'claude', exec_type: 'cli' },
  { id: 'p_sonnet_think', name: 'Claude Sonnet (thinking)', kind: 'claude', exec_type: 'cli' },
  { id: 'p_codex', name: 'GPT (codex)', kind: 'openai', exec_type: 'cli' },
  { id: 'p_codex_mini', name: 'GPT (codex-mini)', kind: 'openai', exec_type: 'cli' },
  { id: 'p_gpt_api', name: 'GPT (api)', kind: 'openai', exec_type: 'api' },
  { id: 'p_gemini_cli', name: 'Gemini CLI', kind: 'gemini', exec_type: 'cli' },
  { id: 'p_gemini_flash', name: 'Gemini Flash', kind: 'gemini', exec_type: 'api' },
  { id: 'p_qwen', name: 'Qwen Coder (로컬)', kind: 'ollama', exec_type: 'local' },
  { id: 'p_deepseek', name: 'DeepSeek (로컬)', kind: 'ollama', exec_type: 'local' },
  { id: 'p_llama', name: 'Llama (로컬)', kind: 'ollama', exec_type: 'local' },
]

/* 시안 문서 화면과 같은 15단계 — 설계 5장 + 작업 5세트 */
function buildSteps() {
  const steps: Record<string, unknown>[] = []
  for (const code of ['DS', 'D', 'P', 'L', 'DB']) {
    steps.push({
      key: `${code}#1`, type: code, ordinal: 1, pair_key: null, pair_role: 'single',
      provider_id: 'p_opus', provider_display_name: 'Claude Opus', note: '기존 설계 문서를 따를 것',
      locked: false, locked_reason: null, origin: 'ai_suggested',
    })
  }
  const pairs: [string, string, number][] = [['N', 'NR', 1], ['T', 'TR', 3], ['TS', 'TSR', 1]]
  for (const [head, tail, count] of pairs) {
    for (let n = 1; n <= count; n += 1) {
      steps.push({
        key: `${head}#${n}`, type: head, ordinal: n, pair_key: `${tail}#${n}`, pair_role: 'instruction',
        provider_id: n === 3 ? 'p_codex' : 'p_opus', provider_display_name: n === 3 ? 'GPT (codex)' : 'Claude Opus',
        note: '기존 코드 스타일을 따를 것', locked: false, locked_reason: null, origin: n === 3 ? 'human' : 'ai_suggested',
      })
      steps.push({
        key: `${tail}#${n}`, type: tail, ordinal: n, pair_key: `${head}#${n}`, pair_role: 'result',
        provider_id: tail === 'TSR' ? null : 'p_opus', provider_display_name: tail === 'TSR' ? null : 'Claude Opus',
        note: tail === 'TSR' ? null : '변경 파일을 빠짐없이 적을 것',
        locked: tail === 'TSR', locked_reason: tail === 'TSR' ? 'server_assembled' : null,
        origin: tail === 'TSR' ? 'system' : 'ai_suggested',
      })
    }
  }
  return steps
}

const body = {
  wp_version: 1,
  binding: 'advisory',
  counted_types: ['DS', 'D', 'P', 'L', 'DB', 'N', 'T', 'TS'],
  quantities: {
    DS: { unit: 'sheet', count: 1 }, D: { unit: 'sheet', count: 1 }, P: { unit: 'sheet', count: 1 },
    L: { unit: 'sheet', count: 1 }, DB: { unit: 'sheet', count: 1 },
    N: { unit: 'set', count: 1 }, T: { unit: 'set', count: 3 }, TS: { unit: 'set', count: 1 },
  },
  provider_candidates: [
    { provider_id: 'p_opus', display_name: 'Claude Opus', group_label: 'Claude · CLI' },
    { provider_id: 'p_codex', display_name: 'GPT (codex)', group_label: 'OpenAI · CLI' },
  ],
  defaults: { provider_id: 'p_opus', note: '기존 코드 스타일을 따를 것' },
  steps: buildSteps(),
}
const shortMode = new URLSearchParams(location.search).get('short') === '1'
const deckBody = shortMode ? {
  ...body,
  counted_types: ['DS', 'D', 'P', 'L', 'DB', 'N', 'T', 'TS'],
  quantities: {
    DS: { unit: 'sheet', count: 1 }, D: { unit: 'sheet', count: 1 }, P: { unit: 'sheet', count: 1 },
    L: { unit: 'sheet', count: 0 }, DB: { unit: 'sheet', count: 0 },
    N: { unit: 'set', count: 0 }, T: { unit: 'set', count: 0 }, TS: { unit: 'set', count: 0 },
  },
  steps: buildSteps().slice(0, 3),
} : body

api.defaults.adapter = async (config) => {
  const url = config.url ?? ''
  let data: any
  if (url.includes('/document-types')) data = { data: types }
  else if (url.includes('/ai-invoke/providers')) data = { ok: true, providers, default_provider_id: 'p_opus' }
  else if (url.includes('/work-plan')) data = {
    ok: true, doc_id: DOC_ID, doc_type: 'WP', title: '0395 작업계획 — 신규 문서타입 추가',
    group_id: 'flowgate.default.0395', parent_doc_id: ROOT_ID, status: 'open',
    doc_review_status: 'pending_review', revision_no: 2, body: deckBody,
    provider_status: [
      { provider_id: 'p_opus', registered: true, current_name: 'Claude Opus', snapshot_name: 'Claude Opus', name_changed: false },
      { provider_id: 'p_codex', registered: true, current_name: 'GPT (codex)', snapshot_name: 'GPT (codex)', name_changed: false },
    ],
    assignment_summary: [
      { provider_id: 'p_opus', display_name: 'Claude Opus', step_count: 13 },
      { provider_id: 'p_codex', display_name: 'GPT (codex)', step_count: 1 },
    ],
    unassigned_step_count: 0,
    totals: shortMode
      ? { design_sheets: 3, work_sets: 0, steps: 3 }
      : { design_sheets: 5, work_sets: 5, steps: 15 },
  }
  else data = { ok: true }
  return { data, status: 200, statusText: 'OK', headers: {}, config }
}

const params = new URLSearchParams(location.search)
const view = params.get('view') ?? 'dialog'
const locale = params.get('locale') ?? 'ko'
;(i18n.global.locale as unknown as { value: string }).value = locale

const App = {
  setup() {
    const visible = ref(true)
    setTimeout(async () => {
      await nextTick()
      await new Promise((resolve) => setTimeout(resolve, 150))
      await nextTick()
      if (view !== 'doc') {
        /* 시안과 같은 선택 상태(타입 8/8 · 프로바이더 6/14)를 만든다. */
        const selectAll = [...document.querySelectorAll('.wpc-sec-hd button')]
          .find((button) => /전체|All/.test(button.textContent ?? '')) as HTMLButtonElement | undefined
        selectAll?.click()
        await nextTick()
        const provItems = [...document.querySelectorAll('.wpc-prov-list .wpc-check')] as HTMLElement[]
        for (const index of [0, 1, 2, 3, 6, 9]) provItems[index]?.click()
        await nextTick()
      }
      await new Promise((resolve) => setTimeout(resolve, 80))
      const list = document.querySelector('.wp-step-list') as HTMLElement | null
      const editor = document.querySelector('.wp-editor') as HTMLElement | null
      const summary = document.querySelector('.wp-sum-cards') as HTMLElement | null
      const firstRow = document.querySelector('.wp-step-row') as HTMLElement | null
      const firstProviderSelect = document.querySelector('.wp-step-row .aip-select-input') as HTMLElement | null
      const listRect = list?.getBoundingClientRect()
      const editorRect = editor?.getBoundingClientRect()
      const summaryRect = summary?.getBoundingClientRect()
      const metrics = {
        locale,
        shortMode,
        viewport: { width: innerWidth, height: innerHeight },
        layoutClass: [...(editor?.classList ?? [])].find((name) => name.startsWith('wp-layout-')) ?? null,
        quantityCardCount: document.querySelectorAll('.wp-qty-card').length,
        stepRowCount: document.querySelectorAll('.wp-step-row').length,
        stepListClientHeight: list?.clientHeight ?? 0,
        stepListScrollHeight: list?.scrollHeight ?? 0,
        stepListScrollable: !!list && list.scrollHeight > list.clientHeight,
        gridTemplateColumns: firstRow ? getComputedStyle(firstRow).gridTemplateColumns : '',
        firstStepRowHeight: firstRow?.getBoundingClientRect().height ?? 0,
        providerSelectPadding: firstProviderSelect ? getComputedStyle(firstProviderSelect).padding : '',
        providerSelectFontSize: firstProviderSelect ? getComputedStyle(firstProviderSelect).fontSize : '',
        providerRobotIconCount: document.querySelectorAll('.wp-defaults-row .aip-select-icon,.wp-step-row .aip-select-icon').length,
        summaryInsideCard: !!summaryRect && !!editorRect && summaryRect.top >= (listRect?.bottom ?? 0) && summaryRect.bottom <= editorRect.bottom,
        removedExtrasAbsent: !document.querySelector('.wp-qty-add,.wp-filter-select,.wp-step-table,.wp-step-cards,.wp-ai-badge,.wp-unassigned-badge,.wp-unavailable-badge'),
      }
      const output = document.createElement('pre')
      output.id = 'deck-metrics'
      output.textContent = JSON.stringify(metrics)
      if (new URLSearchParams(location.search).get('metrics') === '1') {
        document.head.replaceChildren()
        document.body.replaceChildren(output)
      } else document.body.appendChild(output)
      ;(window as any).__deckMetrics = metrics
      ;(window as any).__deckReady = true
    }, 0)
    return () =>
      view === 'doc'
        ? h('div', { style: 'padding:18px; background:var(--bg,#f1f5f9); min-height:100vh;' }, [
            h(WorkPlanEditor, { docId: DOC_ID, projectId: 'flowgate' }),
          ])
        : h(WorkPlanCreateDialog, {
            visible: visible.value,
            parentDocId: ROOT_ID,
            projectId: 'flowgate',
            groupId: 'flowgate.default.0395',
          })
  },
}

const app = createApp(App)
const pinia = createPinia()
setActivePinia(pinia)
useProjectStore().currentProjectId = 'flowgate'
app.use(pinia)
app.use(i18n)
app.mount('#app')
