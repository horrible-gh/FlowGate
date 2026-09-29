import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@shared/i18n'
import FileTreeNode from '@main/components/FileTreeNode.vue'
import { useExplorerStore } from '@main/stores/explorer'

const { getRequest } = vi.hoisted(() => ({ getRequest: vi.fn() }))

vi.mock('@shared/api', () => ({
  default: { head: vi.fn(), get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
  getRequest,
  patchRequest: vi.fn(),
  postRequest: vi.fn(),
  downloadBlobRequest: vi.fn(),
}))
vi.mock('@main/components/common/useToast', () => ({
  useToast: () => ({ showToast: vi.fn() }),
}))
vi.mock('@main/composables/useFileUpload', () => ({
  useFileUpload: () => ({ collectDropFiles: vi.fn(), uploadFiles: vi.fn() }),
}))

const PID = 'flowgate'
const GID = 'flowgate.default.0641'
const OTHER = 'flowgate.default.other'
const FILE = {
  id: 'f1', parent_id: 'd1', type: 'file', name: 'a.py', label: 'a.py',
  path: 'src/a.py', permissions: ['read'],
} as any
const DIR = {
  id: 'd1', parent_id: null, type: 'folder', name: 'src', label: 'src',
  path: 'src', permissions: ['read'],
} as any

const mounted: VueWrapper[] = []

function mountNode(target: any, groupId = GID) {
  const wrapper = mount(FileTreeNode, {
    props: {
      node: target,
      allNodes: target.type === 'folder' ? [target, FILE] : [target],
      projectId: PID,
      groupId,
      readonly: false,
    },
    attachTo: document.body,
    global: {
      plugins: [i18n],
      stubs: {
        AppIcon: true,
        ConfirmModal: true,
        CreateFileFolderModal: true,
      },
    },
  })
  mounted.push(wrapper)
  return wrapper
}

function menuText(): string {
  return document.querySelector('.ctx-menu')?.textContent ?? ''
}

describe('TR2 managed source UX (0641)', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    i18n.global.locale.value = 'en'
    vi.clearAllMocks()
  })

  afterEach(() => {
    while (mounted.length) mounted.pop()!.unmount()
    document.body.innerHTML = ''
  })

  it('refreshes the group-scoped managed set from every live tree payload', async () => {
    getRequest
      .mockResolvedValueOnce({
        data: { data: {
          branch: 'fg-0641',
          commit: 'a'.repeat(40),
          nodes: [FILE],
          tr2_managed_paths: ['src/a.py'],
        } },
      })
      .mockResolvedValueOnce({
        data: { data: {
          branch: 'fg-0641',
          commit: 'a'.repeat(40),
          nodes: [FILE],
          tr2_managed_paths: [],
        } },
      })

    const store = useExplorerStore()
    await store.fetchGroupBranchTree(PID, GID)
    expect(store.isTr2ManagedPath(PID, GID, 'src/a.py')).toBe(true)
    expect(store.hasTr2ManagedDescendant(PID, GID, 'src')).toBe(true)
    expect(store.isTr2ManagedPath(PID, OTHER, 'src/a.py')).toBe(false)

    // The commit is deliberately unchanged: ownership refresh must not depend on
    // a branch-commit change or on the readonly value of an already-open tab.
    await store.fetchGroupBranchTree(PID, GID)
    expect(store.isTr2ManagedPath(PID, GID, 'src/a.py')).toBe(false)
  })

  it('marks managed files and parent folders while preserving ordinary open/read', async () => {
    const store = useExplorerStore()
    store.setTr2ManagedPaths(PID, GID, ['src/a.py'])

    const file = mountNode(FILE)
    const folder = mountNode(DIR)

    expect(file.find('[data-test="tr2-managed-marker"]').exists()).toBe(true)
    expect(folder.find('[data-test="tr2-managed-marker"]').exists()).toBe(true)

    await file.trigger('dblclick')
    expect(file.emitted('open')).toBeTruthy()

    await file.trigger('contextmenu')
    await flushPromises()
    expect(menuText()).toContain('Open')
    expect(menuText()).toContain('Download')
    expect(menuText()).not.toContain('Delete')
  })

  it('keeps the same relative path in another group unmanaged', () => {
    const store = useExplorerStore()
    store.setTr2ManagedPaths(PID, GID, ['src/a.py'])

    expect(store.isTr2ManagedPath(PID, GID, 'src/a.py')).toBe(true)
    expect(store.isTr2ManagedPath(PID, OTHER, 'src/a.py')).toBe(false)
    expect(
      mountNode(FILE, OTHER).find('[data-test="tr2-managed-marker"]').exists(),
    ).toBe(false)
  })

  it('removes delete for a managed file and a folder with a managed descendant', async () => {
    const store = useExplorerStore()
    store.setTr2ManagedPaths(PID, GID, ['src/a.py'])

    const file = mountNode(FILE)
    await file.trigger('contextmenu')
    await flushPromises()
    expect(menuText()).not.toContain('Delete')
    file.unmount()
    mounted.splice(mounted.indexOf(file), 1)
    document.body.innerHTML = ''

    const folder = mountNode(DIR)
    await folder.trigger('contextmenu')
    await flushPromises()
    expect(menuText()).not.toContain('Delete')
  })

  it('removes restore and delete for a deleted managed file', async () => {
    const store = useExplorerStore()
    store.setTr2ManagedPaths(PID, GID, ['src/a.py'])
    store.$patch({
      groupChangedFiles: { [`${PID}:${GID}`]: ['src/a.py'] },
      groupChangeStatuses: { [`${PID}:${GID}`]: { 'src/a.py': 'D' } },
    })

    const file = mountNode(FILE)
    await file.trigger('contextmenu')
    await flushPromises()
    expect(menuText()).not.toContain('Restore File')
    expect(menuText()).not.toContain('Delete')
  })

  it('keeps unmanaged file and folder delete affordances', async () => {
    const file = mountNode(FILE)
    await file.trigger('contextmenu')
    await flushPromises()
    expect(menuText()).toContain('Delete')
    file.unmount()
    mounted.splice(mounted.indexOf(file), 1)
    document.body.innerHTML = ''

    const folder = mountNode(DIR)
    await folder.trigger('contextmenu')
    await flushPromises()
    expect(menuText()).toContain('Delete')
  })
})
