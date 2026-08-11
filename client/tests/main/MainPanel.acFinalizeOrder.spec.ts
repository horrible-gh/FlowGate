// Group 0265 (R0001 / NR0003) — AC final-approval panel ordering.
// Requirement: "문서를 최종 승인하면 [Git 반영] 이 [최종 승인] 의 위로 올라오도록".
// On the AC (final-approval) tab MainPanel mounts two siblings: the [최종 승인]
// card (.ac-final-approval-body) and the [Git 반영] GitFinalizePanel. Before final
// approval the approval card leads (approving is the primary action); once the doc
// is finally approved (docReviewStatus approved | wf_done → isCompletedDoc) the
// GitFinalizePanel rises ABOVE the card, because merge/push is the remaining action.
//
// This guards the DOM order against a regression back to the fixed card-first layout.

import { defineComponent, h } from 'vue'
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
  }
}

const AC_TAB = {
  id: 'p.default.0265.0005-AC',
  title: 'final approval',
  path: '',
  type: 'md' as const,
  typeCode: 'AC',
}

function mountAc(status: string) {
  return mountMainPanel({ tabs: [AC_TAB], stubs: baseStubs(status) })
}

beforeEach(() => {
  setActivePinia(createPinia())
  localStorage.clear()
  delete (window as any).__accessToken__
  getRequest.mockClear()
})

describe('MainPanel — AC finalize panel ordering (0265)', () => {
  it('before final approval → [최종 승인] card is above [Git 반영]', async () => {
    const wrapper = await mountAc('pending_review')
    const html = wrapper.html()
    const cardIdx = html.indexOf('ac-final-approval-body')
    const gitIdx = html.indexOf('git-fin-stub')
    expect(cardIdx).toBeGreaterThanOrEqual(0)
    expect(gitIdx).toBeGreaterThanOrEqual(0)
    expect(cardIdx).toBeLessThan(gitIdx)
  })

  for (const status of ['approved', 'wf_done']) {
    it(`after final approval (${status}) → [Git 반영] rises above [최종 승인] card`, async () => {
      const wrapper = await mountAc(status)
      const html = wrapper.html()
      const cardIdx = html.indexOf('ac-final-approval-body')
      const gitIdx = html.indexOf('git-fin-stub')
      expect(cardIdx).toBeGreaterThanOrEqual(0)
      expect(gitIdx).toBeGreaterThanOrEqual(0)
      expect(gitIdx).toBeLessThan(cardIdx)
    })
  }
})
