import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import GroupTreeNode from '@main/components/GroupTreeNode.vue'
import { useAiInvokeRunsStore } from '@main/stores/aiInvokeRuns'

vi.mock('@shared/api', () => ({
  postRequest: vi.fn(),
  extractApiErrorMessage: (_error: unknown, fallback: string) => fallback,
}))

const GROUP = 'flowgate.default.0424'
const ContextMenuStub = {
  name: 'ContextMenu',
  props: ['visible', 'x', 'y'],
  template: '<div v-if="visible" class="ctx"><slot /></div>',
}
const ContextMenuItemStub = {
  name: 'ContextMenuItem',
  props: ['icon', 'danger', 'disabled', 'title'],
  emits: ['click'],
  template: '<button class="ctx-item" :data-icon="icon" :disabled="disabled" :title="title" @click="$emit(\'click\')"><slot /></button>',
}

function mountGroup() {
  const node = {
    id: GROUP,
    parent_id: 'flowgate.default',
    node_type: 'group',
    label: '0424',
    title: '0424',
    module: 'default',
    permissions: ['read', 'write'],
    is_final_approved: false,
    is_discarded: false,
  } as any
  return mount(GroupTreeNode, {
    props: {
      node,
      allNodes: [node],
      treeNodes: [node],
      // 0454 T0004 — childrenIndex/treeChildrenIndex are now required/optional props
      // carrying the parent->children Map GroupExplorer.vue builds; this single-node
      // fixture has no children, so an empty Map is the correct index for it.
      childrenIndex: new Map(),
      treeChildrenIndex: new Map(),
      projectId: 'flowgate',
    },
    global: {
      plugins: [i18n],
      stubs: {
        ContextMenu: ContextMenuStub,
        ContextMenuItem: ContextMenuItemStub,
        CreateEditGroupModal: true,
        GroupDiscardModal: true,
        GroupTokenIssueModal: true,
        AppIcon: true,
        GroupTreeNode: true,
      },
    },
  })
}

describe('GroupTreeNode AI-run mutation lock (0424)', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    i18n.global.locale.value = 'en'
  })

  it('disables group mutations but leaves token issuance outside the lease lock', async () => {
    const wrapper = mountGroup()
    useAiInvokeRunsStore().trackStarted({
      run_id: 'aiv_0424',
      group_id: GROUP,
      doc_ref: `${GROUP}.0001-R`,
      status: 'running',
    })
    await flushPromises()
    await wrapper.get('.tree-node').trigger('contextmenu')
    await flushPromises()

    const edit = wrapper.get('[data-icon="pencil-simple"]')
    const dispose = wrapper.get('[data-icon="trash"]')
    expect(edit.attributes('disabled')).toBeDefined()
    expect(dispose.attributes('disabled')).toBeDefined()
    expect(edit.attributes('title')).toContain('AI run')

    const token = wrapper.find('[data-icon="key"]')
    if (token.exists()) expect(token.attributes('disabled')).toBeUndefined()
  })
})
