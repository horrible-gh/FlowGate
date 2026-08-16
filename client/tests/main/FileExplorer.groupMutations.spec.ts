import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@shared/i18n'
import FileExplorer from '@main/components/FileExplorer.vue'
import FileTreeNode from '@main/components/FileTreeNode.vue'
import { useLayoutStore } from '@main/stores/layout'
import { useAiInvokeRunsStore } from '@main/stores/aiInvokeRuns'

// flowgate.default.0327 T0004 (B0001 / NR0003) — "브랜치를 변경하면 우측 마우스 버튼이
// 거의 동작을 안한다 / 폴더·파일 생성이나 업로드 같은게 안된다".
//
// The explorer used to read "a group is selected" as "read-only": the project-root
// context menu never opened at all, and every create/upload entry was stripped from
// the per-node menu — including for the group the user was actively working in. The
// server distinguishes the two cases (slots now carry `writable`), so these tests pin
// that the UI follows that signal rather than the mere presence of a selection, and
// that what must STAY blocked really does: every mutation in a group with no worktree.
//
// Delete is included and follows the same rule as create/upload — it targets the
// group's own worktree, so it never touches the base checkout that the finalize E3
// guard watches. (NR0003 권고 4 kept it blocked on the opposite premise.)

const { getRequest, apiGet, apiDelete, postRequest, uploadFiles, downloadBlobRequest } = vi.hoisted(() => ({
  getRequest: vi.fn(),
  apiGet: vi.fn(),
  apiDelete: vi.fn(),
  postRequest: vi.fn(),
  uploadFiles: vi.fn(),
  downloadBlobRequest: vi.fn(),
}))

vi.mock('@shared/api', () => ({
  default: { head: vi.fn(), get: apiGet, post: vi.fn(), patch: vi.fn(), delete: apiDelete },
  getRequest,
  patchRequest: vi.fn(),
  postRequest,
  downloadBlobRequest,
}))
vi.mock('@main/composables/useFileUpload', () => ({
  useFileUpload: () => ({ collectDropFiles: vi.fn(async () => []), uploadFiles }),
}))

const GROUP = 'flowgate.default.0327'

// The real ContextMenu teleports to <body>; stubbing it keeps the items inside the
// wrapper so a test can read what the menu actually offers.
const ContextMenuStub = { name: 'ContextMenu', props: ['visible', 'x', 'y'], template: '<div v-if="visible" class="ctx"><slot /></div>' }
const ContextMenuItemStub = {
  name: 'ContextMenuItem',
  props: ['icon', 'danger'],
  emits: ['click'],
  template: '<button class="ctx-item" :data-icon="icon" @click="$emit(\'click\')"><slot /></button>',
}

function f(partial: Record<string, unknown>) {
  return { permissions: ['read'], ...partial }
}

const NODES = [
  f({ id: 'src', parent_id: null, type: 'folder', name: 'src', label: 'src', path: 'src' }),
  f({ id: 'leaf', parent_id: 'src', type: 'file', name: 'a.md', label: 'a.md', path: 'src/a.md' }),
]

/** Mount the explorer with one group slot whose worktree is (or is not) live. */
async function mountExplorer(opts: { group?: string | null; writable?: boolean } = {}) {
  useLayoutStore().setFileExplorerCollapsed(false)
  // The slot list arrives through the explorer store's getRequest; the finalize
  // badge is the only api.get the component makes.
  getRequest.mockImplementation(async (url: string) => {
    if (url.includes('/git/status')) {
      return {
        data: {
          status: {
            slots: [{ group_id: GROUP, branch: 'fg-0327', status: 'none', writable: opts.writable ?? false }],
          },
        },
      }
    }
    if (url.includes('/git/groups/')) {
      if (url.endsWith('/changes')) return { data: { data: { changes: [] } } }
      return { data: { data: { branch: 'fg-0327', commit: 'c1', nodes: NODES, worktree_untracked: [] } } }
    }
    return { data: { data: { nodes: NODES } } }
  })
  apiGet.mockResolvedValue({ data: { state: { ahead_count: 0, status: 'none' } } })

  const wrapper = mount(FileExplorer, {
    props: { projectId: 'p' },
    global: {
      plugins: [i18n],
      stubs: {
        ContextMenu: ContextMenuStub,
        ContextMenuItem: ContextMenuItemStub,
        CreateFileFolderModal: true,
      },
    },
  })
  await flushPromises()

  if (opts.group) {
    await wrapper.get('.fx-group-select').setValue(opts.group)
    await flushPromises()
  }
  return wrapper
}

