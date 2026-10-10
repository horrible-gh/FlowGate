/**
 * flowgate.v02.0036 — bottom Inspector Frame (0036-R §4 / §7, 0035 v7). The Frame is mounted
 * on its own with plain Graph Nodes, and inside the real Center column with a real Center
 * session (fake Graph source) whose Workspace is stubbed, so no Canvas is needed: selecting a
 * Node through the session must reach the Inspector.
 *
 * flowgate.v02.0048 T#1 (D#1 §3.3, F4): the Frame takes the selection context and lays out a
 * feature registry; the tab / placeholder / `has-settings` / slot cases now run on a fixture
 * registry with the same intent — fixed tab order, only the trailing 설정 may be missing, the
 * selected tab is kept, a missing 설정 sends the selection back to 문서, the 360px width.
 */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { defineComponent, h } from 'vue'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import { createDesignerCenterSession } from '@/features/designer/center/designerCenterSession'
import type { FlowGraph, FlowGraphNode, FlowGraphSelection } from '@/features/designer/center/graph/flowGraph'
import type { FlowGraphSource } from '@/features/designer/center/graph/flowGraphSource'
import DesignerInspector from '@/features/designer/inspector/DesignerInspector.vue'
import { createInspectorRegistry, type InspectorRegistration } from '@/features/designer/inspector/inspectorRegistry'
import {
  DEFAULT_INSPECTOR_TAB,
  INSPECTOR_TAB_GAP,
  INSPECTOR_TAB_MIN_WIDTH,
  INSPECTOR_TAB_STRIP_PADDING_X,
  INSPECTOR_TABS,
  inspectorTabStripMinWidth,
  inspectorTabsWith,
  resolveInspectorTab,
} from '@/features/designer/inspector/inspectorTabs'
import DesignerCenterColumn from '@/features/designer/shell/DesignerCenterColumn.vue'
import {
  DESIGNER_BOTTOM_DOCK_COLUMNS,
  DESIGNER_BOTTOM_INSPECTOR_MIN_WIDTH,
  DESIGNER_BOTTOM_MINIMAP_MIN_WIDTH,
  DESIGNER_CENTER_MIN_WIDTH,
} from '@/features/designer/shell/designerLayout'

registerAppMessages(i18n)

const doc = (docId: string, typeCode: string) => ({ docId, typeCode, seq: 1, title: null, docReviewStatus: null, status: 'open', supersedes: [] })
const REQ: FlowGraphNode = { id: 'f:n1', kind: 'requirement', title: '로그인 토큰 만료 개선', state: 'done', x: 0, y: 0, document: doc('f.0001-R', 'R') }
const TASK: FlowGraphNode = { id: 'f:n2', kind: 'task', title: '토큰 갱신 로직 수정', state: 'in_progress', x: 260, y: 0, document: doc('f.0002-T', 'T') }
const ODD: FlowGraphNode = { id: 'f:n3', kind: 'future-kind', title: '새 종류', state: 'future-state', x: 520, y: 0 }

const FLOW_F: FlowGraph = { flowId: 'f', nodes: [REQ, TASK, ODD], edges: [], ownership: 'v01' }
const sel = (node: FlowGraphNode): FlowGraphSelection => ({ projectId: 'p', graph: FLOW_F, node })

// Fixture registry: one Content part per tab; 설정 only on 작업 cards (the R card has none), and a
// header meta / action part. Each part shows the Node it was given.
const tabPart = (tab: string) =>
  defineComponent({
    props: { target: { type: Object, required: true } },
    setup: (props) => () => h('div', { 'data-test': `injected-${tab}` }, (props.target as { title: string }).title),
  })
const entry = (fields: Partial<InspectorRegistration> & Pick<InspectorRegistration, 'key' | 'order'>): InspectorRegistration => ({
  requiresDocument: true,
  messages: { ko: {}, en: {}, ja: {} },
  ...fields,
})
const REGISTRY = createInspectorRegistry(
  Object.fromEntries(
    [
      entry({ key: 'documents', order: 500, tab: { key: 'documents', component: tabPart('documents') } }),
      entry({ key: 'qna', order: 700, tab: { key: 'qna', component: tabPart('qna') } }),
      entry({ key: 'attachments', order: 800, tab: { key: 'attachments', component: tabPart('attachments') } }),
      entry({ key: 'settings', order: 900, cardKinds: ['task'], tab: { key: 'settings', component: tabPart('settings') } }),
      entry({
        key: 'meta',
        order: 100,
        headerMeta: defineComponent({
          props: { target: { type: Object, required: true } },
          setup: (props) => () => h('span', { 'data-test': 'injected-meta' }, (props.target as { nodeId: string }).nodeId),
        }),
      }),
      entry({ key: 'action', order: 600, headerAction: defineComponent({ setup: () => () => h('button', { 'data-test': 'injected-action' }, 'go') }) }),
    ].map((e) => [`./features/${e.key}/register.ts`, { default: e }]),
  ),
)

