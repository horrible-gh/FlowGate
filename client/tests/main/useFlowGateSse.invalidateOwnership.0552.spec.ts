import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { defineComponent, ref } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import { useFlowGateSse } from '@main/composables/useFlowGateSse'
import { useDashboardStore } from '@main/stores/dashboard'
import { useExplorerStore } from '@main/stores/explorer'
import dashboardSource from '@main/views/DashboardView.vue?raw'

const { showToast } = vi.hoisted(() => ({ showToast: vi.fn() }))

vi.mock('@main/components/common/useToast', () => ({
  useToast: () => ({ showToast }),
}))

class MockEventSource {
  static instances: MockEventSource[] = []
  readyState = 0
  listeners = new Map<string, (event: Event) => void>()

  constructor(_url: string, _options?: EventSourceInit) {
    MockEventSource.instances.push(this)
  }

  addEventListener(type: string, listener: EventListenerOrEventListenerObject) {
    this.listeners.set(type, listener as (event: Event) => void)
  }

  emit(type: string, data?: object) {
    this.listeners.get(type)?.({ data: data ? JSON.stringify(data) : '' } as MessageEvent)
  }

  close() {
    this.readyState = 2
  }

  static get last(): MockEventSource {
    return MockEventSource.instances[MockEventSource.instances.length - 1]
  }
}

const explorerRefreshToken = ref(0)
const overviewRefreshToken = ref(0)

// Keep this callback equivalent to DashboardView.refreshAll: SSE owns invalidation; the
// coalesced view callback owns only the two refresh-token increments.
function refreshAll() {
  explorerRefreshToken.value += 1
  overviewRefreshToken.value += 1
}

const Harness = defineComponent({
  setup() {
    return useFlowGateSse(refreshAll)
  },
  template: '<div />',
})

const COALESCE_MS = 250
let invalidateProject: ReturnType<typeof vi.fn>

function mountHarness() {
  localStorage.setItem('fg_current_project_id', 'proj_alpha')
  const explorerStore = useExplorerStore()
  const dashboardStore = useDashboardStore()
  invalidateProject = vi.fn()
  vi.spyOn(explorerStore, 'invalidateProject').mockImplementation(invalidateProject)
  vi.spyOn(dashboardStore, 'invalidate').mockImplementation(() => {})
  return mount(Harness, { global: { plugins: [i18n] } })
}

beforeEach(() => {
  vi.useFakeTimers()
  setActivePinia(createPinia())
  MockEventSource.instances = []
  explorerRefreshToken.value = 0
  overviewRefreshToken.value = 0
  showToast.mockReset()
  vi.stubGlobal('EventSource', MockEventSource)
  ;(window as any).__accessToken__ = 'tok1'
  sessionStorage.clear()
  localStorage.clear()
})

afterEach(() => {
  vi.restoreAllMocks()
  vi.useRealTimers()
  delete (window as any).__accessToken__
  localStorage.clear()
})

describe('SSE invalidateProject ownership (0552 T0011)', () => {
  it('keeps the extracted callback aligned with DashboardView token-only refreshAll', () => {
    const refreshAllBody = dashboardSource.match(/function refreshAll\(\) \{([\s\S]*?)\n\}/)?.[1] ?? ''
    expect(refreshAllBody).not.toContain('invalidateProject')
    expect(refreshAllBody.match(/explorerRefreshToken\.value \+= 1/g)).toHaveLength(1)
    expect(refreshAllBody.match(/overviewRefreshToken\.value \+= 1/g)).toHaveLength(1)
  })

  it('invalidates once for one event and does not add another invalidation at flush', () => {
    const wrapper = mountHarness()
    MockEventSource.last.emit('open')

    MockEventSource.last.emit('group_view_refresh', { project: 'proj_alpha' })
    expect(invalidateProject).toHaveBeenCalledTimes(1)
    expect(explorerRefreshToken.value).toBe(0)
    expect(overviewRefreshToken.value).toBe(0)

    vi.advanceTimersByTime(COALESCE_MS)

    expect(invalidateProject).toHaveBeenCalledTimes(1)
    expect(explorerRefreshToken.value).toBe(1)
    expect(overviewRefreshToken.value).toBe(1)
    wrapper.unmount()
  })

  it('invalidates once per burst event while flushing both view tokens only once', () => {
    const wrapper = mountHarness()
    MockEventSource.last.emit('open')

    for (let i = 0; i < 3; i++) {
      MockEventSource.last.emit('group_view_refresh', { project: 'proj_alpha' })
    }
    expect(invalidateProject).toHaveBeenCalledTimes(3)
    expect(explorerRefreshToken.value).toBe(0)
    expect(overviewRefreshToken.value).toBe(0)

    vi.advanceTimersByTime(COALESCE_MS)

    expect(invalidateProject).toHaveBeenCalledTimes(3)
    expect(explorerRefreshToken.value).toBe(1)
    expect(overviewRefreshToken.value).toBe(1)
    wrapper.unmount()
  })
})