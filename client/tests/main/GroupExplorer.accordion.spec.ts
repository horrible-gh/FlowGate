import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@shared/i18n'
import GroupExplorer from '@main/components/GroupExplorer.vue'
import { useExplorerStore } from '@main/stores/explorer'

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

async function mountExplorer() {
  getRequest.mockResolvedValue({ data: { data: { nodes: NODES } } })
  const wrapper = mount(GroupExplorer, {
    props: { projectId: 'p' },
    global: { plugins: [i18n] },
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

  it('hides the accordion while search results replace the tree', async () => {
    const wrapper = await mountExplorer()
    await wrapper.get('[data-test="explorer-search-toggle"]').trigger('click')
    await wrapper.get('[data-test="explorer-search-input"]').setValue('r1')
    await flushPromises()

    expect(wrapper.find('[data-test="group-explorer-accordion"]').exists()).toBe(false)
  })
})