let wrapper: VueWrapper<any> | null = null

beforeEach(() => {
  i18n.global.locale.value = 'ko'
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
})

function mountInspector(props: Record<string, unknown>) {
  wrapper = mount(DesignerInspector, { attachTo: document.body, props: { registry: REGISTRY, ...props }, global: { plugins: [i18n] } })
  return wrapper
}

const get = (w: VueWrapper<any>, test: string) => w.get(`[data-test="${test}"]`)
const tabs = (w: VueWrapper<any>) => w.findAll('[data-test="designer-inspector-tabs"] [role="tab"]')
const tabLabels = (w: VueWrapper<any>) => tabs(w).map((t) => t.text())
const activeTabLabel = (w: VueWrapper<any>) =>
  w.get('[data-test="designer-inspector-tabs"] [role="tab"][aria-selected="true"]').text()

async function clickTab(w: VueWrapper<any>, label: string) {
  const tab = tabs(w).find((t) => t.text() === label)
  if (!tab) throw new Error(`tab not rendered: ${label}`)
  await tab.trigger('click')
}

describe('Inspector tab rules', () => {
  it('lists 문서 · Q&A · 첨부 · 설정 in a fixed order whatever order the contributions come in', () => {
    expect(INSPECTOR_TABS).toEqual(['documents', 'qna', 'attachments', 'settings'])
    expect(inspectorTabsWith(['settings', 'attachments', 'documents', 'qna'])).toEqual(['documents', 'qna', 'attachments', 'settings'])
    expect(inspectorTabsWith(['qna', 'documents', 'attachments'])).toEqual(['documents', 'qna', 'attachments'])
    expect(inspectorTabsWith(['documents', 'documents', 'unknown'])).toEqual(['documents'])
    expect(inspectorTabsWith([])).toEqual([])
    expect(DEFAULT_INSPECTOR_TAB).toBe('documents')
  })

  it('keeps an available tab and falls back to the first shown tab (문서) otherwise', () => {
    expect(resolveInspectorTab('qna', inspectorTabsWith(['documents', 'qna', 'attachments']))).toBe('qna')
    expect(resolveInspectorTab('settings', INSPECTOR_TABS)).toBe('settings')
    expect(resolveInspectorTab('settings', inspectorTabsWith(['documents', 'qna', 'attachments']))).toBe('documents')
    expect(resolveInspectorTab('settings', inspectorTabsWith(['qna']))).toBe('qna')
    // No tab shown (a Node without a document): the selection is left for the next Node.
    expect(resolveInspectorTab('settings', [])).toBe('settings')
  })
})

describe('DesignerInspector — Node not selected', () => {
  // flowgate.v02.0042: the empty Inspector carries no guidance text.
  it('shows an empty area with no text and no header, tabs or Content area', () => {
    const w = mountInspector({ selection: null })
    expect(get(w, 'designer-inspector').attributes('data-selected')).toBe('false')
    expect(get(w, 'designer-inspector-empty').text()).toBe('')
    expect(get(w, 'designer-inspector').text()).toBe('')
    expect(w.find('[data-test="designer-inspector-header"]').exists()).toBe(false)
    expect(w.find('[data-test="designer-inspector-tabs"]').exists()).toBe(false)
    expect(w.find('[data-test="designer-inspector-body"]').exists()).toBe(false)
  })
})

