/**
 * flowgate.v02test.0077 (0005-T) — the sidebar (Designer Project Explorer) shows Flow numbers ahead of the names,
 * tells archived rows and the module key, and finds Flows / cards by their display id.
 *
 * Pure parts go through projectExplorerTree; the component is mounted with the real rows and the real project store,
 * only the HTTP layer (`@shared/api`) is mocked (the group tree and the document search the card id is resolved by).
 */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import type { GroupTreeNode } from '@/features/project'
import { ProjectExplorer } from '@/features/designer'
import {
  buildExplorerProject,
  filterExplorerProject,
  flowLabelOf,
  parseExplorerId,
} from '@/features/designer/projectExplorer/projectExplorerTree'
import { OPEN_CARD_EVENT } from '@/features/designer/inspector/features/verification/verificationModel'

const getRequest = vi.hoisted(() => vi.fn())
const postRequest = vi.hoisted(() => vi.fn())

vi.mock('@shared/api', () => ({
  getRequest,
  postRequest,
  extractApiErrorMessage: (_error: unknown, fallback: string) => fallback,
}))

registerAppMessages(i18n)

// ── Pure ─────────────────────────────────────────────────────────────────────

function n(partial: Partial<GroupTreeNode> & Pick<GroupTreeNode, 'id' | 'parentId' | 'kind' | 'label'>): GroupTreeNode {
  return { title: null, number: null, ...partial }
}

const TREE: GroupTreeNode[] = [
  n({ id: 'project:p', parentId: null, kind: 'project', label: 'Alpha' }),
  n({ id: 'module:p:ui', parentId: 'project:p', kind: 'module', label: 'ui', title: 'UI 개선' }),
  n({ id: 'p.ui.0001', parentId: 'module:p:ui', kind: 'group', label: 'Designer', number: '0001', flowLabel: 'F1' }),
  n({ id: 'p.ui.0012', parentId: 'module:p:ui', kind: 'group', label: 'Canvas', number: '0012' }),
  n({ id: 'p.ui.0103', parentId: 'module:p:ui', kind: 'group', label: 'Canvas', number: '0103', archivedAt: '2026-11-01' }),
  n({ id: 'module:p:core', parentId: 'project:p', kind: 'module', label: 'core', title: 'Core' }),
  n({ id: 'p.core.0001', parentId: 'module:p:core', kind: 'group', label: 'Engine', number: '0001' }),
]

const project = buildExplorerProject(TREE, 'project:p')!
const flowIds = (query: string, contextFlowId?: string | null) =>
  filterExplorerProject(project, query, { contextFlowId }).project?.groups.flatMap((g) => g.flows.map((f) => f.id)) ?? []

describe('Flow numbers in the display tree', () => {
  it('makes F + the number without leading zeros, the server rule', () => {
    expect(flowLabelOf('0077')).toBe('F77')
    expect(flowLabelOf('10000')).toBe('F10000')
    expect(flowLabelOf(null)).toBeNull()
    expect(flowLabelOf('x1')).toBeNull()
  })

  it('takes the server flow_label, else makes it from the number, and gives each Group its widest number', () => {
    const [ui, core] = project.groups
    expect(ui.flows.map((f) => f.label)).toEqual(['F1', 'F12', 'F103'])
    expect(core.flows.map((f) => f.label)).toEqual(['F1'])
    // F103 is four characters wide: every number column of that Group is four wide.
    expect(ui.flowLabelWidth).toBe(4)
    expect(core.flowLabelWidth).toBe(2)
    // The server's label wins over the number when both are there.
    const own = buildExplorerProject([TREE[0], TREE[1], n({ ...TREE[2], flowLabel: 'F9' })], 'project:p')!
    expect(own.groups[0].flows[0].label).toBe('F9')
  })
})

