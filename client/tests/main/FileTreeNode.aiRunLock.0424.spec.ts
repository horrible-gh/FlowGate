import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import FileTreeNode from '@main/components/FileTreeNode.vue'
import { useAiInvokeRunsStore } from '@main/stores/aiInvokeRuns'

// flowgate.default.0424 TR0005 rework — rejection: "AI실행중에 버튼들이 안눌리게
// 하던가 없애야지 토스트 띄우면 다인가?" (make the buttons unclickable or remove
// them while AI is running — a toast after the fact is not enough).
//
// The prior revision wired groupBusy into FileTreeNode's restoreFile item only.
// New folder / new file / upload files / upload folder / delete stayed fully
// clickable during an active AI run — the server's 423 plus a toast was the only
// thing standing between the user and the request. These tests pin that all five
// are now :disabled (with the shared AI-running hint as their title) once the
// node's group has an active run, and that drag-and-drop — which bypasses the
// context menu and its disabled buttons entirely — is blocked too.

const { uploadFiles } = vi.hoisted(() => ({ uploadFiles: vi.fn() }))

vi.mock('@shared/api', () => ({
  default: { head: vi.fn(), get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
  extractApiErrorMessage: (_error: unknown, fallback: string) => fallback,
  downloadBlobRequest: vi.fn(),
}))
vi.mock('@main/composables/useFileUpload', () => ({
  useFileUpload: () => ({ collectDropFiles: vi.fn(async () => [{ name: 'a.txt' }]), uploadFiles }),
}))

const GROUP = 'flowgate.default.0424'
const ContextMenuStub = { name: 'ContextMenu', props: ['visible', 'x', 'y'], template: '<div v-if="visible" class="ctx"><slot /></div>' }
const ContextMenuItemStub = {
  name: 'ContextMenuItem',
  props: ['icon', 'danger', 'disabled', 'title'],
  emits: ['click'],
  template: '<button class="ctx-item" :data-icon="icon" :disabled="disabled" :title="title" @click="$emit(\'click\')"><slot /></button>',
}

const DIR = { permissions: ['read'], parent_id: null, id: 'd1', type: 'folder', name: 'src', label: 'src', path: 'src' } as any

async function mountNode(props: Record<string, unknown> = {}) {
  const wrapper = mount(FileTreeNode, {
    props: { node: DIR, allNodes: [DIR], projectId: 'p1', readonly: false, groupId: GROUP, ...props },
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

describe('FileTreeNode AI-run mutation lock (0424)', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    i18n.global.locale.value = 'en'
    vi.clearAllMocks()
  })

  it('disables new folder / new file / upload / delete once the group run starts', async () => {
    const wrapper = await mountNode()
    useAiInvokeRunsStore().trackStarted({
      run_id: 'aiv_0424',
      group_id: GROUP,
      doc_ref: `${GROUP}.0001-R`,
      status: 'running',
    })
    await flushPromises()

    for (const icon of ['folder-simple-plus', 'file-plus']) {
      const items = wrapper.findAll(`[data-icon="${icon}"]`)
      expect(items.length).toBeGreaterThan(0)
      items.forEach((item) => {
        expect(item.attributes('disabled')).toBeDefined()
        expect(item.attributes('title')).toContain('AI run')
      })
    }
    const uploadItems = wrapper.findAll('[data-icon="upload-simple"]')
    expect(uploadItems.length).toBe(2)
    uploadItems.forEach((item) => {
      expect(item.attributes('disabled')).toBeDefined()
      expect(item.attributes('title')).toContain('AI run')
    })
  })

  it('leaves the same actions enabled while the group is idle', async () => {
    const wrapper = await mountNode()
    const newFolder = wrapper.get('[data-icon="folder-simple-plus"]')
    const upload = wrapper.findAll('[data-icon="upload-simple"]')
    expect(newFolder.attributes('disabled')).toBeUndefined()
    upload.forEach((item) => expect(item.attributes('disabled')).toBeUndefined())
  })

  it('disables delete on a file node once the group run starts', async () => {
    const FILE = { permissions: ['read'], parent_id: null, id: 'f1', type: 'file', name: 'a.md', label: 'a.md', path: 'src/a.md' } as any
    const wrapper = await mountNode({ node: FILE, allNodes: [FILE] })
    useAiInvokeRunsStore().trackStarted({
      run_id: 'aiv_0424',
      group_id: GROUP,
      doc_ref: `${GROUP}.0001-R`,
      status: 'running',
    })
    await flushPromises()

    const del = wrapper.get('[data-icon="trash"]')
    expect(del.attributes('disabled')).toBeDefined()
    expect(del.attributes('title')).toContain('AI run')
  })

  it('blocks drag-and-drop upload while the group run is active (bypasses the disabled menu items)', async () => {
    const wrapper = await mountNode()
    useAiInvokeRunsStore().trackStarted({
      run_id: 'aiv_0424',
      group_id: GROUP,
      doc_ref: `${GROUP}.0001-R`,
      status: 'running',
    })
    await flushPromises()

    await wrapper.get('.tree-node').trigger('drop', { dataTransfer: { items: [{}] } })
    await flushPromises()

    expect(uploadFiles).not.toHaveBeenCalled()
  })

  it('allows drag-and-drop upload while the group is idle', async () => {
    const wrapper = await mountNode()
    await wrapper.get('.tree-node').trigger('drop', { dataTransfer: { items: [{}] } })
    await flushPromises()

    expect(uploadFiles).toHaveBeenCalledTimes(1)
  })
})
