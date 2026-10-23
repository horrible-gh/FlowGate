import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import i18n from '@shared/i18n'
import { registerAppMessages } from '@/app/i18n'
import { setCurrentUser } from '@/core/auth/currentUser'
import AiEvaluationDetailModal from '@/features/ai/evaluation/AiEvaluationDetailModal.vue'
import AiEvaluationPanel from '@/features/ai/evaluation/AiEvaluationPanel.vue'
import * as api from '@/features/ai/api'
import type { AiEvaluation, ProviderItem } from '@/features/ai/types'

registerAppMessages(i18n)

// AI 평가 (flowgate.v02.0017): a judge AI scores a target AI on a Scratch task. The 0015
// per-project "current evaluation" card and suitable/unsuitable verdict were retired.

const providers: ProviderItem[] = [
  { id: 'p1', name: 'Claude Sonnet 5', exec_type: 'cli', kind: 'claude', enabled: true },
  { id: 'p2', name: 'OpenAI GPT-4.1', exec_type: 'api', kind: 'openai', enabled: true },
]

function evaluation(overrides: Partial<AiEvaluation>): AiEvaluation {
  return {
    evaluation_id: 'ev-1',
    target: { provider_id: 'p1', provider_name: 'Claude Sonnet 5' },
    judge: { provider_id: 'p2', provider_name: 'OpenAI GPT-4.1' },
    lifecycle: 'completed',
    task: { title: 'Refactor task', summary: null },
    total_score: 86,
    final_score: 86,
    human_review: null,
    evaluation_set: null,
    review_summary: null,
    scores: [],
    created_at: '2026-09-30T00:00:00Z',
    ...overrides,
  }
}

const history: AiEvaluation[] = [
  evaluation({}),
  evaluation({
    evaluation_id: 'ev-failed', lifecycle: 'failed', total_score: null, final_score: null,
    failure_stage: 'running', reason_code: 'timeout',
  }),
  evaluation({
    evaluation_id: 'ev-other', target: { provider_id: 'p2', provider_name: 'OpenAI GPT-4.1' },
    task: { title: null, summary: null }, evaluation_set: { set_id: 's1', name: 'Docs set' },
    total_score: 52, final_score: 52,
  }),
]

const mounted: { unmount: () => void }[] = []

async function mountPanel(list: ProviderItem[] = providers) {
  const wrapper = mount(AiEvaluationPanel, { props: { providers: list }, attachTo: document.body, global: { plugins: [i18n] } })
  mounted.push(wrapper)
  await flushPromises()
  return wrapper
}

