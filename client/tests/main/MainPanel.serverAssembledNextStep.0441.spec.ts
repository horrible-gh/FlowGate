import { shallowMount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { useTabsStore } from '@main/stores/tabs'
import i18n from '@shared/i18n'
import MainPanel from '@main/components/MainPanel.vue'

/**
 * flowgate.default.0441 T0004 item 2 + item 4 — MainPanel's own defense against manual
 * authoring of a server-assembled document (TSR).
 *
 * B0001 / NR0003 §2: while the workflow head is a still-empty TSR slot, every open tab of
 * the group receives nextStepCode='TSR', and on a non-TS tab the action bar used to render
 * the generic next-step drop-up. Its [Create Empty Doc] called /documents/next-empty with
 * type_code=TSR and the server — whose only check was "head type == requested type" —
 * created the document for real. [Copy Mention] had the same reach through an
 * action_scope='new' token.
 *
 * ReviewActionBar no longer offers those items (ReviewActionBar.spec.ts pins that), but the
 * bar is only the display. These handlers are also reached from the workflow strip's
 * next-action list, so they refuse on their own — and say why, rather than returning silently.
 */

const { postRequest, getRequest, patchRequest, showToast, issueToken } = vi.hoisted(() => ({
  postRequest: vi.fn().mockResolvedValue({ data: {} }),
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

const GROUP = 'test.test.0441'
const R_TAB = 'test.test.0441.0001-R'
const TS_TAB = 'test.test.0441.0004-TS'

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

/**
 * The exact state NR0003 reproduced: the TS is approved and the workflow's effective head
 * is its TSR slot, with no result document bound yet. The server hands that head to EVERY
 * document of the group, which is why `headType` is seeded identically on both tabs.
 */
function seedTab(vm: any, tabId: string, typeCode: string, reviewStatus: string, headType = 'TSR') {
  vm.docHeaderRefs[tabId] = {
    docTypeCode: typeCode,
    docProjectId: 'test',
    docModule: 'test',
    groupId: GROUP,
    docReviewStatus: reviewStatus,
    workflowRootType: 'R',
    workflowSteps: ['TS', 'TSR'],
    workflowHeadType: headType,
    workflowHeadIndex: 1,
    headStatus: 'pending',
    headDocId: null,
    headDocReviewStatus: null,
    nextStepExists: true,
    parentRDocId: R_TAB,
  }
}

function refusalMessages(): string[] {
  return showToast.mock.calls
    .map(call => String(call[0]))
    .filter(message => message.includes('created by the server'))
}

describe('0441 MainPanel: a server-assembled next step (TSR) has no manual entry point', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    i18n.global.locale.value = 'en'
    postRequest.mockClear()
    getRequest.mockClear()
    showToast.mockClear()
    issueToken.mockClear()
  })

  it('R root: [Create empty doc] makes no request, opens no dialog, and says why', async () => {
    const wrapper = mountPanel()
    const vm = wrapper.vm as any
    seedTab(vm, R_TAB, 'R', 'wf_in_progress')

    // Precondition: this tab really is in the state that used to expose the drop-up —
    // otherwise an earlier guard (canNextAction) would be doing the refusing instead.
    expect(vm.getWorkflowViewState(R_TAB).mode).toBe('next')
    expect(vm.getWorkflowViewState(R_TAB).nextStepCode).toBe('TSR')
    expect(vm.canOpenNextAction(R_TAB)).toBe(true)

    vm.onActionBarCreateEmpty(R_TAB)
    await wrapper.vm.$nextTick()

    expect(vm.nextEmptyDocModalVisible).toBe(false)
    expect(vm.nextEmptyDocType).not.toBe('TSR')
    expect(postRequest).not.toHaveBeenCalled()
    expect(refusalMessages()).toHaveLength(1)

    wrapper.unmount()
  })

  it('R root: [Copy mention] issues no token', async () => {
    const wrapper = mountPanel()
    const vm = wrapper.vm as any
    seedTab(vm, R_TAB, 'R', 'wf_in_progress')

    vm.onActionBarCopyNextMention(R_TAB)
    await wrapper.vm.$nextTick()

    expect(issueToken).not.toHaveBeenCalled()
    expect(postRequest).not.toHaveBeenCalled()
    expect(refusalMessages()).toHaveLength(1)

    wrapper.unmount()
  })

  it('R root: [Invoke AI] does not open the continuous-work dialog', async () => {
    const wrapper = mountPanel()
    const vm = wrapper.vm as any
    seedTab(vm, R_TAB, 'R', 'wf_in_progress')

    vm.onActionBarContinuousWork(R_TAB)
    await wrapper.vm.$nextTick()

    expect(vm.continuousDialogVisible).toBe(false)
    expect(refusalMessages()).toHaveLength(1)

    wrapper.unmount()
  })

  // ── 0441 TR0005 rev9 ────────────────────────────────────────────────────────────────
  // The rejection was two-part: the standing "open the approved TS and run the test"
  // paragraph had to go, AND the control next to it must not sit disabled while nothing is
  // running. What replaced the paragraph is the button doing what the paragraph asked for,
  // so the handler behind it is now part of this file's contract: it must navigate, and it
  // must still not create/copy/invoke anything.
  it('open-test-scenario opens the group TS document instead of creating anything', async () => {
    // The tabs store rehydrates from localStorage, which is shared across tests in this file.
    localStorage.clear()
    getRequest.mockImplementation((url: string) => {
      if (String(url).includes('/groups/tree')) {
        return Promise.resolve({
          data: {
            data: {
              nodes: [
                { id: GROUP, parent_id: null, node_type: 'group', type_code: null, number: null, label: 'g' },
                { id: R_TAB, parent_id: GROUP, node_type: 'document', type_code: 'R', number: '0001-R', label: 'R doc' },
                { id: TS_TAB, parent_id: GROUP, node_type: 'document', type_code: 'TS', number: '0004-TS', label: 'TS doc', title: 'Scenario' },
              ],
            },
          },
        })
      }
      return Promise.resolve({ data: {} })
    })

    const wrapper = mountPanel()
    const vm = wrapper.vm as any
    seedTab(vm, R_TAB, 'R', 'wf_in_progress')

    await vm.onOpenTestScenarioDoc({ docId: R_TAB, projectId: 'test', groupId: GROUP })
    await wrapper.vm.$nextTick()

    const tabsStore = useTabsStore()
    expect(tabsStore.tabs.map((t: any) => t.id)).toContain(TS_TAB)
    expect(tabsStore.activeTabId).toBe(TS_TAB)
    expect(postRequest).not.toHaveBeenCalled()
    expect(issueToken).not.toHaveBeenCalled()
    expect(vm.nextEmptyDocModalVisible).toBe(false)
    expect(vm.continuousDialogVisible).toBe(false)

    getRequest.mockReset()
    getRequest.mockResolvedValue({ data: {} })
    wrapper.unmount()
  })

  it('open-test-scenario with no TS document in the group opens nothing and says so', async () => {
    localStorage.clear()
    getRequest.mockImplementation((url: string) => {
      if (String(url).includes('/groups/tree')) {
        return Promise.resolve({
          data: {
            data: {
              nodes: [
                { id: GROUP, parent_id: null, node_type: 'group', type_code: null, number: null, label: 'g' },
                { id: R_TAB, parent_id: GROUP, node_type: 'document', type_code: 'R', number: '0001-R', label: 'R doc' },
              ],
            },
          },
        })
      }
      return Promise.resolve({ data: {} })
    })

    const wrapper = mountPanel()
    const vm = wrapper.vm as any
    seedTab(vm, R_TAB, 'R', 'wf_in_progress')

    await vm.onOpenTestScenarioDoc({ docId: R_TAB, projectId: 'test', groupId: GROUP })
    await wrapper.vm.$nextTick()

    const tabsStore = useTabsStore()
    expect(tabsStore.tabs.map((t: any) => t.id)).not.toContain(TS_TAB)
    expect(postRequest).not.toHaveBeenCalled()
    expect(showToast).toHaveBeenCalled()

    getRequest.mockReset()
    getRequest.mockResolvedValue({ data: {} })
    wrapper.unmount()
  })

  it('approved sibling document: the same three entries are refused there too', async () => {
    const wrapper = mountPanel()
    const vm = wrapper.vm as any
    const dTab = 'test.test.0441.0002-D'
    seedTab(vm, dTab, 'D', 'approved')

    expect(vm.getWorkflowViewState(dTab).nextStepCode).toBe('TSR')

    vm.onActionBarCreateEmpty(dTab)
    vm.onActionBarCopyNextMention(dTab)
    vm.onActionBarContinuousWork(dTab)
    await wrapper.vm.$nextTick()

    expect(vm.nextEmptyDocModalVisible).toBe(false)
    expect(vm.continuousDialogVisible).toBe(false)
    expect(issueToken).not.toHaveBeenCalled()
    expect(postRequest).not.toHaveBeenCalled()
    expect(refusalMessages()).toHaveLength(3)

    wrapper.unmount()
  })

  it('the next-action list reaches the same refusal without going through the action bar', async () => {
    const wrapper = mountPanel()
    const vm = wrapper.vm as any
    seedTab(vm, R_TAB, 'R', 'wf_in_progress')
    vm.nextActionModalTabId = R_TAB
    vm.nextActionModalDocRef = R_TAB
    vm.nextActionModalProjectId = 'test'
    vm.nextActionModalGroupId = GROUP
    vm.nextActionModalModuleName = 'test'
    vm.nextActionModalTypeCode = 'TSR'

    vm.onNextActionCreateEmpty()
    await vm.onNextActionCopyMention()
    vm.onNextActionInvokeAi()
    await wrapper.vm.$nextTick()

    expect(vm.nextEmptyDocModalVisible).toBe(false)
    expect(vm.continuousDialogVisible).toBe(false)
    expect(issueToken).not.toHaveBeenCalled()
    expect(postRequest).not.toHaveBeenCalled()
    expect(refusalMessages()).toHaveLength(3)

    wrapper.unmount()
  })

  // ── 0441 TR0005 rev11 ──────────────────────────────────────────────────────────────
  // Review flagged two more NextActionModal delegates that reached issueToken(action_scope
  // ='new') without going through blockServerAssembledNextStep at all: [Invoke command] and
  // [Copy mention (add message)]. Neither has a TS-caret counterpart (that caret only ever
  // offers copy-mention / invoke-ai for a TSR head), so both are refused unconditionally —
  // even on the TS tab, unlike onNextActionCopyMention / onNextActionInvokeAi.
  it('the next-action list refuses [Invoke command] and [Copy mention (add message)] too', async () => {
    const wrapper = mountPanel()
    const vm = wrapper.vm as any
    seedTab(vm, R_TAB, 'R', 'wf_in_progress')
    vm.nextActionModalTabId = R_TAB
    vm.nextActionModalDocRef = R_TAB
    vm.nextActionModalProjectId = 'test'
    vm.nextActionModalGroupId = GROUP
    vm.nextActionModalModuleName = 'test'
    vm.nextActionModalTypeCode = 'TSR'

    await vm.onNextActionInvokeCommand()
    await vm.onNextActionCopyMentionWithMessage()
    await wrapper.vm.$nextTick()

    expect(vm.commandSelectorVisible).toBe(false)
    expect(vm.mmDialogVisible).toBe(false)
    expect(issueToken).not.toHaveBeenCalled()
    expect(postRequest).not.toHaveBeenCalled()
    // No candidate-message / document-type lookups either — the guard fires before
    // onNextActionCopyMentionWithMessage's own GET calls, unlike the unrelated mount-time polling.
    expect(getRequest).not.toHaveBeenCalledWith(expect.stringContaining('/messages'), expect.anything())
    expect(refusalMessages()).toHaveLength(2)

    wrapper.unmount()
  })

  it('control: the TS tab does NOT keep [Invoke command] / [Copy mention (add message)] for a TSR head', async () => {
    const wrapper = mountPanel()
    const vm = wrapper.vm as any
    seedTab(vm, TS_TAB, 'TS', 'approved')
    vm.nextActionModalTabId = TS_TAB
    vm.nextActionModalDocRef = TS_TAB
    vm.nextActionModalProjectId = 'test'
    vm.nextActionModalGroupId = GROUP
    vm.nextActionModalModuleName = 'test'
    vm.nextActionModalTypeCode = 'TSR'

    await vm.onNextActionInvokeCommand()
    await vm.onNextActionCopyMentionWithMessage()
    await wrapper.vm.$nextTick()

    expect(vm.commandSelectorVisible).toBe(false)
    expect(vm.mmDialogVisible).toBe(false)
    expect(issueToken).not.toHaveBeenCalled()
    expect(postRequest).not.toHaveBeenCalled()
    expect(getRequest).not.toHaveBeenCalledWith(expect.stringContaining('/messages'), expect.anything())
    expect(refusalMessages()).toHaveLength(2)

    wrapper.unmount()
  })

  // ── Positive controls ──────────────────────────────────────────────────────────────
  // Without these, every expectation above would also hold for a MainPanel that simply
  // refuses everything.

  it('control: an ordinary next step still opens the empty-document dialog', async () => {
    const wrapper = mountPanel()
    const vm = wrapper.vm as any
    seedTab(vm, R_TAB, 'R', 'wf_in_progress', 'D')

    vm.onActionBarCreateEmpty(R_TAB)
    await wrapper.vm.$nextTick()

    expect(vm.nextEmptyDocModalVisible).toBe(true)
    expect(vm.nextEmptyDocType).toBe('D')
    expect(refusalMessages()).toHaveLength(0)

    wrapper.unmount()
  })

  // ── 0441 TR0005 rev12 ──────────────────────────────────────────────────────────────
  // Review on rev11: this positive control used to mock issueToken and assert only that it
  // was called — issueToken maps a TS-tab copy-mention to a non-continuous /workflow/advance
  // call, which the server (workflow_decision_service.py:935) always 409s for a
  // server-assembled head, so the mocked assertion could not tell a working hand-off from a
  // guaranteed-to-fail one. [Copy mention] on this exact head now goes through the dedicated
  // manned endpoint (test_run_service.issue_test_run_request via POST
  // /documents/test-run-request, keyed to the TS document itself) instead of issueToken, so
  // this asserts the real outgoing request shape rather than a mid-level abstraction.
  it('control: the TS tab keeps its own [Copy mention] and [Invoke AI] on the same TSR head', async () => {
    const wrapper = mountPanel()
    const vm = wrapper.vm as any
    seedTab(vm, TS_TAB, 'TS', 'approved')

    postRequest.mockImplementation((url: string) => {
      if (String(url).includes('/documents/test-run-request')) {
        return Promise.resolve({ data: { mention: 'MENTION_TEXT' } })
      }
      return Promise.resolve({ data: {} })
    })

    vm.onActionBarCopyNextMention(TS_TAB)
    vm.onActionBarContinuousWork(TS_TAB)
    await wrapper.vm.$nextTick()
    await wrapper.vm.$nextTick()

    // The TS document is where the test run is started from, so its split-caret escape
    // hatches are not manual authoring — they hand off to the run. [Copy mention] does so via
    // the manned test-run-request route (doc_id = the TS tab itself, never the root R), and
    // [Invoke AI] via the continuous-work dialog (which sets continuous=true on its own).
    // Only [Create empty doc], which that caret never offers, is absolute.
    expect(postRequest).toHaveBeenCalledWith('/api/v1/documents/test-run-request', { doc_id: TS_TAB })
    expect(issueToken).not.toHaveBeenCalled()
    expect(postRequest).not.toHaveBeenCalledWith('/api/v1/workflow/advance', expect.anything())
    expect(vm.continuousDialogVisible).toBe(true)
    expect(refusalMessages()).toHaveLength(0)

    postRequest.mockReset()
    postRequest.mockResolvedValue({ data: {} })
    wrapper.unmount()
  })

  it('control: [Copy mention] on the TS tab surfaces the real server refusal, not a generic clipboard-failure toast', async () => {
    const wrapper = mountPanel()
    const vm = wrapper.vm as any
    seedTab(vm, TS_TAB, 'TS', 'approved')

    postRequest.mockImplementation((url: string) => {
      if (String(url).includes('/documents/test-run-request')) {
        return Promise.reject({ response: { data: { error: 'run_in_progress' } } })
      }
      return Promise.resolve({ data: {} })
    })

    vm.onActionBarCopyNextMention(TS_TAB)
    await wrapper.vm.$nextTick()
    await wrapper.vm.$nextTick()

    expect(postRequest).toHaveBeenCalledWith('/api/v1/documents/test-run-request', { doc_id: TS_TAB })
    expect(showToast).toHaveBeenCalledWith('A test run is already in progress.', 'danger')

    postRequest.mockReset()
    postRequest.mockResolvedValue({ data: {} })
    wrapper.unmount()
  })

  it('control: [Create empty doc] for a TSR is refused even on the TS tab', async () => {
    const wrapper = mountPanel()
    const vm = wrapper.vm as any
    seedTab(vm, TS_TAB, 'TS', 'approved')

    vm.onActionBarCreateEmpty(TS_TAB)
    await wrapper.vm.$nextTick()

    expect(vm.nextEmptyDocModalVisible).toBe(false)
    expect(postRequest).not.toHaveBeenCalled()
    expect(refusalMessages()).toHaveLength(1)

    wrapper.unmount()
  })
})
