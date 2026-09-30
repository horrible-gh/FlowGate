import { mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it } from 'vitest'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import AiOverviewPanel from '@/features/ai/overview/AiOverviewPanel.vue'
import type { AiEvaluation, AiGroup, AiOverviewData, ProviderItem } from '@/features/ai/types'

registerAppMessages(i18n)

const mockOverview: AiOverviewData = {
  providers: {
    mode: 'inherit',
    total: 2,
    enabled: 2,
    default_provider_id: 'p1',
  },
  evaluation: {
    providers_with_current_result: 1,
    running: 0,
    failed: 0,
    stale: 0,
  },
  ai_groups: {
    total: 1,
    invalid: 0,
  },
  errors: [],
}

const mockProviders: ProviderItem[] = [
  { id: 'p1', name: 'Claude Sonnet 5', exec_type: 'cli', kind: 'claude', enabled: true },
  { id: 'p2', name: 'OpenAI GPT-4.1', exec_type: 'api', kind: 'openai', enabled: true },
]

const mockEvaluations: AiEvaluation[] = [
  {
    evaluation_id: 'ev-1',
    project_id: 'proj-1',
    provider_id: 'p1',
    provider_name: 'Claude Sonnet 5',
    lifecycle: 'completed',
    verdict: 'suitable',
    is_stale: false,
    created_at: '2026-09-30T00:00:00Z',
  },
]

const mockGroups: AiGroup[] = [
  {
    ai_group_id: 'g1',
    project_id: 'proj-1',
    name: '개발팀 A',
    description: '기본 개발팀',
    work_executor: { provider_id: 'p1', provider_name: 'Claude Sonnet 5', is_available: true },
    reviewer: { provider_id: 'p2', provider_name: 'OpenAI GPT-4.1', is_available: true },
    reworker: null,
  },
]

describe('AiOverviewPanel (0015 T0003)', () => {
  beforeEach(() => {
    i18n.global.locale.value = 'ko'
  })

  it('renders 3-step banner cards with titles and navigation triggers', async () => {
    const wrapper = mount(AiOverviewPanel, {
      props: {
        overview: mockOverview,
        providers: mockProviders,
        evaluations: mockEvaluations,
        groups: mockGroups,
      },
      global: { plugins: [i18n] },
    })

    const steps = wrapper.findAll('.step-card')
    expect(steps).toHaveLength(3)

    const buttons = wrapper.findAll('.step-action')
    expect(buttons).toHaveLength(3)

    await buttons[0].trigger('click')
    expect(wrapper.emitted('navigate')?.[0]).toEqual(['providers'])

    await buttons[1].trigger('click')
    expect(wrapper.emitted('navigate')?.[1]).toEqual(['evaluation'])

    await buttons[2].trigger('click')
    expect(wrapper.emitted('navigate')?.[2]).toEqual(['groups'])
  })

  it('renders 3 lower summary panels for Providers, Evaluation, and Groups', () => {
    const wrapper = mount(AiOverviewPanel, {
      props: {
        overview: mockOverview,
        providers: mockProviders,
        evaluations: mockEvaluations,
        groups: mockGroups,
      },
      global: { plugins: [i18n] },
    })

    const panels = wrapper.findAll('.panel')
    expect(panels).toHaveLength(3)

    // Panel 1: Providers list
    const providerRows = wrapper.findAll('.provider-row')
    expect(providerRows).toHaveLength(2)
    expect(providerRows[0].text()).toContain('Claude Sonnet 5')
    expect(providerRows[1].text()).toContain('OpenAI GPT-4.1')

    // Panel 2: Evaluations
    const evalRows = wrapper.findAll('.eval-row')
    expect(evalRows).toHaveLength(1)
    expect(evalRows[0].text()).toContain('Claude Sonnet 5')
    expect(evalRows[0].text()).toContain('적합')
    expect(evalRows[0].text()).not.toContain('8.6')
    expect(evalRows[0].find('.bars').exists()).toBe(false)

    // Panel 3: Groups
    const groupCards = wrapper.findAll('.team-card')
    expect(groupCards).toHaveLength(1)
    expect(groupCards[0].text()).toContain('개발팀 A')
    expect(groupCards[0].text()).toContain('Supervisor')
    expect(groupCards[0].text()).toContain('Claude Sonnet 5')
    // Ordinary groups do not fabricate a Default Team badge
    expect(groupCards[0].text()).not.toContain('기본팀')
    expect(groupCards[0].text()).not.toContain('Default Team')
    expect(wrapper.find('.team-badge').exists()).toBe(false)
  })

  it('handles partial section errors without failing the entire panel', () => {
    const partialErrorOverview: AiOverviewData = {
      ...mockOverview,
      errors: [{ section: 'evaluation', code: 'overview_section_failed' }],
    }

    const wrapper = mount(AiOverviewPanel, {
      props: {
        overview: partialErrorOverview,
        providers: mockProviders,
        evaluations: mockEvaluations,
        groups: mockGroups,
      },
      global: { plugins: [i18n] },
    })

    // Evaluation panel should render error box
    const panelErrors = wrapper.findAll('.panel-error')
    expect(panelErrors).toHaveLength(1)
    expect(panelErrors[0].text()).toContain('정보를 불러오지 못했습니다')

    // Providers and Groups panels still render normally
    expect(wrapper.findAll('.provider-row')).toHaveLength(2)
    expect(wrapper.findAll('.team-card')).toHaveLength(1)
  })

  it('propagates sectionErrors to individual cards and displays panel error instead of empty state', () => {
    const wrapper = mount(AiOverviewPanel, {
      props: {
        overview: mockOverview,
        providers: [],
        evaluations: [],
        groups: [],
        sectionErrors: {
          settings: 'Failed to load project AI settings',
          evaluations: 'Failed to load evaluation history',
          groups: 'Failed to load AI groups',
        },
      },
      global: { plugins: [i18n] },
    })

    const panelErrors = wrapper.findAll('.panel-error')
    expect(panelErrors).toHaveLength(3)

    // Should NOT display false empty states when data failed to load
    expect(wrapper.find('.panel-empty').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('등록된 AI Provider가 없습니다')
    expect(wrapper.text()).not.toContain('평가 결과가 아직 없습니다')
    expect(wrapper.text()).not.toContain('구성된 AI 그룹이 없습니다')
  })

  it('renders disabled mode label explicitly when backend mode is disabled', () => {
    const disabledOverview: AiOverviewData = {
      ...mockOverview,
      providers: {
        mode: 'disabled',
        total: 0,
        enabled: 0,
        default_provider_id: null,
      },
    }

    const wrapper = mount(AiOverviewPanel, {
      props: {
        overview: disabledOverview,
        providers: [],
        evaluations: mockEvaluations,
        groups: mockGroups,
      },
      global: { plugins: [i18n] },
    })

    expect(wrapper.text()).toContain('AI 비활성화')
    expect(wrapper.text()).not.toContain('프로젝트 개별 설정')
  })
})