function rootRow(wrapper: ReturnType<typeof mount>) {
  return wrapper.get('.tree-node')
}

function menuLabels(wrapper: ReturnType<typeof mount>): string[] {
  return wrapper.findAll('.ctx-item').map((n) => n.text())
}

describe('FileExplorer group-branch mutations (0327 T0004 / B0001)', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    localStorage.clear()
    vi.clearAllMocks()
  })

  it('opens the project-root context menu on a writable group and offers create + upload', async () => {
    const wrapper = await mountExplorer({ group: GROUP, writable: true })
    await rootRow(wrapper).trigger('contextmenu')
    await flushPromises()

    const labels = menuLabels(wrapper)
    expect(labels).toContain('New Folder')
    expect(labels).toContain('New File')
    expect(labels).toContain('Upload Files')
    expect(labels).toContain('Refresh')
  })

  it('locks a writable group immediately when its AI run becomes active', async () => {
    const wrapper = await mountExplorer({ group: GROUP, writable: true })
    useAiInvokeRunsStore().trackStarted({
      run_id: 'aiv_0424',
      group_id: GROUP,
      doc_ref: `${GROUP}.0001-R`,
      status: 'running',
    })
    await flushPromises()
    await rootRow(wrapper).trigger('contextmenu')
    await flushPromises()

    const labels = menuLabels(wrapper)
    expect(labels).toContain('Refresh')
    expect(labels).not.toContain('New Folder')
    expect(labels).not.toContain('Upload Files')
    expect(wrapper.text()).toContain('AI run')
  })

  it('still opens the root menu on a worktree-less group, but only with the read-only entries', async () => {
    const wrapper = await mountExplorer({ group: GROUP, writable: false })
    await rootRow(wrapper).trigger('contextmenu')
    await flushPromises()

    const labels = menuLabels(wrapper)
    // The original complaint: the menu did not open AT ALL. Refresh is always safe.
    expect(labels).toContain('Refresh')
    // NR0003 권고 5 — a group with no worktree stays fully read-only.
    expect(labels).not.toContain('New Folder')
    expect(labels).not.toContain('Upload Files')
  })

  it('passes the selected group to the create modal so the file lands in that worktree', async () => {
    const wrapper = await mountExplorer({ group: GROUP, writable: true })
    const modal = wrapper.findComponent({ name: 'CreateFileFolderModal' })
    expect(modal.props('groupId')).toBe(GROUP)
  })

  it('sends the group id with a root upload', async () => {
    const wrapper = await mountExplorer({ group: GROUP, writable: true })
    await rootRow(wrapper).trigger('drop', { dataTransfer: { items: [{}] } })
    await flushPromises()

    expect(uploadFiles).toHaveBeenCalledTimes(1)
    expect(uploadFiles.mock.calls[0][4]).toBe(GROUP)
  })

  it('does not upload at all when the selected group has no worktree', async () => {
    const wrapper = await mountExplorer({ group: GROUP, writable: false })
    await rootRow(wrapper).trigger('drop', { dataTransfer: { items: [{}] } })
    await flushPromises()

    expect(uploadFiles).not.toHaveBeenCalled()
  })

  it('marks the writable group view as editable rather than read-only', async () => {
    const rw = await mountExplorer({ group: GROUP, writable: true })
    expect(rw.get('.fx-readonly-badge').classes()).toContain('fx-readonly-badge--rw')
    expect(rw.get('.fx-readonly-badge').text()).toContain('Editing')

    setActivePinia(createPinia())
    const ro = await mountExplorer({ group: GROUP, writable: false })
    expect(ro.get('.fx-readonly-badge').classes()).not.toContain('fx-readonly-badge--rw')
    expect(ro.get('.fx-readonly-badge').text()).toContain('read-only')
  })

  it('leaves the base checkout view untouched', async () => {
    const wrapper = await mountExplorer()
    expect(wrapper.find('.fx-readonly-badge').exists()).toBe(false)
    await rootRow(wrapper).trigger('contextmenu')
    await flushPromises()
    expect(menuLabels(wrapper)).toContain('New Folder')
  })
})

// ── The per-node menu ────────────────────────────────────────────────────────

