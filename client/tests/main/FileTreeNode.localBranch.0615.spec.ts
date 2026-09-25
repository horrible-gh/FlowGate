// flowgate.default.0615 T0004 §4.2 — an ordinary local branch node must never show the
// group-only dirty/new badges (they would compare against the wrong tree) and must
// never offer a download (no branch-aware download endpoint exists).
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@shared/i18n'
import FileTreeNode from '@main/components/FileTreeNode.vue'
import { useExplorerStore } from '@main/stores/explorer'

const { downloadBlobRequest } = vi.hoisted(() => ({ downloadBlobRequest: vi.fn() }))

vi.mock('@shared/api', () => ({
  default: { head: vi.fn(), get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
  getRequest: vi.fn(),
  patchRequest: vi.fn(),
  postRequest: vi.fn(),
  downloadBlobRequest,
}))
vi.mock('@main/composables/useFileUpload', () => ({
  useFileUpload: () => ({ collectDropFiles: vi.fn(async () => []), uploadFiles: vi.fn() }),
}))

const ContextMenuStub = { name: 'ContextMenu', props: ['visible', 'x', 'y'], template: '<div v-if="visible" class="ctx"><slot /></div>' }
const ContextMenuItemStub = {
  name: 'ContextMenuItem',
  props: ['icon', 'danger'],
  emits: ['click'],
  template: '<button class="ctx-item" :data-icon="icon" @click="$emit(\'click\')"><slot /></button>',
}

const FILE = { permissions: ['read'], parent_id: null, id: 'f1', type: 'file', name: 'a.md', label: 'a.md', path: 'src/a.md' } as any

async function mountNode(props: Record<string, unknown> = {}) {
  const wrapper = mount(FileTreeNode, {
    props: { node: FILE, allNodes: [FILE], projectId: 'p1', ...props },
    global: {
      plugins: [i18n],
      stubs: {
        ContextMenu: ContextMenuStub,
        ContextMenuItem: ContextMenuItemStub,
        ConfirmModal: true,
        CreateFileFolderModal: true,
        AppIcon: true,
        FileTreeNode: true,
      },
    },
  })
  await wrapper.get('.tree-node').trigger('contextmenu')
  await flushPromises()
  return wrapper
}

beforeEach(() => {
  setActivePinia(createPinia())
  vi.clearAllMocks()
})

describe('FileTreeNode on an ordinary local branch (0615 T0004)', () => {
  it('never shows "변경 내용 보기" even when the base checkout reports the same path as dirty', async () => {
    const explorerStore = useExplorerStore()
    explorerStore.setBaseDirtyFiles('p1', ['src/a.md'])
    const wrapper = await mountNode({ readonly: true, localBranch: 'test-branch' })
    expect(wrapper.find('[data-icon="git-diff"]').exists()).toBe(false)
  })

  it('never shows the new-file badge from the base checkout\'s untracked channel', async () => {
    const explorerStore = useExplorerStore()
    explorerStore.setBaseUntrackedFiles('p1', ['src/a.md'])
    const wrapper = await mountNode({ readonly: true, localBranch: 'test-branch' })
    expect(wrapper.find('[data-icon="git-diff"]').exists()).toBe(false)
  })

  it('hides download on a local branch node', async () => {
    const wrapper = await mountNode({ readonly: true, localBranch: 'test-branch' })
    expect(wrapper.find('[data-icon="download-simple"]').exists()).toBe(false)
    expect(downloadBlobRequest).not.toHaveBeenCalled()
  })

  it('offers no create/upload/delete on a local branch node', async () => {
    const wrapper = await mountNode({ readonly: true, localBranch: 'test-branch' })
    expect(wrapper.find('[data-icon="trash"]').exists()).toBe(false)
    expect(wrapper.find('[data-icon="folder-simple-plus"]').exists()).toBe(false)
    expect(wrapper.find('[data-icon="upload-simple"]').exists()).toBe(false)
  })

  it('still offers download on the base checkout (unaffected)', async () => {
    downloadBlobRequest.mockResolvedValue({ headers: {}, data: new Blob(['x']) })
    const wrapper = await mountNode({ readonly: false })
    expect(wrapper.find('[data-icon="download-simple"]').exists()).toBe(true)
  })
})
