import { shallowMount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@shared/i18n'
import MainPanel from '@main/components/MainPanel.vue'

const { getRequest } = vi.hoisted(() => ({
  getRequest: vi.fn().mockResolvedValue({ data: { questions: [] } }),
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
    copyMentToClipboard: vi.fn(),
  }),
  splitGroupId: () => ({ module: '', group: '' }),
}))

beforeEach(() => {
  setActivePinia(createPinia())
  localStorage.clear()
  delete window.__accessToken__
  getRequest.mockClear()
})

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
        QTDetailViewer: true,
        NewQModal: true,
      },
    },
  })
}

function makeQueries(n: number) {
  return Array.from({ length: n }, (_, i) => ({
    doc_id: `flowgate.default.0080.000${i}-T`,
    seq: i + 1,
    title: `질의 ${i + 1}`,
    type_code: 'T',
  }))
}

// T0002 (group 0080) Work 1: open queries reuse the activity/workflow fold idiom —
// preview the first OPEN_Q_PREVIEW_COUNT (5) when collapsed, all when expanded.
describe('MainPanel open-queries fold (dashboard 열린 질의)', () => {
  it('previews only OPEN_Q_PREVIEW_COUNT (5) items when the list exceeds it', () => {
    const wrapper = mountPanel()
    ;(wrapper.vm as any).qList = makeQueries(8)

    expect((wrapper.vm as any).allOpenQueries).toHaveLength(8)
    expect((wrapper.vm as any).openQueries).toHaveLength(5)
  })

  it('shows the full list when expanded, then collapses back to the preview', () => {
    const wrapper = mountPanel()
    ;(wrapper.vm as any).qList = makeQueries(8)

    ;(wrapper.vm as any).toggleQListExpanded()
    expect((wrapper.vm as any).qListExpanded).toBe(true)
    expect((wrapper.vm as any).openQueries).toHaveLength(8)

    ;(wrapper.vm as any).toggleQListExpanded()
    expect((wrapper.vm as any).qListExpanded).toBe(false)
    expect((wrapper.vm as any).openQueries).toHaveLength(5)
  })

  it('shows every item without a fold when at or under the preview count', () => {
    const wrapper = mountPanel()
    ;(wrapper.vm as any).qList = makeQueries(5)

    // No slicing loss, and the [전체보기] gate (allOpenQueries.length > 5) stays closed.
    expect((wrapper.vm as any).openQueries).toHaveLength(5)
    expect((wrapper.vm as any).allOpenQueries.length > 5).toBe(false)
  })
})
