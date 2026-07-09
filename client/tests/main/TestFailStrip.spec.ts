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
})
