// flowgate.default.0555 T0010 (T#3): the AC document is never a second Git
// finalize owner. ReviewActionBar owns the pre-approval choice and submission;
// after approval the AC card is status-only. Root/header panels keep monitoring
// and recovery, but no GitFinalizePanel is mounted inside FinalApprovalBody.

import { defineComponent, h } from 'vue'
import { flushPromises } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { mountMainPanel } from '../helpers/mountMainPanel'

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

// Keep the workflow view resolution off the real (network-driven) path — the
// order under test depends only on isCompletedDoc, not on the workflow strip.
vi.mock('@main/workflow/workflowViewState', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@main/workflow/workflowViewState')>()
  return {
    ...actual,
    resolveWorkflowViewState: () => ({
      mode: 'review' as const,
      canNextAction: false,
      currentStepCode: null,
      highlightStepCode: null,
      nextStepCode: null,
      nextStepActive: false,
      headDocLabel: null,
      headDocId: null,
      highlightDesignSeries: false,
      stepStates: [],
      nextStepIndex: null,
    }),
  }
})

// The function ref (bindActiveRef) registers this instance's exposed values as
// docHeaderRefs[tabId]; exposing docReviewStatus is what drives isCompletedDoc.
function docHeaderStub(status: string) {
  return defineComponent({
    name: 'DocHeader',
    inheritAttrs: false,
    setup(_props, { expose }) {
      expose({ docReviewStatus: status, groupId: 'p.0265', docTypeCode: 'AC' })
      return () => h('div', { class: 'doc-header-stub' })
    },
  })
}

const GitFinalizePanelStub = defineComponent({
  name: 'GitFinalizePanel',
  inheritAttrs: false,
  setup: () => () => h('div', { class: 'git-fin-stub' }),
})

function baseStubs(status: string) {
  return {
    DocHeader: docHeaderStub(status),
    GitFinalizePanel: GitFinalizePanelStub,
    FinalApprovalGitStatus: false,
  }
}

const AC_TAB = {
  id: 'p.default.0265.0005-AC',
  title: 'final approval',
  path: '',
  type: 'md' as const,
  typeCode: 'AC',
}

async function mountAc(status: string) {
  const wrapper = await mountMainPanel({ tabs: [AC_TAB], stubs: baseStubs(status) })
  await flushPromises()
  return wrapper
}

beforeEach(() => {
  setActivePinia(createPinia())
  localStorage.clear()
  delete (window as any).__accessToken__
  getRequest.mockReset()
  getRequest.mockImplementation((url: string) =>
    url.includes('/git/finalize')
      ? Promise.resolve({ data: { state: { branch: 'group-branch', base_branch: 'main', status: 'merged', ahead_count: 0, behind_count: 0, merge_commit: 'abc123' } } })
      : Promise.resolve({ data: { questions: [] } }),
  )
})

describe('MainPanel — AC has one final-approval execution owner (0555 T#3)', () => {
  it('before final approval renders the approval card without a Git execute panel', async () => {
    const wrapper = await mountAc('pending_review')
    expect(wrapper.find('.ac-final-approval-body').exists()).toBe(true)
    expect(wrapper.find('.git-fin-stub').exists()).toBe(false)
    expect(wrapper.find('[data-test="ac-git-status"]').exists()).toBe(true)
    expect(wrapper.text()).not.toContain('[실행]')
  })

  it('shows terminal Git plus approval-pending without adding an execute surface', async () => {
    getRequest.mockImplementation((url: string) =>
      url.includes('/git/finalize')
        ? Promise.resolve({ data: { state: {
          branch: 'group-branch', base_branch: 'main', status: 'merged',
          ahead_count: 0, behind_count: 0, merge_commit: 'abc123',
          approval_pending: true,
        } } })
        : Promise.resolve({ data: { questions: [] } }),
    )
    const wrapper = await mountAc('pending_review')
    expect(wrapper.text()).toContain('Git 완료 · 승인 미완료')
    expect(wrapper.find('.git-fin-stub').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('[실행]')
  })

  for (const status of ['approved', 'wf_done']) {
    it(`after final approval (${status}) keeps the AC status-only`, async () => {
      const wrapper = await mountAc(status)
      expect(wrapper.find('.ac-final-approval-body').exists()).toBe(true)
      expect(wrapper.find('.git-fin-stub').exists()).toBe(false)
    expect(wrapper.find('[data-test="ac-git-status"]').exists()).toBe(true)
    expect(wrapper.text()).not.toContain('[실행]')
    })
  }
})
