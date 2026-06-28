import { shallowMount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@shared/i18n'
import MainPanel from '@main/components/MainPanel.vue'

const {
  requestWorkflowDecision,
  copyMentToClipboard,
} = vi.hoisted(() => ({
  requestWorkflowDecision: vi.fn().mockResolvedValue({
    raw_token: 'workflow-token',
    token_id: 'tok-1',
    expires_at: '',
    scratch_dir: 'C:/scratch/tok-1',
    action_scope: 'workflow_decide',
    doc_ref: 'test.none.0002.0001-R',
    mention: 'workflow decision mention',
  }),
  copyMentToClipboard: vi.fn().mockResolvedValue(true),
}))

vi.mock('@shared/api', () => ({
  default: { head: vi.fn(), get: vi.fn(), post: vi.fn(), patch: vi.fn() },
  getRequest: vi.fn().mockResolvedValue({ data: {} }),
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
    requestWorkflowDecision,
    composeMention: (token: any) => token?.mention ?? '',
    copyMentToClipboard,
  }),
  splitGroupId: (gid: string) => ({ groupCode: gid }),
}))

const payload = {
  docId: 'test.none.0002.0001-R',
  projectId: 'test',
  groupId: 'test.none.0002',
  docRef: 'test.none.0002.0001-R',
}

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

const writeText = vi.fn().mockResolvedValue(undefined)

beforeEach(() => {
  setActivePinia(createPinia())
  requestWorkflowDecision.mockClear()
  copyMentToClipboard.mockClear()
  writeText.mockClear()
  // B0001: the handler now writes the composed mention to the clipboard directly (via the
  // activation-preserving deferred path), not through copyMentToClipboard. jsdom has no
  // ClipboardItem, so the deferred helper degrades to awaiting the text and calling writeText.
  Object.defineProperty(navigator, 'clipboard', {
    value: { writeText },
    configurable: true,
  })
})

describe('MainPanel workflow decision worker actions', () => {
  it('copies the dedicated workflow decision mention', async () => {
    const wrapper = mountPanel()

    await (wrapper.vm as any).onWorkflowDecisionCopyMention(payload)

    expect(requestWorkflowDecision).toHaveBeenCalledWith(payload.docId)
    expect(writeText).toHaveBeenCalledWith('workflow decision mention')
  })

  it('opens the command selector with the dedicated worker environment', async () => {
    const wrapper = mountPanel()

    await (wrapper.vm as any).onWorkflowDecisionInvokeCommand(payload)

    expect((wrapper.vm as any).pendingEnvOverrides).toEqual({
      FLOWGATE_TOKEN: 'workflow-token',
      FLOWGATE_SCRATCH: 'C:/scratch/tok-1',
    })
    expect((wrapper.vm as any).commandSelectorVisible).toBe(true)
  })
})