describe('DesignerInspector — Node selected', () => {
  it('shows the header row from the Graph Node: kind, title and state', () => {
    const w = mountInspector({ selection: sel(TASK), registry: createInspectorRegistry({}) })
    expect(get(w, 'designer-inspector').attributes('data-selected')).toBe('true')
    expect(w.find('[data-test="designer-inspector-empty"]').exists()).toBe(false)
    expect(get(w, 'designer-inspector-header').attributes('data-kind')).toBe('task')
    expect(get(w, 'designer-inspector-kind').text()).toBe('작업')
    expect(get(w, 'designer-inspector-title').text()).toBe('토큰 갱신 로직 수정')
    expect(get(w, 'designer-inspector-title').attributes('title')).toBe('토큰 갱신 로직 수정')
    expect(get(w, 'designer-inspector-state').text()).toBe('진행 중')
    // No action is wired in this group: the actions area is not even rendered.
    expect(w.find('[data-test="designer-inspector-actions"]').exists()).toBe(false)
    expect(w.findAll('[data-test="designer-inspector-header"] button')).toHaveLength(0)
  })

  it('shows an unknown kind with the default look and omits an unknown state', () => {
    const w = mountInspector({ selection: sel(ODD) })
    expect(get(w, 'designer-inspector-header').attributes('data-kind')).toBe('unknown')
    expect(get(w, 'designer-inspector-kind').text()).toBe('노드')
    expect(w.find('[data-test="designer-inspector-state"]').exists()).toBe(false)
  })

  it('shows the four v7 tabs with 문서 selected and its registered Content — no placeholder', () => {
    const w = mountInspector({ selection: sel(TASK) })
    expect(tabLabels(w)).toEqual(['문서', 'Q&A', '첨부', '설정'])
    expect(activeTabLabel(w)).toBe('문서')
    const body = get(w, 'designer-inspector-body')
    expect(body.attributes('role')).toBe('tabpanel')
    expect(body.attributes('data-tab')).toBe('documents')
    expect(get(w, 'injected-documents').text()).toBe('토큰 갱신 로직 수정')
    expect(w.find('[data-test="designer-inspector-placeholder"]').exists()).toBe(false)
  })

  it('switches the Content area with the tab', async () => {
    const w = mountInspector({ selection: sel(TASK) })
    await clickTab(w, '첨부')
    expect(activeTabLabel(w)).toBe('첨부')
    expect(get(w, 'designer-inspector-body').attributes('data-tab')).toBe('attachments')
    expect(get(w, 'injected-attachments').exists()).toBe(true)
    expect(w.find('[data-test="injected-documents"]').exists()).toBe(false)
  })

  it('keeps the selected tab when another Node is selected and after a moment with no selection', async () => {
    const w = mountInspector({ selection: sel(TASK) })
    await clickTab(w, 'Q&A')
    await w.setProps({ selection: sel(REQ) })
    expect(get(w, 'designer-inspector-title').text()).toBe('로그인 토큰 만료 개선')
    expect(activeTabLabel(w)).toBe('Q&A')
    await w.setProps({ selection: null })
    await w.setProps({ selection: sel(TASK) })
    expect(activeTabLabel(w)).toBe('Q&A')
  })

  it('shows no 설정 for a Node no settings feature applies to and sends a 설정 selection back to 문서', async () => {
    const w = mountInspector({ selection: sel(TASK) })
    await clickTab(w, '설정')
    expect(activeTabLabel(w)).toBe('설정')
    await w.setProps({ selection: sel(REQ) })
    expect(tabLabels(w)).toEqual(['문서', 'Q&A', '첨부'])
    expect(activeTabLabel(w)).toBe('문서')
    // The fallback sticks: coming back to a Node with settings stays on 문서.
    await w.setProps({ selection: sel(TASK) })
    expect(tabLabels(w)).toEqual(['문서', 'Q&A', '첨부', '설정'])
    expect(activeTabLabel(w)).toBe('문서')
  })

  it('lays out the registered header parts and one Content panel per tab, given the target', async () => {
    const w = mountInspector({ selection: sel(TASK) })
    expect(get(w, 'injected-meta').text()).toBe('f:n2')
    expect(get(w, 'designer-inspector-actions').get('[data-test="injected-action"]').text()).toBe('go')
    expect(w.find('[data-test="injected-qna"]').exists()).toBe(false)
    await clickTab(w, 'Q&A')
    expect(get(w, 'injected-qna').text()).toBe('토큰 갱신 로직 수정')
  })

  it('shows no tab at all for a Node without a document (header only)', () => {
    const w = mountInspector({ selection: sel(ODD) })
    expect(get(w, 'designer-inspector-title').text()).toBe('새 종류')
    expect(w.find('[data-test="designer-inspector-tabs"]').exists()).toBe(false)
    expect(w.find('[data-test="designer-inspector-body"]').exists()).toBe(false)
  })
})

