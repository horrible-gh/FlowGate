import { shallowMount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@shared/i18n'
import MainPanel from '@main/components/MainPanel.vue'
import { useTabsStore } from '@main/stores/tabs'

// Regression: the [Request review] button on a NOT-yet-approved doc must copy a review-request
// mention (read the doc → evaluate → submit a verdict via inbox action:review), NOT a
// create-next handoff. The pre-approval branch must take priority over the design-handoff
// / advance (create-next) branches, which are valid only once the doc is approved.

const { getRequest, issueToken, requestReview, copyMentToClipboard } = vi.hoisted(() => ({
  getRequest: vi.fn().mockResolvedValue({ data: {} }),
  issueToken: vi.fn(),
  requestReview: vi.fn().mockResolvedValue({
    raw_token: 'tok',
    token_id: 'id',
    expires_at: '',
    scratch_dir: 'D:/scratch',
    action_scope: 'review',
    doc_ref: 'test.none.0002.0003-DS',
    mention: '## Document information\n---\nREVIEW MENTION',
  }),
  copyMentToClipboard: vi.fn().mockResolvedValue(true),
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
  useFlowGateToken: () => ({ issuing: { value: false }, issueToken, requestReview, copyMentToClipboard }),
  splitGroupId: (gid: string) => ({ groupCode: gid }),
}))

const PAYLOAD = {
  docId: 'test.none.0002.0003-DS',
  projectId: 'test',
  groupId: 'test.none.0002',
  docRef: 'test.none.0002.0003-DS',
}

function mountWith(reviewStatus: string, workflowSteps: string[]) {
  const store = useTabsStore()
  store.tabs = [{ id: PAYLOAD.docId, title: 'DS doc', path: '', type: 'md', typeCode: 'DS' }]
  store.activeTabId = PAYLOAD.docId

  return shallowMount(MainPanel, {
    global: {
      plugins: [i18n],
      stubs: {
        TabBar: true,
        DocHeader: {
          template: '<div />',
          setup() {
            return {
              docLoaded: true,
              docProjectId: 'test',
              groupId: 'test.none.0002',
              docReviewStatus: reviewStatus,
              workflowSteps,
              workflowHeadType: 'D',
            }
          },
        },
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
  localStorage.clear()
  issueToken.mockClear()
  requestReview.mockClear()
  copyMentToClipboard.mockClear()
  Object.defineProperty(navigator, 'clipboard', {
    value: { writeText: vi.fn().mockResolvedValue(undefined) },
    configurable: true,
  })
})

describe('MainPanel review-request vs handoff mention', () => {
  it('pending_review doc → copies a review-request mention (no create-next handoff)', async () => {
    const wrapper = mountWith('pending_review', ['DS', 'D'])
    await wrapper.vm.$nextTick()

    await (wrapper.vm as any).onReviewOpenMentionDialog({ ...PAYLOAD })

    expect(requestReview).toHaveBeenCalledTimes(1)
    expect(requestReview).toHaveBeenCalledWith(expect.objectContaining({ doc_id: PAYLOAD.docId }))
    expect(issueToken).not.toHaveBeenCalled()
    expect((wrapper.vm as any).designHandoffVisible).toBe(false)

    wrapper.unmount()
  })

  it('approved doc with design next → opens create-next handoff, not review', async () => {
    const wrapper = mountWith('approved', ['DS', 'D'])
    await wrapper.vm.$nextTick()

    await (wrapper.vm as any).onReviewOpenMentionDialog({ ...PAYLOAD })

    expect(requestReview).not.toHaveBeenCalled()
    expect((wrapper.vm as any).designHandoffVisible).toBe(true)

    wrapper.unmount()
  })
})
