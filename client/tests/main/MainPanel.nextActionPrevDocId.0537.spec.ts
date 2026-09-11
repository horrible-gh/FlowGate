import { shallowMount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@shared/i18n'
import MainPanel from '@main/components/MainPanel.vue'

const { postRequest, getRequest, patchRequest, showToast, issueToken } = vi.hoisted(() => ({
  postRequest: vi.fn(),
  getRequest: vi.fn().mockResolvedValue({ data: {} }),
  patchRequest: vi.fn().mockResolvedValue({ data: {} }),
  showToast: vi.fn(),
  issueToken: vi.fn().mockResolvedValue(null),
}))

vi.mock('@shared/api', () => ({
  default: { head: vi.fn(), get: vi.fn(), post: vi.fn(), patch: vi.fn() },
  getRequest,
  patchRequest,
  postRequest,
}))

vi.mock('../composables/useShortcuts', () => ({
  useShortcuts: () => ({ register: vi.fn(), unregister: vi.fn() }),
}))

vi.mock('@main/components/common/useToast', () => ({
  useToast: () => ({ showToast }),
}))

vi.mock('@main/composables/useFlowGateToken', () => ({
  useFlowGateToken: () => ({
    issueToken,
    requestReview: vi.fn(),
    requestWorkflowDecision: vi.fn(),
    composeMention: (token: any) => token?.mention ?? '',
    copyMentToClipboard: vi.fn(),
  }),
  splitGroupId: (gid: string) => ({ groupCode: gid }),
}))

const GROUP = 'flowgate.default.0537'
const R_ROOT = GROUP + '.0001-R'
const B_ROOT = GROUP + '.0002-B'
const cases = [
  { label: 'AC tab', tabId: GROUP + '.0003-AC', typeCode: 'AC', rootId: R_ROOT },
  { label: 'workflow member tab', tabId: GROUP + '.0004-T', typeCode: 'T', rootId: R_ROOT },
  { label: 'R root tab', tabId: R_ROOT, typeCode: 'R', rootId: R_ROOT },
  { label: 'B root tab', tabId: B_ROOT, typeCode: 'B', rootId: B_ROOT },
] as const

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
        WorkPlanCreateDialog: true,
        CommandSelectorModal: true,
        QTDetailViewer: true,
        NewQModal: true,
      },
    },
  })
}

function seedTab(vm: any, tabId: string, typeCode: string, rootId: string, nextType: 'N' | 'CH') {
  const fetchDoc = vi.fn().mockResolvedValue(undefined)
  vm.docHeaderRefs[tabId] = {
    docTypeCode: typeCode,
    docProjectId: 'flowgate',
    docModule: 'default',
    groupId: GROUP,
    docReviewStatus: ['R', 'B'].includes(typeCode) ? 'wf_in_progress' : 'approved',
    workflowRootType: typeCode === 'B' ? 'B' : 'R',
    workflowSteps: [nextType],
    workflowHeadType: nextType,
    workflowHeadIndex: 0,
    headStatus: 'pending',
    headDocId: null,
    headDocReviewStatus: null,
    nextStepExists: true,
    parentRDocId: ['R', 'B'].includes(typeCode) ? null : rootId,
    fetchDoc,
  }
  return fetchDoc
}

describe('0537 MainPanel direct actions use the canonical workflow root as prev_doc_id', () => {
  beforeEach(() => {
    localStorage.clear()
    setActivePinia(createPinia())
    i18n.global.locale.value = 'en'
    postRequest.mockReset()
    postRequest.mockResolvedValue({ data: { doc_id: GROUP + '.0099-N' } })
    getRequest.mockClear()
    patchRequest.mockClear()
    showToast.mockClear()
    issueToken.mockClear()
  })

  for (const entry of cases) {
    it(entry.label + ': next-approved sends the canonical root and leaves the outgoing tab unread', async () => {
      const wrapper = mountPanel()
      const vm = wrapper.vm as any
      const fetchDoc = seedTab(vm, entry.tabId, entry.typeCode, entry.rootId, 'N')

      vm.onActionBarCreateApproved(entry.tabId)
      await vm.doCreateApprovedDocument()

      expect(postRequest).toHaveBeenCalledWith(
        '/api/v1/documents/next-approved',
        expect.objectContaining({
          prev_doc_id: entry.rootId,
          type_code: 'N',
        }),
      )
      // 0552 T0006 §4 — the switch to the new document is decided right here
      // (`openAfter: true` below), which unmounts this DocHeader. Re-reading the outgoing
      // document only produces a bundle that lands after the unmount and is discarded
      // (0552.0005-NR §3.4). This spec's subject, prev_doc_id, is asserted above.
      expect(fetchDoc).not.toHaveBeenCalled()
      expect(wrapper.emitted('related-doc-created')?.at(-1)?.[0]).toEqual({
        docId: GROUP + '.0099-N',
        openAfter: true,
        projectId: 'flowgate',
      })

      wrapper.unmount()
    })

    it(entry.label + ': next-empty CH sends the canonical root and leaves the outgoing tab unread', async () => {
      const wrapper = mountPanel()
      const vm = wrapper.vm as any
      const fetchDoc = seedTab(vm, entry.tabId, entry.typeCode, entry.rootId, 'CH')
      postRequest.mockResolvedValue({ data: { doc_id: GROUP + '.0099-CH' } })

      await vm.onActionBarCreateConversation(entry.tabId)

      expect(postRequest).toHaveBeenCalledWith(
        '/api/v1/documents/next-empty',
        expect.objectContaining({
          prev_doc_id: entry.rootId,
          type_code: 'CH',
        }),
      )
      // 0552 T0006 §4 — the switch to the new document is decided right here
      // (`openAfter: true` below), which unmounts this DocHeader. Re-reading the outgoing
      // document only produces a bundle that lands after the unmount and is discarded
      // (0552.0005-NR §3.4). This spec's subject, prev_doc_id, is asserted above.
      expect(fetchDoc).not.toHaveBeenCalled()
      expect(wrapper.emitted('related-doc-created')?.at(-1)?.[0]).toEqual({
        docId: GROUP + '.0099-CH',
        openAfter: true,
        projectId: 'flowgate',
      })

      wrapper.unmount()
    })
  }
})