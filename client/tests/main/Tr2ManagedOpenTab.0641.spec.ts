import { flushPromises } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@shared/i18n'
import { useExplorerStore } from '@main/stores/explorer'
import { useTabsStore } from '@main/stores/tabs'
import {
  expectDocumentBranchMounted,
  mountMainPanel,
} from '../helpers/mountMainPanel'

vi.mock('@shared/api', () => ({
  default: { head: vi.fn(), get: vi.fn(), post: vi.fn(), patch: vi.fn(), delete: vi.fn() },
  getRequest: vi.fn().mockResolvedValue({ data: { runs: [] } }),
  patchRequest: vi.fn(),
  postRequest: vi.fn(),
  downloadBlobRequest: vi.fn(),
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
    copyMentToClipboard: vi.fn(),
  }),
  splitGroupId: () => ({ module: 'default', group: '0641' }),
}))

const PID = 'flowgate'
const GID = 'flowgate.default.0641'
const OTHER = 'flowgate.default.other'

function fileTab(groupId: string) {
  return {
    id: `git:${groupId}:f1`,
    title: 'a.py',
    path: 'src/a.py',
    type: 'md' as const,
    mdPath: 'src/a.py',
    projectId: PID,
    gitGroupId: groupId,
    gitCommit: 'a'.repeat(40),
    readonly: false,
  }
}

beforeEach(() => {
  setActivePinia(createPinia())
  i18n.global.locale.value = 'en'
})

describe('TR2 managed already-open source tabs (0641)', () => {
  it('removes edit UI reactively when an already-open tab becomes managed', async () => {
    const tab = fileTab(GID)
    const wrapper = await mountMainPanel({
      tabs: [tab],
      activeTabId: tab.id,
    })
    expectDocumentBranchMounted(wrapper)

    expect(wrapper.find('.md-preview-card').exists()).toBe(true)
    expect(wrapper.find('.edit-dropdown-wrap').exists()).toBe(true)
    expect(useTabsStore().tabs).toHaveLength(1)

    // Same tab object, no reopen: only the live group managed-set changes after refresh.
    useExplorerStore().setTr2ManagedPaths(PID, GID, ['src/a.py'])
    await flushPromises()
    await wrapper.vm.$nextTick()

    expect(wrapper.find('.md-preview-card').exists()).toBe(true)
    expect(wrapper.find('.edit-dropdown-wrap').exists()).toBe(false)
    expect(useTabsStore().tabs).toHaveLength(1)
    expect(useTabsStore().activeTabId).toBe(tab.id)

    // Cancel unlocks the already-open source tab without a reopen.
    useExplorerStore().setTr2ManagedPaths(PID, GID, [])
    await flushPromises()
    await wrapper.vm.$nextTick()
    expect(wrapper.find('.edit-dropdown-wrap').exists()).toBe(true)
    expect(useTabsStore().activeTabId).toBe(tab.id)

    // Forward reapply relocks that same tab from the live snapshot.
    useExplorerStore().setTr2ManagedPaths(PID, GID, ['src/a.py'])
    await flushPromises()
    await wrapper.vm.$nextTick()
    expect(wrapper.find('.edit-dropdown-wrap').exists()).toBe(false)
    expect(useTabsStore().activeTabId).toBe(tab.id)
  })

  it('does not remove edit UI for the same relative path in another group', async () => {
    useExplorerStore().setTr2ManagedPaths(PID, GID, ['src/a.py'])
    const tab = fileTab(OTHER)
    const wrapper = await mountMainPanel({
      tabs: [tab],
      activeTabId: tab.id,
    })
    expectDocumentBranchMounted(wrapper)
    await flushPromises()

    expect(wrapper.find('.edit-dropdown-wrap').exists()).toBe(true)
    expect(useTabsStore().activeTabId).toBe(tab.id)
  })
})
