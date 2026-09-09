import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { defineComponent } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import { useFlowGateSse } from '@main/composables/useFlowGateSse'
import { useDashboardStore } from '@main/stores/dashboard'
import { useExplorerStore } from '@main/stores/explorer'

// 0543 T0004 — AI review arrival must reconcile the open document's review/history
// immediately (no F5), but a same-project event about a DIFFERENT document must not
// force this tab to re-read review data it does not own (§3). The server pairs
// ai_review_arrived with a group_view_refresh(reason: review_added) that names the
// same reviewed doc_id (inbox_routes.py's review push), so both are scoped here the
// same way and coalesce into one doc-targeted fg:open_docs_refresh. Every other
// group_view_refresh reason (workflow decisions, sibling doc creation, git archive,
// …) keeps the pre-existing project-wide refresh, since those events' doc_id often
// names a document OTHER than the one that needs re-reading (e.g. a newly created
// sibling whose existence the currently open parent tab must learn about).

const { showToast } = vi.hoisted(() => ({ showToast: vi.fn() }))

vi.mock('@main/components/common/useToast', () => ({
  useToast: () => ({ showToast }),
}))

class MockEventSource {
  static instances: MockEventSource[] = []
  url: string
  readyState = 0
  closed = false
  listeners = new Map<string, (event: Event) => void>()

  constructor(url: string, _options?: EventSourceInit) {
    this.url = url
    MockEventSource.instances.push(this)
  }

  addEventListener(type: string, listener: EventListenerOrEventListenerObject) {
    this.listeners.set(type, listener as (event: Event) => void)
  }

  emit(type: string, data?: object) {
    this.listeners.get(type)?.({ data: data ? JSON.stringify(data) : '' } as MessageEvent)
  }

  close() {
    this.closed = true
    this.readyState = 2
  }

  static get last(): MockEventSource {
    return MockEventSource.instances[MockEventSource.instances.length - 1]
  }
}

const Harness = defineComponent({
  setup() {
    return useFlowGateSse(refreshAll)
  },
  template: '<div />',
})

const COALESCE_MS = 250

let refreshAll: ReturnType<typeof vi.fn>
let openDocsRefresh: ReturnType<typeof vi.fn>
let invalidateProject: ReturnType<typeof vi.fn>

function mountWithCurrentProject(pid = 'proj_alpha') {
  localStorage.setItem('fg_current_project_id', pid)
  const explorerStore = useExplorerStore()
  const dashboardStore = useDashboardStore()
  invalidateProject = vi.fn()
  vi.spyOn(explorerStore, 'invalidateProject').mockImplementation(invalidateProject)
  vi.spyOn(dashboardStore, 'invalidate').mockImplementation(() => {})
  return mount(Harness, { global: { plugins: [i18n] } })
}

function lastOpenDocsRefreshDetail(): any {
  const call = openDocsRefresh.mock.calls[openDocsRefresh.mock.calls.length - 1]
  return (call?.[0] as CustomEvent)?.detail
}

beforeEach(() => {
  vi.useFakeTimers()
  setActivePinia(createPinia())
  MockEventSource.instances = []
  showToast.mockReset()
  refreshAll = vi.fn()
  openDocsRefresh = vi.fn()
  window.addEventListener('fg:open_docs_refresh', openDocsRefresh)
  vi.stubGlobal('EventSource', MockEventSource)
  ;(window as any).__accessToken__ = 'tok1'
  sessionStorage.clear()
  localStorage.clear()
})

afterEach(() => {
  window.removeEventListener('fg:open_docs_refresh', openDocsRefresh)
  vi.restoreAllMocks()
  vi.useRealTimers()
  delete (window as any).__accessToken__
  localStorage.clear()
})

