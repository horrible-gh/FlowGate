// Group 0166: manual test-run entry point for an approved TS (NR0003 — the only
// prior caller of POST /documents/test-run was the failure strip, which cannot
// offer the FIRST run). Covers the visibility gate (mirror of the backend
// admission gate), the run launch, the error mapping, and the AI delegation copy.
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import i18n from '@shared/i18n'

const postRequest = vi.fn()
vi.mock('@shared/api', () => ({
  postRequest: (...args: unknown[]) => postRequest(...args),
  // The provider store (0268 B0001 invoke path) loads its list through getRequest; it
  // swallows failures into an empty list, so the invoke assertions below stay about the
  // POST body rather than about provider availability.
  getRequest: async () => ({ data: { providers: [] } }),
}))

const showToast = vi.fn()
vi.mock('@main/components/common/useToast', () => ({
  useToast: () => ({ showToast }),
}))

// jsdom has no ClipboardItem/execCommand — replace the deferred-copy primitive with
// a faithful stub that resolves the producer (as the real fallback path does).
const copiedTexts: string[] = []
let clipboardResult = true
vi.mock('@main/utils/clipboard', () => ({
  ClipboardAbort: class ClipboardAbort extends Error {},
  copyToClipboardDeferred: async (produce: () => Promise<string>) => {
    try {
      copiedTexts.push(await produce())
    } catch {
      return false
    }
    return clipboardResult
  },
  // No failed text pending → the manual-copy fallback modal (B0001 / group 0221) cannot
  // open and the failure path falls back to the toast this suite asserts.
  consumeLastFailedCopyText: () => null,
}))

import TestRunStrip from '@main/components/TestRunStrip.vue'

function mountStrip(props: Record<string, unknown> = {}) {
  return mount(TestRunStrip, {
    props: {
      typeCode: 'TS',
      reviewStatus: 'approved',
      testRun: null,
      groupDisposed: false,
      docLoaded: true,
      docId: 'proj.mod.0001.0005-TS',
      ...props,
    },
    global: { plugins: [i18n] },
  })
}

beforeEach(() => {
  postRequest.mockReset()
  showToast.mockReset()
  copiedTexts.length = 0
  clipboardResult = true
  i18n.global.locale.value = 'en'
})

