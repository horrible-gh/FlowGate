import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@shared/i18n'
import GroupExplorer from '@main/components/GroupExplorer.vue'
import { useExplorerStore } from '@main/stores/explorer'
import { useLayoutStore } from '@main/stores/layout'

// 0245 R0001 / NR0003 §F4·§F5 — as in the file explorer, these mount the REAL
// recursive GroupTreeNode so the cascade is exercised. The document tree also
// persists expansion, and other surfaces (dashboard / notification navigation)
// reveal a document by expanding its ancestors; both must keep working.

const { getRequest } = vi.hoisted(() => ({ getRequest: vi.fn() }))

vi.mock('@shared/api', () => ({
  default: { head: vi.fn(), get: vi.fn(), post: vi.fn(), patch: vi.fn() },
  getRequest,
  patchRequest: vi.fn(),
  postRequest: vi.fn(),
}))

function n(partial: Record<string, unknown>) {
  return {
    type_code: null,
    number: null,
    filename: null,
    has_md: false,
    md_path: null,
    ...partial,
  }
}

// project > module > group > document — three expandable levels above the leaf.
const NODES = [
  n({ id: 'project:p', parent_id: null, node_type: 'project', label: 'P' }),
  n({ id: 'module:p:default', parent_id: 'project:p', node_type: 'module', label: 'default' }),
  n({ id: 'p.default.0001', parent_id: 'module:p:default', node_type: 'group', label: 'G1' }),
  n({
    id: 'p.default.0001.0001-R',
    parent_id: 'p.default.0001',
    node_type: 'document',
    type_code: 'R',
    label: '[R]: r1',
    has_md: true,
    md_path: 'r1.md',
  }),
]

// attachTo is opt-in: only the focus case needs a real document.activeElement, and
// attaching leaks DOM between cases unless the caller unmounts.
async function mountExplorer(opts: { attach?: boolean } = {}) {
  getRequest.mockResolvedValue({ data: { data: { nodes: NODES } } })
  const wrapper = mount(GroupExplorer, {
    props: { projectId: 'p' },
    global: { plugins: [i18n] },
    ...(opts.attach ? { attachTo: document.body } : {}),
  })
  await flushPromises()
  return wrapper
}

function labels(wrapper: ReturnType<typeof mount>): string[] {
  return wrapper.findAll('.tree-lbl').map((el) => el.text())
}

