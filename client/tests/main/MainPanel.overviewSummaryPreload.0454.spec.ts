import { flushPromises, shallowMount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@shared/i18n'
import MainPanel from '@main/components/MainPanel.vue'
import { useProjectStore } from '@main/stores/project'
import { useExplorerStore } from '@main/stores/explorer'

// 0454 T0007 — the overview cards (총 문서 수 / 진행 중 / 타입 분포) went through several rejected
// designs before this one:
//   rev0: MainPanel warmed the FULL group tree itself on every default-screen entry, landing on
//   top of the sidebar's own pruned default fetch — two tree payloads on the wire for one screen.
//   rev1: MainPanel instead called a dedicated `fetchGroupOverviewSummary` endpoint of its own.
//   Smaller on the wire, but it reran the server's full get_group_tree DB path a second time per
//   page load, and nothing refetched it when an ordinary SSE/manual refresh invalidated the
//   cache, so the cards went stale until an unrelated project/tab switch happened to reload them.
//   rev2 removed the fetch from MainPanel and rode `overview_summary` inside the `/groups/tree`
//   response GroupExplorer (the sidebar) already fetches — but that changed what `/groups/tree`
//   itself returns to every pre-existing caller, breaking T0006 §1.1 (review finding on rev2).
//   rev3 restored a dedicated MainPanel-owned fetch (rev1's shape) against its own
//   `/groups/tree/overview_summary` route — fixing rev1's two problems, but as two INDEPENDENT
//   Vue watchers (GroupExplorer's tree fetch and MainPanel's summary fetch) reacting to the same
//   trigger with no guarantee they overlapped: a sequential arrival cost a second full
//   `get_group_tree` DB call every time (the rev4 review finding).
//
// rev5 removes the fetch from MainPanel ENTIRELY. `overview_summary` now rides GroupExplorer's
// own `/groups/tree` request (explorer.ts's fetchGroupTree, `include_summary=true` — see its
// doc comment and explorerGroupTreeVariants.0454.spec.ts), which fires on exactly the triggers
// MainPanel's cards need (project switch, toggle, SSE/manual refresh, `fg:group_tree_changed`).
// MainPanel just reads `explorerStore.getCachedGroupOverviewSummary` reactively — there is no
// request left for THIS file to pin the shape of; what's pinned here is the absence of one, and
// that the cards still react correctly to the cache however it gets filled.

const { getRequest } = vi.hoisted(() => ({
  getRequest: vi.fn(),
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
  splitGroupId: (gid: string) => ({ groupCode: gid }),
}))

function mountPanel(props: Record<string, unknown> = {}) {
  return shallowMount(MainPanel, {
    props,
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

const statNum = (wrapper: ReturnType<typeof mountPanel>, index: number) =>
  wrapper.findAll('.stat-num')[index]?.text()

beforeEach(() => {
  setActivePinia(createPinia())
  getRequest.mockReset()
  getRequest.mockResolvedValue({ data: {} })
})

describe('MainPanel overview cards — read the store cache, issue no request of their own (0454 T0007 rev5)', () => {
  it('makes no /groups/tree (or any other) request on mount', async () => {
    const projectStore = useProjectStore()
    projectStore.currentProjectId = 'proj-1'

    mountPanel()
    await flushPromises()

    const groupTreeCalls = getRequest.mock.calls
      .map((call) => String(call[0]))
      .filter((url) => url.includes('/groups/tree'))
    expect(groupTreeCalls).toEqual([])
  })

  it('makes no request on overviewRefreshToken change either', async () => {
    const projectStore = useProjectStore()
    projectStore.currentProjectId = 'proj-1'

    const wrapper = mountPanel()
    await flushPromises()
    const callsAfterMount = getRequest.mock.calls.length

    await wrapper.setProps({ overviewRefreshToken: 1 })
    await flushPromises()

    const summaryCalls = getRequest.mock.calls
      .slice(callsAfterMount)
      .map((call) => String(call[0]))
      .filter((url) => url.includes('/groups/tree'))
    expect(summaryCalls).toEqual([])
  })

  it('renders "—" while the store has no cached summary yet', async () => {
    const projectStore = useProjectStore()
    projectStore.currentProjectId = 'proj-1'

    const wrapper = mountPanel()
    await flushPromises()

    // index 0: activeProjects, 1: totalDocs, 2: inProgressWorkflows, 3: workingGroups.
    expect(statNum(wrapper, 1)).toBe('—')
    expect(statNum(wrapper, 3)).toBe('—')
  })

  it('picks up the summary reactively once the store caches it — however that happened', async () => {
    const projectStore = useProjectStore()
    projectStore.currentProjectId = 'proj-1'
    const explorerStore = useExplorerStore()

    const wrapper = mountPanel()
    await flushPromises()
    expect(statNum(wrapper, 1)).toBe('—')

    // Simulates the cache being populated from ANY source — MainPanel's own fetch above, or
    // (in the real app) a concurrently-resolving one — the card must react either way.
    explorerStore.groupOverviewSummaryCache['proj-1:main'] = {
      total_documents: 7,
      working_groups: 2,
      type_distribution: [{ type: 'T', count: 7 }],
    }
    await flushPromises()

    expect(statNum(wrapper, 1)).toBe('7')
    expect(statNum(wrapper, 3)).toBe('2')
  })

  it('goes back to "—" after invalidateProject clears the cache, and stays there until the cache is refilled — not from bumping overviewRefreshToken, which issues no request', async () => {
    const projectStore = useProjectStore()
    projectStore.currentProjectId = 'proj-1'
    const explorerStore = useExplorerStore()
    explorerStore.groupOverviewSummaryCache['proj-1:main'] = {
      total_documents: 7,
      working_groups: 2,
      type_distribution: [{ type: 'T', count: 7 }],
    }

    const wrapper = mountPanel()
    await flushPromises()
    expect(statNum(wrapper, 1)).toBe('7')

    // An SSE/manual refresh invalidates the project's caches (DashboardView.vue refreshAll /
    // manualRefresh) before bumping BOTH explorerRefreshToken and overviewRefreshToken.
    explorerStore.invalidateProject('proj-1')
    await flushPromises()

    expect(statNum(wrapper, 1)).toBe('—')
    expect(statNum(wrapper, 3)).toBe('—')

    // rev5: overviewRefreshToken bumping issues NO request from MainPanel any more — see
    // 'makes no request on overviewRefreshToken change either' above. The cards stay at "—"
    // until GroupExplorer's OWN tree reload (driven by the SAME refresh, via explorerRefreshToken
    // — not simulated here, see explorerGroupTreeVariants.0454.spec.ts) refills the cache.
    await wrapper.setProps({ overviewRefreshToken: 1 })
    await flushPromises()
    expect(statNum(wrapper, 1)).toBe('—')

    explorerStore.groupOverviewSummaryCache['proj-1:main'] = {
      total_documents: 9, working_groups: 3, type_distribution: [{ type: 'T', count: 9 }],
    }
    await flushPromises()

    expect(statNum(wrapper, 1)).toBe('9')
    expect(statNum(wrapper, 3)).toBe('3')
  })

  it('does not fetch anything when no project is selected', async () => {
    const projectStore = useProjectStore()
    projectStore.currentProjectId = null

    mountPanel()
    await flushPromises()

    const groupTreeCalls = getRequest.mock.calls
      .map((call) => String(call[0]))
      .filter((url) => url.includes('/groups/tree'))
    expect(groupTreeCalls).toEqual([])
  })
})
