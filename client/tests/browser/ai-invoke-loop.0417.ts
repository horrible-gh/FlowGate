/**
 * flowgate.default.0417 — 시안(u3digra2 v6 화면 2~5) 대조용 렌더 하네스.
 *
 * 실제 AiInvokeDialog 를 review 스코프로 그대로 띄운다. 시안 페이지와 나란히 열어
 * 요소 유무와 수치를 대조하기 위한 화면이며, 제품 코드는 건드리지 않는다.
 *
 *   /tests/browser/ai-invoke-loop.0417.html?mode=single   → 시안 화면 2 (현재)
 *   /tests/browser/ai-invoke-loop.0417.html?tab=review    → 시안 화면 3
 *   /tests/browser/ai-invoke-loop.0417.html?tab=rework    → 시안 화면 4
 *   /tests/browser/ai-invoke-loop.0417.html?tab=stop      → 시안 화면 5
 *   /tests/browser/ai-invoke-loop.0417.html?view=run      → 시안 화면 6 (실행 카드)
 */
import { createApp, h, nextTick, ref } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '../../shared/i18n'
import api from '../../shared/api'
import '../../shared/variables.css'
import '../../shared/app.css'
import AiInvokeDialog from '../../src/main/components/AiInvokeDialog.vue'
import AiInvokeInline from '../../src/main/components/AiInvokeInline.vue'
import { useAiInvokeRunsStore } from '../../src/main/stores/aiInvokeRuns'

const DOC_ID = 'flowgate.example.0099.0003-N'
const GROUP_ID = 'flowgate.example.0099'

/* 시안 셀렉트와 같은 3개 */
const providers = [
  { id: 'p_sonnet', name: 'Claude Sonnet 5 (기본)', kind: 'claude', exec_type: 'cli' },
  { id: 'p_gpt', name: 'GPT-5.1', kind: 'openai', exec_type: 'cli' },
  { id: 'p_gemini', name: 'Gemini 3 Pro', kind: 'gemini', exec_type: 'cli' },
]

api.defaults.adapter = async (config) => {
  const url = config.url ?? ''
  let data: any
  if (url.includes('/ai-invoke/providers')) data = { ok: true, project: 'flowgate', providers, default_provider_id: 'p_sonnet' }
  else data = { ok: true }
  return { data, status: 200, statusText: 'OK', headers: {}, config }
}

const params = new URLSearchParams(location.search)
const view = params.get('view') ?? 'dialog'
const mode = params.get('mode') ?? 'loop'
const tab = params.get('tab') ?? 'review'
const locale = params.get('locale') ?? 'ko'
;(i18n.global.locale as unknown as { value: string }).value = locale

function texts(query: string): string[] {
  return [...document.querySelectorAll(query)].map((node) => (node.textContent ?? '').trim())
}

function optionTexts(query: string): string[] {
  return texts(`${query} option`)
}

