import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { defineComponent } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import { useFlowGateSse } from '@main/composables/useFlowGateSse'
import { useDashboardStore } from '@main/stores/dashboard'
import { useExplorerStore } from '@main/stores/explorer'

// 0565 T0028 §B-2 — a TR2 approval attempt publishes group_view_refresh
// (reason: tr2_approval_changed) on every phase/state change. Those events name the TR2
// document, so the open-tab refetch is narrowed to that tab like review_added is; the
// TR2 body listens to fg:open_docs_refresh and re-reads its read model.

const { showToast } = vi.hoisted(() => ({ showToast: vi.fn() }))
vi.mock('@main/components/common/useToast', () => ({ useToast: () => ({ showToast }) }))

class MockEventSource {
  static instances: MockEventSource[] = []
  listeners = new Map<string, (event: Event) => void>()
  readyState = 0
  constructor(public url: string) { MockEventSource.instances.push(this) }
  addEventListener(type: string, listener: EventListenerOrEventListenerObject) { this.listeners.set(type, listener as (event: Event) => void) }
  emit(type: string, data?: object) { this.listeners.get(type)?.({ data: data ? JSON.stringify(data) : '' } as MessageEvent) }
  close() { this.readyState = 2 }
  static get last() { return MockEventSource.instances[MockEventSource.instances.length - 1] }
}

const Harness = defineComponent({ setup() { return useFlowGateSse(vi.fn()) }, template: '<div />' })
const TR2 = 'proj_alpha.default.0565.0009-TR2'
let openDocsRefresh: ReturnType<typeof vi.fn>

beforeEach(() => {
  vi.useFakeTimers()
  setActivePinia(createPinia())
  MockEventSource.instances = []
  openDocsRefresh = vi.fn()
  window.addEventListener('fg:open_docs_refresh', openDocsRefresh)
  vi.stubGlobal('EventSource', MockEventSource)
  ;(window as any).__accessToken__ = 'tok1'
  localStorage.clear()
})
afterEach(() => {
  window.removeEventListener('fg:open_docs_refresh', openDocsRefresh)
  vi.restoreAllMocks()
  vi.useRealTimers()
  delete (window as any).__accessToken__
  localStorage.clear()
})

function mountHarness() {
  localStorage.setItem('fg_current_project_id', 'proj_alpha')
  vi.spyOn(useExplorerStore(), 'invalidateProject').mockImplementation(() => {})
  vi.spyOn(useDashboardStore(), 'invalidate').mockImplementation(() => {})
  return mount(Harness, { global: { plugins: [i18n] } })
}

describe('useFlowGateSse TR2 approval phase events (0565)', () => {
  it('coalesces a whole approval into one refresh scoped to the TR2 tab', () => {
    const wrapper = mountHarness()
    MockEventSource.last.emit('open')
    for (const phase of ['created', 'precheck', 'snapshot', 'apply', 'validation']) {
      MockEventSource.last.emit('group_view_refresh', {
        project: 'proj_alpha', doc_id: TR2,
        payload: { group_id: 'proj_alpha.default.0565', reason: 'tr2_approval_changed', doc_id: TR2, state: 'in_progress', phase },
      })
    }
    vi.advanceTimersByTime(250)
    expect(openDocsRefresh).toHaveBeenCalledTimes(1)
    expect((openDocsRefresh.mock.calls[0][0] as CustomEvent).detail).toEqual({ project: 'proj_alpha', doc_id: TR2 })
    wrapper.unmount()
  })

  it('invalidates ownership caches and scopes tr2_history_changed to its TR2 tab', () => {
    localStorage.setItem('fg_current_project_id', 'proj_alpha')
    const invalidate = vi.spyOn(useExplorerStore(), 'invalidateProject').mockImplementation(() => {})
    vi.spyOn(useDashboardStore(), 'invalidate').mockImplementation(() => {})
    const wrapper = mount(Harness, { global: { plugins: [i18n] } })

    MockEventSource.last.emit('open')
    MockEventSource.last.emit('group_view_refresh', {
      project: 'proj_alpha', doc_id: TR2,
      payload: {
        group_id: 'proj_alpha.default.0565',
        reason: 'tr2_history_changed',
        doc_id: TR2,
      },
    })
    vi.advanceTimersByTime(250)

    expect(invalidate).toHaveBeenCalledWith('proj_alpha')
    expect(openDocsRefresh).toHaveBeenCalledTimes(1)
    expect((openDocsRefresh.mock.calls[0][0] as CustomEvent).detail).toEqual({
      project: 'proj_alpha', doc_id: TR2,
    })
    wrapper.unmount()
  })

  it('keeps other group_view_refresh reasons project-wide', () => {
    const wrapper = mountHarness()
    MockEventSource.last.emit('open')
    MockEventSource.last.emit('group_view_refresh', {
      project: 'proj_alpha', doc_id: TR2, payload: { group_id: 'proj_alpha.default.0565', reason: 'document_added' },
    })
    vi.advanceTimersByTime(250)
    expect((openDocsRefresh.mock.calls[0][0] as CustomEvent).detail.doc_id).toBeNull()
    wrapper.unmount()
  })
})
