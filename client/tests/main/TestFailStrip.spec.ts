import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import { postRequest } from '@shared/api'
import TestFailStrip from '@main/components/TestFailStrip.vue'
import type { TestRun } from '@main/types/testRun'

vi.mock('@shared/api', () => ({
  postRequest: vi.fn(),
}))

const failedRun = (overrides: Partial<TestRun> = {}): TestRun => ({
  run_id: '41',
  status: 'failed',
  case_total: 2,
  case_failed: 1,
  started_at: '2026-07-09T05:49:00Z',
  finished_at: '2026-07-09T05:49:30Z',
  cases: [
    {
      case_no: 'TC-1',
      case_title: 'fails once',
      result: 'fail',
      exit_code: 1,
      output_tail: 'expected failure',
    },
  ],
  ...overrides,
})

describe('TestFailStrip', () => {
  beforeEach(() => {
    i18n.global.locale.value = 'en'
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-07-09T05:50:00Z'))
    vi.mocked(postRequest).mockResolvedValue({} as any)
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.clearAllMocks()
  })

  it('shows an optimistic running state immediately after a failed run is re-run', async () => {
    const wrapper = mount(TestFailStrip, {
      props: {
        docId: 'flowgate.default.0183.0004-TS',
        testRun: failedRun(),
      },
      global: { plugins: [i18n] },
    })

    await wrapper.find('.fail-strip-btn--rerun').trigger('click')
    await flushPromises()

    expect(postRequest).toHaveBeenCalledWith('/api/v1/documents/test-run', {
      doc_id: 'flowgate.default.0183.0004-TS',
    })
    expect(wrapper.emitted('run-started')).toHaveLength(1)
    expect(wrapper.find('.fail-strip').classes()).toContain('fail-strip--running')
    expect(wrapper.text()).toContain('Test run starting...')

    vi.advanceTimersByTime(1500)
    await flushPromises()
    expect(wrapper.find('.fail-strip').classes()).not.toContain('fail-strip--running')
  })

  it('distinguishes a fresh repeated failure with completion time and run id', () => {
    const wrapper = mount(TestFailStrip, {
      props: {
        docId: 'flowgate.default.0183.0004-TS',
        testRun: failedRun({ run_id: '42', finished_at: '2026-07-09T05:49:45Z' }),
      },
      global: { plugins: [i18n] },
    })

    expect(wrapper.find('.fail-strip').classes()).toContain('fail-strip--fresh')
    expect(wrapper.find('.fail-strip-fresh-badge').text()).toContain('Finished')
    expect(wrapper.text()).toContain('run 42')
  })

  // flowgate.default.0358 T0004 §8: the rerun POST's run_id must be kept (was:
  // discarded at :202-209) and the optimistic window must not be ended by the fixed
  // timer alone — it must wait for the real embed to carry that same run_id.
  describe('cancel control across the optimistic rerun window (0358 T0004)', () => {
    it('keeps the optimistic indicator with an active cancel control past the fixed timer until the real running embed catches up', async () => {
      vi.mocked(postRequest).mockResolvedValue({ data: { run_id: '99' } } as any)
      const wrapper = mount(TestFailStrip, {
        props: {
          docId: 'flowgate.default.0183.0004-TS',
          testRun: failedRun({ run_id: '41' }),
        },
        global: { plugins: [i18n] },
      })

      await wrapper.find('.fail-strip-btn--rerun').trigger('click')
      await flushPromises()

      // The fixed 1.5s window elapses, but no fresh embed (run_id 99) has arrived yet —
      // must NOT silently drop back to the stale rerun/log buttons with no cancel option.
      vi.advanceTimersByTime(1500)
      await flushPromises()
      expect(wrapper.find('.fail-strip').classes()).toContain('fail-strip--running')
      expect(wrapper.find('.fail-strip-cancel').exists()).toBe(true)

      // The real running embed for THIS rerun (run_id 99) now lands — hand off.
      await wrapper.setProps({ testRun: failedRun({ run_id: '99', status: 'running' }) })
      await flushPromises()
      // status is 'running', not 'failed' -> the whole strip hides; TestRunStrip owns it.
      expect(wrapper.find('.fail-strip').exists()).toBe(false)
    })

    it('cancel click during the optimistic window POSTs the pending run_id', async () => {
      vi.mocked(postRequest).mockResolvedValue({ data: { run_id: '99' } } as any)
      const wrapper = mount(TestFailStrip, {
        props: {
          docId: 'flowgate.default.0183.0004-TS',
          testRun: failedRun({ run_id: '41' }),
        },
        global: { plugins: [i18n] },
      })

      await wrapper.find('.fail-strip-btn--rerun').trigger('click')
      await flushPromises()

      vi.mocked(postRequest).mockClear()
      await wrapper.find('.fail-strip-cancel').trigger('click')
      await flushPromises()

      expect(postRequest).toHaveBeenCalledWith('/api/v1/documents/test-run/99/cancel', {})
    })
  })
})
