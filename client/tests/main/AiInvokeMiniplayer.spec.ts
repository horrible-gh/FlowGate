import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import AiInvokeMiniplayer from '@main/components/AiInvokeMiniplayer.vue'
import { useAiInvokeRunsStore } from '@main/stores/aiInvokeRuns'

const { getRequest, postRequest } = vi.hoisted(() => ({ getRequest: vi.fn(), postRequest: vi.fn() }))
vi.mock('@shared/api', () => ({ getRequest, postRequest }))

const t = (key: string, args?: Record<string, unknown>) => i18n.global.t(key, args ?? {})

function mountPlayer() {
  return mount(AiInvokeMiniplayer, { global: { plugins: [i18n] } })
}

describe('AiInvokeMiniplayer', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    getRequest.mockReset()
    postRequest.mockReset()
    localStorage.removeItem('flowgate.aiMiniplayer.collapsed')
    // bootstrap (active-all) + title/detail lookups
    getRequest.mockImplementation(async (url: string) => {
      if (url.includes('active-all')) return { data: { ok: true, runs: [], paused: [] } }
      if (url.includes('/documents/detail')) {
        return { data: { doc_id: 'x', title: '테스트 문서', type_code: 'R', file_path: 'a.md' } }
      }
      return { data: {} }
    })
  })

  afterEach(() => {
    document.body.innerHTML = ''
  })

  // 0269 재점검: with no run the monitor used to disappear entirely, so on the dashboard
  // there was nothing to see at all. It now stays mounted as a muted idle pill.
  it('stays visible as an idle pill while there is no run to monitor', async () => {
    const wrapper = mountPlayer()
    await flushPromises()
    const root = wrapper.find('.aiv-mini')
    expect(root.exists()).toBe(true)
    expect(root.classes()).toContain('aiv-mini--idle')
    expect(wrapper.text()).toContain(t('main.ai_miniplayer.idle_summary'))
    // Idle shows the empty line instead of run cards.
    expect(wrapper.find('.aiv-mini__empty').text()).toBe(t('main.ai_miniplayer.empty'))
    expect(wrapper.find('.aiv-mini__card').exists()).toBe(false)
    wrapper.unmount()
  })

  it('drops the idle state as soon as a run arrives', async () => {
    const wrapper = mountPlayer()
    const store = useAiInvokeRunsStore()
    store.trackStarted({
      run_id: 'run-idle', group_id: 'flowgate.default.3000',
      doc_ref: 'flowgate.default.3000.0001-R', mode: 'single',
    })
    await flushPromises()
    expect(wrapper.find('.aiv-mini').classes()).not.toContain('aiv-mini--idle')
    expect(wrapper.find('.aiv-mini__empty').exists()).toBe(false)
    expect(wrapper.find('.aiv-mini__card').exists()).toBe(true)
    wrapper.unmount()
  })

  it('shows the pause button only for continuous running cards', async () => {
    const wrapper = mountPlayer()
    const store = useAiInvokeRunsStore()
    store.trackStarted({
      run_id: 'run-c', group_id: 'flowgate.default.3001',
      doc_ref: 'flowgate.default.3001.0001-R', mode: 'continuous', docs_target: 6,
    })
    await flushPromises()
    expect(wrapper.text()).toContain(t('main.ai_miniplayer.btn_pause'))

    // Control: a single-mode card must NOT offer pause (D0007 정지 흐름).
    store.trackStarted({
      run_id: 'run-s', group_id: 'flowgate.default.3002',
      doc_ref: 'flowgate.default.3002.0001-R', mode: 'single',
    })
    store.dismiss('flowgate.default.3001')
    delete store.runsByGroup['flowgate.default.3001']
    await flushPromises()
    expect(wrapper.text()).not.toContain(t('main.ai_miniplayer.btn_pause'))
    wrapper.unmount()
  })

  it('renders a resume button and paused state for a paused chain', async () => {
    const wrapper = mountPlayer()
    const store = useAiInvokeRunsStore()
    store.trackStarted({
      run_id: 'run-p', group_id: 'flowgate.default.3003',
      doc_ref: 'flowgate.default.3003.0001-R', mode: 'continuous', docs_target: 6,
    })
    store.trackFinished({
      run_id: 'run-p', group_id: 'flowgate.default.3003',
      end_reason: 'user_paused', docs_reached: 3, docs_target: 6,
    })
    await flushPromises()
    expect(wrapper.text()).toContain(t('main.ai_miniplayer.btn_resume'))
    expect(wrapper.text()).toContain(t('main.ai_miniplayer.state_paused'))
    expect(wrapper.text()).not.toContain(t('common.close'))
    wrapper.unmount()
  })

  it('highlights awaiting-answer cards with the Q badge', async () => {
    const wrapper = mountPlayer()
    const store = useAiInvokeRunsStore()
    store.trackStarted({
      run_id: 'run-q', group_id: 'flowgate.default.3004',
      doc_ref: 'flowgate.default.3004.0001-R', mode: 'continuous',
    })
    store.trackQuestionRegistered('flowgate.default.3004.0005-Q')
    await flushPromises()
    expect(wrapper.find('.aiv-mini__card--awaiting').exists()).toBe(true)
    expect(wrapper.text()).toContain(
      t('main.ai_miniplayer.awaiting_q_badge', { count: 1 }),
    )
    expect(wrapper.text()).toContain(t('main.ai_miniplayer.awaiting_q_line'))
    wrapper.unmount()
  })

  it('collapses to a FAB with the running/waiting summary', async () => {
    const wrapper = mountPlayer()
    const store = useAiInvokeRunsStore()
    store.trackStarted({
      run_id: 'run-1', group_id: 'flowgate.default.3005',
      doc_ref: 'flowgate.default.3005.0001-R', mode: 'continuous',
    })
    store.trackFinished({
      run_id: 'run-x', group_id: 'flowgate.default.3006', end_reason: 'user_paused',
    })
    await flushPromises()

    await wrapper.find('.aiv-mini__iconbtn').trigger('click')
    const fab = wrapper.find('.aiv-mini__fab')
    expect(fab.exists()).toBe(true)
    expect(fab.text()).toContain(
      t('main.ai_miniplayer.fab_summary', { running: 1, waiting: 1 }),
    )

    await fab.trigger('click')
    expect(wrapper.find('.aiv-mini__panel').exists()).toBe(true)
    wrapper.unmount()
  })

  it('opens the first pending Q from an awaiting card', async () => {
    getRequest.mockImplementation(async (url: string) => {
      if (url.includes('active-all')) return { data: { ok: true, runs: [], paused: [] } }
      if (url.includes('/documents/detail')) {
        const docId = decodeURIComponent(url.split('doc_id=')[1] ?? '')
        return {
          data: {
            doc_id: docId, title: 'Q 문서',
            type_code: docId.endsWith('-Q') ? 'Q' : 'R', file_path: 'q.md',
          },
        }
      }
      return { data: {} }
    })
    const wrapper = mountPlayer()
    const store = useAiInvokeRunsStore()
    store.trackStarted({
      run_id: 'run-q2', group_id: 'flowgate.default.3007',
      doc_ref: 'flowgate.default.3007.0001-R', mode: 'continuous',
    })
    store.trackQuestionRegistered('flowgate.default.3007.0006-Q')
    await flushPromises()

    const openBtn = wrapper.findAll('button').find(
      b => b.text().includes(t('main.ai_miniplayer.btn_open_doc')),
    )
    expect(openBtn).toBeDefined()
    await openBtn!.trigger('click')
    await flushPromises()

    expect(getRequest).toHaveBeenCalledWith(
      expect.stringContaining('flowgate.default.3007.0006-Q'),
    )
    wrapper.unmount()
  })

  // jsdom does not apply SFC <style> blocks, so the stacking order cannot be observed
  // through mount(); pin the layering contract at the source level instead. The document
  // full view dims the whole screen via the shared .modal-bg layer, and the miniplayer
  // must stay visible above it while a document is being read (0269 D0002).
  it('stacks above the shared modal layer so cards stay visible in document full view', () => {
    const read = (rel: string) => readFileSync(fileURLToPath(new URL(rel, import.meta.url)), 'utf-8')
    const sfc = read('../../src/main/components/AiInvokeMiniplayer.vue')
    const appCss = read('../../shared/app.css')

    const mini = Number(/\.aiv-mini\s*\{[^}]*z-index:\s*(\d+)/s.exec(sfc)?.[1])
    const modalBg = Number(/\.modal-bg\s*\{[^}]*z-index:\s*(\d+)/s.exec(appCss)?.[1])
    const modalOverlay = Number(/\.modal-overlay\s*\{[^}]*z-index:\s*(\d+)/s.exec(appCss)?.[1])

    expect(Number.isFinite(mini)).toBe(true)
    expect(Number.isFinite(modalBg)).toBe(true)
    expect(Number.isFinite(modalOverlay)).toBe(true)
    expect(mini).toBeGreaterThan(modalBg)
    expect(mini).toBeGreaterThan(modalOverlay)
  })
})
