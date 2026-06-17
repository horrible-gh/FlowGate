import { shallowMount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@shared/i18n'
import MainPanel from '@main/components/MainPanel.vue'
import { useTabsStore } from '@main/stores/tabs'

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

describe('MainPanel openDocFromQuery (dashboard 열린 질의)', () => {
  it('opens the HOST document with its real typeCode (not a Q-tree viewer)', () => {
    // TR0005 rework: the click must open the document the Q is bound to so MdViewer
    // loads it (`:doc-id="tab.typeCode ? tab.id : null"`). Opening type:'qtui' (the
    // QTDetailViewer) was the wrong target; opening type:'md' WITHOUT typeCode was
    // the original bug (doc-id → null → no_md_file).
    const store = useTabsStore()
    const openTabSpy = vi.spyOn(store, 'openTab')

    const wrapper = mountPanel()

    ;(wrapper.vm as any).openDocFromQuery({
      doc_id: 'flowgate.default.0077.0001-B',
      seq: 2,
      title: '대시보드 질의응답 화면 버그',
      type_code: 'B',
    })

    expect(openTabSpy).toHaveBeenCalledWith(
      expect.objectContaining({
        id: 'flowgate.default.0077.0001-B',
        title: 'flowgate.default.0077.0001-B — 대시보드 질의응답 화면 버그',
        path: '',
        type: 'md',
        typeCode: 'B',
      }),
    )
    // Must NOT fall back to the QT viewer tab type.
    expect(openTabSpy).not.toHaveBeenCalledWith(
      expect.objectContaining({ type: 'qtui' }),
    )
  })

  it('falls back to bare doc_id title and undefined typeCode when metadata is missing', () => {
    const store = useTabsStore()
    const openTabSpy = vi.spyOn(store, 'openTab')

    const wrapper = mountPanel()

    ;(wrapper.vm as any).openDocFromQuery({
      doc_id: 'flowgate.default.0077.0009-T',
      seq: 1,
      title: null,
      type_code: null,
    })

    expect(openTabSpy).toHaveBeenCalledWith(
      expect.objectContaining({
        id: 'flowgate.default.0077.0009-T',
        title: 'flowgate.default.0077.0009-T',
        type: 'md',
        typeCode: undefined,
      }),
    )
  })
})
