import { createApp, h, nextTick, ref } from 'vue'
import { createPinia } from 'pinia'
import i18n from '../../shared/i18n'
// The real global sheet — without it .modal-bg / .doc-tag / .btn are unstyled and the
// picker would be judged on a layout the product never shows.
import '../../shared/app.css'
import DocWorkflow from '../../src/main/components/DocWorkflow.vue'
import WorkflowDecisionModal from '../../src/main/components/WorkflowDecisionModal.vue'
import { useDocTypeStore } from '../../src/main/stores/docTypeStore'

// flowgate.default.0395 T0021 — "[워크플로 시퀀스] 에 나와야하는거 아닌가?"
// Renders the real components in a browser so the two claims can be measured rather
// than argued: (1) [작업계획 생성] is in the sequence section and stays there after the
// workflow is finished, (2) the type picker offers 작업계획 and no longer offers 커밋.

const ROOT_ID = 'flowgate.default.0395.0001-R'
const LABELS: Record<string, string> = {
  R: '요건정의', WP: '작업계획', DS: '설계지시', D: '기본설계', P: '프로토콜',
  L: '로직설계', DB: 'DB 설계', N: '조사지시', NR: '조사레포트', T: '작업지시',
  TR: '작업레포트', TS: '테스트지시', TSR: '테스트레포트', M: '메모', CH: '대화',
  AC: '최종승인', C: '커밋',
}

function step(code: string, visual: string) {
  const className = {
    done: 'done',
    highlight: 'wf-next-action dip-step-clickable dip-step-active',
    rejected: 'wf-rejected dip-step-rejected',
    future: 'future dip-step-disabled',
  }[visual] as string
  const iconClass = visual === 'done' ? 'check-circle' : visual === 'rejected' ? 'x-circle' : 'circle'
  return { code, visual, className, iconClass }
}

const LIVE = [step('R', 'done'), step('WP', 'highlight'), step('T', 'future'), step('AC', 'future')]
const DONE = [step('R', 'done'), step('WP', 'done'), step('T', 'done'), step('AC', 'done')]
const REJECTED = [step('R', 'done'), step('T', 'rejected'), step('AC', 'future')]

i18n.global.locale.value = 'ko'

