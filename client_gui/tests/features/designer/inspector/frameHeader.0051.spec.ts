/**
 * flowgate.v02.0051 stage 2 — the Frame's two new header places and the header's v7 line: a registration
 * may contribute the kind badge (`headerKind`, replacing the static one) and ⋯ menu items (`headerMenu`,
 * after ID 복사); the three state texts sit in one pill group; a B root takes the bug look. Last, the real
 * registry on Nodes without a document: a Graph gate and a Graph R Node no longer show the header only.
 */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { defineComponent, h } from 'vue'
import DesignerInspector from '@/features/designer/inspector/DesignerInspector.vue'
import { closeAllDocumentWindows, documentWindowList } from '@/features/documentWindow'
import { registrationViolations } from '@/features/designer/inspector/inspectorRegistry'
import {
  fixtureRegistry,
  prepareInspectorI18n,
  probePart,
  registration,
  registrationModules,
  SELECTIONS,
  selectionOf,
  UNASSIGNED_GRAPH,
  UNASSIGNED_REQ_NODE,
} from './support/inspectorSupport'

const i18n = prepareInspectorI18n()

let wrapper: VueWrapper<any> | null = null

beforeEach(() => {
  i18n.global.locale.value = 'ko'
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  document.body.innerHTML = ''
})

const get = (w: VueWrapper<any>, test: string) => w.get(`[data-test="${test}"]`)
const tabLabels = (w: VueWrapper<any>) => w.findAll('[data-test="designer-inspector-tabs"] [role="tab"]').map((t) => t.text())
const sections = (w: VueWrapper<any>) => w.findAll('[data-test="designer-inspector-section"]').map((s) => s.attributes('data-key'))

async function clickTab(w: VueWrapper<any>, label: string) {
  const tab = w.findAll('[data-test="designer-inspector-tabs"] [role="tab"]').find((t) => t.text() === label)
  if (!tab) throw new Error(`no tab ${label}`)
  await tab.trigger('click')
  await flushPromises()
}

function mountFrame(selection: Parameters<typeof selectionOf>[0] | (typeof SELECTIONS)[keyof typeof SELECTIONS], registry?: ReturnType<typeof fixtureRegistry>) {
  wrapper = mount(DesignerInspector, {
    attachTo: document.body,
    props: { selection: selection as never, ...(registry ? { registry } : {}) },
    global: { plugins: [i18n] },
  })
  return wrapper
}

async function openMenu(w: VueWrapper<any>) {
  await get(w, 'designer-inspector-more').trigger('click')
  await flushPromises()
  return [...document.body.querySelectorAll<HTMLElement>('.ui-dropdown-menu [role="menuitem"]')]
}

/** A ⋯ menu probe: one item naming its key and whether it got `close`. */
const menuProbe = defineComponent({
  props: { target: { type: Object, required: true }, close: { type: Function, required: false } },
  setup: (props) => () => h('button', { type: 'button', role: 'menuitem', 'data-test': 'probe-menu', 'data-close': typeof props.close === 'function' ? 'yes' : 'no' }, 'probe'),
})

describe('registry contract (flowgate.v02.0051)', () => {
  it('a registration may contribute only a header kind badge, or only ⋯ menu items', () => {
    const modules = registrationModules([
      registration({ key: 'kind', order: 1, requiresDocument: false, headerKind: probePart('kind') }),
      registration({ key: 'menu', order: 2, requiresDocument: false, headerMenu: menuProbe }),
    ])
    expect(registrationViolations(modules)).toEqual([])
  })

  it('a registration contributing nothing names every place', () => {
    expect(registrationViolations(registrationModules([registration({ key: 'none', order: 1 })]))).toEqual([
      'features/none: contributes no tab, header action, header meta, header kind, header menu or header memo',
    ])
  })
})