const FILE = { permissions: ['read'], parent_id: null, id: 'f1', type: 'file', name: 'a.md', label: 'a.md', path: 'src/a.md' } as any
const DIR = { permissions: ['read'], parent_id: null, id: 'd1', type: 'folder', name: 'src', label: 'src', path: 'src' } as any

async function mountNode(target: any, props: Record<string, unknown> = {}) {
  const wrapper = mount(FileTreeNode, {
    props: { node: target, allNodes: [target], projectId: 'p1', ...props },
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
  // The stub only renders while visible, so open the menu the way a user does.
  await wrapper.get('.tree-node').trigger('contextmenu')
  await flushPromises()
  return wrapper
}

describe('FileTreeNode in a writable group worktree (0327 T0004)', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    localStorage.clear()
    vi.clearAllMocks()
  })

  it('offers create and upload on a folder once the group is writable', async () => {
    const wrapper = await mountNode(DIR, { readonly: false, groupId: GROUP })
    expect(wrapper.find('[data-icon="folder-simple-plus"]').exists()).toBe(true)
    expect(wrapper.find('[data-icon="file-plus"]').exists()).toBe(true)
    expect(wrapper.find('[data-icon="upload-simple"]').exists()).toBe(true)
  })

  it('offers delete wherever the tree is writable, and nowhere else', async () => {
    // Supersedes NR0003 권고 4: a writable group worktree accepts delete like any
    // other mutation…
    const writable = await mountNode(FILE, { readonly: false, groupId: GROUP })
    expect(writable.find('[data-icon="trash"]').exists()).toBe(true)

    // …while a group with no worktree stays fully read-only.
    const readonly = await mountNode(FILE, { readonly: true, groupId: GROUP })
    expect(readonly.find('[data-icon="trash"]').exists()).toBe(false)

    // Base checkout is unchanged — delete is still available there.
    const base = await mountNode(FILE, { readonly: false })
    expect(base.find('[data-icon="trash"]').exists()).toBe(true)
  })

  it('sends group_id so the delete resolves the group worktree, not base', async () => {
    apiDelete.mockResolvedValue({ data: { deleted: 'src/a.md', type: 'file' } })
    const wrapper = await mountNode(FILE, { readonly: false, groupId: GROUP })

    await wrapper.find('[data-icon="trash"]').trigger('click')
    wrapper.findComponent({ name: 'ConfirmModal' }).vm.$emit('confirm')
    await flushPromises()

    expect(apiDelete).toHaveBeenCalledWith(
      '/api/v1/projects/p1/files',
      { data: { path: 'src/a.md', type: 'file', group_id: GROUP } },
    )
  })

  it('omits group_id when deleting from the base checkout', async () => {
    apiDelete.mockResolvedValue({ data: { deleted: 'src/a.md', type: 'file' } })
    const wrapper = await mountNode(FILE, { readonly: false })

    await wrapper.find('[data-icon="trash"]').trigger('click')
    wrapper.findComponent({ name: 'ConfirmModal' }).vm.$emit('confirm')
    await flushPromises()

    expect(apiDelete).toHaveBeenCalledWith(
      '/api/v1/projects/p1/files',
      { data: { path: 'src/a.md', type: 'file', group_id: undefined } },
    )
  })

  it('offers download in a group view and reads it from that group (NR0003 권고 3)', async () => {
    downloadBlobRequest.mockResolvedValue({ headers: {}, data: new Blob(['x']) })
    const wrapper = await mountNode(FILE, { readonly: true, groupId: GROUP })

    const download = wrapper.find('[data-icon="download-simple"]')
    expect(download.exists()).toBe(true)
    await download.trigger('click')
    await flushPromises()

    expect(downloadBlobRequest).toHaveBeenCalledWith(
      '/api/v1/projects/p1/files/download',
      { path: 'src/a.md', group_id: GROUP },
    )
  })

  it('omits group_id when downloading from the base checkout', async () => {
    downloadBlobRequest.mockResolvedValue({ headers: {}, data: new Blob(['x']) })
    const wrapper = await mountNode(FILE, { readonly: false })

    await wrapper.find('[data-icon="download-simple"]').trigger('click')
    await flushPromises()

    expect(downloadBlobRequest).toHaveBeenCalledWith(
      '/api/v1/projects/p1/files/download',
      { path: 'src/a.md' },
    )
  })
})
