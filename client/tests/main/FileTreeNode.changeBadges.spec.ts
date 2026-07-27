import { mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@shared/i18n'
import FileTreeNode from '@main/components/FileTreeNode.vue'
import { useExplorerStore } from '@main/stores/explorer'

// flowgate.default.0333 T0004 / B0001 — 파일트리 변경상태 뱃지.
// NR0003: the badges existed, but their computeds were gated on `readonly && groupId`
// while 0327 T0004 had redefined `readonly` to mean "this group has NO live worktree".
// A normal, writable group branch is therefore readonly=false, so the gate never fired
// and the badges silently read the BASE checkout's change list instead of the group's.
// These tests pin the corrected rule: the data source follows `groupId` alone.

vi.mock('@shared/api', () => ({
  default: { head: vi.fn(), get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
  getRequest: vi.fn(),
  patchRequest: vi.fn(),
  postRequest: vi.fn(),
  downloadBlobRequest: vi.fn(),
}))
vi.mock('@main/components/common/useToast', () => ({ useToast: () => ({ showToast: vi.fn() }) }))
vi.mock('@main/composables/useFileUpload', () => ({
  useFileUpload: () => ({ collectDropFiles: vi.fn(), uploadFiles: vi.fn() }),
}))

const PID = 'p1'
const GID = 'flowgate.default.0333'

function node(partial: Record<string, unknown>) {
  return { permissions: ['read'], parent_id: null, ...partial } as any
}

const FILE = node({ id: 'f1', type: 'file', name: 'a.md', label: 'a.md', path: 'docs/a.md' })
const DIR = node({ id: 'd1', type: 'folder', name: 'docs', label: 'docs', path: 'docs' })

function mountNode(target: any, props: Record<string, unknown> = {}) {
  return mount(FileTreeNode, {
    props: { node: target, allNodes: [target], projectId: PID, ...props },
    global: {
      plugins: [i18n],
      stubs: {
        ContextMenu: true,
        ContextMenuItem: true,
        ConfirmModal: true,
        CreateFileFolderModal: true,
        AppIcon: true,
        FileTreeNode: true,
      },
    },
  })
}

/** '' | 'new' | 'dirty' — what the row actually renders. */
function badge(wrapper: ReturnType<typeof mount>): string {
  if (wrapper.find('.tree-new-marker').exists()) return 'new'
  if (wrapper.find('.tree-dirty-marker').exists()) return 'dirty'
  return ''
}

describe('FileTreeNode change badges — writable group branch (B0001)', () => {
  beforeEach(() => setActivePinia(createPinia()))

  // The regression itself: a live worktree means readonly=false, and that used to
  // wipe out both badges even though the group's own change data was in the store.
  it('shows the NEW badge for an untracked file in a WRITABLE group', () => {
    useExplorerStore().setGroupUntrackedFiles(PID, GID, ['docs/a.md'])
    expect(badge(mountNode(FILE, { groupId: GID, readonly: false }))).toBe('new')
  })

  it('shows the MODIFIED badge for a tracked-changed file in a WRITABLE group', () => {
    useExplorerStore().$patch({ groupChangedFiles: { [`${PID}:${GID}`]: ['docs/a.md'] } })
    expect(badge(mountNode(FILE, { groupId: GID, readonly: false }))).toBe('dirty')
  })

  // The pre-0333 fallback path, kept intact: a group without a live worktree still
  // reads its OWN data, not base's — only the base checkout reads base.
  it('still reads group data when the group is read-only (no live worktree)', () => {
    useExplorerStore().setGroupUntrackedFiles(PID, GID, ['docs/a.md'])
    expect(badge(mountNode(FILE, { groupId: GID, readonly: true }))).toBe('new')
  })

  // The bug's signature: base data must never bleed into a group tree. Before the
  // fix a writable group rendered exactly this base list and so showed a false badge.
  it('ignores the BASE change lists while a group is selected', () => {
    const store = useExplorerStore()
    store.setBaseUntrackedFiles(PID, ['docs/a.md'])
    store.setBaseDirtyFiles(PID, ['docs/a.md'])
    expect(badge(mountNode(FILE, { groupId: GID, readonly: false }))).toBe('')
  })

  it('keeps the base checkout (no group selected) on the base change lists', () => {
    useExplorerStore().setBaseUntrackedFiles(PID, ['docs/a.md'])
    expect(badge(mountNode(FILE, { groupId: null, readonly: false }))).toBe('new')
  })

  it('propagates the NEW badge to an ancestor folder in a writable group', () => {
    useExplorerStore().setGroupUntrackedFiles(PID, GID, ['docs/a.md'])
    expect(badge(mountNode(DIR, { groupId: GID, readonly: false }))).toBe('new')
  })

  // B0001 also asked whether DELETE behaves. A deleted file has no tree node to badge
  // (the same structural limit VSCode's explorer has), but the group's change list
  // carries its D-status path, so the folder it was deleted from is marked changed —
  // the deletion is never invisible in the tree.
  it('marks the ancestor folder of a DELETED file as modified', () => {
    useExplorerStore().$patch({ groupChangedFiles: { [`${PID}:${GID}`]: ['docs/gone.md'] } })
    expect(badge(mountNode(DIR, { groupId: GID, readonly: false }))).toBe('dirty')
  })

  // Priority contract (template order): NEW wins over MODIFIED on a folder holding both.
  it('prefers NEW over MODIFIED on a folder that holds both', () => {
    const store = useExplorerStore()
    store.setGroupUntrackedFiles(PID, GID, ['docs/fresh.md'])
    store.$patch({ groupChangedFiles: { [`${PID}:${GID}`]: ['docs/old.md'] } })
    expect(badge(mountNode(DIR, { groupId: GID, readonly: false }))).toBe('new')
  })
})