describe('TestRunStrip', () => {
  it('renders the first-run entry point for an approved TS with no run history', () => {
    const wrapper = mountStrip()
    expect(wrapper.find('.run-strip').exists()).toBe(true)
    expect(wrapper.text()).toContain(i18n.global.t('main.test_run_strip.run'))
    expect(wrapper.text()).toContain(i18n.global.t('main.test_run_strip.ready'))
  })

  it('stays hidden for non-TS docs, unapproved TS, unloaded docs, and disposed groups', () => {
    expect(mountStrip({ typeCode: 'TR' }).find('.run-strip').exists()).toBe(false)
    expect(mountStrip({ reviewStatus: 'draft' }).find('.run-strip').exists()).toBe(false)
    expect(mountStrip({ reviewStatus: 'rejected' }).find('.run-strip').exists()).toBe(false)
    expect(mountStrip({ docLoaded: false }).find('.run-strip').exists()).toBe(false)
    expect(mountStrip({ groupDisposed: true }).find('.run-strip').exists()).toBe(false)
    // pending_review/revised WITHOUT run history is not re-runnable (backend 409s) -> hidden.
    expect(mountStrip({ reviewStatus: 'pending_review' }).find('.run-strip').exists()).toBe(false)
    expect(mountStrip({ reviewStatus: 'revised' }).find('.run-strip').exists()).toBe(false)
  })

  it('offers a re-run for pending_review with a prior passed run (0163 relaxation)', () => {
    const wrapper = mountStrip({
      reviewStatus: 'pending_review',
      testRun: { run_id: 'trun_1', status: 'passed', case_passed: 3, case_total: 3 },
    })
    expect(wrapper.find('.run-strip').exists()).toBe(true)
    expect(wrapper.text()).toContain(i18n.global.t('main.test_run_strip.rerun'))
    expect(wrapper.text()).toContain('trun_1')
  })

  it('offers a re-run for revised with a prior passed run (0169 follow-up)', () => {
    const wrapper = mountStrip({
      reviewStatus: 'revised',
      testRun: { run_id: 'trun_2', status: 'passed', case_passed: 3, case_total: 3 },
    })
    expect(wrapper.find('.run-strip').exists()).toBe(true)
    expect(wrapper.text()).toContain(i18n.global.t('main.test_run_strip.rerun'))
    expect(wrapper.text()).toContain('trun_2')
  })

  it('hides itself when the latest run failed — TestFailStrip owns that state', () => {
    const wrapper = mountStrip({
      reviewStatus: 'pending_review',
      testRun: { run_id: 'trun_1', status: 'failed' },
    })
    expect(wrapper.find('.run-strip').exists()).toBe(false)
  })

  it('shows a running indicator with a cancel control (no start/delegate buttons) while a run is in progress', () => {
    const wrapper = mountStrip({
      reviewStatus: 'pending_review',
      testRun: { run_id: 'trun_2', status: 'running' },
    })
    expect(wrapper.find('.run-strip').exists()).toBe(true)
    expect(wrapper.text()).toContain(i18n.global.t('main.test_run_strip.running'))
    expect(wrapper.find('.run-strip-btn').exists()).toBe(false)
    expect(wrapper.find('.run-strip-cancel').exists()).toBe(true)
    expect(wrapper.find('.run-strip-cancel').attributes('disabled')).toBeUndefined()
  })

  // flowgate.default.0358 T0004: replaces the old "no action buttons while running"
  // assertion — the stop control now lives in this same always-mounted strip.
  describe('cancel (0358 T0004)', () => {
    it('click POSTs the cancel route and disables the button immediately', async () => {
      postRequest.mockResolvedValue({ data: { ok: true, run_id: 'trun_2', status: 'cancelling' } })
      const wrapper = mountStrip({
        reviewStatus: 'pending_review',
        testRun: { run_id: 'trun_2', status: 'running' },
      })
      const btn = wrapper.find('.run-strip-cancel')
      await btn.trigger('click')
      expect(postRequest).toHaveBeenCalledWith(
        '/api/v1/documents/test-run/trun_2/cancel',
        {},
      )
      // Local flag flips synchronously on click, before the POST resolves.
      expect(wrapper.find('.run-strip-cancel').attributes('disabled')).toBeDefined()
      expect(wrapper.text()).toContain(i18n.global.t('main.test_run_strip.cancelling'))
      await flushPromises()
    })

    it('stays disabled with the cancelling label while the server reports status=cancelling', () => {
      const wrapper = mountStrip({
        reviewStatus: 'pending_review',
        testRun: { run_id: 'trun_2', status: 'cancelling' },
      })
      expect(wrapper.find('.run-strip-cancel').exists()).toBe(true)
      expect(wrapper.find('.run-strip-cancel').attributes('disabled')).toBeDefined()
      expect(wrapper.text()).toContain(i18n.global.t('main.test_run_strip.cancelling'))
    })

    it('a double click only fires one cancel POST', async () => {
      postRequest.mockResolvedValue({ data: { ok: true, run_id: 'trun_2', status: 'cancelling' } })
      const wrapper = mountStrip({
        reviewStatus: 'pending_review',
        testRun: { run_id: 'trun_2', status: 'running' },
      })
      const btn = wrapper.find('.run-strip-cancel')
      await btn.trigger('click')
      await btn.trigger('click')
      await flushPromises()
      expect(postRequest).toHaveBeenCalledTimes(1)
    })

    it('cancel failure re-enables the button and toasts cancel_failed', async () => {
      postRequest.mockRejectedValue({ response: { data: { error: 'internal_error' } } })
      const wrapper = mountStrip({
        reviewStatus: 'pending_review',
        testRun: { run_id: 'trun_2', status: 'running' },
      })
      await wrapper.find('.run-strip-cancel').trigger('click')
      await flushPromises()
      expect(showToast).toHaveBeenCalledWith(
        i18n.global.t('main.test_run_strip.cancel_failed'),
        'error',
      )
      expect(wrapper.find('.run-strip-cancel').attributes('disabled')).toBeUndefined()
    })

    it('the final cancelled embed shows the cancelled label and a re-run action, not the cancel button', () => {
      const wrapper = mountStrip({
        reviewStatus: 'pending_review',
        testRun: { run_id: 'trun_2', status: 'cancelled' },
      })
      expect(wrapper.find('.run-strip').exists()).toBe(true)
      expect(wrapper.text()).toContain(i18n.global.t('main.test_run_strip.cancelled'))
      expect(wrapper.find('.run-strip-cancel').exists()).toBe(false)
      expect(wrapper.text()).toContain(i18n.global.t('main.test_run_strip.rerun'))
    })
  })

  it('POSTs /documents/test-run on run click, toasts, and emits run-started', async () => {
    postRequest.mockResolvedValue({ data: { ok: true, run_id: 'trun_3' } })
    const wrapper = mountStrip()
    await wrapper.find('.run-strip-btn--run').trigger('click')
    await flushPromises()
    expect(postRequest).toHaveBeenCalledWith('/api/v1/documents/test-run', {
      doc_id: 'proj.mod.0001.0005-TS',
    })
    expect(showToast).toHaveBeenCalledWith(
      i18n.global.t('main.test_run_strip.run_started'),
      'info',
    )
    expect(wrapper.emitted('run-started')).toHaveLength(1)
  })

  it('maps backend admission errors to their dedicated messages', async () => {
    for (const [code, key] of [
      ['doc_not_approved', 'err_not_approved'],
      ['run_in_progress', 'err_in_progress'],
      ['permission_denied', 'err_denied'],
      ['group_disposed', 'err_disposed'],
      ['src_root_missing', 'err_src_missing'],
      ['no_test_cases', 'err_no_cases'],
      ['something_else', 'err_failed'],
    ] as const) {
      postRequest.mockRejectedValueOnce({ response: { data: { error: code } } })
      showToast.mockClear()
      const wrapper = mountStrip()
      await wrapper.find('.run-strip-btn--run').trigger('click')
      await flushPromises()
      expect(showToast).toHaveBeenCalledWith(
        i18n.global.t(`main.test_run_strip.${key}`),
        'error',
      )
      expect(wrapper.emitted('run-started')).toBeUndefined()
    }
  })

  it('delegate click issues a test-run-request and copies the mention', async () => {
    postRequest.mockResolvedValue({ data: { mention: 'MENTION TEXT + token' } })
    const wrapper = mountStrip()
    const buttons = wrapper.findAll('.run-strip-btn')
    await buttons[0].trigger('click')
    await flushPromises()
    expect(postRequest).toHaveBeenCalledWith('/api/v1/documents/test-run-request', {
      doc_id: 'proj.mod.0001.0005-TS',
    })
    expect(copiedTexts).toEqual(['MENTION TEXT + token'])
    expect(showToast).toHaveBeenCalledWith(
      i18n.global.t('main.test_run_strip.delegate_copied'),
      'info',
    )
  })

  it('delegate API failure toasts the mapped error and skips the copy-failed toast', async () => {
    postRequest.mockRejectedValue({ response: { data: { error: 'permission_denied' } } })
    const wrapper = mountStrip()
    await wrapper.findAll('.run-strip-btn')[0].trigger('click')
    await flushPromises()
    expect(showToast).toHaveBeenCalledTimes(1)
    expect(showToast).toHaveBeenCalledWith(
      i18n.global.t('main.test_run_strip.err_denied'),
      'error',
    )
  })

  it('delegate clipboard failure (API ok) toasts the copy-failed message', async () => {
    postRequest.mockResolvedValue({ data: { mention: 'MENTION' } })
    clipboardResult = false
    const wrapper = mountStrip()
    await wrapper.findAll('.run-strip-btn')[0].trigger('click')
    await flushPromises()
    expect(showToast).toHaveBeenCalledWith(
      i18n.global.t('main.test_run_strip.delegate_copy_failed'),
      'error',
    )
  })

  // 0268 B0001: this strip used to offer the clipboard copy ALONE while labelling it
  // "AI에게 위임" with a robot icon, so it read as though the in-app call already existed.
  // Both entrances must now be present and independently reachable.
  describe('in-app AI invoke (0268 B0001)', () => {
    it('offers the copy and the AI-invoke entrances side by side', () => {
      const wrapper = mountStrip()
      expect(wrapper.text()).toContain(i18n.global.t('main.test_run_strip.delegate'))
      expect(wrapper.text()).toContain(i18n.global.t('main.test_run_strip.invoke_ai'))
      expect(wrapper.find('.run-strip-btn--invoke').exists()).toBe(true)
    })

    it('invoke click starts a test_run-scoped run and never touches the clipboard', async () => {
      setActivePinia(createPinia())
      postRequest.mockResolvedValue({ data: { ok: true, run_id: 'air_1' } })
      const wrapper = mountStrip()
      await wrapper.find('.run-strip-btn--invoke').trigger('click')
      await flushPromises()
      expect(postRequest).toHaveBeenCalledWith('/api/v1/ai-invoke/start', {
        project: 'proj',
        module: 'mod',
        group: '0001',
        doc_ref: 'proj.mod.0001.0005-TS',
        action_scope: 'test_run',
        mode: 'single',
        provider_id: undefined,
      })
      // The whole point of the parallel entrance: the mention goes to the worker
      // server-side, so the raw token never reaches the browser or the clipboard.
      expect(copiedTexts).toEqual([])
      expect(showToast).toHaveBeenCalledWith(
        i18n.global.t('main.test_run_strip.invoke_ai_started'),
        'info',
      )
    })

    it('invoke failure surfaces the mapped error and leaves the copy path usable', async () => {
      setActivePinia(createPinia())
      postRequest.mockRejectedValue({ response: { data: { error: 'permission_denied' } } })
      const wrapper = mountStrip()
      await wrapper.find('.run-strip-btn--invoke').trigger('click')
      await flushPromises()
      expect(showToast).toHaveBeenCalledWith(
        i18n.global.t('main.test_run_strip.err_denied'),
        'error',
      )
      // busy must release, or a failed invoke would strand the copy button too.
      expect(wrapper.find('.run-strip-btn--invoke').attributes('disabled')).toBeUndefined()
    })
  })
})