describe('useFlowGateSse ai_review_arrived doc scoping (0543 T0004 §3)', () => {
  it('scopes fg:open_docs_refresh to the reviewed document', () => {
    const wrapper = mountWithCurrentProject('proj_alpha')
    MockEventSource.last.emit('open')

    MockEventSource.last.emit('ai_review_arrived', {
      project: 'proj_alpha',
      doc_id: 'proj_alpha.none.0001.0002-R',
      payload: { doc_id: 'proj_alpha.none.0001.0002-R', title: 'doc', verdict: 'pass', finding_count: 0 },
    })
    vi.advanceTimersByTime(COALESCE_MS)

    expect(openDocsRefresh).toHaveBeenCalledTimes(1)
    expect(lastOpenDocsRefreshDetail()).toEqual({ project: 'proj_alpha', doc_id: 'proj_alpha.none.0001.0002-R' })
    // Explorer/dashboard invalidation (tree badges) still runs — only the open-tab
    // refetch is narrowed, not the sibling-refresh machinery §7 says to keep.
    expect(invalidateProject).toHaveBeenCalledWith('proj_alpha')
    wrapper.unmount()
  })

  it('coalesces the paired review_added group_view_refresh into the same doc-scoped emission', () => {
    const wrapper = mountWithCurrentProject('proj_alpha')
    MockEventSource.last.emit('open')

    // inbox_routes.py's review push broadcasts both events back to back for the
    // same doc_id.
    MockEventSource.last.emit('ai_review_arrived', {
      project: 'proj_alpha',
      doc_id: 'proj_alpha.none.0001.0002-R',
      payload: { doc_id: 'proj_alpha.none.0001.0002-R', title: 'doc' },
    })
    MockEventSource.last.emit('group_view_refresh', {
      project: 'proj_alpha',
      doc_id: 'proj_alpha.none.0001.0002-R',
      payload: { group_id: 'proj_alpha.none.0001', reason: 'review_added' },
    })
    vi.advanceTimersByTime(COALESCE_MS)

    expect(openDocsRefresh).toHaveBeenCalledTimes(1)
    expect(lastOpenDocsRefreshDetail().doc_id).toBe('proj_alpha.none.0001.0002-R')
  })

  it('widens to a project-wide refresh when a same-window event names no document', () => {
    const wrapper = mountWithCurrentProject('proj_alpha')
    MockEventSource.last.emit('open')

    MockEventSource.last.emit('ai_review_arrived', {
      project: 'proj_alpha',
      doc_id: 'proj_alpha.none.0001.0002-R',
      payload: { doc_id: 'proj_alpha.none.0001.0002-R', title: 'doc' },
    })
    // A sibling doc creation lands in the same 250ms window — this DOES need every
    // open tab to refresh, so the narrowing must not suppress it.
    MockEventSource.last.emit('group_view_refresh', {
      project: 'proj_alpha',
      doc_id: 'proj_alpha.none.0001.0003-DS',
      payload: { group_id: 'proj_alpha.none.0001', reason: 'document_added' },
    })
    vi.advanceTimersByTime(COALESCE_MS)

    expect(openDocsRefresh).toHaveBeenCalledTimes(1)
    expect(lastOpenDocsRefreshDetail().doc_id).toBeNull()
    wrapper.unmount()
  })

  it('widens to a project-wide refresh when two different docs both arrive in one window', () => {
    const wrapper = mountWithCurrentProject('proj_alpha')
    MockEventSource.last.emit('open')

    MockEventSource.last.emit('ai_review_arrived', {
      project: 'proj_alpha',
      doc_id: 'proj_alpha.none.0001.0002-R',
      payload: { doc_id: 'proj_alpha.none.0001.0002-R', title: 'doc A' },
    })
    MockEventSource.last.emit('ai_review_arrived', {
      project: 'proj_alpha',
      doc_id: 'proj_alpha.none.0002.0001-R',
      payload: { doc_id: 'proj_alpha.none.0002.0001-R', title: 'doc B' },
    })
    vi.advanceTimersByTime(COALESCE_MS)

    expect(openDocsRefresh).toHaveBeenCalledTimes(1)
    expect(lastOpenDocsRefreshDetail().doc_id).toBeNull()
    wrapper.unmount()
  })

  it('leaves other group_view_refresh reasons project-wide (unscoped)', () => {
    const wrapper = mountWithCurrentProject('proj_alpha')
    MockEventSource.last.emit('open')

    MockEventSource.last.emit('group_view_refresh', {
      project: 'proj_alpha',
      doc_id: 'proj_alpha.none.0001.0002-R',
      payload: { group_id: 'proj_alpha.none.0001', reason: 'workflow_decided' },
    })
    vi.advanceTimersByTime(COALESCE_MS)

    expect(openDocsRefresh).toHaveBeenCalledTimes(1)
    expect(lastOpenDocsRefreshDetail().doc_id).toBeNull()
    wrapper.unmount()
  })

  it('repeated arrivals for the same document within one window still emit a single scoped refresh', () => {
    const wrapper = mountWithCurrentProject('proj_alpha')
    MockEventSource.last.emit('open')

    for (let i = 0; i < 3; i++) {
      MockEventSource.last.emit('ai_review_arrived', {
        project: 'proj_alpha',
        doc_id: 'proj_alpha.none.0001.0002-R',
        payload: { doc_id: 'proj_alpha.none.0001.0002-R', title: 'doc' },
      })
    }
    vi.advanceTimersByTime(COALESCE_MS)

    expect(openDocsRefresh).toHaveBeenCalledTimes(1)
    expect(lastOpenDocsRefreshDetail().doc_id).toBe('proj_alpha.none.0001.0002-R')
    wrapper.unmount()
  })
})