describe('parseExplorerId', () => {
  it.each([
    ['F77', { kind: 'flow', flowNo: 77 }],
    ['f0077', { kind: 'flow', flowNo: 77 }],
    ['77', { kind: 'flow', flowNo: 77, bare: true }],
    ['v02test.F77', { kind: 'flow', flowNo: 77, module: 'v02test' }],
    ['flowgate.v02test.F77', { kind: 'flow', flowNo: 77, project: 'flowgate', module: 'v02test' }],
    ['F77-12', { kind: 'card', flowNo: 77, cardNo: 12 }],
    ['F77-012', { kind: 'card', flowNo: 77, cardNo: 12 }],
    [' FLOWGATE.V02TEST.F77-12 ', { kind: 'card', flowNo: 77, cardNo: 12, project: 'flowgate', module: 'v02test' }],
    ['#12', { kind: 'local', cardNo: 12 }],
  ])('%s', (input, expected) => {
    expect(parseExplorerId(input)).toEqual(expected)
  })

  it('does not read names or stored ids as display ids', () => {
    for (const input of ['', 'Canvas', 'flowgate.v02test.0077', 'flowgate.v02test.0077.0003-T', 'F77-', 'F-12', 'gn_abc']) {
      expect(parseExplorerId(input), input).toBeNull()
    }
  })
})

describe('filterExplorerProject by number', () => {
  it('finds a Flow by F number, digits only, or leading zeros', () => {
    expect(flowIds('F12')).toEqual(['p.ui.0012'])
    expect(flowIds('12')).toEqual(['p.ui.0012'])
    expect(flowIds('f0001')).toEqual(['p.ui.0001', 'p.core.0001'])
    expect(filterExplorerProject(project, 'F12').idMatchIds).toEqual(['p.ui.0012'])
  })

  it('narrows by the module / project key the id carries', () => {
    expect(flowIds('ui.F1')).toEqual(['p.ui.0001'])
    expect(flowIds('p.core.f1')).toEqual(['p.core.0001'])
    expect(flowIds('q.ui.F1')).toEqual([])
    expect(flowIds('other.F1')).toEqual([])
  })

  it('shows the Flow of a card id, and the open Flow for #n', () => {
    expect(flowIds('F12-3')).toEqual(['p.ui.0012'])
    expect(flowIds('p.ui.F12-3')).toEqual(['p.ui.0012'])
    expect(flowIds('#3', 'p.ui.0012')).toEqual(['p.ui.0012'])
    expect(flowIds('#3')).toEqual([])
  })

  it('keeps the name search as it was', () => {
    expect(flowIds('canvas')).toEqual(['p.ui.0012', 'p.ui.0103'])
    expect(filterExplorerProject(project, 'canvas').idMatchIds).toEqual([])
    expect(flowIds('p.ui.0012')).toEqual([])
  })
})

// ── Component ────────────────────────────────────────────────────────────────

function raw(partial: Record<string, unknown>) {
  return { type_code: null, number: null, filename: null, has_md: false, md_path: null, ...partial }
}

const NODES = [
  raw({ id: 'project:p', parent_id: null, node_type: 'project', label: 'Alpha' }),
  raw({ id: 'module:p:ui', parent_id: 'project:p', node_type: 'module', label: 'ui', title: 'UI 개선' }),
  raw({ id: 'p.ui.0001', parent_id: 'module:p:ui', node_type: 'group', label: 'Designer', number: '0001', flow_label: 'F1' }),
  raw({ id: 'p.ui.0012', parent_id: 'module:p:ui', node_type: 'group', label: 'Canvas', number: '0012', flow_label: 'F12' }),
  raw({ id: 'p.ui.0103', parent_id: 'module:p:ui', node_type: 'group', label: 'Canvas', number: '0103', flow_label: 'F103', archived_at: '2026-11-01' }),
]

const CARD_ITEM = {
  doc_id: 'p.ui.0012.0005-T', type: 'T', title: '화면 표시', group_id: 'p.ui.0012', match: 'card',
  card: {
    group_id: 'p.ui.0012', node_key: 'gn_card3',
    flow_ref: { flow_no: 12, label: 'F12', module_ref: 'ui.F12', full: 'p.ui.F12' },
    card_ref: { card_no: 3, local: '#3', short: 'F12-3', module_ref: 'ui.F12-3', full: 'p.ui.F12-3' },
  },
}

let searchItems: unknown[] = []

function serve() {
  getRequest.mockImplementation(async (url: string, params: Record<string, unknown> = {}) => {
    if (url === '/api/v1/search/documents') return { data: { ok: true, items: searchItems } }
    const nodes = params.include_archived === 'true' ? NODES : NODES.filter((node) => !node.archived_at)
    return { data: { data: { nodes } } }
  })
}

const searchCalls = () => getRequest.mock.calls.filter(([url]) => url === '/api/v1/search/documents')
const wait = (ms: number) => new Promise((r) => setTimeout(r, ms))

