import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { defineComponent } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import { useFlowGateSse } from '@main/composables/useFlowGateSse'
import { useDashboardStore } from '@main/stores/dashboard'
import { useExplorerStore } from '@main/stores/explorer'

// 0291 P1-2 (client project guard) + P1-3 (refresh coalescing), NR0003 §4 P1.
//
// P1-1 made the server route broadcasts by project, but rules 3 and 4 of D0005 §3-1
// are deliberate fallbacks: an event with no project, or a subscription that never
// declared one, still reaches every screen. The client guard is the second line —
// an event belonging to a project the user is not looking at must not trigger a full
// screen re-read. P1-3 then collapses a burst of events about the *same* project
// into one re-read, since a single logical change publishes several events.

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
let notificationRefresh: ReturnType<typeof vi.fn>
let invalidateProject: ReturnType<typeof vi.fn>

/** Mount with `proj_alpha` selected, with the stores' own network work stubbed out. */
function mountWithCurrentProject(pid = 'proj_alpha') {
  localStorage.setItem('fg_current_project_id', pid)
  const explorerStore = useExplorerStore()
  const dashboardStore = useDashboardStore()
  invalidateProject = vi.fn()
  vi.spyOn(explorerStore, 'invalidateProject').mockImplementation(invalidateProject)
  // The dashboard store debounces + fetches on its own; not under test here.
  vi.spyOn(dashboardStore, 'invalidate').mockImplementation(() => {})
  return mount(Harness, { global: { plugins: [i18n] } })
}

beforeEach(() => {
  vi.useFakeTimers()
  setActivePinia(createPinia())
  MockEventSource.instances = []
  showToast.mockReset()
  refreshAll = vi.fn()
  openDocsRefresh = vi.fn()
  notificationRefresh = vi.fn()
  window.addEventListener('fg:open_docs_refresh', openDocsRefresh)
  window.addEventListener('fg:notification', notificationRefresh)
  vi.stubGlobal('EventSource', MockEventSource)
  ;(window as any).__accessToken__ = 'tok1'
  sessionStorage.clear()
  localStorage.clear()
})

afterEach(() => {
  window.removeEventListener('fg:open_docs_refresh', openDocsRefresh)
  window.removeEventListener('fg:notification', notificationRefresh)
  vi.restoreAllMocks()
  vi.useRealTimers()
  delete (window as any).__accessToken__
  localStorage.clear()
})

describe('useFlowGateSse project guard (P1-2)', () => {
  it('does not re-read the screen for an event belonging to another project', () => {
    const wrapper = mountWithCurrentProject('proj_alpha')
    MockEventSource.last.emit('open')

    MockEventSource.last.emit('group_view_refresh', { project: 'proj_beta' })
    vi.advanceTimersByTime(COALESCE_MS * 4)

    expect(refreshAll).not.toHaveBeenCalled()
    expect(openDocsRefresh).not.toHaveBeenCalled()
    expect(notificationRefresh).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('still invalidates the other project cache so a later switch re-reads it', () => {
    // Skipping the refresh must not leave a stale tree behind: the cached tree for
    // the untouched project is dropped, it is only the *fetch* that is skipped.
    const wrapper = mountWithCurrentProject('proj_alpha')
    MockEventSource.last.emit('open')

    MockEventSource.last.emit('group_view_refresh', { project: 'proj_beta' })

    expect(invalidateProject).toHaveBeenCalledWith('proj_beta')
    wrapper.unmount()
  })

  it('re-reads the screen for an event belonging to the current project', () => {
    const wrapper = mountWithCurrentProject('proj_alpha')
    MockEventSource.last.emit('open')

    MockEventSource.last.emit('group_view_refresh', { project: 'proj_alpha' })
    vi.advanceTimersByTime(COALESCE_MS)

    expect(refreshAll).toHaveBeenCalledTimes(1)
    expect(openDocsRefresh).toHaveBeenCalledTimes(1)
    wrapper.unmount()
  })

  it('re-reads the screen for an event that carries no project (fallback)', () => {
    // D0005 §3-1 rule 3: with no project on the event there is no basis to judge, so
    // the safe side is to refresh. Guarding here would turn a fan-out fix into missed
    // updates.
    const wrapper = mountWithCurrentProject('proj_alpha')
    MockEventSource.last.emit('open')

    MockEventSource.last.emit('group_view_refresh', {})
    vi.advanceTimersByTime(COALESCE_MS)

    expect(refreshAll).toHaveBeenCalledTimes(1)
    wrapper.unmount()
  })
})

describe('useFlowGateSse refresh coalescing (P1-3)', () => {
  it('collapses a burst of events into a single screen re-read', () => {
    const wrapper = mountWithCurrentProject('proj_alpha')
    MockEventSource.last.emit('open')

    // One document handled by a worker publishes several events back to back.
    MockEventSource.last.emit('document_explorer_refresh', { project: 'proj_alpha' })
    MockEventSource.last.emit('doc_review_status_changed', { project: 'proj_alpha', payload: {} })
    MockEventSource.last.emit('group_view_refresh', { project: 'proj_alpha' })

    expect(refreshAll).not.toHaveBeenCalled()
    vi.advanceTimersByTime(COALESCE_MS)

    expect(refreshAll).toHaveBeenCalledTimes(1)
    expect(openDocsRefresh).toHaveBeenCalledTimes(1)
    expect(notificationRefresh).toHaveBeenCalledTimes(1)
    wrapper.unmount()
  })

  it('does not let a steady event stream postpone the re-read', () => {
    // Fixed window, not a sliding debounce: a later event joins the pending window
    // instead of pushing the deadline back, so the screen cannot be starved of
    // refreshes exactly while the most is happening.
    const wrapper = mountWithCurrentProject('proj_alpha')
    MockEventSource.last.emit('open')

    MockEventSource.last.emit('group_view_refresh', { project: 'proj_alpha' })
    vi.advanceTimersByTime(COALESCE_MS - 50)
    MockEventSource.last.emit('group_view_refresh', { project: 'proj_alpha' })
    vi.advanceTimersByTime(50)

    expect(refreshAll).toHaveBeenCalledTimes(1)
    wrapper.unmount()
  })

  it('starts a new window for the next burst', () => {
    const wrapper = mountWithCurrentProject('proj_alpha')
    MockEventSource.last.emit('open')

    MockEventSource.last.emit('group_view_refresh', { project: 'proj_alpha' })
    vi.advanceTimersByTime(COALESCE_MS)
    MockEventSource.last.emit('group_view_refresh', { project: 'proj_alpha' })
    vi.advanceTimersByTime(COALESCE_MS)

    expect(refreshAll).toHaveBeenCalledTimes(2)
    wrapper.unmount()
  })

  it('re-reads immediately on reconnect instead of waiting out the window', () => {
    // §3-3: the resync after a (re)connect is the safety net for events missed while
    // disconnected — it must not be deferred behind the coalescing window.
    const wrapper = mountWithCurrentProject('proj_alpha')
    MockEventSource.last.emit('open')
    MockEventSource.last.emit('error')
    vi.advanceTimersByTime(1000)
    MockEventSource.last.emit('open')

    expect(refreshAll).toHaveBeenCalledTimes(1)
    wrapper.unmount()
  })

  it('drops a pending re-read when the view unmounts', () => {
    const wrapper = mountWithCurrentProject('proj_alpha')
    MockEventSource.last.emit('open')
    MockEventSource.last.emit('group_view_refresh', { project: 'proj_alpha' })

    wrapper.unmount()
    vi.advanceTimersByTime(COALESCE_MS * 4)

    expect(refreshAll).not.toHaveBeenCalled()
  })
})