describe('the Frame lays out the kind badge and the ⋯ menu items', () => {
  const registry = fixtureRegistry([
    registration({ key: 'kind', order: 1, requiresDocument: false, headerKind: probePart('kind') }),
    registration({ key: 'kindToo', order: 2, requiresDocument: false, headerKind: probePart('kindToo') }),
    registration({ key: 'menu', order: 3, requiresDocument: false, headerMenu: menuProbe }),
    registration({ key: 'docs', order: 4, tab: { key: 'documents', component: probePart('documents') } }),
  ])

  it('the first applying headerKind replaces the static badge; headerMenu items follow ID 복사 with `close`', async () => {
    const w = mountFrame(SELECTIONS.v01, registry)
    const header = get(w, 'designer-inspector-header')
    expect(header.find('[data-test="probe-kind"]').exists()).toBe(true)
    expect(header.find('[data-test="probe-kindToo"]').exists()).toBe(false)
    expect(header.find('[data-test="designer-inspector-kind"]').exists()).toBe(false)
    const items = await openMenu(w)
    expect(items.map((item) => item.getAttribute('data-test'))).toEqual(['designer-inspector-copy-id', 'probe-menu'])
    expect(items[1].getAttribute('data-close')).toBe('yes')
  })

  it('without a headerKind the static badge stays', () => {
    const w = mountFrame(SELECTIONS.v01, fixtureRegistry([registration({ key: 'docs', order: 4, tab: { key: 'documents', component: probePart('documents') } })]))
    expect(get(w, 'designer-inspector-kind').text()).toBe('작업')
  })
})

describe('the header line (flowgate.v02.0051 stage 2)', () => {
  const none = fixtureRegistry([])

  // flowgate.v02.0052 묶음 A: one label (the Canvas state) in its tone; server states that say the same
  // are not repeated; the raw values stay in hidden spans and the tooltip.
  it('the states sit in one pill that takes the Canvas state tone and says each thing once', () => {
    const w = mountFrame(SELECTIONS.graph, none)
    const pill = get(w, 'designer-inspector-pill')
    expect(pill.classes()).toContain('inspector-header-pill--success')
    expect(pill.find('[data-test="designer-inspector-pill-label"]').text()).toBe('완료')
    expect(pill.find('[data-test="designer-inspector-pill-sub"]').exists()).toBe(false)
    expect(pill.find('[data-test="designer-inspector-doc-status"]').attributes('data-value')).toBe('approved')
    expect(pill.find('[data-test="designer-inspector-run-status"]').attributes('data-value')).toBe('completed')
    expect(pill.find('[data-test="designer-inspector-state"]').attributes('data-value')).toBe('done')
    expect(pill.attributes('title')).toBe('문서 상태: 승인됨 (approved) / 실행 상태: 완료 (completed)')
  })

  it('a Node with no state at all has no pill', () => {
    const node = { ...UNASSIGNED_REQ_NODE, id: 'gn_bare', state: null, document: null }
    const w = mountFrame(selectionOf({ ...UNASSIGNED_GRAPH, nodes: [node] }, node.id), none)
    expect(w.find('[data-test="designer-inspector-pill"]').exists()).toBe(false)
    // The support gate has the Canvas state 'draft': the pill holds that state alone.
    const gate = mountFrame(SELECTIONS.graphGate, none)
    expect(gate.get('[data-test="designer-inspector-pill-label"]').text()).toBe('작성 중')
    expect(gate.find('[data-test="designer-inspector-pill-sub"]').exists()).toBe(false)
  })

  it('a B root: the 버그 name, the bug glyph and the red accent', () => {
    const node = { ...UNASSIGNED_REQ_NODE, id: 'doc:b', docType: 'B', document: { ...UNASSIGNED_REQ_NODE.document!, docId: 'flowgate.v02.0050.0001-B', typeCode: 'B' } }
    const w = mountFrame(selectionOf({ ...UNASSIGNED_GRAPH, nodes: [node] }, node.id), none)
    expect(get(w, 'designer-inspector-kind').text()).toBe('버그')
    expect((get(w, 'designer-inspector-header').element as HTMLElement).style.getPropertyValue('--inspector-kind-accent')).toBe('#ef4444')
    w.unmount()
    wrapper = null
    i18n.global.locale.value = 'en'
    expect(get(mountFrame(selectionOf({ ...UNASSIGNED_GRAPH, nodes: [node] }, node.id), none), 'designer-inspector-kind').text()).toBe('Bug')
  })
})

