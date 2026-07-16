import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@shared/i18n'
import FileExplorer from '@main/components/FileExplorer.vue'
import { useLayoutStore } from '@main/stores/layout'

// 0245 R0001 / NR0003 §F4 — the accordion's whole difficulty is the cascade: a
// folder's children are only mounted while it is expanded, so "expand all" cannot
// reach them by prop or event and would open one level per press. These tests mount
// the REAL recursive FileTreeNode (no stub) so a regression to per-node local state
// shows up as a subtree that stays hidden.

const { getRequest, apiGet } = vi.hoisted(() => ({ getRequest: vi.fn(), apiGet: vi.fn() }))

vi.mock('@shared/api', () => ({
  default: { head: vi.fn(), get: apiGet, post: vi.fn(), patch: vi.fn() },
  getRequest,
  patchRequest: vi.fn(),
  postRequest: vi.fn(),
  downloadBlobRequest: vi.fn(),
}))

function f(partial: Record<string, unknown>) {
  return { permissions: ['read'], ...partial }
}

// src/ > deep/ > nested/ > leaf.txt — three folder levels, so a one-level-only
// expand is distinguishable from a real cascade.
const NODES = [
  f({ id: 'src', parent_id: null, type: 'folder', name: 'src', label: 'src', path: 'src' }),
  f({ id: 'deep', parent_id: 'src', type: 'folder', name: 'deep', label: 'deep', path: 'src/deep' }),
  f({ id: 'nested', parent_id: 'deep', type: 'folder', name: 'nested', label: 'nested', path: 'src/deep/nested' }),
  f({ id: 'leaf', parent_id: 'nested', type: 'file', name: 'leaf.txt', label: 'leaf.txt', path: 'src/deep/nested/leaf.txt' }),
]

async function mountExplorer() {
  // Tree-accordion cases exercise the expanded frame explicitly; the application
  // default is intentionally folded by the approved vertical-panel prototype.
  useLayoutStore().setFileExplorerCollapsed(false)
  getRequest.mockResolvedValue({ data: { data: { nodes: NODES } } })
  apiGet.mockResolvedValue({ data: { status: { slots: [] } } })
  const wrapper = mount(FileExplorer, {
    props: { projectId: 'p' },
    global: { plugins: [i18n] },
  })
  await flushPromises()
  return wrapper
}

function labels(wrapper: ReturnType<typeof mount>): string[] {
  return wrapper.findAll('.tree-lbl').map((n) => n.text())
}

describe('FileExplorer tree accordion (0245 R0001)', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    localStorage.clear()
    vi.clearAllMocks()
  })

  it('starts collapsed: only the top-level folder is rendered', async () => {
    const wrapper = await mountExplorer()
    expect(labels(wrapper)).toContain('src')
    expect(labels(wrapper)).not.toContain('deep')
  })

  it('expands every level in one press, including nodes that mount only as their parent opens', async () => {
    const wrapper = await mountExplorer()
    await wrapper.get('[data-test="file-explorer-accordion"]').trigger('click')
    await flushPromises()

    const shown = labels(wrapper)
    expect(shown).toContain('deep')
    expect(shown).toContain('nested')
    // The file three levels down proves the cascade rather than a single level.
    expect(shown).toContain('leaf.txt')
  })

  it('collapses the whole tree on the second press', async () => {
    const wrapper = await mountExplorer()
    await wrapper.get('[data-test="file-explorer-accordion"]').trigger('click')
    await flushPromises()
    await wrapper.get('[data-test="file-explorer-accordion"]').trigger('click')
    await flushPromises()

    expect(labels(wrapper)).toContain('src')
    expect(labels(wrapper)).not.toContain('deep')
  })

  it('reflects the tree state in the button label so the press is predictable', async () => {
    const wrapper = await mountExplorer()
    const btn = () => wrapper.get('[data-test="file-explorer-accordion"]')
    expect(btn().attributes('aria-label')).toBe('Expand all')
    expect(btn().attributes('aria-pressed')).toBe('false')

    await btn().trigger('click')
    await flushPromises()
    expect(btn().attributes('aria-label')).toBe('Collapse all')
    expect(btn().attributes('aria-pressed')).toBe('true')
  })

  it('folds only the file frame and hides its tree accordion', async () => {
    const wrapper = await mountExplorer()
    const layout = useLayoutStore()
    const documentState = layout.documentExplorerCollapsed
    const panelToggle = wrapper.get('[data-test="file-explorer-panel-toggle"]')

    await panelToggle.trigger('click')
    await flushPromises()

    expect(layout.fileExplorerCollapsed).toBe(true)
    expect(layout.documentExplorerCollapsed).toBe(documentState)
    expect(panelToggle.attributes('aria-expanded')).toBe('false')
    expect(wrapper.find('[data-test="file-explorer-accordion"]').exists()).toBe(false)
  })

  it('does not persist file-tree expansion across a remount (unchanged behaviour)', async () => {
    const wrapper = await mountExplorer()
    await wrapper.get('[data-test="file-explorer-accordion"]').trigger('click')
    await flushPromises()
    expect(labels(wrapper)).toContain('leaf.txt')

    setActivePinia(createPinia())
    const fresh = await mountExplorer()
    expect(labels(fresh)).not.toContain('deep')
  })
})
