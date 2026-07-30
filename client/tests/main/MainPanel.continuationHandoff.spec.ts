import { shallowMount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@shared/i18n'
import MainPanel from '@main/components/MainPanel.vue'
import { useAiInvokeRunsStore } from '@main/stores/aiInvokeRuns'

const { getRequest } = vi.hoisted(() => ({
  getRequest: vi.fn().mockResolvedValue({ data: {} }),
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
    requestReview: vi.fn(),
    requestWorkflowDecision: vi.fn(),
    copyMentToClipboard: vi.fn(),
  }),
  splitGroupId: (groupId: string) => ({ groupCode: groupId }),
}))

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

function lifecycle(kind: 'started' | 'finished', payload: Record<string, unknown>) {
  window.dispatchEvent(new CustomEvent('fg:ai_invoke', {
    detail: { kind, payload },
  }))
}

beforeEach(() => {
  vi.useFakeTimers()
  sessionStorage.clear()
  localStorage.clear()
  setActivePinia(createPinia())
  getRequest.mockReset()
  getRequest.mockResolvedValue({ data: {} })
})

afterEach(() => {
  vi.clearAllTimers()
  vi.useRealTimers()
})

describe('MainPanel continuous invoke handoff', () => {
  it('keeps the monitor running between hops and rejects a late finish from the old run', () => {
    const groupId = 'flowgate.default.0345'
    const store = useAiInvokeRunsStore()
    store.trackStarted({
      run_id: 'airun-hop-1',
      group_id: groupId,
      doc_ref: 'flowgate.default.0345.0001-B',
      mode: 'continuous',
      docs_target: 3,
      chain_id: 'airun-hop-1',
      chain_docs_target: 3,
      chain_docs_reached: 0,
    })
    const wrapper = mountPanel()

    lifecycle('started', {
      run_id: 'airun-hop-1',
      group_id: groupId,
      mode: 'continuous',
      status: 'running',
      docs_target: 3,
      docs_reached_so_far: 1,
      chain_id: 'airun-hop-1',
      chain_docs_target: 3,
      chain_docs_reached: 1,
      continuation_pending: true,
    })
    lifecycle('finished', {
      run_id: 'airun-hop-1',
      group_id: groupId,
      mode: 'continuous',
      outcome: 'complete',
      end_reason: 'exited',
      docs_target: 3,
      docs_reached: 1,
      chain_id: 'airun-hop-1',
      chain_docs_target: 3,
      chain_docs_reached: 1,
      duration_ms: 2_500,
    })

    const handoff = store.runsByGroup[groupId]
    expect(handoff.runId).toBe('airun-hop-1')
    expect(handoff.phase).toBe('running')
    expect(handoff.docsReachedSoFar).toBe(1)
    expect(handoff.chainDocsReached).toBe(1)
    expect(handoff.chainDocsTarget).toBe(3)
    expect(handoff.finishedPayload).toBeNull()

    lifecycle('started', {
      run_id: 'airun-hop-2',
      group_id: groupId,
      mode: 'continuous',
      status: 'running',
      docs_target: 2,
      chain_id: 'airun-hop-1',
      chain_docs_target: 3,
      chain_docs_reached: 1,
    })
    lifecycle('finished', {
      run_id: 'airun-hop-1',
      group_id: groupId,
      mode: 'continuous',
      outcome: 'complete',
      end_reason: 'exited',
      docs_target: 3,
      docs_reached: 1,
      chain_id: 'airun-hop-1',
      chain_docs_target: 3,
      chain_docs_reached: 1,
    })

    const replacement = store.runsByGroup[groupId]
    expect(replacement.runId).toBe('airun-hop-2')
    expect(replacement.phase).toBe('running')
    expect(replacement.docsTarget).toBe(2)
    expect(replacement.docsReachedSoFar).toBe(0)
    expect(replacement.chainDocsTarget).toBe(3)
    expect(replacement.chainDocsReached).toBe(1)

    wrapper.unmount()
  })
})
