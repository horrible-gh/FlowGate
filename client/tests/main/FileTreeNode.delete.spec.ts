import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@shared/i18n'
import FileTreeNode from '@main/components/FileTreeNode.vue'
import { useExplorerStore } from '@main/stores/explorer'
import { useTabsStore } from '@main/stores/tabs'

// flowgate.default.0267 TR0005 / NR0003 필수 테스트 — file & folder deletion:
// confirm-modal gating (no request on cancel), delete request payload, open-tab &
// selection cleanup (incl. folder sub-paths), base-dirty refresh from the response,
// read-only menu suppression, and tree preservation on failure.

const { apiDelete, showToast } = vi.hoisted(() => ({ apiDelete: vi.fn(), showToast: vi.fn() }))

vi.mock('@shared/api', () => ({
  default: { head: vi.fn(), get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: apiDelete },
  getRequest: vi.fn(),
  patchRequest: vi.fn(),
  postRequest: vi.fn(),
  downloadBlobRequest: vi.fn(),
}))
vi.mock('@main/components/common/useToast', () => ({ useToast: () => ({ showToast }) }))
vi.mock('@main/composables/useFileUpload', () => ({
  useFileUpload: () => ({ collectDropFiles: vi.fn(), uploadFiles: vi.fn() }),
}))

const ContextMenuStub = { name: 'ContextMenu', template: '<div class="ctx"><slot /></div>' }
const ContextMenuItemStub = {
  name: 'ContextMenuItem',
  props: ['icon', 'danger'],
  emits: ['click'],
  template: '<button class="ctx-item" :data-icon="icon" @click="$emit(\'click\')"><slot /></button>',
}
const ConfirmModalStub = {
  name: 'ConfirmModal',
  props: ['visible', 'title', 'message', 'danger', 'confirmLabel'],
  emits: ['confirm', 'update:visible'],
  template: '<div v-if="visible" class="confirm"><button class="confirm-ok" @click="$emit(\'confirm\')" /></div>',
}

function node(partial: Record<string, unknown>) {
  return { permissions: ['read'], parent_id: null, ...partial } as any
}

const FILE = node({ id: 'f1', type: 'file', name: 'a.md', label: 'a.md', path: 'docs/a.md' })
const DIR = node({ id: 'dir1', type: 'folder', name: 'dir1', label: 'dir1', path: 'docs/dir1' })
const DIR_CHILD = node({ id: 'c1', parent_id: 'dir1', type: 'file', name: 'x.md', label: 'x.md', path: 'docs/dir1/x.md' })

function mountNode(target: any, allNodes: any[], props: Record<string, unknown> = {}) {
  return mount(FileTreeNode, {
    props: { node: target, allNodes, projectId: 'p1', ...props },
    global: {
      plugins: [i18n],
      stubs: {
        ContextMenu: ContextMenuStub,
        ContextMenuItem: ContextMenuItemStub,
        ConfirmModal: ConfirmModalStub,
        CreateFileFolderModal: true,
        AppIcon: true,
        FileTreeNode: true,
      },
    },
  })
}

async function clickDelete(wrapper: ReturnType<typeof mount>) {
  await wrapper.find('[data-icon="trash"]').trigger('click')
}