// --- Narrow width: the 360px Center minimum -------------------------------------------------
// jsdom has no layout engine, so the check is done on the numbers the Dock grid and the tab
// strip are built from (the CSS that consumes them is guarded in structure.0036).

/** Width of each bottom Dock track for a Center width, as CSS grid resolves `minmax(min, n fr)`. */
function bottomDockTracks(centerWidth: number) {
  const inspectorMin = DESIGNER_BOTTOM_INSPECTOR_MIN_WIDTH
  const minimapMin = DESIGNER_BOTTOM_MINIMAP_MIN_WIDTH
  let inspector = (centerWidth * 7) / 10
  let minimap = (centerWidth * 3) / 10
  if (inspector < inspectorMin) [inspector, minimap] = [inspectorMin, centerWidth - inspectorMin]
  if (minimap < minimapMin) [inspector, minimap] = [centerWidth - minimapMin, minimapMin]
  return { inspector, minimap }
}

describe('Inspector at the 360px Center minimum', () => {
  it('the Dock grid fits both minimums into the Center minimum and reads them from the layout', () => {
    expect(DESIGNER_BOTTOM_INSPECTOR_MIN_WIDTH + DESIGNER_BOTTOM_MINIMAP_MIN_WIDTH).toBeLessThanOrEqual(DESIGNER_CENTER_MIN_WIDTH)
    expect(DESIGNER_BOTTOM_DOCK_COLUMNS).toBe(
      `minmax(${DESIGNER_BOTTOM_INSPECTOR_MIN_WIDTH}px, 7fr) minmax(${DESIGNER_BOTTOM_MINIMAP_MIN_WIDTH}px, 3fr)`,
    )
  })

  it.each([DESIGNER_CENTER_MIN_WIDTH, 400, 480, 900])('a %ipx Center leaves the Inspector room for every v7 tab', (center) => {
    const { inspector, minimap } = bottomDockTracks(center)
    expect(inspector + minimap).toBeCloseTo(center)
    expect(minimap).toBeGreaterThanOrEqual(DESIGNER_BOTTOM_MINIMAP_MIN_WIDTH)
    expect(inspector).toBeGreaterThanOrEqual(inspectorTabStripMinWidth(INSPECTOR_TABS.length))
  })

  it('the minimum tab still holds a two-letter ko label at the v7 12px tab font', () => {
    // 2 Hangul glyphs at 12px + 12px padding on each side.
    expect(INSPECTOR_TAB_MIN_WIDTH).toBeGreaterThanOrEqual(2 * 12 + 2 * 12)
    expect(inspectorTabStripMinWidth(4)).toBe(4 * INSPECTOR_TAB_MIN_WIDTH + 3 * INSPECTOR_TAB_GAP + 2 * INSPECTOR_TAB_STRIP_PADDING_X)
    expect(inspectorTabStripMinWidth(0)).toBe(2 * INSPECTOR_TAB_STRIP_PADDING_X)
  })

  it('applies the strip geometry to the rendered tab row and keeps all four tabs clickable', async () => {
    const w = mountInspector({ selection: sel(TASK) })
    const strip = get(w, 'designer-inspector-tabs').element as HTMLElement
    expect(strip.style.gap).toBe(`${INSPECTOR_TAB_GAP}px`)
    expect(strip.style.paddingLeft).toBe(`${INSPECTOR_TAB_STRIP_PADDING_X}px`)
    expect(strip.style.paddingRight).toBe(`${INSPECTOR_TAB_STRIP_PADDING_X}px`)
    expect(strip.style.getPropertyValue('--inspector-tab-min-width')).toBe(`${INSPECTOR_TAB_MIN_WIDTH}px`)
    // The trailing tab is a live tab like the others: reaching it changes the Content area.
    await clickTab(w, '설정')
    expect(get(w, 'designer-inspector-body').attributes('data-tab')).toBe('settings')
  })
})

// --- Center session -> Bottom Dock -> Inspector ---------------------------------------------

function graphSource(graphs: Record<string, FlowGraph>): FlowGraphSource {
  return {
    isSample: false,
    loadGraph: (_projectId: string, flowId: string) =>
      graphs[flowId] ? Promise.resolve(graphs[flowId]) : Promise.reject(new Error('missing')),
  }
}

