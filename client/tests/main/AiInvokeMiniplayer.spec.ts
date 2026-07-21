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

const read = (rel: string) => readFileSync(fileURLToPath(new URL(rel, import.meta.url)), 'utf-8')

function mountPlayer() {
  return mount(AiInvokeMiniplayer, { global: { plugins: [i18n] } })
}

// The monitor is a header chip whose cards live in a popover, so every card-level
// assertion has to open it first (0269 NR0011).
async function openPopover(wrapper: ReturnType<typeof mountPlayer>) {
  await wrapper.find('.aiv-mini__chip').trigger('click')
  await flushPromises()
}

describe('AiInvokeMiniplayer', () => {
  beforeEach(() => {
    sessionStorage.clear()
    setActivePinia(createPinia())
    getRequest.mockReset()
    postRequest.mockReset()
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
  // there was nothing to see at all. It now stays as a muted chip in the header.
  it('stays visible as a muted chip while there is no run to monitor', async () => {
    const wrapper = mountPlayer()
    await flushPromises()
    const root = wrapper.find('.aiv-mini')
    expect(root.exists()).toBe(true)
    expect(root.classes()).toContain('aiv-mini--idle')

    const chip = wrapper.find('[data-test="ai-miniplayer-chip"]')
    expect(chip.exists()).toBe(true)
    expect(chip.attributes('title')).toBe(t('main.ai_miniplayer.idle_summary'))
    // Nothing to count while idle -> no badge.
    expect(wrapper.find('[data-test="ai-miniplayer-chip-badge"]').exists()).toBe(false)

    await openPopover(wrapper)
    expect(wrapper.find('.aiv-mini__empty').text()).toBe(t('main.ai_miniplayer.empty'))
    expect(wrapper.find('.aiv-mini__card').exists()).toBe(false)
    wrapper.unmount()
  })

  // CH0009 사용자 지시: "글자는 안넣어도 되니까" — the chip carries an icon and a count
  // only. Guard against a label creeping back in and widening the header.
  it('keeps the chip text-free and puts the summary in the tooltip', async () => {
    const wrapper = mountPlayer()
    const store = useAiInvokeRunsStore()
    store.trackStarted({
      run_id: 'run-chip', group_id: 'flowgate.default.3010',
      doc_ref: 'flowgate.default.3010.0001-R', mode: 'continuous',
    })
    store.trackFinished({
      run_id: 'run-chip2', group_id: 'flowgate.default.3011', end_reason: 'user_paused',
    })
    await flushPromises()

    const chip = wrapper.find('[data-test="ai-miniplayer-chip"]')
    const summary = t('main.ai_miniplayer.fab_summary', { running: 1, waiting: 1 })
    expect(chip.attributes('title')).toBe(summary)
    expect(chip.attributes('aria-label')).toContain(summary)
    // The only text inside the chip is the numeric badge — no label.
    expect(chip.text().trim()).toBe('2')
    wrapper.unmount()
  })

  it('toggles the popover from the chip and closes on Escape', async () => {
    const wrapper = mountPlayer()
    await flushPromises()
    expect(wrapper.find('.aiv-mini__panel').exists()).toBe(false)

    await openPopover(wrapper)
    expect(wrapper.find('.aiv-mini__panel').exists()).toBe(true)
    expect(wrapper.find('.aiv-mini__chip').attributes('aria-expanded')).toBe('true')

    window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    await flushPromises()
    expect(wrapper.find('.aiv-mini__panel').exists()).toBe(false)
    wrapper.unmount()
  })

  // The popover hides everything while closed, so an unanswered 질의 has to be visible
  // on the chip itself (NR0011 §5.2).
  it('badges the chip with the awaiting-answer count', async () => {
    const wrapper = mountPlayer()
    const store = useAiInvokeRunsStore()
    store.trackStarted({
      run_id: 'run-qb', group_id: 'flowgate.default.3012',
      doc_ref: 'flowgate.default.3012.0001-R', mode: 'continuous',
    })
    store.trackQuestionRegistered('flowgate.default.3012.0005-Q')
    await flushPromises()

    const chip = wrapper.find('[data-test="ai-miniplayer-chip"]')
    expect(chip.classes()).toContain('aiv-mini__chip--awaiting')
    expect(wrapper.find('[data-test="ai-miniplayer-chip-badge"]').text()).toBe('1')
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
    await openPopover(wrapper)
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
    await openPopover(wrapper)
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
    await openPopover(wrapper)
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
    await openPopover(wrapper)
    expect(wrapper.find('.aiv-mini__card--awaiting').exists()).toBe(true)
    expect(wrapper.text()).toContain(
      t('main.ai_miniplayer.awaiting_q_badge', { count: 1 }),
    )
    expect(wrapper.text()).toContain(t('main.ai_miniplayer.awaiting_q_line'))
    wrapper.unmount()
  })

  it('opens the first pending Q from an awaiting card and closes the popover', async () => {
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
    await openPopover(wrapper)

    const openBtn = wrapper.findAll('button').find(
      b => b.text().includes(t('main.ai_miniplayer.btn_open_doc')),
    )
    expect(openBtn).toBeDefined()
    await openBtn!.trigger('click')
    await flushPromises()

    expect(getRequest).toHaveBeenCalledWith(
      expect.stringContaining('flowgate.default.3007.0006-Q'),
    )
    // Navigating away from the popover closes it — it must not linger over the document.
    expect(wrapper.find('.aiv-mini__panel').exists()).toBe(false)
    // ...and the run is still live, so opening its Q must NOT take the card away (0290).
    expect(store.runsByGroup['flowgate.default.3007']).toBeDefined()
    wrapper.unmount()
  })

  // 0290 R0001 §1: the card is the completion notice, so reading it (문서 열기) is what
  // retires it — not a stopwatch the user never sees.
  it('retires a finished card once its document has been opened', async () => {
    const wrapper = mountPlayer()
    const store = useAiInvokeRunsStore()
    store.trackStarted({
      run_id: 'run-d', group_id: 'flowgate.default.3010',
      doc_ref: 'flowgate.default.3010.0001-R', mode: 'single',
    })
    store.trackFinished({
      run_id: 'run-d', group_id: 'flowgate.default.3010',
      doc_ref: 'flowgate.default.3010.0001-R', outcome: 'complete',
    })
    await flushPromises()
    await openPopover(wrapper)

    const openBtn = wrapper.findAll('button').find(
      b => b.text().includes(t('main.ai_miniplayer.btn_open_doc')),
    )
    await openBtn!.trigger('click')
    await flushPromises()

    expect(store.runsByGroup['flowgate.default.3010']).toBeUndefined()
    wrapper.unmount()
  })

  it('offers a per-card remove and a bulk clear for finished cards only', async () => {
    const wrapper = mountPlayer()
    const store = useAiInvokeRunsStore()
    for (const n of ['3011', '3012']) {
      store.trackStarted({
        run_id: `run-${n}`, group_id: `flowgate.default.${n}`,
        doc_ref: `flowgate.default.${n}.0001-R`, mode: 'single',
      })
      store.trackFinished({
        run_id: `run-${n}`, group_id: `flowgate.default.${n}`,
        doc_ref: `flowgate.default.${n}.0001-R`, outcome: 'complete',
      })
    }
    store.trackStarted({
      run_id: 'run-live', group_id: 'flowgate.default.3013',
      doc_ref: 'flowgate.default.3013.0001-R', mode: 'single',
    })
    await flushPromises()
    await openPopover(wrapper)

    // "닫기" was ambiguous next to the popover's own collapse control (0290 NR0003 §5.2).
    expect(wrapper.text()).toContain(t('main.ai_miniplayer.btn_remove'))
    const removes = wrapper.findAll('[data-test="ai-miniplayer-remove"]')
    expect(removes).toHaveLength(2)
    await removes[0].trigger('click')
    expect(store.finishedCount).toBe(1)

    await wrapper.find('[data-test="ai-miniplayer-clear-finished"]').trigger('click')
    await flushPromises()
    expect(store.finishedCount).toBe(0)
    // The live run is untouched, and with nothing finished left the bulk action goes away.
    expect(store.runsByGroup['flowgate.default.3013']?.phase).toBe('running')
    expect(wrapper.find('[data-test="ai-miniplayer-clear-finished"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('orders awaiting and running cards above finished ones', async () => {
    const wrapper = mountPlayer()
    const store = useAiInvokeRunsStore()
    // Ascending group id alone would invert this order.
    store.trackStarted({
      run_id: 'run-fin', group_id: 'flowgate.default.3020',
      doc_ref: 'flowgate.default.3020.0001-R', mode: 'single',
    })
    store.trackFinished({
      run_id: 'run-fin', group_id: 'flowgate.default.3020',
      doc_ref: 'flowgate.default.3020.0001-R', outcome: 'complete',
    })
    store.trackStarted({
      run_id: 'run-live', group_id: 'flowgate.default.3021',
      doc_ref: 'flowgate.default.3021.0001-R', mode: 'single',
    })
    store.trackStarted({
      run_id: 'run-q', group_id: 'flowgate.default.3022',
      doc_ref: 'flowgate.default.3022.0001-R', mode: 'continuous',
    })
    store.trackQuestionRegistered('flowgate.default.3022.0002-Q')
    await flushPromises()
    await openPopover(wrapper)

    const docs = wrapper.findAll('.aiv-mini__doc').map(el => el.text())
    expect(docs).toEqual([
      'flowgate.default.3022.0001-R',
      'flowgate.default.3021.0001-R',
      'flowgate.default.3020.0001-R',
    ])
    wrapper.unmount()
  })

  // jsdom does not apply SFC <style> blocks nor render AppHeader's layout here, so these
  // two contracts are pinned at the source level instead.

  // The document full view dims the screen with the shared .modal-bg layer. The monitor
  // now lives in the header, so that dim has to start below the header or the chip is
  // unreachable while a document is being read (0269 D0002 / NR0011 §3).
  it('keeps the header reachable under the document full view overlay', () => {
    const mainPanel = read('../../src/main/components/MainPanel.vue')
    const appCss = read('../../shared/app.css')

    // The full view uses the below-header variant, not the bare full-screen dim.
    expect(mainPanel).toMatch(/class="modal-bg modal-bg--below-header"/)
    // ...and that variant actually starts at the header height.
    const variant = /\.modal-bg--below-header\s*\{([^}]*)\}/s.exec(appCss)?.[1] ?? ''
    expect(variant).toContain('top: var(--hdr-h)')
  })

  // The whole point of moving into the header: overlap is impossible by structure, so no
  // component measures another one's height any more (NR0011 §6).
  it('positions itself in the header instead of measuring bottom-fixed UI', () => {
    const sfc = read('../../src/main/components/AiInvokeMiniplayer.vue')
    const actionBar = read('../../src/main/components/ReviewActionBar.vue')

    const rootBlock = /\.aiv-mini\s*\{([^}]*)\}/s.exec(sfc)?.[1] ?? ''
    expect(rootBlock).toContain('position: relative')
    expect(rootBlock).not.toContain('position: fixed')
    // The action-bar height channel is gone on both ends: nobody reads the custom
    // property and nobody publishes it (the only remaining mention is a comment).
    expect(sfc).not.toContain('var(--fg-actionbar-h')
    expect(actionBar).not.toContain("setProperty('--fg-actionbar-h")
  })

  // T0017: the chip carries a quiet outline so it reads as a button before the badge
  // ever appears — but no fill, an outline fainter than the select's .14 beside it, and
  // still no divider line between the two (rev1 반려).
  it('outlines the chip faintly, with no fill and no divider against the provider selector', () => {
    const sfc = read('../../src/main/components/AiInvokeMiniplayer.vue')

    const rootBlock = /\.aiv-mini\s*\{([^}]*)\}/s.exec(sfc)?.[1] ?? ''
    expect(rootBlock).not.toMatch(/border(-right)?:\s*(?!none)/)

    const chipBlock = /\.aiv-mini__chip\s*\{([^}]*)\}/s.exec(sfc)?.[1] ?? ''
    const alpha = /border:\s*1px solid rgba\(255, 255, 255, \.(\d+)\)/.exec(chipBlock)?.[1]
    expect(alpha).toBeDefined()
    expect(Number(`.${alpha}`)).toBeLessThan(.14)
    expect(chipBlock).toContain('background: transparent')

    // The outline is constant: no state restates it, so hover/open/awaiting differ by
    // wash and colour only and the badge keeps the run signal to itself.
    const afterChipBlock = sfc.indexOf('}', sfc.indexOf('.aiv-mini__chip {')) + 1
    const chipStates = sfc.slice(afterChipBlock, sfc.indexOf('.aiv-mini__empty'))
    expect(chipStates).not.toContain('border-color')
    expect(chipStates).not.toMatch(/\bborder:/)
  })
})
