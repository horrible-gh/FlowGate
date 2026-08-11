// Group 0137 (R0001 / NR0003) — file-mode action bar must be hidden.
// R0001: "파일모드 시 액션바는 아직 아무것도 나오지 않게" — when a source file
// opened from the explorer is the active tab (a "file tab": projectId present, no
// typeCode), the sticky footer action bar must NOT render.
//
// Root cause (NR0003 §3): a file tab permanently lacks typeCode, so
// getWorkflowViewInput yields tabTypeCode=null and workflowViewState returns the
// doc-loading placeholder mode='review'. The fix guards getActionBarMode on
// POSITIVE file-tab identity (isFileTab), so a still-loading DOC tab keeps its
// placeholder bar (feedback_actionbar_always_shows) while a file tab shows none.
//
// Memory: [feedback_actionbar_always_shows]

import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { expectDocumentBranchMounted, mountMainPanel } from '../helpers/mountMainPanel'

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

// Force the workflow view to the loading placeholder (mode='review', non-null) so
// that any tab reaching getWorkflowViewState resolves to a VISIBLE bar. This
// isolates the file-tab guard: if the bar hides for a file tab, it is the guard —
// not an incidental null mode — that hid it.
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

function mountWith(tabs: any[], activeTabId: string) {
  return mountMainPanel({ tabs, activeTabId })
}

beforeEach(() => {
  setActivePinia(createPinia())
  localStorage.clear()
  delete window.__accessToken__
  getRequest.mockClear()
})

describe('MainPanel — file-mode action bar (0137)', () => {
  const FILE_TAB = {
    id: 'test-project:/src/util.py',
    title: 'util.py',
    path: '/src/util.py',
    type: 'md' as const,
    projectId: 'test-project',
    // NOTE: no typeCode — this is what makes it a file tab.
  }

  it('file tab active → getActionBarMode returns null (bar suppressed)', async () => {
    const wrapper = await mountWith([FILE_TAB], FILE_TAB.id)
    const vm = wrapper.vm as any
    expect(vm.getActionBarMode(FILE_TAB.id)).toBeNull()
  })

  it('file tab active → ReviewActionBar is NOT rendered and no has-sticky-footer', async () => {
    const wrapper = await mountWith([FILE_TAB], FILE_TAB.id)
    // 0394 T0004 (NR0003 §5.1): both expectations below are "X is absent", which the
    // bootstrap placeholder satisfies for free — it renders no document branch at all.
    // Prove the branch is up first, so an absent action bar means the file-tab guard
    // suppressed it rather than the gate never opening.
    expectDocumentBranchMounted(wrapper)
    expect(wrapper.findComponent({ name: 'ReviewActionBar' }).exists()).toBe(false)
    expect(wrapper.find('.has-sticky-footer').exists()).toBe(false)
  })

  it('doc tab (has typeCode) → action bar still renders (guard is file-tab-only)', async () => {
    const docTab = {
      id: 'test.p.0001.0003-D',
      title: 'D doc',
      path: '',
      type: 'md' as const,
      typeCode: 'D',
    }
    const wrapper = await mountWith([docTab], docTab.id)
    const vm = wrapper.vm as any
    // Not a file tab → falls through to the placeholder mode from the mock.
    expect(vm.getActionBarMode(docTab.id)).toBe('review')
    expect(wrapper.findComponent({ name: 'ReviewActionBar' }).exists()).toBe(true)
  })

  it('loading doc tab (typeCode not yet arrived, no projectId) → placeholder bar preserved', async () => {
    // A doc tab whose header data has not loaded yet has no typeCode but also no
    // projectId, so isFileTab is false and the always-render placeholder survives.
    const loadingDocTab = {
      id: 'test.p.0001.0004-DS',
      title: 'DS doc',
      path: '',
      type: 'md' as const,
      // no typeCode, no projectId
    }
    const wrapper = await mountWith([loadingDocTab], loadingDocTab.id)
    const vm = wrapper.vm as any
    expect(vm.getActionBarMode(loadingDocTab.id)).toBe('review')
  })
})
