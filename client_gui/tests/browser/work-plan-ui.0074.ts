/** Actual Vue components in Chromium, isolated API fixture; no production data is touched. */
import { createApp, h, nextTick } from 'vue'
import axios from 'axios'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import { DOC, FLOW, planBody, readAnswer } from '../features/workPlanWindow/workPlanFixtures'
import '@/app/styles/theme.css'

const saved = sessionStorage.getItem('wp-ui-body')
let body: any = saved ? JSON.parse(saved) : planBody()
body.steps[0].pre_instruction_text = '기존 설계지시'
let revision = 2
const calls: any[] = []
axios.defaults.adapter = async (config) => {
  const path = config.url ?? ''
  const method = config.method
  const payload = typeof config.data === 'string' ? JSON.parse(config.data) : config.data
  calls.push({ path, method, payload })
  let data: any = { ok: true }
  if (path.includes('/work-plan/deploy')) data = {
    ok: true, wp_revision_no: revision, deployed_count: 0, target_count: 4, deployable: true, ownership: 'graph', cards: [],
    modes: { first: true, add: false, cleanup: false },
  }
  else if (path.endsWith('/work-plan')) {
    if (method === 'put') {
      body = payload.body
      revision += 1
      sessionStorage.setItem('wp-ui-body', JSON.stringify(body))
    }
    const empty = path.includes('empty-WP')
    data = readAnswer({ body: empty ? { ...body, steps: [] } : body, revision_no: revision, step_execution_status: [],
      totals: { design_sheets: 1, work_sets: 3, steps: body.steps.length }, unassigned_steps: 0 })
  }
  else if (path.includes('/ai-invoke/active')) data = { active: false }
  else if (path.includes('/settings')) data = { values: { instruction: 'omit' }, settings: { instruction: 'omit' }, fields: [] }
  else if (path.includes('/documents/')) data = { doc_id: 'design-instruction', doc_type: 'DS', type_code: 'DS', title: '기존 설계지시', status: 'open', content: '기존 설계지시', body: '기존 설계지시', items: [] }
  return { config, data, status: 200, statusText: 'OK', headers: {} }
}
const { DocumentWindows, documentWindowList, closeAllDocumentWindows } = await import('@/features/documentWindow')
const { openWorkPlanEditWindow, closeAllWorkPlanEditWindows } = await import('@/features/workPlanWindow')
const WorkPlanTab = (await import('@/features/designer/inspector/features/workPlan/WorkPlanTab.vue')).default
const DesignSlots = (await import('@/features/designer/inspector/features/designCard/DesignSlots.vue')).default
registerAppMessages(i18n)
i18n.global.locale.value = 'ko'
const base = {
  projectId: 'flowgate', flowId: FLOW, nodeId: 'gn_wp', nodeType: 'document',
  docType: 'WP', docId: 'empty-WP', docStatus: 'open', docReviewStatus: null, nodeRun: null,
  readOnly: null, editLock: null, ownership: 'graph', cardDocuments: [], flowRevision: null,
}
const design = { ...base, nodeId: 'gn_design', docId: 'design-report', docType: 'D', cardDocuments: [
  { slot: 'instruction', docType: 'DS', docId: 'design-instruction', author: 'instructed', status: 'open', docReviewStatus: null, hasBody: true, revisionNo: 0 },
  { slot: 'report', docType: 'D', docId: 'design-report', author: 'ai', status: 'open', docReviewStatus: null, hasBody: true, revisionNo: 0 },
] }
createApp({ render: () => h('div', { style: 'padding:20px;color:var(--fg2-text)' }, [
  h('div', { style: 'width:360px', id: 'inspector-fixture' }, [
    h(WorkPlanTab, { target: base as any }), h(DesignSlots, { target: design as any }),
  ]),
  h(DocumentWindows),
]) }).use(i18n).mount('#app')
const open = () => openWorkPlanEditWindow({ docId: DOC, projectId: 'flowgate', title: '작업계획' })
;(window as any).__fixture = {
  open, close: () => { closeAllWorkPlanEditWindows(); closeAllDocumentWindows() },
  body: () => body, calls: () => calls, documents: () => documentWindowList().map((w) => w.docId),
}
await nextTick()
;(window as any).__harnessReady = true