describe('FileTreeNode deletion (TR0005 / NR0003)', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    localStorage.clear()
    vi.clearAllMocks()
  })

  it('deletes a file: request payload, tab/selection cleanup, base-dirty refresh', async () => {
    apiDelete.mockResolvedValue({ data: { deleted: 'docs/a.md', type: 'file', base_git: { dirty: true, files: ['docs/a.md'] } } })
    const explorer = useExplorerStore()
    const tabs = useTabsStore()
    explorer.selectedFileNodeId = 'f1'
    tabs.tabs.push({ id: 'f1', title: 'a.md', path: 'docs/a.md', type: 'md', projectId: 'p1' })
    tabs.tabs.push({ id: 'other', title: 'b.md', path: 'docs/b.md', type: 'md', projectId: 'p1' })
    const setBaseDirty = vi.spyOn(explorer, 'setBaseDirtyFiles')
    const invalidate = vi.spyOn(explorer, 'invalidateProject')

    const wrapper = mountNode(FILE, [FILE])
    await clickDelete(wrapper)          // opens the confirm modal — no request yet
    expect(apiDelete).not.toHaveBeenCalled()
    await wrapper.find('.confirm-ok').trigger('click')
    await flushPromises()

    expect(apiDelete).toHaveBeenCalledWith(
      '/api/v1/projects/p1/files',
      { data: { path: 'docs/a.md', type: 'file', group_id: undefined } },
    )
    // deleted file's tab is closed; unrelated tab survives
    expect(tabs.tabs.map((t) => t.id)).toEqual(['other'])
    expect(explorer.selectedFileNodeId).toBeNull()
    expect(setBaseDirty).toHaveBeenCalledWith('p1', ['docs/a.md'])
    expect(invalidate).toHaveBeenCalledWith('p1')
    expect(wrapper.emitted('tree-changed')).toBeTruthy()
  })

  it('does not issue a request when the confirmation is cancelled', async () => {
    const wrapper = mountNode(FILE, [FILE])
    await clickDelete(wrapper)          // modal opens, user never confirms
    await flushPromises()
    expect(apiDelete).not.toHaveBeenCalled()
    expect(wrapper.emitted('tree-changed')).toBeFalsy()
  })

  it('recursively cleans tabs and selection under a deleted folder', async () => {
    apiDelete.mockResolvedValue({ data: { deleted: 'docs/dir1', type: 'folder', base_git: { files: [] } } })
    const explorer = useExplorerStore()
    const tabs = useTabsStore()
    explorer.selectedFileNodeId = 'c1'  // a file inside the deleted folder is selected
    tabs.tabs.push({ id: 'c1', title: 'x.md', path: 'docs/dir1/x.md', type: 'md', projectId: 'p1' })
    tabs.tabs.push({ id: 'keep', title: 'other.md', path: 'docs/other.md', type: 'md', projectId: 'p1' })

    const wrapper = mountNode(DIR, [DIR, DIR_CHILD])
    await clickDelete(wrapper)
    await wrapper.find('.confirm-ok').trigger('click')
    await flushPromises()

    expect(apiDelete).toHaveBeenCalledWith(
      '/api/v1/projects/p1/files',
      { data: { path: 'docs/dir1', type: 'folder', group_id: undefined } },
    )
    expect(tabs.tabs.map((t) => t.id)).toEqual(['keep'])
    expect(explorer.selectedFileNodeId).toBeNull()
  })

  it('keeps the tree intact and toasts on failure', async () => {
    apiDelete.mockRejectedValue({ response: { status: 404, data: { error: { code: 'NOT_FOUND' } } } })
    const explorer = useExplorerStore()
    const invalidate = vi.spyOn(explorer, 'invalidateProject')

    const wrapper = mountNode(FILE, [FILE])
    await clickDelete(wrapper)
    await wrapper.find('.confirm-ok').trigger('click')
    await flushPromises()

    expect(showToast).toHaveBeenCalledTimes(1)
    expect(showToast.mock.calls[0][1]).toBe('danger')
    expect(showToast.mock.calls[0][0]).toBeTruthy()
    expect(invalidate).not.toHaveBeenCalled()      // tree not invalidated on failure
    expect(wrapper.emitted('tree-changed')).toBeFalsy()
  })

  it('hides the delete menu item in the read-only group-branch view', async () => {
    const editable = mountNode(FILE, [FILE], { readonly: false })
    expect(editable.find('[data-icon="trash"]').exists()).toBe(true)
    const readonly = mountNode(FILE, [FILE], { readonly: true, groupId: 'flowgate.default.0267' })
    expect(readonly.find('[data-icon="trash"]').exists()).toBe(false)
  })
})