describe('GroupExplorer tree accordion (0245 R0001)', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    localStorage.clear()
    vi.clearAllMocks()
  })

  it('expands every level in one press, down to the document leaf', async () => {
    const wrapper = await mountExplorer()
    expect(labels(wrapper)).not.toContain('default')

    await wrapper.get('[data-test="group-explorer-accordion"]').trigger('click')
    await flushPromises()

    const shown = labels(wrapper)
    expect(shown).toContain('default')
    expect(shown).toContain('G1')
    expect(shown).toContain('[R]: r1')
  })

  it('collapses the whole tree on the second press', async () => {
    const wrapper = await mountExplorer()
    const btn = () => wrapper.get('[data-test="group-explorer-accordion"]')
    await btn().trigger('click')
    await flushPromises()
    await btn().trigger('click')
    await flushPromises()

    expect(labels(wrapper)).toContain('P')
    expect(labels(wrapper)).not.toContain('default')
  })

  it('persists expansion under the established key, so it survives a remount', async () => {
    const wrapper = await mountExplorer()
    await wrapper.get('[data-test="group-explorer-accordion"]').trigger('click')
    await flushPromises()
    expect(localStorage.getItem('flowgate:grp-exp:p:module:p:default')).toBe('1')

    setActivePinia(createPinia())
    const fresh = await mountExplorer()
    expect(labels(fresh)).toContain('G1')
  })

  it('reveals a document by expanding its ancestors, without waiting for a remount', async () => {
    // Guards the dashboard / notification navigation path (NR0003 §F5): it used to
    // write localStorage and rely on the explorer remounting to read it back.
    const wrapper = await mountExplorer()
    expect(labels(wrapper)).not.toContain('G1')

    const store = useExplorerStore()
    store.expandGroupAncestors('p', NODES, 'p.default.0001.0001-R')
    await flushPromises()

    expect(labels(wrapper)).toContain('G1')
    expect(labels(wrapper)).toContain('[R]: r1')
  })

  it('folds only the document frame and restores its body independently', async () => {
    const wrapper = await mountExplorer()
    const layout = useLayoutStore()
    const fileState = layout.fileExplorerCollapsed
    const panelToggle = wrapper.get('[data-test="document-explorer-panel-toggle"]')

    await panelToggle.trigger('click')
    await flushPromises()
    expect(layout.documentExplorerCollapsed).toBe(true)
    expect(layout.fileExplorerCollapsed).toBe(fileState)
    expect(panelToggle.attributes('aria-expanded')).toBe('false')
    expect(wrapper.find('[data-test="group-explorer-accordion"]').exists()).toBe(false)
    expect(wrapper.find('.sdb-scroll').exists()).toBe(false)

    await panelToggle.trigger('click')
    await flushPromises()
    expect(layout.documentExplorerCollapsed).toBe(false)
    expect(wrapper.find('.sdb-scroll').exists()).toBe(true)
  })

  // NR0004 §5.4 — one exposure policy per panel: an action whose effect is invisible
  // or broken while folded is hidden. The search toggle reveals a box that lives in
  // the folded-away body, so it hides alongside the tree accordion. The refresh and
  // show-final-approved controls stay: they act on data, and the result is simply
  // there on re-expand.
  it('hides the search toggle while the frame is folded', async () => {
    const wrapper = await mountExplorer()
    expect(wrapper.find('[data-test="explorer-search-toggle"]').exists()).toBe(true)

    await wrapper.get('[data-test="document-explorer-panel-toggle"]').trigger('click')
    await flushPromises()
    expect(wrapper.find('[data-test="explorer-search-toggle"]').exists()).toBe(false)

    await wrapper.get('[data-test="document-explorer-panel-toggle"]').trigger('click')
    await flushPromises()
    expect(wrapper.find('[data-test="explorer-search-toggle"]').exists()).toBe(true)
  })

  // NR0004 §7.9 — folding unmounts the body but not the component, so an active
  // search survives the round-trip and comes back with its query and results.
  it('keeps an active search across a fold/unfold round-trip', async () => {
    const wrapper = await mountExplorer()
    await wrapper.get('[data-test="explorer-search-toggle"]').trigger('click')
    await wrapper.get('[data-test="explorer-search-input"]').setValue('r1')
    await flushPromises()
    expect(wrapper.find('[data-test="explorer-search-input"]').exists()).toBe(true)

    const toggle = () => wrapper.get('[data-test="document-explorer-panel-toggle"]')
    await toggle().trigger('click')
    await flushPromises()
    expect(wrapper.find('[data-test="explorer-search-input"]').exists()).toBe(false)

    await toggle().trigger('click')
    await flushPromises()
    const input = wrapper.get('[data-test="explorer-search-input"]')
    expect(input.exists()).toBe(true)
    expect((input.element as HTMLInputElement).value).toBe('r1')
    // Still in search mode: the results list, not the tree, owns the body.
    expect(wrapper.find('[data-test="group-explorer-accordion"]').exists()).toBe(false)
  })

  // NR0004 §7.11 — the frame toggle is the one control that must never be v-if'd away,
  // or folding by keyboard would drop focus to <body> and strand the user.
  it('keeps keyboard focus on the frame toggle across a fold/unfold', async () => {
    const wrapper = await mountExplorer({ attach: true })
    try {
      const toggle = wrapper.get('[data-test="document-explorer-panel-toggle"]')
      const el = toggle.element as HTMLButtonElement
      el.focus()
      expect(document.activeElement).toBe(el)

      await toggle.trigger('click')
      await flushPromises()
      expect(document.activeElement).toBe(el)
      expect(toggle.attributes('aria-expanded')).toBe('false')

      await toggle.trigger('click')
      await flushPromises()
      expect(document.activeElement).toBe(el)
      expect(toggle.attributes('aria-expanded')).toBe('true')
    } finally {
      wrapper.unmount()
    }
  })

  it('hides the accordion while search results replace the tree', async () => {
    const wrapper = await mountExplorer()
    await wrapper.get('[data-test="explorer-search-toggle"]').trigger('click')
    await wrapper.get('[data-test="explorer-search-input"]').setValue('r1')
    await flushPromises()

    expect(wrapper.find('[data-test="group-explorer-accordion"]').exists()).toBe(false)
  })
})