const App = {
  setup() {
    const metrics = ref('waiting')
    const store = useDocTypeStore()
    store.labelMap = LABELS
    store.loaded = true

    setTimeout(async () => {
      await nextTick()
      const sections = [...document.querySelectorAll('.wf-section')]
      const paletteNames = [...document.querySelectorAll('.wdm-type-btn .wdm-type-name')]
        .map((el) => (el.textContent ?? '').trim())
      const paletteTags = [...document.querySelectorAll('.wdm-type-btn .doc-tag')]
        .map((el) => (el.textContent ?? '').trim())
      const wpButtons = [...document.querySelectorAll('.wf-wp-btn')] as HTMLElement[]
      const result = {
        // 1) the section-level entry point, per state
        createButtonPerState: sections.map((section, index) => ({
          state: ['live', 'wf_done', 'rejected', 'member-doc(T)'][index],
          hasCreateButton: !!section.querySelector('.wf-wp-btn'),
          label: (section.querySelector('.wf-wp-btn') as HTMLElement | null)?.innerText.trim() ?? null,
        })),
        createButtonsVisible: wpButtons.every((el) => {
          const r = el.getBoundingClientRect()
          return r.width > 0 && r.height > 0
        }),
        // the button must not overlap the [시퀀스 수정] button beside it
        buttonsDoNotOverlap: sections.every((section) => {
          const edit = section.querySelector('.wf-edit-btn') as HTMLElement | null
          const wp = section.querySelector('.wf-wp-btn') as HTMLElement | null
          if (!edit || !wp) return true
          const a = edit.getBoundingClientRect()
          const b = wp.getBoundingClientRect()
          return a.right <= b.left + 0.5 || b.right <= a.left + 0.5
        }),
        // 2) a WP step is drawn in the strip with its Korean label
        wpStepLabels: [...document.querySelectorAll('.wf-step .s-lbl')]
          .map((el) => (el.textContent ?? '').trim())
          .filter((text) => text === '작업계획').length,
        // 3) the type picker
        paletteTags,
        paletteNames,
        hasWorkPlanInPalette: paletteTags.includes('WP'),
        hasCommitInPalette: paletteTags.includes('C'),
        commitTextAnywhere: (document.body.innerText.match(/커밋/g) ?? []).length,
      }
      metrics.value = JSON.stringify(result, null, 1)
      document.body.dataset.regressionReady = 'true'
    }, 900)

    return () => [
      h('div', { class: 'reg-grid' }, [
        h('section', { class: 'reg-case' }, [
          h('h3', {}, '① 워크플로 진행 중 (head = 작업계획)'),
          h(DocWorkflow, {
            tab: { id: ROOT_ID, typeCode: 'R' }, workflowDecided: true,
            parentRDocId: ROOT_ID, stepStates: LIVE, canNextAction: true,
          }),
        ]),
        h('section', { class: 'reg-case' }, [
          h('h3', {}, '② 워크플로 종료(wf_done) — 예전에는 단추가 사라지던 구간'),
          h(DocWorkflow, {
            tab: { id: ROOT_ID, typeCode: 'R' }, workflowDecided: true,
            parentRDocId: ROOT_ID, stepStates: DONE, canNextAction: false,
          }),
        ]),
        h('section', { class: 'reg-case' }, [
          h('h3', {}, '③ 현재 단계 반려 — 역시 사라지던 구간'),
          h(DocWorkflow, {
            tab: { id: ROOT_ID, typeCode: 'R' }, workflowDecided: true,
            parentRDocId: ROOT_ID, stepStates: REJECTED, canNextAction: false,
          }),
        ]),
        h('section', { class: 'reg-case' }, [
          h('h3', {}, '④ 요건정의가 아닌 문서(작업지시) — 서버가 422로 거절하던 자리'),
          h(DocWorkflow, {
            tab: { id: 'flowgate.default.0395.0021-T', typeCode: 'T' }, workflowDecided: true,
            parentRDocId: ROOT_ID, stepStates: LIVE, canNextAction: true,
          }),
        ]),
      ]),
      h(WorkflowDecisionModal, { visible: true, docClass: 'R' }),
      h('pre', { id: 'regression-metrics' }, metrics.value),
    ]
  },
}

const style = document.createElement('style')
style.textContent = `
  *{box-sizing:border-box} html,body,#app{margin:0;width:100%;font-family:'Malgun Gothic',Arial,sans-serif}
  body{background:#eef2f7}
  .reg-grid{display:flex;flex-direction:column;gap:10px;padding:14px;width:820px}
  .reg-case{background:#fff;border:1px solid #cbd5e1;border-radius:8px;padding:12px}
  .reg-case h3{margin:0 0 8px;font-size:.8rem;color:#475569}
  .sec-title{font-size:.72rem;font-weight:700;color:#334155;margin-bottom:8px}
  .sec-title::after{content:'';flex:1;height:1px;background:#e2e8f0;margin:0 8px}
  .wf-flow{display:flex;flex-wrap:wrap;align-items:center;gap:4px}
  .wf-unit{display:flex;align-items:center;gap:4px}
  .wf-step{display:inline-flex;align-items:center;gap:5px;padding:4px 10px;border:1px solid #cbd5e1;border-radius:999px;font-size:.74rem;background:#fff}
  .wf-step.done{background:#dcfce7;border-color:#86efac;color:#166534}
  .wf-step.wf-next-action{background:#dbeafe;border-color:#93c5fd;color:#1d4ed8;font-weight:700}
  .wf-step.wf-rejected{background:#fee2e2;border-color:#fca5a5;color:#b91c1c}
  .wf-step.future{opacity:.55}
  #regression-metrics{margin:0;padding:8px;background:#111;color:#0f0;font-size:11px;white-space:pre-wrap}
`
document.head.appendChild(style)

createApp(App).use(createPinia()).use(i18n).mount('#app')
