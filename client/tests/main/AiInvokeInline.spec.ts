import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { nextTick } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import AiInvokeInline from '@main/components/AiInvokeInline.vue'
import { useAiInvokeRunsStore } from '@main/stores/aiInvokeRuns'

const { getRequest, postRequest } = vi.hoisted(() => ({ getRequest: vi.fn(), postRequest: vi.fn() }))
vi.mock('@shared/api', () => ({ getRequest, postRequest }))

describe('AiInvokeInline', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    getRequest.mockReset()
    postRequest.mockReset()
    getRequest.mockResolvedValue({ data: { active: false } })
  })

  afterEach(() => {
    document.body.innerHTML = ''
  })

  it('resets only the content scroller when a run starts and renders zero-document diagnostics', async () => {
    const content = document.createElement('div')
    content.className = 'content-wrap'
    const host = document.createElement('div')
    content.appendChild(host)
    document.body.appendChild(content)
    const scrollTo = vi.fn()
    Object.defineProperty(content, 'scrollTo', { value: scrollTo, configurable: true })

    const wrapper = mount(AiInvokeInline, {
      attachTo: host,
      props: { groupId: 'flowgate.default.0231' },
      global: { plugins: [i18n] },
    })
    const store = useAiInvokeRunsStore()
    store.trackStarted({
      run_id: 'run-0231', group_id: 'flowgate.default.0231',
      doc_ref: 'flowgate.default.0231.0001-B', status: 'running',
    })
    await nextTick()
    await nextTick()
    expect(scrollTo).toHaveBeenCalledWith({ top: 0, behavior: 'auto' })

    store.trackFinished({
      run_id: 'run-0231', group_id: 'flowgate.default.0231', outcome: 'none', docs_reached: 0,
      register_errors: [{ status: 403, reason: 'context_binding', turn: 2 }],
      tool_call_misses: 2, turn_limit_exhausted: true, oracle_mismatch: false,
      end_reason: 'exited',
    })
    await flushPromises()

    const text = wrapper.text()
    expect(text).toContain('HTTP 403')
    expect(text).toContain('context_binding')
    expect(text).toContain(i18n.global.t('main.ai_invoke_dialog.turn_limit_exhausted'))
    wrapper.unmount()
  })
})