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
      'features/none: contributes no tab, header action, header meta, header kind or header menu',
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
    expect(sections(w)).toEqual(['gateCard'])
    expect(get(w, 'inspector-gate-card').find('[data-test="inspector-slot-name"]').text()).toBe('확인 결과')
    const confirm = get(w, 'designer-inspector-actions').get('[data-test="gate-confirm"]')
    expect(confirm.attributes('disabled')).toBeDefined()
  })

  it('a Graph R Node without a document: the 요구사항 line and the AI 위임 settings, the static badge (no switch without a document)', async () => {
    const node = { ...UNASSIGNED_REQ_NODE, id: 'gn_r', document: null }
    const w = mountFrame(selectionOf({ ...UNASSIGNED_GRAPH, ownership: 'graph', nodes: [node] }, node.id))
    await flushPromises()
    expect(tabLabels(w)).toEqual(['문서', 'Q&A', '첨부', '설정'])
    expect(sections(w)).toEqual(['requirementCard'])
    expect(get(w, 'inspector-requirement-card').find('[data-test="inspector-slot-empty"]').text()).toBe('아직 없음')
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
    expect(sections(w)).toEqual(['researchCard'])
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
    expect(get(w, 'designer-inspector-tab-state').text()).toContain('연결된 문서가 없습니다.')
    await clickTab(w, '첨부')
    expect(get(w, 'designer-inspector-tab-state').attributes('data-state')).toBe('empty')
    await clickTab(w, '설정')
    expect(get(w, 'designer-inspector-tab-state').attributes('data-state')).toBe('disabled')
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
    const open = vi.spyOn(window, 'open').mockImplementation(() => null)
    // flowgate.v02.0052 T#3: the ↗ tooltip says it opens the v0.1 screen in a new tab that may sign in again.
    expect(get(w, 'inspector-task-card').get('[data-test="inspector-slot-open"]').attributes('title')).toBe(
      '새 탭에서 v0.1 문서 화면으로 엽니다. 새 탭에서는 다시 로그인해야 할 수 있습니다.',
    )
    await get(w, 'inspector-task-card').get('[data-test="inspector-slot-open"]').trigger('click')
    expect(open).toHaveBeenCalledWith(`/main/?doc=${encodeURIComponent(SELECTIONS.v01.node.document!.docId)}`, '_blank', 'noopener')
    open.mockRestore()
  })

  it('shows unavailable settings for a Graph research card without a settings contribution', async () => {
    const node = { ...UNASSIGNED_REQ_NODE, id: 'gn_n_layout', kind: 'research', docType: 'N', document: null }
    const w = mountFrame(selectionOf({ ...UNASSIGNED_GRAPH, ownership: 'graph', nodes: [node] }, node.id))
    await clickTab(w, '설정')
    expect(get(w, 'designer-inspector-tab-state').attributes('data-state')).toBe('disabled')
    // flowgate.v02.0052 T#2: it says why — a Graph Node's run settings are not supported in the Inspector yet.
    expect(get(w, 'designer-inspector-tab-state').text()).toContain('Graph 플로우 Node의 실행 설정은 Inspector에서 아직 지원하지 않습니다.')
  })

  it('names why the settings tab is empty for an undecided Flow (flowgate.v02.0052 T#2)', async () => {
    const node = { ...UNASSIGNED_REQ_NODE, id: 'doc_n', kind: 'research', docType: 'N', document: null }
    const w = mountFrame(selectionOf({ ...UNASSIGNED_GRAPH, ownership: 'unassigned', nodes: [node] }, node.id))
    await clickTab(w, '설정')
    expect(get(w, 'designer-inspector-tab-state').text()).toContain('실행 방식(v0.1 · Graph)이 정해지지 않은 플로우라 실행 설정이 없습니다.')
  })
})