const App = {
  setup() {
    const visible = ref(true)
    setTimeout(async () => {
      await nextTick()
      await new Promise((resolve) => setTimeout(resolve, 150))
      await nextTick()
      if (view === 'run') {
        // 회차 표는 서버가 정본으로 내려준다(document_review_loop.history). 여기서는 서버가
        // build_document_review_loop_history 로 조립해 보내는 것과 같은 모양의 응답 한 번을
        // 그대로 흘려보낸다 — 새로고침 직후의 카드가 바로 이 상태다.
        const store = useAiInvokeRunsStore()
        const base = { run_id: 'aiv_deck', group_id: GROUP_ID, doc_ref: DOC_ID, status: 'running' }
        const history = [
          { round_no: 1, stage: 'review', result: 'issues', finding_count: 3, at: '2026-08-29T12:04:00+09:00' },
          { round_no: 1, stage: 'rework', result: 'complete', revision_no: 4, at: '2026-08-29T12:19:00+09:00' },
          { round_no: 2, stage: 'review', result: 'issues', finding_count: 1, at: '2026-08-29T12:26:00+09:00' },
          { round_no: 2, stage: 'rework', result: 'complete', revision_no: 5, at: '2026-08-29T12:38:00+09:00' },
          { round_no: 3, stage: 'review', result: 'passed', finding_count: 0, at: '2026-08-29T12:44:00+09:00' },
        ]
        store.trackStarted({
          ...base,
          document_review_loop: { round_no: 3, current_stage: 'review', history },
        })
        await nextTick()
        store.trackFinished({
          ...base, status: 'finished', outcome: 'complete',
          document_review_loop: {
            round_no: 3, current_stage: 'stopped', stop_reason: 'review_passed', history,
          },
        })
        await nextTick()
      }
      if (view === 'dialog' && mode === 'loop') {
        const loop = document.querySelector('input[type="radio"][value="loop"]') as HTMLInputElement | null
        if (loop) {
          loop.checked = true
          loop.dispatchEvent(new Event('change'))
        }
        await nextTick()
        ;(document.querySelector(`[data-test="review-loop-tab-${tab}"]`) as HTMLButtonElement | null)?.click()
        await nextTick()
      }
      await new Promise((resolve) => setTimeout(resolve, 80))
      const box = document.querySelector('.modal-aiv') as HTMLElement | null
      const stopRow = document.querySelector('[data-test="review-loop-stop-row"]') as HTMLElement | null
      const activeTab = document.querySelector('.aiv-loop-tab--active') as HTMLElement | null
      const providerSelect = document.querySelector('.aiv-provider-row select') as HTMLSelectElement | null
      const metrics = {
        locale,
        mode,
        tab,
        modalWidth: box?.getBoundingClientRect().width ?? 0,
        modalLoopClass: !!box?.classList.contains('modal-aiv--loop'),
        bodyFont: getComputedStyle(document.body).fontFamily,
        singleRadioCount: document.querySelectorAll('input[type="radio"][value="single"]').length,
        loopRadioCount: document.querySelectorAll('input[type="radio"][value="loop"]').length,
        modeTitles: texts('.aiv-mode-title'),
        modeDescs: texts('.aiv-mode-desc'),
        newTag: texts('.aiv-mode-new-tag'),
        providerRowPresent: !!providerSelect,
        providerSelectDisabled: !!providerSelect?.disabled,
        flowSteps: texts('.rlp-step'),
        flowLoop: texts('.rlp-loop'),
        tabLabels: texts('.aiv-loop-tab'),
        activeTabLabel: activeTab?.textContent?.trim() ?? '',
        activeTabUnderline: activeTab ? getComputedStyle(activeTab).borderBottomColor : '',
        panelLabels: texts('.aiv-loop-panel .aiv-loop-label'),
        intro: texts('.aiv-loop-intro'),
        sectionTitle: texts('.aiv-loop-section-title'),
        toggleTitles: texts('.aiv-loop-toggle-title'),
        toggleDescs: texts('.aiv-loop-toggle-desc'),
        summary: texts('.aiv-loop-summary'),
        countOptions: optionTexts('[data-test="review-loop-review-count"]'),
        criteriaOptions: optionTexts('[data-test="review-loop-criteria"]'),
        reworkTimeoutOptions: optionTexts('[data-test="review-loop-rework-timeout"]'),
        restartOptions: optionTexts('[data-test="review-loop-failure-restart"]'),
        totalTimeoutOptions: optionTexts('[data-test="review-loop-total-timeout"]'),
        messageInputTag: (document.querySelector('[data-test="review-loop-rework-message"]') as HTMLElement | null)?.tagName ?? '',
        messagePlaceholder: (document.querySelector('[data-test="review-loop-rework-message"]') as HTMLInputElement | null)?.placeholder ?? '',
        runRows: texts('[data-test="review-loop-history-row"] .rlr-name'),
        runBadges: texts('[data-test="review-loop-history-row"] .rlr-badge'),
        runTimes: texts('[data-test="review-loop-history-row"] .rlr-time'),
        runStopRow: stopRow?.textContent?.replace(/\s+/g, ' ').trim() ?? '',
        runStopRowClass: [...(stopRow?.classList ?? [])].join(' '),
        runStopBadgeBg: stopRow ? getComputedStyle(stopRow.querySelector('.rlr-badge') as HTMLElement).backgroundColor : '',
        horizontalOverflow: document.documentElement.scrollWidth - document.documentElement.clientWidth,
      }
      const output = document.createElement('pre')
      output.id = 'deck-metrics'
      output.textContent = JSON.stringify(metrics)
      if (params.get('metrics') === '1') {
        document.head.replaceChildren()
        document.body.replaceChildren(output)
      } else document.body.appendChild(output)
      ;(window as any).__deckMetrics = metrics
      ;(window as any).__deckReady = true
    }, 0)
    return () =>
      view === 'run'
        ? h('div', { style: 'padding:18px; background:var(--bg,#f1f5f9); min-height:100vh;' }, [
            h(AiInvokeInline, { groupId: GROUP_ID }),
          ])
        : h(AiInvokeDialog, {
        visible: visible.value,
        project: 'flowgate',
        module: 'default',
        group: '0099',
        docRef: DOC_ID,
        actionScope: 'review',
        continuationInstructionMode: 'auto_approved',
      })
  },
}

const app = createApp(App)
const pinia = createPinia()
setActivePinia(pinia)
app.use(pinia)
app.use(i18n)
app.mount('#app')