describe('the real registry on Nodes without a document', () => {
  it('a Graph gate: the documents tab with the 확인 결과 line, ✓ 확인 / ✕ 반려 ▾ disabled in the header', async () => {
    const w = mountFrame(SELECTIONS.graphGate)
    await flushPromises()
    expect(tabLabels(w)).toEqual(['문서', 'Q&A', '첨부', '설정'])
    // flowgate.v02.0056 F2-1: the card approval bar is a part of a Graph Flow's documents tab; a gate draws none.
    // flowgate.v02.0056 F2-2: the card's memos sit between the card lines and the approval bar.
    expect(sections(w)).toEqual(['gateCard', 'cardMemo', 'cardApproval'])
    expect(w.find('[data-test="inspector-card-approval"]').exists()).toBe(false)
    expect(get(w, 'inspector-gate-card').find('[data-test="inspector-slot-name"]').text()).toBe('확인 결과')
    const confirm = get(w, 'designer-inspector-actions').get('[data-test="gate-confirm"]')
    expect(confirm.attributes('disabled')).toBeDefined()
  })

  it('a Graph R Node without a document: the 요구사항 line and the AI 위임 settings, the static badge (no switch without a document)', async () => {
    const node = { ...UNASSIGNED_REQ_NODE, id: 'gn_r', document: null }
    const w = mountFrame(selectionOf({ ...UNASSIGNED_GRAPH, ownership: 'graph', nodes: [node] }, node.id))
    await flushPromises()
    expect(tabLabels(w)).toEqual(['문서', 'Q&A', '첨부', '설정'])
    // flowgate.v02.0056 F2-1: the card approval bar draws nothing on a card without a run.
    // flowgate.v02.0056 F2-2: the card's memos sit between the card lines and the approval bar.
    expect(sections(w)).toEqual(['requirementCard', 'cardMemo', 'cardApproval'])
    expect(w.find('[data-test="inspector-card-approval"]').exists()).toBe(false)
    // flowgate.v02.0053 T#5: a line without a document is a hollow dot saying so — no "아직 없음" text.
    expect(get(w, 'inspector-requirement-card').get('[data-test="inspector-slot-state"]').attributes('title')).toBe('문서 없음')
    expect(get(w, 'inspector-requirement-card').text()).not.toContain('아직 없음')
    expect(get(w, 'designer-inspector-kind').text()).toBe('요구사항')
    await clickTab(w, '설정')
    expect(sections(w)).toEqual(['requirementSettings'])
    expect(get(w, 'inspector-requirement-settings').attributes('data-delegation')).toBe('off')
  })

  it('a Graph research Node without a document: both lines empty and the run-card ⋯ items disabled', async () => {
    const node = { ...UNASSIGNED_REQ_NODE, id: 'gn_n', kind: 'research', docType: 'N', document: null }
    const w = mountFrame(selectionOf({ ...UNASSIGNED_GRAPH, ownership: 'graph', nodes: [node] }, node.id))
    await flushPromises()
    expect(tabLabels(w)).toEqual(['문서', 'Q&A', '첨부', '설정'])
    // flowgate.v02.0056 F2-2: the card's memos sit between the card lines and the approval bar.
    expect(sections(w)).toEqual(['researchCard', 'cardMemo', 'cardApproval'])
    expect(w.find('[data-test="inspector-card-approval"]').exists()).toBe(false)
    const items = await openMenu(w)
    expect(items.map((item) => item.textContent?.trim())).toEqual(['ID 복사', '실행 이력'])
    expect(items.slice(1).every((item) => (item as HTMLButtonElement).disabled)).toBe(true)
  })
})

