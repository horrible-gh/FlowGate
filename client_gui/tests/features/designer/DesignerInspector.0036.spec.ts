/**
 * flowgate.v02.0036 — bottom Inspector Frame (0036-R §4 / §7, 0035 v7). The Frame is mounted
 * on its own with plain Graph Nodes, and inside the real Center column with a real Center
 * session (fake Graph source) whose Workspace is stubbed, so no Canvas is needed: selecting a
 * Node through the session must reach the Inspector.
 */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { defineComponent, h } from 'vue'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import { createDesignerCenterSession } from '@/features/designer/center/designerCenterSession'
import type { FlowGraph, FlowGraphNode } from '@/features/designer/center/graph/flowGraph'
import type { FlowGraphSource } from '@/features/designer/center/graph/flowGraphSource'
import DesignerInspector from '@/features/designer/inspector/DesignerInspector.vue'
import {
  DEFAULT_INSPECTOR_TAB,
  INSPECTOR_TAB_GAP,
  INSPECTOR_TAB_MIN_WIDTH,
  INSPECTOR_TAB_STRIP_PADDING_X,
  INSPECTOR_TABS,
  inspectorTabStripMinWidth,
  inspectorTabsFor,
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

const REQ: FlowGraphNode = { id: 'f:n1', kind: 'requirement', title: '로그인 토큰 만료 개선', state: 'done', x: 0, y: 0 }
const TASK: FlowGraphNode = { id: 'f:n2', kind: 'task', title: '토큰 갱신 로직 수정', state: 'in_progress', x: 260, y: 0 }
const ODD: FlowGraphNode = { id: 'f:n3', kind: 'future-kind', title: '새 종류', state: 'future-state', x: 520, y: 0 }

let wrapper: VueWrapper<any> | null = null

beforeEach(() => {
  i18n.global.locale.value = 'ko'
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
})

function mountInspector(props: Record<string, unknown>, slots: Record<string, unknown> = {}) {
  wrapper = mount(DesignerInspector, { attachTo: document.body, props, slots: slots as any, global: { plugins: [i18n] } })
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
  it('lists 문서 · Q&A · 첨부 · 설정 in a fixed order and only ever drops the trailing 설정', () => {
    expect(INSPECTOR_TABS).toEqual(['documents', 'qna', 'attachments', 'settings'])
    expect(inspectorTabsFor(true)).toEqual(['documents', 'qna', 'attachments', 'settings'])
    expect(inspectorTabsFor(false)).toEqual(['documents', 'qna', 'attachments'])
    expect(DEFAULT_INSPECTOR_TAB).toBe('documents')
  })

  it('keeps an available tab and falls back to 문서 otherwise', () => {
    expect(resolveInspectorTab('qna', inspectorTabsFor(false))).toBe('qna')
    expect(resolveInspectorTab('settings', inspectorTabsFor(true))).toBe('settings')
    expect(resolveInspectorTab('settings', inspectorTabsFor(false))).toBe('documents')
  })
})

describe('DesignerInspector — Node not selected', () => {
  // flowgate.v02.0042: the empty Inspector carries no guidance text.
  it('shows an empty area with no text and no header, tabs or Content area', () => {
    const w = mountInspector({ node: null })
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
    const w = mountInspector({ node: TASK })
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
    const w = mountInspector({ node: ODD })
    expect(get(w, 'designer-inspector-header').attributes('data-kind')).toBe('unknown')
    expect(get(w, 'designer-inspector-kind').text()).toBe('노드')
    expect(w.find('[data-test="designer-inspector-state"]').exists()).toBe(false)
  })

  it('shows the four v7 tabs with 문서 selected and a placeholder in the Content area', () => {
    const w = mountInspector({ node: TASK })
    expect(tabLabels(w)).toEqual(['문서', 'Q&A', '첨부', '설정'])
    expect(activeTabLabel(w)).toBe('문서')
    const body = get(w, 'designer-inspector-body')
    expect(body.attributes('role')).toBe('tabpanel')
    expect(body.attributes('data-tab')).toBe('documents')
    expect(get(w, 'designer-inspector-placeholder').text()).toBe('문서 내용은 후속 작업에서 연결됩니다.')
  })

  it('switches the Content area with the tab', async () => {
    const w = mountInspector({ node: TASK })
    await clickTab(w, '첨부')
    expect(activeTabLabel(w)).toBe('첨부')
    expect(get(w, 'designer-inspector-body').attributes('data-tab')).toBe('attachments')
    expect(get(w, 'designer-inspector-placeholder').text()).toBe('첨부 내용은 후속 작업에서 연결됩니다.')
  })

  it('keeps the selected tab when another Node is selected and after a moment with no selection', async () => {
    const w = mountInspector({ node: TASK })
    await clickTab(w, 'Q&A')
    await w.setProps({ node: REQ })
    expect(get(w, 'designer-inspector-title').text()).toBe('로그인 토큰 만료 개선')
    expect(activeTabLabel(w)).toBe('Q&A')
    await w.setProps({ node: null })
    await w.setProps({ node: TASK })
    expect(activeTabLabel(w)).toBe('Q&A')
  })

  it('hides 설정 for a Node without settings and sends a 설정 selection back to 문서', async () => {
    const w = mountInspector({ node: TASK })
    await clickTab(w, '설정')
    expect(activeTabLabel(w)).toBe('설정')
    await w.setProps({ node: REQ, hasSettings: false })
    expect(tabLabels(w)).toEqual(['문서', 'Q&A', '첨부'])
    expect(activeTabLabel(w)).toBe('문서')
    // The fallback sticks: coming back to a Node with settings stays on 문서.
    await w.setProps({ node: TASK, hasSettings: true })
    expect(tabLabels(w)).toEqual(['문서', 'Q&A', '첨부', '설정'])
    expect(activeTabLabel(w)).toBe('문서')
  })

  it('lets a later group inject header parts and one Content panel per tab, given the Node', async () => {
    const w = mountInspector(
      { node: TASK },
      {
        'header-meta': ({ node }: { node: FlowGraphNode }) => h('span', { 'data-test': 'injected-meta' }, node.id),
        'header-actions': () => h('button', { 'data-test': 'injected-action' }, 'go'),
        'tab-qna': ({ node }: { node: FlowGraphNode }) => h('div', { 'data-test': 'injected-qna' }, node.title),
      },
    )
    expect(get(w, 'injected-meta').text()).toBe('f:n2')
    expect(get(w, 'designer-inspector-actions').get('[data-test="injected-action"]').text()).toBe('go')
    // 문서 has no injected panel: the placeholder stays.
    expect(w.find('[data-test="injected-qna"]').exists()).toBe(false)
    expect(w.find('[data-test="designer-inspector-placeholder"]').exists()).toBe(true)
    await clickTab(w, 'Q&A')
    expect(get(w, 'injected-qna').text()).toBe('토큰 갱신 로직 수정')
    expect(w.find('[data-test="designer-inspector-placeholder"]').exists()).toBe(false)
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
    const w = mountInspector({ node: TASK })
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

  it('lays the Inspector and the Minimap placeholder side by side in the Bottom Dock', async () => {
    const { w } = await mountColumn()
    const dock = get(w, 'designer-bottom-dock')
    expect(dock.attributes('style')).toContain('height: 300px')
    expect([...dock.element.children].map((c) => c.getAttribute('data-test'))).toEqual([
      'designer-inspector',
      'designer-bottom-minimap',
    ])
    expect(get(w, 'designer-bottom-minimap').get('[data-test="designer-placeholder-label"]').text()).toBe('MINIMAP')
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
