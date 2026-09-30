import { mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import AiEvaluationPanel from '@/features/ai/evaluation/AiEvaluationPanel.vue'
import * as api from '@/features/ai/api'
import type { AiEvaluation, ProviderItem } from '@/features/ai/types'

registerAppMessages(i18n)

const mockProviders: ProviderItem[] = [
  { id: 'p1', name: 'Claude Sonnet 5', exec_type: 'cli', kind: 'claude', enabled: true },
  { id: 'p2', name: 'OpenAI GPT-4.1', exec_type: 'api', kind: 'openai', enabled: true },
]

const mockCurrentEvaluation: AiEvaluation = {
  evaluation_id: 'ev-1',
  project_id: 'proj-1',
  provider_id: 'p1',
  provider_name: 'Claude Sonnet 5',
  lifecycle: 'completed',
  verdict: 'suitable',
  reason_code: null,
  diagnostic_summary: 'Clean run',
  is_stale: false,
  created_at: '2026-09-30T00:00:00Z',
}

describe('AiEvaluationPanel (0015 T0003)', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    i18n.global.locale.value = 'ko'
    vi.spyOn(api, 'fetchCurrentEvaluation').mockResolvedValue({
      provider_id: 'p1',
      current: mockCurrentEvaluation,
      latest_history: mockCurrentEvaluation,
    })
    vi.spyOn(api, 'fetchEvaluationHistory').mockResolvedValue({
      items: [mockCurrentEvaluation],
      next_cursor: 'cursor-2',
    })
  })

  afterEach(() => {
    document.body.innerHTML = ''
  })

  it('renders current evaluation card and score rubric for selected provider', async () => {
    const wrapper = mount(AiEvaluationPanel, {
      props: {
        projectId: 'proj-1',
        providers: mockProviders,
      },
      global: { plugins: [i18n] },
    })

    await new Promise((r) => setTimeout(r, 20))

    expect(wrapper.find('.current-eval-card').exists()).toBe(true)
    expect(wrapper.find('.current-provider b').text()).toBe('Claude Sonnet 5')
    expect(wrapper.text()).not.toContain('8.6')
    expect(wrapper.find('.current-score-box').text()).toContain('적합')
    expect(wrapper.find('.current-bars').exists()).toBe(false)
    expect(wrapper.find('.detail-bars').exists()).toBe(false)
  })

  it('triggers start evaluation and updates active run', async () => {
    const startSpy = vi.spyOn(api, 'startEvaluation').mockResolvedValue({
      ...mockCurrentEvaluation,
      evaluation_id: 'ev-new',
      lifecycle: 'running',
      verdict: null,
    })

    const wrapper = mount(AiEvaluationPanel, {
      props: {
        projectId: 'proj-1',
        providers: mockProviders,
      },
      global: { plugins: [i18n] },
    })

    await new Promise((r) => setTimeout(r, 20))

    const startBtn = wrapper.find('.detail-actions .ui-btn--primary')
    await startBtn.trigger('click')

    expect(startSpy).toHaveBeenCalledWith('proj-1', 'p1')
  })

  it('handles 409 evaluation_already_running conflict gracefully', async () => {
    const conflictErr = {
      response: {
        status: 409,
        data: {
          error: {
            code: 'evaluation_already_running',
            evaluation_id: 'ev-running-conflict',
          },
        },
      },
    }
    vi.spyOn(api, 'startEvaluation').mockRejectedValue(conflictErr)
    const detailSpy = vi.spyOn(api, 'fetchEvaluationDetail').mockResolvedValue({
      ...mockCurrentEvaluation,
      evaluation_id: 'ev-running-conflict',
      lifecycle: 'running',
    })

    const wrapper = mount(AiEvaluationPanel, {
      props: {
        projectId: 'proj-1',
        providers: mockProviders,
      },
      global: { plugins: [i18n] },
    })

    await new Promise((r) => setTimeout(r, 20))

    const startBtn = wrapper.find('.detail-actions .ui-btn--primary')
    await startBtn.trigger('click')
    await new Promise((r) => setTimeout(r, 20))

    expect(detailSpy).toHaveBeenCalledWith('proj-1', 'ev-running-conflict')
    expect(wrapper.find('.panel-error-alert').exists()).toBe(false)
  })

  it('supports pagination via load more button', async () => {
    const wrapper = mount(AiEvaluationPanel, {
      props: {
        projectId: 'proj-1',
        providers: mockProviders,
      },
      global: { plugins: [i18n] },
    })

    await new Promise((r) => setTimeout(r, 20))

    const moreBtn = wrapper.find('.load-more-row button')
    expect(moreBtn.exists()).toBe(true)

    const moreHistorySpy = vi.spyOn(api, 'fetchEvaluationHistory').mockResolvedValue({
      items: [{ ...mockCurrentEvaluation, evaluation_id: 'ev-page-2' }],
      next_cursor: null,
    })

    await moreBtn.trigger('click')
    expect(moreHistorySpy).toHaveBeenCalledWith('proj-1', 'p1', 20, 'cursor-2')
  })

  it('renders honest status-specific messages for history rows with missing diagnostic_summary', async () => {
    const historyItems: AiEvaluation[] = [
      {
        ...mockCurrentEvaluation,
        evaluation_id: 'ev-failed',
        lifecycle: 'failed',
        verdict: null,
        diagnostic_summary: null,
      },
      {
        ...mockCurrentEvaluation,
        evaluation_id: 'ev-running',
        lifecycle: 'running',
        verdict: null,
        diagnostic_summary: '',
      },
      {
        ...mockCurrentEvaluation,
        evaluation_id: 'ev-cancelled',
        lifecycle: 'cancelled',
        verdict: null,
        diagnostic_summary: null,
      },
      {
        ...mockCurrentEvaluation,
        evaluation_id: 'ev-unsuitable',
        lifecycle: 'completed',
        verdict: 'unsuitable',
        diagnostic_summary: null,
      },
    ]

    vi.spyOn(api, 'fetchEvaluationHistory').mockResolvedValue({
      items: historyItems,
      next_cursor: null,
    })

    const wrapper = mount(AiEvaluationPanel, {
      props: {
        projectId: 'proj-1',
        providers: mockProviders,
      },
      global: { plugins: [i18n] },
    })

    await new Promise((r) => setTimeout(r, 20))

    const rows = wrapper.findAll('.evaluation-detail-row')
    expect(rows).toHaveLength(4)

    // Row 0: failed evaluation without diagnostic summary must NOT say baseline_passed
    expect(rows[0].text()).toContain('평가 실패')
    expect(rows[0].text()).not.toContain('기본 기준 테스트를 통과')

    // Row 1: running evaluation without diagnostic summary
    expect(rows[1].text()).toContain('실행 중')
    expect(rows[1].text()).not.toContain('기본 기준 테스트를 통과')

    // Row 2: cancelled evaluation without diagnostic summary
    expect(rows[2].text()).toContain('평가 취소됨')
    expect(rows[2].text()).not.toContain('기본 기준 테스트를 통과')

    // Row 3: unsuitable evaluation without diagnostic summary
    expect(rows[3].text()).toContain('부적합')
    expect(rows[3].text()).not.toContain('기본 기준 테스트를 통과')
  })

  it('excludes disabled providers from selectable runnable targets', () => {
    const mixedProviders: ProviderItem[] = [
      { id: 'p1', name: 'Active Provider', exec_type: 'cli', kind: 'claude', enabled: true },
      { id: 'p2-disabled', name: 'Disabled Provider', exec_type: 'api', kind: 'openai', enabled: false },
    ]

    const wrapper = mount(AiEvaluationPanel, {
      props: {
        projectId: 'proj-1',
        providers: mixedProviders,
      },
      global: { plugins: [i18n] },
    })

    const select = wrapper.find('#eval-target')
    expect(select.exists()).toBe(true)
    const options = select.findAll('option')
    expect(options).toHaveLength(1)
    expect(options[0].text()).toContain('Active Provider')
    expect(wrapper.text()).not.toContain('Disabled Provider')
  })
})