describe('Inspector layout assembly (flowgate.v02.0051 stage 3)', () => {
  it('keeps v7 tabs and a card result line on a documentless Graph gate', async () => {
    const w = mountFrame(SELECTIONS.graphGate)
    await flushPromises()
    expect(tabLabels(w)).toEqual(['문서', 'Q&A', '첨부', '설정'])
    expect(get(w, 'inspector-gate-card').find('[data-test="inspector-slot-name"]').text()).toBe('확인 결과')
    await clickTab(w, 'Q&A')
    expect(get(w, 'designer-inspector-tab-state').attributes('data-state')).toBe('empty')
    // flowgate.v02.0053 T#6: no "no linked document" sentence on Q&A and attachments.
    expect(get(w, 'designer-inspector-tab-state').text()).toBe('')
    await clickTab(w, '첨부')
    expect(get(w, 'designer-inspector-tab-state').attributes('data-state')).toBe('empty')
    expect(get(w, 'designer-inspector-tab-state').text()).toBe('')
    // flowgate.v02.0053 T#7: a Graph gate has the v7 확인 settings (용도 · Git 반영), not an empty state.
    await clickTab(w, '설정')
    expect(sections(w)).toEqual(['checkSettings'])
    expect(w.find('[data-test="designer-inspector-tab-state"]').exists()).toBe(false)
    expect(get(w, 'inspector-check-settings').exists()).toBe(true)
  })

  it('shows only card slots and keeps the document open action reachable', async () => {
    const w = mountFrame(SELECTIONS.v01)
    await flushPromises()
    expect(sections(w)).toContain('taskCard')
    expect(sections(w)).not.toContain('documents')
    expect(get(w, 'inspector-task-card').find('[data-test="inspector-slot-list"]').exists()).toBe(true)
    expect(w.find('[data-test="designer-inspector-details"]').exists()).toBe(false)
    expect(w.find('[data-test="inspector-documents"]').exists()).toBe(false)
    expect(get(w, 'designer-inspector-body').text()).not.toContain('현재 문서')
    expect(get(w, 'designer-inspector-body').text()).not.toContain('문서 상태 open rev 0')
    expect(get(w, 'designer-inspector-body').text()).not.toContain('문서 작성·편집은')
    closeAllDocumentWindows()
    const open = vi.spyOn(window, 'open').mockImplementation(() => null)
    // flowgate.v02.0053 T#3: ↗ says 열기 again and opens the document window in this tab — never a browser
    // tab; ✎ opens the same window straight in its editor.
    const docId = SELECTIONS.v01.node.document!.docId
    expect(get(w, 'inspector-task-card').get('[data-test="inspector-slot-open"]').attributes('title')).toBe('열기')
    await get(w, 'inspector-task-card').get('[data-test="inspector-slot-open"]').trigger('click')
    expect(open).not.toHaveBeenCalled()
    expect(documentWindowList().map((win) => [win.docId, win.mode])).toEqual([[docId, 'view']])
    expect(get(w, 'inspector-task-card').get('[data-test="inspector-slot-edit"]').attributes('title')).toBe('편집')
    await get(w, 'inspector-task-card').get('[data-test="inspector-slot-edit"]').trigger('click')
    expect(documentWindowList().map((win) => [win.docId, win.mode])).toEqual([[docId, 'edit']])
    open.mockRestore()
    closeAllDocumentWindows()
  })

  // flowgate.v02.0053 T#7: a Graph card has its own settings (T#2) — the v7 form, not "not supported yet".
  it('shows the settings form on a Graph research card', async () => {
    const node = {
      ...UNASSIGNED_REQ_NODE,
      id: 'gn_n_layout',
      kind: 'research',
      docType: 'N',
      document: null,
      definition: { nodeType: 'document', docType: 'N', paletteItemId: 'doc_n', label: '조사', assignee: null, rootBound: false },
    }
    const w = mountFrame(selectionOf({ ...UNASSIGNED_GRAPH, ownership: 'graph', nodes: [node] }, node.id))
    await clickTab(w, '설정')
    expect(sections(w)).toEqual(['settings'])
    expect(w.find('[data-test="designer-inspector-tab-state"]').exists()).toBe(false)
    expect(get(w, 'inspector-settings').attributes('data-source')).toBe('card')
    expect(w.text()).not.toContain('아직 지원하지 않습니다')
  })

  // flowgate.v02.0053 T#7 (0053 CH: no sentence on an empty screen): a Node with no settings (a document Node of
  // an undecided Flow outside any card or sequence) has an empty settings area — the 0052 T#2 reason is gone.
  it('keeps the settings tab of a Node without settings empty, without a reason line', async () => {
    const node = { ...UNASSIGNED_REQ_NODE, id: 'doc_n', kind: 'research', docType: 'N', document: null }
    const w = mountFrame(selectionOf({ ...UNASSIGNED_GRAPH, ownership: 'unassigned', nodes: [node] }, node.id))
    await clickTab(w, '설정')
    expect(sections(w)).toEqual([])
    expect(get(w, 'designer-inspector-tab-state').text()).toBe('')
    expect(w.text()).not.toContain('실행 설정이 없습니다')
  })
})