describe('AiEvaluationPanel (0015 T0003, judge-scored since 0017)', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
    i18n.global.locale.value = 'ko'
    setCurrentUser({ username: 'admin', email: null, isAdmin: true })
    vi.spyOn(api, 'fetchEvaluationHistory').mockResolvedValue({ items: history, next_cursor: 'cursor-2' })
    vi.spyOn(api, 'listEvaluationSets').mockResolvedValue([])
    vi.spyOn(api, 'fetchEvaluationDetail').mockResolvedValue(evaluation({ evaluation_id: 'ev-new', lifecycle: 'running' }))
  })

  afterEach(() => {
    mounted.splice(0).forEach((wrapper) => wrapper.unmount())
    setCurrentUser(null)
    document.body.innerHTML = ''
  })

  it('lists one row per evaluation with the score or an honest state', async () => {
    const wrapper = await mountPanel()

    expect(api.fetchEvaluationHistory).toHaveBeenCalledWith(undefined, 20)
    const rows = wrapper.findAll('.evaluation-detail-row')
    expect(rows).toHaveLength(3)

    expect(rows[0].text()).toContain('Claude Sonnet 5')
    expect(rows[0].text()).toContain('Refactor task')
    expect(rows[0].find('.score').text()).toBe('8.6')

    // A failed run shows its state and reason, never a score.
    expect(rows[1].find('.score').exists()).toBe(false)
    expect(rows[1].find('.score-state').text()).toBe('실패')
    expect(rows[1].find('.failure-line').text()).toContain('시간 초과')

    // The evaluation set name stands for the task.
    expect(rows[2].text()).toContain('Docs set')
    expect(rows[2].find('.score').text()).toBe('5.2')
  })

  it('filters the history by search query', async () => {
    const wrapper = await mountPanel()

    await wrapper.find('.detail-search input').setValue('docs')
    const rows = wrapper.findAll('.evaluation-detail-row')
    expect(rows).toHaveLength(1)
    expect(rows[0].text()).toContain('OpenAI GPT-4.1')
  })

  it('loads the next page with the cursor', async () => {
    const wrapper = await mountPanel()

    const more = wrapper.find('.load-more button')
    expect(more.exists()).toBe(true)
    const nextPage = vi.spyOn(api, 'fetchEvaluationHistory').mockResolvedValue({
      items: [evaluation({ evaluation_id: 'ev-page-2' })],
      next_cursor: null,
    })
    await more.trigger('click')
    await flushPromises()

    expect(nextPage).toHaveBeenCalledWith(undefined, 20, 'cursor-2')
    expect(wrapper.findAll('.evaluation-detail-row')).toHaveLength(4)
    expect(wrapper.find('.load-more').exists()).toBe(false)
  })

  it('starts an evaluation with a target and a different judge', async () => {
    const startSpy = vi.spyOn(api, 'startEvaluation').mockResolvedValue(evaluation({ evaluation_id: 'ev-new', lifecycle: 'running' }))
    const wrapper = await mountPanel()

    await wrapper.find('.detail-actions .ui-btn--primary').trigger('click')
    await flushPromises()
    const startBtn = document.querySelector('.dialog-actions .ui-btn--primary') as HTMLButtonElement
    expect(startBtn).toBeTruthy()
    expect(startBtn.disabled).toBe(false)
    startBtn.click()
    await flushPromises()

    expect(startSpy).toHaveBeenCalledWith({
      targetProviderId: 'p1',
      judgeProviderId: 'p2',
      evaluationSetId: null,
      locale: 'ko',
    })
  })

  it('cannot start with fewer than two AI', async () => {
    const wrapper = await mountPanel([providers[0]])

    await wrapper.find('.detail-actions .ui-btn--primary').trigger('click')
    await flushPromises()
    const startBtn = document.querySelector('.dialog-actions .ui-btn--primary') as HTMLButtonElement
    expect(startBtn.disabled).toBe(true)
    expect(document.body.textContent).toContain('2개 이상 필요합니다')
  })

  it('shows results read-only to a non-administrator', async () => {
    setCurrentUser({ username: 'member', email: null, isAdmin: false })
    vi.spyOn(api, 'fetchEvaluationHistory').mockResolvedValue({
      items: [evaluation({ evaluation_id: 'ev-running', lifecycle: 'running', total_score: null, final_score: null })],
      next_cursor: null,
    })
    const wrapper = await mountPanel()

    expect(wrapper.find('.detail-actions').exists()).toBe(false)
    expect(wrapper.findAll('.evaluation-detail-row')).toHaveLength(1)
    expect(wrapper.find('.action-btn--danger').exists()).toBe(false)
  })
})

// flowgate.v02.0088 F09: evaluation dates follow the app's language, not the browser's default (jsdom's here), and an
// open list / detail re-renders on a language switch; the stored timestamp is not changed.
describe('evaluation dates follow the app language (0088 F09)', () => {
  const STAMP = '2026-09-30T00:00:00Z'
  const shown = (locale: string) => new Date(STAMP).toLocaleString(locale)

  beforeEach(() => {
    vi.restoreAllMocks()
    i18n.global.locale.value = 'ja'
    setCurrentUser({ username: 'admin', email: null, isAdmin: true })
    vi.spyOn(api, 'fetchEvaluationHistory').mockResolvedValue({ items: [evaluation({})], next_cursor: null })
    vi.spyOn(api, 'listEvaluationSets').mockResolvedValue([])
    vi.spyOn(api, 'fetchEvaluationDetail').mockResolvedValue(evaluation({ started_at: STAMP, finished_at: null }))
  })

  afterEach(() => {
    mounted.splice(0).forEach((wrapper) => wrapper.unmount())
    setCurrentUser(null)
    document.body.innerHTML = ''
    i18n.global.locale.value = 'ko'
  })

  it('the list uses the app language and changes with it', async () => {
    expect(shown('ja')).not.toBe(shown('en'))
    const wrapper = await mountPanel()
    expect(wrapper.find('.date-cell').text()).toBe(shown('ja'))
    i18n.global.locale.value = 'en'
    await flushPromises()
    expect(wrapper.find('.date-cell').text()).toBe(shown('en'))
    expect(api.fetchEvaluationHistory).toHaveBeenCalledTimes(1)
  })

  it('the open detail uses the app language and changes with it', async () => {
    const wrapper = mount(AiEvaluationDetailModal, { props: { open: true, evaluationId: 'ev-1' }, attachTo: document.body, global: { plugins: [i18n] } })
    mounted.push(wrapper)
    await flushPromises()
    const meta = () => document.querySelector('.meta')?.textContent ?? ''
    expect(meta()).toContain(shown('ja'))
    i18n.global.locale.value = 'ko'
    await flushPromises()
    expect(meta()).toContain(shown('ko'))
    expect(meta()).not.toContain(shown('ja'))
  })
})