let wrapper: VueWrapper<any> | null = null

async function mountExplorer(props: Record<string, unknown> = { projectId: 'p' }) {
  wrapper = mount(ProjectExplorer, { props, attachTo: document.body, global: { plugins: [i18n] } })
  await flushPromises()
  return wrapper
}

function row(w: VueWrapper<any>, kind: 'group' | 'flow', id: string) {
  const found = w.findAll(`[data-test="project-explorer-${kind}"]`).find((r) => r.attributes('data-row-id') === id)
  if (!found) throw new Error(`row not rendered: ${id}`)
  return found
}

async function search(w: VueWrapper<any>, text: string) {
  await w.get('[data-test="project-explorer-search"]').setValue(text)
  await wait(200)
  await flushPromises()
}

async function pressEnter(w: VueWrapper<any>) {
  await w.get('[data-test="project-explorer-search"]').trigger('keydown', { key: 'Enter' })
  await flushPromises()
}

beforeEach(() => {
  setActivePinia(createPinia())
  localStorage.clear()
  getRequest.mockReset()
  postRequest.mockReset()
  searchItems = []
  i18n.global.locale.value = 'ko'
  serve()
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
})

describe('ProjectExplorer — Flow numbers (screen 1)', () => {
  it('draws F number ahead of the name, one column width per Group, the count untouched', async () => {
    const w = await mountExplorer()
    await row(w, 'group', 'module:p:ui').find('.explorer-row').trigger('click', { detail: 1 })
    const flow = row(w, 'flow', 'p.ui.0012')
    const number = flow.get('[data-test="project-explorer-flow-number"]')
    const label = flow.get('[data-test="project-explorer-label"]')
    expect(number.text()).toBe('F12')
    expect(label.text()).toBe('Canvas')
    // Number first, then the name, on the same line.
    expect(number.element.compareDocumentPosition(label.element) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy()
    expect(flow.attributes('aria-label')).toBe('F12 Canvas')
    // Same width on every line of the Group (both F1 and F12 get the widest, here 3 characters).
    const widths = w.findAll('[data-test="project-explorer-flow-number"]').map((el) => (el.element as HTMLElement).style.minWidth)
    expect(widths).toEqual(['3ch', '3ch'])
    expect(row(w, 'group', 'module:p:ui').find('.row-count').text()).toBe('2')
  })

  it('shows the module key on hovering the Group (screen 2)', async () => {
    const w = await mountExplorer()
    const title = row(w, 'group', 'module:p:ui').find('.explorer-row').attributes('title')
    expect(title).toBe('UI 개선 · ID에 쓰는 이름: ui')
    expect(row(w, 'group', 'module:p:ui').attributes('aria-label')).toBe('UI 개선')
  })

  it('tells same-name Flows apart by number and marks the archived one with 아카이브 (screen 3)', async () => {
    const w = await mountExplorer()
    await w.get('[data-test="project-explorer-include-archived"]').setValue(true)
    await flushPromises()
    await row(w, 'group', 'module:p:ui').find('.explorer-row').trigger('click', { detail: 1 })
    const live = row(w, 'flow', 'p.ui.0012')
    const archived = row(w, 'flow', 'p.ui.0103')
    expect(live.attributes('aria-label')).toBe('F12 Canvas')
    expect(archived.attributes('aria-label')).toBe('F103 Canvas')
    expect(live.find('[data-test="project-explorer-archived"]').exists()).toBe(false)
    expect(archived.get('[data-test="project-explorer-archived"]').text()).toBe('아카이브')
    expect(archived.classes()).toContain('is-archived')
    // F103 widens the column of every Flow of that Group.
    expect(w.findAll('[data-test="project-explorer-flow-number"]').map((el) => (el.element as HTMLElement).style.minWidth))
      .toEqual(['4ch', '4ch', '4ch'])
  })
})

describe('ProjectExplorer — search by number (screen 4)', () => {
  it('finds F12 / 12 / f0012 and opens that Flow on Enter without asking the server', async () => {
    const w = await mountExplorer()
    for (const text of ['F12', '12', 'f0012', 'p.ui.F12']) {
      await search(w, text)
      expect(w.findAll('[data-test="project-explorer-flow"]').map((r) => r.attributes('data-row-id')), text).toEqual(['p.ui.0012'])
    }
    await pressEnter(w)
    expect(w.emitted('activate-flow')?.at(-1)?.[0]).toMatchObject({ projectId: 'p', groupId: 'module:p:ui', flowId: 'p.ui.0012', flowName: 'Canvas' })
    expect(searchCalls()).toHaveLength(0)
  })

  it('puts the card of F12-3 on top and Enter opens its Flow and selects the card', async () => {
    searchItems = [CARD_ITEM, { doc_id: 'p.ui.0001.0001-R', title: 'F12-3 in a title', group_id: 'p.ui.0001' }]
    const w = await mountExplorer()
    const opened: unknown[] = []
    const listener = (event: Event) => opened.push((event as CustomEvent).detail)
    window.addEventListener(OPEN_CARD_EVENT, listener)
    try {
      await search(w, 'F12-3')
      expect(searchCalls().at(-1)).toEqual(['/api/v1/search/documents', { q: 'F12-3', project: 'p', limit: 20 }])
      const cards = w.findAll('[data-test="project-explorer-card"]')
      // Only the exact card match, not the title hit of the same answer.
      expect(cards).toHaveLength(1)
      expect(cards[0].find('.project-explorer-card-id').text()).toBe('F12-3')
      expect(cards[0].text()).toContain('화면 표시')
      expect(cards[0].attributes('title')).toBe('p.ui.F12-3')
      // The tree under it shows that card's Flow with its number ahead.
      expect(w.findAll('[data-test="project-explorer-flow"]').map((r) => r.attributes('aria-label'))).toEqual(['F12 Canvas'])

      await pressEnter(w)
      expect(w.emitted('activate-flow')?.at(-1)?.[0]).toMatchObject({ flowId: 'p.ui.0012', groupId: 'module:p:ui' })
      expect(opened).toEqual([{ projectId: 'p', flowId: 'p.ui.0012', nodeKey: 'gn_card3', flowName: 'Canvas' }])

      // A click on the card row opens it the same way.
      await w.get('[data-test="project-explorer-card"]').trigger('click')
      expect(opened).toHaveLength(2)
    } finally {
      window.removeEventListener(OPEN_CARD_EVENT, listener)
    }
  })

  it('reads the full id too, and #3 only in the open Flow', async () => {
    searchItems = [CARD_ITEM]
    const w = await mountExplorer({ projectId: 'p', activeFlowId: null })
    await search(w, 'p.ui.F12-3')
    expect(searchCalls().at(-1)?.[1]).toEqual({ q: 'p.ui.F12-3', project: 'p', limit: 20 })
    expect(w.findAll('[data-test="project-explorer-card"]')).toHaveLength(1)

    getRequest.mockClear()
    await search(w, '#3')
    expect(searchCalls()).toHaveLength(0)
    expect(w.find('[data-test="project-explorer-card-results"]').exists()).toBe(false)

    await w.setProps({ activeFlowId: 'p.ui.0012' })
    await wait(200)
    await flushPromises()
    expect(searchCalls().at(-1)?.[1]).toEqual({ q: '#3', project: 'p', limit: 20, flow: 'p.ui.0012' })
    expect(w.findAll('[data-test="project-explorer-card"]')).toHaveLength(1)
  })

  it('drops the card when the query stops being a card id, and a name search never asks for one', async () => {
    searchItems = [CARD_ITEM]
    const w = await mountExplorer()
    await search(w, 'F12-3')
    expect(w.findAll('[data-test="project-explorer-card"]')).toHaveLength(1)
    getRequest.mockClear()
    await search(w, 'Canvas')
    expect(w.find('[data-test="project-explorer-card-results"]').exists()).toBe(false)
    expect(searchCalls()).toHaveLength(0)
    // Enter on a name search does nothing, as before.
    await pressEnter(w)
    expect(w.emitted('activate-flow')).toBeUndefined()
  })

  it('shows no card when the lookup fails, the tree match stays', async () => {
    getRequest.mockImplementation(async (url: string) => {
      if (url === '/api/v1/search/documents') throw new Error('boom')
      return { data: { data: { nodes: NODES.filter((node) => !node.archived_at) } } }
    })
    const w = await mountExplorer()
    await search(w, 'F12-3')
    expect(w.find('[data-test="project-explorer-card-results"]').exists()).toBe(false)
    expect(w.findAll('[data-test="project-explorer-flow"]').map((r) => r.attributes('data-row-id'))).toEqual(['p.ui.0012'])
  })
})
