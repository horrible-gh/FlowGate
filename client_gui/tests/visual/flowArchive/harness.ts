// T-c browser harness: real Toolbar, Explorer, context menu and HTTP APIs; no archive HTTP mocks.
import { createApp, h, nextTick, ref } from 'vue'
import { createPinia } from 'pinia'
import api from '@shared/api'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import { useProjectStore } from '@/features/project'
import DesignerCenter from '@/features/designer/center/DesignerCenter.vue'
import { createDesignerCenterSession } from '@/features/designer/center/designerCenterSession'
import type { FlowGraph } from '@/features/designer/center/graph/flowGraph'
import '@vue-flow/core/dist/style.css'
import '@vue-flow/core/dist/theme-default.css'
import DesignerToolbar from '@/features/designer/top/DesignerToolbar.vue'
import ProjectExplorer from '@/features/designer/projectExplorer/ProjectExplorer.vue'
import DesignerContextMenu from '@/features/designer/contextMenu/DesignerContextMenu.vue'
import { useDesignerContextMenu } from '@/features/designer/contextMenu/attach/designerMenuConnector'
import '@/app/styles/theme.css'

const FLOW = 'archive.ui.0001'
api.defaults.baseURL = location.origin
sessionStorage.setItem('fg_access_token', 'user')
window.__accessToken__ = 'user'
registerAppMessages(i18n)
;(i18n.global.locale as unknown as { value: string }).value = 'ko'
const pinia = createPinia()
const store = useProjectStore(pinia)
store.projects = [{ id: 'archive', name: 'Archive test', description: null, createdAt: null, updatedAt: null }] as never
store.setCurrentProject('archive')
const eligible = ref(false)
const refresh = ref(0)
const actions: string[] = []
let actionError: unknown
let writes = 0
// The graph fixture makes editing possible when live; archive/tree requests use the real server.
const graph = {
  flowId: FLOW, ownership: 'graph', viewMode: 'draft', latestRevisionNo: 1,
  draft: { version: 1, editable: true, confirmable: true, blockedReason: null, baseRevisionNo: 1 },
  nodes: [{ id: 'a', kind: 'task', title: '보관된 카드', x: 0, y: 0, movable: true, structureEditable: true,
    definition: { nodeType: 'document', docType: 'T', rootBound: false, assignee: null, paletteItemId: null } }],
  edges: [], run: { runId: 'current', status: 'idle', lastEventSeq: 1 },
} as unknown as FlowGraph
const center = createDesignerCenterSession({
  source: { isSample: false, loadGraph: async () => graph,
    loadRun: async () => ({ runId: 'current', status: 'idle', openNodeKeys: [], nodeRuns: [], viewerCanApprove: true }),
    saveLayout: async () => { writes++; return { ignoredNodeIds: [] } },
  },
  readArchive: store.readFlowArchive,
})
center.setProject('archive')
async function change(id: string, archived: boolean) {
  try {
    await store.setFlowArchived('archive', id, archived)
    center.setFlowArchived(id, archived)
    actions.push(archived ? 'archive' : 'restore')
    eligible.value = !archived
    refresh.value++
  } catch (error) { actionError = String(error) }
}
const app = createApp({ setup() {
  const connector = useDesignerContextMenu({
    center,
    projectId: () => 'archive', canSelectAll: () => false, selectAll() {}, openGroupCreate() {},
    openFlowCreate() {}, selectPalette() {}, arrange: { groups: [], enabled: () => false, run() {} },
    archive: { read: store.readFlowArchive, busy: (id) => !!store.archiveBusy[id],
      archiveFlow: (id) => change(id, true), restoreFlow: (id) => change(id, false) },
  })
  return () => h('div', [
    h(DesignerToolbar, { runPhase: 'idle', density: 'normal', zoom: 100,
      targets: { project: true, group: true, flow: center.state.tabs.length > 0, readOnly: center.isFlowReadOnly(FLOW) },
      canArchive: eligible.value, archiveBusy: !!store.archiveBusy[FLOW], onArchive: () => change(FLOW, true) }),
    h('div', { style: 'width:340px;height:260px;margin-top:12px' }, [h(ProjectExplorer, {
      projectId: 'archive', refreshToken: refresh.value, includeTerminal: false,
      onActivateFlow: (flow: { flowId: string; flowName: string }) => center.openFlow({ projectId: 'archive', ...flow }),
    })]),
    h('div', { style: 'width:840px;height:310px;position:absolute;left:378px;top:240px;display:flex' }, [h(DesignerCenter, { center })]),
    h(DesignerContextMenu, { host: connector.host, onRequest: connector.run }),
  ])
} })
app.use(pinia).use(i18n).mount('#real')
const q = (s: string) => document.querySelector<HTMLElement>(s)
const delay = () => new Promise((resolve) => setTimeout(resolve, 25))
async function until(check: () => unknown, label: string) {
  for (let i = 0; i < 600; i++) { if (check()) return; if (actionError) throw Error(String(actionError)); await delay() }
  throw Error('Timeout: ' + label)
}
function rect(el: Element) {
  const r = el.getBoundingClientRect()
  return { x: r.x, y: r.y, width: r.width, height: r.height, right: r.right }
}
function measure(button: Element, density?: Element | null) {
  const css = getComputedStyle(button), svg = button.querySelector('svg')!
  return { button: rect(button), icon: rect(svg), label: button.textContent?.trim(),
    font: css.fontSize, gap: css.gap, lineHeight: css.lineHeight, padding: css.padding,
    border: css.borderWidth, radius: css.borderRadius, color: css.color, strokeWidth: getComputedStyle(svg).strokeWidth,
    density: density ? rect(density) : null,
    svg: svg.innerHTML.replace(/ data-v-[\w]+=""/g, '') }
}
async function run() {
  eligible.value = (await store.readFlowArchive(FLOW)).eligible
  if (!eligible.value) throw Error('Completed run did not qualify')
  await nextTick()
  await document.fonts.ready
  const frame = document.getElementById('mockframe') as HTMLIFrameElement
  await until(() => frame.contentDocument?.querySelector('.archive'), 'mockup')
  const button = q('[data-test="designer-tool-archive"]') as HTMLButtonElement
  const density = q('[data-test="designer-tool-density"]')!
  const mockButton = frame.contentDocument!.querySelector('.archive')!
  const real = measure(button, density), mock = measure(mockButton)
  const adjacent = density.closest('.designer-tool-group')!.nextElementSibling === button.closest('.designer-tool-group')
  if (!adjacent || real.button.x < real.density!.right || real.label !== mock.label) throw Error('Archive placement/label mismatch')
  for (const key of ['width', 'height'] as const) {
    if (real.button[key] !== mock.button[key] || real.icon[key] !== mock.icon[key]) throw Error(`Archive ${key} mismatch`)
  }
  for (const key of ['font', 'gap', 'lineHeight', 'padding', 'border', 'radius', 'color', 'strokeWidth', 'svg'] as const) {
    if (real[key] !== mock[key]) throw Error(`Archive style mismatch: ${key}`)
  }
  const idleRunButtons = document.querySelectorAll('[data-test="designer-tool-run"]').length
  button.click()
  await until(() => actions.length === 1 && !store.archiveBusy[FLOW], 'archive POST')
  const hidden = await store.fetchGroupTree('archive', { force: true, includeTerminal: true })
  if (hidden.some((n) => n.id.startsWith(FLOW))) throw Error('Archived flow/document leaked into normal tree')
  await until(() => !q('[data-test="project-explorer-loading"]'), 'hidden tree')
  const toggle = q('[data-test="project-explorer-include-archived"]') as HTMLInputElement
  toggle.click()
  await until(() => store.getCachedGroupTree('archive', false, true)?.some((n) => n.id === FLOW), 'archive-inclusive tree')
  await nextTick()
  for (const kind of ['project', 'group']) {
    const row = q(`[data-test="project-explorer-${kind}"]`)
    if (row && row.getAttribute('aria-expanded') !== 'true') row.querySelector<HTMLElement>('.explorer-row')!.click()
    await nextTick()
  }
  await until(() => q('[data-test="project-explorer-flow"]'), 'dimmed archived row')
  const row = q('[data-test="project-explorer-flow"]')!
  const archivedOpacity = getComputedStyle(row).opacity
  if (!(Number(archivedOpacity) < 1)) throw Error('Archived row is not dimmed')
  row.querySelector<HTMLElement>('.explorer-row')!.dispatchEvent(new MouseEvent('dblclick', { bubbles: true }))
  await until(() => q('[data-test="designer-flow-tab"]') && q('.vue-flow__node'), 'archived tab and canvas')
  if (!center.isFlowReadOnly(FLOW) || center.runMarks(FLOW).lock !== 'archived') throw Error('Archived canvas unlocked')
  if (q('.vue-flow__node')!.classList.contains('draggable')) throw Error('Archived card can be dragged')
  if ([...document.querySelectorAll<HTMLButtonElement>('[data-test="designer-tool-run"]')].some(button => !button.disabled)) throw Error('Archived run command enabled')
  center.selectNode(FLOW, 'a')
  if (center.activeSelection.value?.titleEdit) throw Error('Archived title editable')
  center.commitViewport(FLOW, { x: 30, y: 40, zoom: 1.2 })
  await new Promise(resolve => setTimeout(resolve, 600))
  if (writes) throw Error('Read-only viewport wrote to server')
  row.dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, cancelable: true, clientX: 170, clientY: 240 }))
  await until(() => q('[data-key="flow.restore"]')?.getAttribute('aria-disabled') === 'false', 'restore context menu')
  q('[data-key="flow.restore"]')!.click()
  await until(() => actions.length === 2 && !store.archiveBusy[FLOW], 'restore POST')
  if (center.isFlowReadOnly(FLOW) || center.state.tabs.length !== 1) throw Error('Restore did not retain and unlock tab')
  toggle.click()
  const restored = await store.fetchGroupTree('archive', { force: true, includeTerminal: false })
  if (!restored.some((n) => n.id === FLOW && !n.archivedAt)) throw Error('Restored terminal flow hidden')
  // Independent fresh HTTP read is the data authority used by an F5 restore.
  store.invalidateGroupTree('archive')
  const reread = await store.fetchGroupTree('archive', { force: true, includeTerminal: false })
  if (!reread.some((n) => n.id === FLOW)) throw Error('Restore did not persist')
  await until(() => q('[data-test="project-explorer-flow"]') && !q('.is-archived'), 'restored explorer')
  return { adjacent, real, mock, archivedOpacity, actions, normalTreeHidden: true, restoredTerminalVisible: true,
    archivedOpenedReadOnly: true, restoredOpenTab: true, layoutWrites: writes, idleRunButtons, archivedRunCommandsDisabled: true, ua: navigator.userAgent, dpr: devicePixelRatio }
}
run().then((result) => {
  document.getElementById('fg-measure')!.textContent = `FGMEASURE${JSON.stringify(result)}FGMEASURE`
  document.title = 'FG_DONE'
}).catch((error) => {
  document.getElementById('fg-measure')!.textContent = `FGMEASURE${JSON.stringify({ error: String(error?.stack ?? error), actions })}FGMEASURE`
})
