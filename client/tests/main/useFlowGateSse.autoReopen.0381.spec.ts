import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { defineComponent, h } from 'vue'
import i18n from '@shared/i18n'
import { useFlowGateSse } from '@main/composables/useFlowGateSse'
import { useToast } from '@main/components/common/useToast'

vi.mock('@shared/api', () => ({
  getRequest: vi.fn().mockResolvedValue({ data: {} }),
  postRequest: vi.fn().mockResolvedValue({ data: {} }),
  serverLogout: vi.fn(),
}))

type Listener = (e: MessageEvent) => void

const listeners = new Map<string, Listener[]>()

class StubEventSource {
  static instances: StubEventSource[] = []
  onerror: ((e: Event) => void) | null = null
  onopen: ((e: Event) => void) | null = null
  readyState = 1
  constructor(public url: string) {
    StubEventSource.instances.push(this)
  }
  addEventListener(type: string, fn: Listener) {
    const bucket = listeners.get(type) ?? []
    bucket.push(fn)
    listeners.set(type, bucket)
  }
  close() {
    this.readyState = 2
  }
}

function emit(type: string, data: unknown) {
  for (const fn of listeners.get(type) ?? []) {
    fn({ data: JSON.stringify(data) } as MessageEvent)
  }
}

const Host = defineComponent({
  setup() {
    useFlowGateSse(() => {})
    return () => h('div')
  },
})

beforeEach(() => {
  setActivePinia(createPinia())
  listeners.clear()
  StubEventSource.instances = []
  useToast().toasts.value = []
  vi.stubGlobal('EventSource', StubEventSource as unknown as typeof EventSource)
  i18n.global.locale.value = 'ko'
})

// R0001 group 0381: a CODE RED puts the failing TS back through the time machine to the
// pre-approval step. Without a named notice the only trace on screen is a status badge
// quietly flipping behind a "test failed" toast — which reads as nothing having happened.
describe('auto TS reopen notice (0381)', () => {
  it('names the pre-approval return when the server reports the auto reopen', () => {
    mount(Host, { global: { plugins: [i18n] } })

    emit('group_view_refresh', {
      project: 'p',
      payload: {
        group_id: 'g',
        reason: 'test_run_code_failure_auto_reopen',
        doc_id: 'flowgate.default.0381.0003-TS',
        target_seq: 3,
        run_id: 'run-1',
      },
    })

    const messages = useToast().toasts.value.map((toast) => toast.message)
    expect(messages).toHaveLength(1)
    expect(messages[0]).toContain('flowgate.default.0381.0003-TS')
    expect(messages[0]).toContain('승인이전')
  })

  it('stays silent for an ordinary group refresh', () => {
    mount(Host, { global: { plugins: [i18n] } })

    emit('group_view_refresh', {
      project: 'p',
      payload: { group_id: 'g', reason: 'test_run_finished' },
    })

    expect(useToast().toasts.value).toHaveLength(0)
  })
})
