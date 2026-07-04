import { flushPromises, shallowMount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@shared/i18n'
import MainPanel from '@main/components/MainPanel.vue'
import { useProjectStore } from '@main/stores/project'

const { getRequest } = vi.hoisted(() => ({
  getRequest: vi.fn().mockResolvedValue({ data: {} }),
}))

vi.mock('@shared/api', () => ({
  default: { head: vi.fn(), get: vi.fn(), post: vi.fn(), patch: vi.fn() },
  getRequest,
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

function mountPanel(props: Record<string, unknown> = {}) {
  return shallowMount(MainPanel, {
    props,
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

beforeEach(() => {
  setActivePinia(createPinia())
  getRequest.mockClear()
})

describe('MainPanel overview refresh button', () => {
  it('emits refresh-overview when the overview refresh button is clicked', async () => {
    const projectStore = useProjectStore()
    projectStore.currentProjectId = 'proj-1'

    const wrapper = mountPanel()
    // Let the mount-time dashboard fetch settle so the button leaves its
    // initial-loading (disabled) state.
    await flushPromises()
    const btn = wrapper.find('.overview-refresh')
    expect(btn.exists()).toBe(true)
    expect(btn.attributes('disabled')).toBeUndefined()

    await btn.trigger('click')

    expect(wrapper.emitted('refresh-overview')).toBeTruthy()
    expect(wrapper.emitted('refresh-overview')).toHaveLength(1)
  })

  it('disables the overview refresh button when no project is selected', () => {
    const projectStore = useProjectStore()
    projectStore.currentProjectId = null

    const wrapper = mountPanel()
    const btn = wrapper.find('.overview-refresh')
    expect(btn.exists()).toBe(true)
    expect(btn.attributes('disabled')).toBeDefined()
  })

  it('refetches open queries when the overview refresh token changes', async () => {
    const projectStore = useProjectStore()
    projectStore.currentProjectId = 'proj-1'

    const wrapper = mountPanel({ overviewRefreshToken: 0 })
    await flushPromises()
    getRequest.mockClear()

    await wrapper.setProps({ overviewRefreshToken: 1 })
    await flushPromises()

    expect(getRequest).toHaveBeenCalledWith('/api/v1/q', { project_id: 'proj-1' })
  })
})
