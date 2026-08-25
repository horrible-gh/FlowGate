import { flushPromises, shallowMount } from '@vue/test-utils'
import { nextTick } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@shared/i18n'
import MainPanel from '@main/components/MainPanel.vue'
import { useTabsStore } from '@main/stores/tabs'

// Regression guard for the workflow-decision-button bug (group 0064, NR0003).
//
// Root cause (NR0003 §2): the active DocHeader was bound with `ref="..."` placed
// inside a v-for, so Vue collected it into an in-place array, and the watch over
// that plain ref never fired on a same-tab unmount→remount (e.g. inline edit).
// The action bar read the registry (`docHeaderRefs[tabId]`) while the decision
// click wrote through `getActiveDocHeader()`; after a remount these pointed at
// DIFFERENT instances — the registry stuck on the DEAD instance — so a decision
// applied to the live instance never reached the action bar.
//
// Fix: a function ref maintains the registry directly (fires on every mount AND
// unmount), and `getActiveDocHeader()` reads the SAME registry. These tests lock
// the invariant the previous mechanism violated.

vi.mock('@shared/api', () => ({
  default: { head: vi.fn(), get: vi.fn().mockResolvedValue({ data: { content: '' } }), post: vi.fn(), patch: vi.fn() },
  getRequest: vi.fn().mockResolvedValue({ data: { content: '' } }),
  patchRequest: vi.fn(),
  postRequest: vi.fn(),
}))

vi.mock('../composables/useShortcuts', () => ({
  useShortcuts: () => ({ register: vi.fn(), unregister: vi.fn() }),
}))

vi.mock('@main/components/common/useToast', () => ({
  useToast: () => ({ showToast: vi.fn() }),
}))

vi.mock('@main/composables/useFlowGateToken', () => ({
  useFlowGateToken: () => ({
    issueToken: vi.fn(),
    requestReview: vi.fn(),
    requestWorkflowDecision: vi.fn(),
    copyMentToClipboard: vi.fn(),
  }),
  splitGroupId: (gid: string) => ({ groupCode: gid }),
}))

function mountPanel() {
  return shallowMount(MainPanel, {
    global: {
      plugins: [i18n],
      stubs: {
        TabBar: true,
        DocHeader: true,
        DocWorkflow: true,
        MdViewer: true,
        TextViewer: true,
        DocInfoPanel: true,
        ReviewActionBar: true,
        ReviewRejectDialog: true,
        DesignHandoffDialog: true,
        NextActionModal: true,
        NextEmptyDocModal: true,
        CommandSelectorModal: true,
        QTDetailViewer: true,
        NewQModal: true,
      },
    },
  })
}

const docTab = {
  id: 'flowgate.default.0064.0001-B',
  title: 'bug',
  path: 'documents/flowgate/main/default/0064/0001-B.md',
  type: 'md' as const,
  typeCode: 'B',
  projectId: 'flowgate',
}

beforeEach(() => {
  setActivePinia(createPinia())
})

describe('MainPanel DocHeader ref identity (group 0064 regression)', () => {
  it('binds the live DocHeader instance and reads/writes the same one', async () => {
    const wrapper = mountPanel()
    useTabsStore().openTab({ ...docTab })
    await nextTick()
    await flushPromises()

    const vm = wrapper.vm as any
    expect(vm.activeTabId).toBe(docTab.id)
    // Read path (action bar) and write path (decision click) resolve to the
    // SAME live instance.
    expect(vm.getActiveDocHeader()).toBeTruthy()
    expect(vm.getActiveDocHeader()).toBe(vm.docHeaderRefs[docTab.id])
  })

  it('re-binds the registry to the live instance after a same-tab unmount→remount', async () => {
    const wrapper = mountPanel()
    useTabsStore().openTab({ ...docTab })
    await nextTick()
    await flushPromises()

    const vm = wrapper.vm as any
    const before = vm.getActiveDocHeader()
    expect(before).toBeTruthy()

    // [문서 수정]: opening the edit modal unmounts DocHeader for this tab.
    await vm.openEditModal({ ...docTab })
    await nextTick()
    await flushPromises()
    expect(vm.docHeaderRefs[docTab.id]).toBeUndefined()
    expect(vm.getActiveDocHeader()).toBeNull()

    // Saving/closing remounts DocHeader in the SAME tab — the exact step the old
    // watch-on-ref missed, stranding the registry on the dead instance.
    vm.closeEditModal()
    await nextTick()
    await flushPromises()

    const after = vm.getActiveDocHeader()
    expect(after).toBeTruthy()
    // THE invariant: read path and write path agree on the live instance,
    // i.e. the registry is not stuck on the dead pre-edit instance.
    expect(after).toBe(vm.docHeaderRefs[docTab.id])
  })

  // 0460 TR0005 rev6 — the [헤더 수정]/[헤더 숨김] toggle inside the raw-content edit
  // modal used to be ORed into DocHeader's v-if, so every click of that in-modal
  // button also unmounted/remounted this background DocHeader (a fresh non-silent
  // fetchDoc() blanks it before it pops back in — "disappears then reappears").
  // DocHeader must stay unmounted for the WHOLE edit session, unaffected by which
  // textarea mode the modal shows, and remount exactly once on close.
  it('does not remount DocHeader when the in-modal header-edit-mode toggle flips', async () => {
    const wrapper = mountPanel()
    useTabsStore().openTab({ ...docTab })
    await nextTick()
    await flushPromises()

    const vm = wrapper.vm as any
    await vm.openEditModal({ ...docTab })
    await nextTick()
    await flushPromises()
    expect(vm.docHeaderRefs[docTab.id]).toBeUndefined()

    vm.toggleHeaderEditMode()
    await nextTick()
    await flushPromises()
    expect(vm.headerEditModeVisible).toBe(true)
    expect(vm.docHeaderRefs[docTab.id]).toBeUndefined()

    vm.toggleHeaderEditMode()
    await nextTick()
    await flushPromises()
    expect(vm.headerEditModeVisible).toBe(false)
    expect(vm.docHeaderRefs[docTab.id]).toBeUndefined()

    vm.closeEditModal()
    await nextTick()
    await flushPromises()
    expect(vm.docHeaderRefs[docTab.id]).toBeTruthy()
  })
})
