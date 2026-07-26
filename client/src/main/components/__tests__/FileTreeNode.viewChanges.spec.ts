// flowgate.default.0326 R0001 / N0004 §1 — the tree's ONLY new affordance is a
// context-menu entry on changed files. N0004 approved exactly that and explicitly
// refused the rest of TR0003 §3 ("파일트리는 나머지 건드리지 말고"), so these tests pin
// both halves: the entry appears (and emits) for a changed file, and nothing else
// about the row's behaviour moves.
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

vi.mock('vue-i18n', () => ({
  useI18n: () => ({ t: (k: string) => k }),
}))
vi.mock('@shared/api', () => ({
  default: { get: vi.fn(), delete: vi.fn(), head: vi.fn() },
  downloadBlobRequest: vi.fn(),
}))

import FileTreeNode from '../FileTreeNode.vue'
import { useExplorerStore, type FileNode } from '../../stores/explorer'

const PROJECT_ID = 'p1'

function fileNode(path: string): FileNode {
  const name = path.split('/').pop() as string
  return {
    id: `id-${path}`,
    parent_id: null,
    type: 'file',
    name,
    label: name,
    path,
    permissions: ['read', 'download'],
  } as FileNode
}

// The context menu teleports to <body>, so every wrapper is attached there and must
// be unmounted between tests — clearing innerHTML instead would detach live Vue nodes.
const mounted: Array<{ unmount: () => void }> = []

function mountNode(node: FileNode, options: { groupId?: string | null } = {}) {
  const wrapper = mount(FileTreeNode, {
    attachTo: document.body,
    props: {
      node,
      allNodes: [node],
      projectId: PROJECT_ID,
      readonly: !!options.groupId,
      groupId: options.groupId ?? null,
    },
  })
  mounted.push(wrapper)
  return wrapper
}

function menuLabels(): string[] {
  return [...document.body.querySelectorAll('.ctx-item')].map(
    (el) => (el.textContent ?? '').trim(),
  )
}

async function openContextMenu(wrapper: ReturnType<typeof mountNode>) {
  await wrapper.find('li').trigger('contextmenu')
}

describe('FileTreeNode — 변경 내용 보기', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  afterEach(() => {
    while (mounted.length) mounted.pop()?.unmount()
  })

  it('is hidden for an unchanged file', async () => {
    const wrapper = mountNode(fileNode('src/clean.ts'))
    await openContextMenu(wrapper)

    expect(menuLabels()).toContain('main.file_tree_node.open')
    expect(menuLabels()).not.toContain('main.file_tree_node.view_changes')
  })

  it('is shown right under 열기 for a modified file, and emits open-diff', async () => {
    const store = useExplorerStore()
    store.setBaseDirtyFiles(PROJECT_ID, ['src/dirty.ts'])
    const node = fileNode('src/dirty.ts')
    const wrapper = mountNode(node)
    await openContextMenu(wrapper)

    const labels = menuLabels()
    expect(labels.indexOf('main.file_tree_node.view_changes')).toBe(
      labels.indexOf('main.file_tree_node.open') + 1,
    )

    const items = [...document.body.querySelectorAll('.ctx-item')] as HTMLElement[]
    items[labels.indexOf('main.file_tree_node.view_changes')].click()
    await wrapper.vm.$nextTick()

    expect(wrapper.emitted('open-diff')?.[0]).toEqual([node])
    // The editor entry point is untouched: no `open` event came from this click.
    expect(wrapper.emitted('open')).toBeUndefined()
  })

  it('is shown for a new (untracked) file', async () => {
    const store = useExplorerStore()
    store.setBaseUntrackedFiles(PROJECT_ID, ['src/fresh.ts'])
    const wrapper = mountNode(fileNode('src/fresh.ts'))
    await openContextMenu(wrapper)

    expect(menuLabels()).toContain('main.file_tree_node.view_changes')
  })

  it('is shown in the read-only group-branch view for a changed file', async () => {
    const store = useExplorerStore()
    store.setGroupUntrackedFiles(PROJECT_ID, 'flowgate.default.0326', ['src/wip.ts'])
    const wrapper = mountNode(fileNode('src/wip.ts'), { groupId: 'flowgate.default.0326' })
    await openContextMenu(wrapper)

    const labels = menuLabels()
    expect(labels).toContain('main.file_tree_node.view_changes')
    // read-only mode still hides the mutating entries — unchanged by this feature.
    expect(labels).not.toContain('main.file_tree_node.download')
    expect(labels).not.toContain('common.delete')
  })

  it('never offers the entry on a folder row', async () => {
    const store = useExplorerStore()
    store.setBaseDirtyFiles(PROJECT_ID, ['src/dirty.ts'])
    const folder = { ...fileNode('src'), type: 'folder' } as FileNode
    const wrapper = mountNode(folder)
    await openContextMenu(wrapper)

    expect(menuLabels()).not.toContain('main.file_tree_node.view_changes')
  })
})
