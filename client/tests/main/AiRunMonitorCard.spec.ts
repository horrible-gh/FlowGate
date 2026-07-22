import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import AiRunMonitorCard from '@main/components/AiRunMonitorCard.vue'
import { useAiInvokeRunsStore } from '@main/stores/aiInvokeRuns'

const { getRequest, postRequest } = vi.hoisted(() => ({ getRequest: vi.fn(), postRequest: vi.fn() }))
vi.mock('@shared/api', () => ({ getRequest, postRequest }))

const t = (key: string, args?: Record<string, unknown>) => i18n.global.t(key, args ?? {})

function mountCard() {
  return mount(AiRunMonitorCard, { global: { plugins: [i18n] } })
}

describe('AiRunMonitorCard (dashboard)', () => {
  beforeEach(() => {
    sessionStorage.clear()
    setActivePinia(createPinia())
    getRequest.mockReset()
    postRequest.mockReset()
    getRequest.mockImplementation(async (url: string) => {
      if (url.includes('active-all')) return { data: { ok: true, runs: [], paused: [] } }
      if (url.includes('/documents/detail')) {
        const docId = decodeURIComponent(url.split('doc_id=')[1] ?? '')
        return { data: { doc_id: docId, title: '대상 문서', type_code: 'R', file_path: 'a.md' } }
      }
      return { data: {} }
    })
  })

  it('renders on the dashboard with an empty state when nothing is running', async () => {
    const wrapper = mountCard()
    await flushPromises()
    expect(wrapper.find('[data-test="ai-run-monitor-card"]').exists()).toBe(true)
    expect(wrapper.text()).toContain(t('main.ai_miniplayer.dash_title'))
    expect(wrapper.text()).toContain(t('main.ai_miniplayer.dash_empty'))
    expect(wrapper.findAll('.airm-row')).toHaveLength(0)
    wrapper.unmount()
  })

  it('lists running, paused and awaiting-Q runs', async () => {
    const wrapper = mountCard()
    const store = useAiInvokeRunsStore()
    store.trackStarted({
      run_id: 'run-a', group_id: 'flowgate.default.4001',
      doc_ref: 'flowgate.default.4001.0001-R', mode: 'continuous', docs_target: 4,
    })
    store.trackFinished({
      run_id: 'run-b', group_id: 'flowgate.default.4002',
      doc_ref: 'flowgate.default.4002.0001-R', end_reason: 'user_paused',
    })
    store.trackStarted({
      run_id: 'run-c', group_id: 'flowgate.default.4003',
      doc_ref: 'flowgate.default.4003.0001-R', mode: 'single',
    })
    store.trackQuestionRegistered('flowgate.default.4003.0002-Q')
    await flushPromises()

    expect(wrapper.findAll('.airm-row')).toHaveLength(3)
    expect(wrapper.text()).toContain(t('main.ai_miniplayer.dash_state_running'))
    expect(wrapper.text()).toContain(t('main.ai_miniplayer.dash_state_paused'))
    expect(wrapper.text()).toContain(t('main.ai_miniplayer.dash_state_awaiting'))
    expect(wrapper.text()).not.toContain(t('main.ai_miniplayer.dash_empty'))
    wrapper.unmount()
  })

  it('opens the waiting Q — not the run document — from an awaiting row', async () => {
    const wrapper = mountCard()
    const store = useAiInvokeRunsStore()
    store.trackStarted({
      run_id: 'run-q', group_id: 'flowgate.default.4004',
      doc_ref: 'flowgate.default.4004.0001-R', mode: 'continuous',
    })
    store.trackQuestionRegistered('flowgate.default.4004.0007-Q')
    await flushPromises()

    await wrapper.find('.airm-row-main').trigger('click')
    await flushPromises()
    expect(getRequest).toHaveBeenCalledWith(
      expect.stringContaining('flowgate.default.4004.0007-Q'),
    )
    // 0290: opening acknowledges a FINISHED card only — a live run awaiting an answer
    // must stay on the dashboard after its Q is opened.
    expect(store.runsByGroup['flowgate.default.4004']).toBeDefined()
    wrapper.unmount()
  })

  it('removes a finished row when it is opened, and offers an explicit remove button', async () => {
    const wrapper = mountCard()
    const store = useAiInvokeRunsStore()
    store.trackStarted({
      run_id: 'run-f', group_id: 'flowgate.default.4005',
      doc_ref: 'flowgate.default.4005.0001-R', mode: 'single',
    })
    store.trackFinished({
      run_id: 'run-f', group_id: 'flowgate.default.4005',
      doc_ref: 'flowgate.default.4005.0001-R', outcome: 'complete',
    })
    store.trackStarted({
      run_id: 'run-g', group_id: 'flowgate.default.4006',
      doc_ref: 'flowgate.default.4006.0001-R', mode: 'single',
    })
    store.trackFinished({
      run_id: 'run-g', group_id: 'flowgate.default.4006',
      doc_ref: 'flowgate.default.4006.0001-R', outcome: 'complete',
    })
    await flushPromises()

    // The remove button only appears on finished rows, and it is a sibling of the row
    // button — never nested inside it.
    expect(wrapper.findAll('[data-test="ai-run-monitor-remove"]')).toHaveLength(2)
    // Address the row by its document, not by list position — two runs can finish in the
    // same millisecond, so the finished band's order is not something to assert here.
    const rowFor = (docRef: string) => wrapper.findAll('.airm-row')
      .find(row => row.text().includes(docRef))!
    await rowFor('flowgate.default.4005.0001-R')
      .find('[data-test="ai-run-monitor-remove"]').trigger('click')
    expect(store.runsByGroup['flowgate.default.4005']).toBeUndefined()

    await rowFor('flowgate.default.4006.0001-R').find('.airm-row-main').trigger('click')
    await flushPromises()
    expect(store.runsByGroup['flowgate.default.4006']).toBeUndefined()
    wrapper.unmount()
  })

  it('keeps live runs above finished ones', async () => {
    const wrapper = mountCard()
    const store = useAiInvokeRunsStore()
    // Group id order alone would put the finished run first — state has to win.
    store.trackStarted({
      run_id: 'run-old', group_id: 'flowgate.default.4001',
      doc_ref: 'flowgate.default.4001.0001-R', mode: 'single',
    })
    store.trackFinished({
      run_id: 'run-old', group_id: 'flowgate.default.4001',
      doc_ref: 'flowgate.default.4001.0001-R', outcome: 'complete',
    })
    store.trackStarted({
      run_id: 'run-live', group_id: 'flowgate.default.4009',
      doc_ref: 'flowgate.default.4009.0001-R', mode: 'single',
    })
    await flushPromises()

    const docs = wrapper.findAll('.airm-row-doc').map(el => el.text())
    expect(docs[0]).toBe('flowgate.default.4009.0001-R')
    wrapper.unmount()
  })

  // The card must actually be wired into the dashboard overview, not merely exist:
  // a component nobody mounts is exactly the "안 보인다" failure this fixes.
  it('is mounted by the dashboard overview panel', () => {
    const mainPanel = readFileSync(
      resolve(process.cwd(), 'src/main/components/MainPanel.vue'),
      'utf-8',
    )
    expect(mainPanel).toContain("import AiRunMonitorCard from './AiRunMonitorCard.vue'")
    const overview = mainPanel.slice(mainPanel.indexOf('class="overview-panel"'))
    expect(overview.slice(0, overview.indexOf('</script>'))).toContain('<AiRunMonitorCard />')
  })
})