const GRAPHS: Record<string, FlowGraph> = {
  f: { flowId: 'f', nodes: [REQ, TASK], edges: [{ id: 'e1', source: REQ.id, target: TASK.id }] },
  g: { flowId: 'g', nodes: [{ ...TASK, id: 'g:n1', title: '다른 플로우 작업' }], edges: [] },
}

function setupSession() {
  const center = createDesignerCenterSession({ source: graphSource(GRAPHS) })
  center.setProject('p')
  return center
}

describe('Center session — activeSelectedNode', () => {
  it('is null until a ready Graph of the active Flow has a selected Node', async () => {
    const center = setupSession()
    expect(center.activeSelectedNode.value).toBeNull()
    center.openFlow({ projectId: 'p', flowId: 'f', flowName: 'F' })
    expect(center.activeSelectedNode.value).toBeNull()
    await flushPromises()
    expect(center.activeSelectedNode.value).toBeNull()
    center.selectNode('f', TASK.id)
    expect(center.activeSelectedNode.value).toEqual(TASK)
    center.selectNode('f', null)
    expect(center.activeSelectedNode.value).toBeNull()
  })

  it('follows the active tab and is cleared by closing the tab or changing the Project', async () => {
    const center = setupSession()
    center.openFlow({ projectId: 'p', flowId: 'f', flowName: 'F' })
    center.openFlow({ projectId: 'p', flowId: 'g', flowName: 'G' })
    await flushPromises()
    center.selectNode('f', REQ.id)
    expect(center.activeSelectedNode.value).toBeNull()
    center.activateFlow('f')
    expect(center.activeSelectedNode.value?.id).toBe(REQ.id)
    center.closeFlow('f')
    expect(center.activeSelectedNode.value).toBeNull()
    center.activateFlow('g')
    center.selectNode('g', 'g:n1')
    expect(center.activeSelectedNode.value?.title).toBe('다른 플로우 작업')
    center.setProject('q')
    expect(center.activeSelectedNode.value).toBeNull()
  })
})

describe('DesignerCenterColumn — Bottom Dock with the Inspector', () => {
  const WorkspaceStub = defineComponent({ name: 'DesignerWorkspace', render: () => h('section', { 'data-test': 'designer-workspace' }) })

  async function mountColumn() {
    const center = setupSession()
    wrapper = mount(DesignerCenterColumn, {
      attachTo: document.body,
      props: { center, bottomHeight: 300 },
      global: { plugins: [i18n], stubs: { DesignerWorkspace: WorkspaceStub } },
    })
    await flushPromises()
    return { w: wrapper, center }
  }

  // flowgate.v02.0049: the Minimap placeholder became the real Minimap (nothing to draw yet).
  it('lays the Inspector and the Minimap side by side in the Bottom Dock', async () => {
    const { w } = await mountColumn()
    const dock = get(w, 'designer-bottom-dock')
    expect(dock.attributes('style')).toContain('height: 300px')
    expect([...dock.element.children].map((c) => c.getAttribute('data-test'))).toEqual([
      'designer-inspector',
      'designer-bottom-minimap',
    ])
    expect(get(w, 'designer-bottom-minimap').find('[data-test^="designer-placeholder-"]').exists()).toBe(false)
    expect(get(w, 'designer-bottom-minimap').get('[data-test="designer-minimap"]').attributes('data-empty')).toBe('true')
    expect((dock.element as HTMLElement).style.gridTemplateColumns).toBe(DESIGNER_BOTTOM_DOCK_COLUMNS)
  })

  it('shows the Node selected in the Center session and goes back to the empty area', async () => {
    const { w, center } = await mountColumn()
    expect(get(w, 'designer-inspector').attributes('data-selected')).toBe('false')

    center.openFlow({ projectId: 'p', flowId: 'f', flowName: 'F' })
    await flushPromises()
    expect(w.find('[data-test="designer-inspector-empty"]').exists()).toBe(true)

    center.selectNode('f', REQ.id)
    await flushPromises()
    expect(get(w, 'designer-inspector-title').text()).toBe('로그인 토큰 만료 개선')
    expect(get(w, 'designer-inspector-kind').text()).toBe('요구사항')

    center.selectNode('f', TASK.id)
    await flushPromises()
    expect(get(w, 'designer-inspector-title').text()).toBe('토큰 갱신 로직 수정')

    center.closeFlow('f')
    await flushPromises()
    expect(get(w, 'designer-inspector-empty').text()).toBe('')
  })
})
