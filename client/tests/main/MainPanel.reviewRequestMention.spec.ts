import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { expectDocumentBranchMounted, mountMainPanel } from '../helpers/mountMainPanel'

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

// 0394 T0004 (NR0003 §5.1): the review status below is the whole point of both cases,
// and it only reaches MainPanel through DocHeader's exposed values. While MainPanel's
// bootstrap gate is still closed DocHeader is not mounted at all, so the status reads
// back as undefined — and the product's pre-approval branch treats undefined the same
// as 'pending_review'. That made the first case pass without ever using the status it
// planted. mountMainPanel awaits the gate, so the stub is actually consulted.
function mountWith(reviewStatus: string, workflowSteps: string[]) {
  return mountMainPanel({
    tabs: [{ id: PAYLOAD.docId, title: 'DS doc', path: '', type: 'md', typeCode: 'DS' }],
    stubs: {
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
    const wrapper = await mountWith('pending_review', ['DS', 'D'])
    expectDocumentBranchMounted(wrapper)

    await (wrapper.vm as any).onReviewOpenMentionDialog({ ...PAYLOAD })

    expect(requestReview).toHaveBeenCalledTimes(1)
    expect(requestReview).toHaveBeenCalledWith(expect.objectContaining({ doc_id: PAYLOAD.docId }))
    expect(issueToken).not.toHaveBeenCalled()
    expect((wrapper.vm as any).designHandoffVisible).toBe(false)

    wrapper.unmount()
  })

  it('approved doc with design next → opens create-next handoff, not review', async () => {
    const wrapper = await mountWith('approved', ['DS', 'D'])
    expectDocumentBranchMounted(wrapper)

    await (wrapper.vm as any).onReviewOpenMentionDialog({ ...PAYLOAD })

    expect(requestReview).not.toHaveBeenCalled()
    expect((wrapper.vm as any).designHandoffVisible).toBe(true)

    wrapper.unmount()
  })
})
