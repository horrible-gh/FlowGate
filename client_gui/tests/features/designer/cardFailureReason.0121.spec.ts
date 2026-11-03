/**
 * flowgate.v02.0121 T#4 — a card whose AI call failed (node_run.dispatch.error_code `ai_invoke_failed`) shows 실행 미완료
 * with the server's short cause (dispatch.error_detail) as its own sentence — e.g. "모델 수용량 초과로 호출이
 * 종료되었습니다." — never the provider's output; a failure with no cause told keeps 실행 미완료 as before. Every cause has a
 * ko / en / ja string.
 *
 * rej_01M6FFMXBCPX855V (P2): the Canvas card and the Inspector state pill word the cause by one rule
 * (`cardStateReasonText`): the Inspector pill's tooltip shows the same sentence, never the raw code; a cause this screen
 * does not know and every other card state's reason (an intervention's, an approval wait's) are shown as sent.
 */
import { mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, describe, expect, it } from 'vitest'
import en from '@/app/i18n/en'
import ja from '@/app/i18n/ja'
import ko from '@/app/i18n/ko'
import FlowNodeCard from '@/features/designer/center/canvas/FlowNodeCard.vue'
import type { FlowGateNodeData } from '@/features/designer/center/canvas/vueFlowMapping'
import type { FlowGraphNodeRun } from '@/features/designer/center/graph/flowGraph'
import {
  CARD_FAILURE_REASONS, cardStateReasonText, cardStateView, type CardRunFacts,
} from '@/features/designer/center/graph/cardStateView'
import InspectorHeader from '@/features/designer/inspector/InspectorHeader.vue'
import { GRAPH_GRAPH, GRAPH_TEST_NODE, prepareInspectorI18n, selectionOf, targetOf } from './inspector/support/inspectorSupport'

const i18n = prepareInspectorI18n()
type Locale = 'ko' | 'en' | 'ja'
const MESSAGES = { ko, en, ja } as const
const LOCALES: readonly Locale[] = ['ko', 'en', 'ja']

const REASONS = ['provider_capacity', 'provider_failed', 'timeout', 'providers_exhausted'] as const
const failed = (fields: Partial<CardRunFacts> = {}): CardRunFacts => ({
  status: 'open', stage: 'result', dispatchErrorCode: 'ai_invoke_failed', runningExecutionCount: 0, requestedExecutionCount: 0,
  nodeRunning: false, ...fields,
})
const sentence = (locale: Locale, reason: (typeof REASONS)[number]): string =>
  MESSAGES[locale].designer.center.card_failure_reasons[reason]
const incompleteLabel = (locale: Locale): string => MESSAGES[locale].designer.center.card_states.incomplete

describe('cardStateView — a failed AI call names its short cause (0121 T#4)', () => {
  it.each([
    ['the model at capacity', failed({ dispatchErrorDetail: 'provider_capacity' }), 'provider_capacity'],
    ['another provider failure', failed({ dispatchErrorDetail: 'provider_failed' }), 'provider_failed'],
    ['no cause told', failed(), null],
    ['a blank cause', failed({ dispatchErrorDetail: '  ' }), null],
  ] as const)('%s', (_label, facts, reason) => {
    expect(cardStateView(facts)).toMatchObject({ key: 'incomplete', phase: 'other', tone: 'warning', reason })
  })

  it('a writer with no result and a running retry keep their own states', () => {
    expect(cardStateView(failed({ dispatchErrorCode: 'no_result' }))?.key).toBe('no_result')
    expect(cardStateView(failed({ dispatchErrorCode: null, dispatchErrorDetail: null, nodeRunning: true }))?.key).toBe('writing_result')
  })

  it('every cause has its sentence in ko / en / ja', () => {
    expect([...CARD_FAILURE_REASONS]).toEqual([...REASONS])
    expect(ko.designer.center.card_failure_reasons.provider_capacity).toBe('모델 수용량 초과로 호출이 종료되었습니다.')
    for (const locale of LOCALES) {
      for (const reason of REASONS) expect(typeof sentence(locale, reason)).toBe('string')
    }
  })
})

describe('cardStateReasonText — one wording rule for every screen (rej_01M6FFMXBCPX855V)', () => {
  const t = (key: string) => `<${key}>`

  it('an incomplete card’s known cause is its sentence', () => {
    for (const reason of REASONS) {
      expect(cardStateReasonText(cardStateView(failed({ dispatchErrorDetail: reason })), t))
        .toBe(`<gui.designer.center.card_failure_reasons.${reason}>`)
    }
  })

  it('an unknown cause, no cause and every other state’s reason are kept as they are', () => {
    expect(cardStateReasonText(cardStateView(failed({ dispatchErrorDetail: 'something_new' })), t)).toBe('something_new')
    expect(cardStateReasonText(cardStateView(failed()), t)).toBeNull()
    expect(cardStateReasonText(null, t)).toBeNull()
    // the same word in another state's reason is that state's own code, never a failed call's sentence
    expect(cardStateReasonText({ key: 'needs_intervention', reason: 'timeout' }, t)).toBe('timeout')
    expect(cardStateReasonText(cardStateView({ status: 'review', awaitingApproval: true, approvalWaitReason: 'approval_refused' }), t))
      .toBe('approval_refused')
  })
})

describe('the Canvas card shows the cause as a sentence (FlowNodeCard)', () => {
  let wrapper: VueWrapper<any> | null = null
  afterEach(() => {
    wrapper?.unmount()
    wrapper = null
    i18n.global.locale.value = 'ko'
  })
  const titleOf = (facts: CardRunFacts) => {
    wrapper?.unmount()
    const data: FlowGateNodeData = { kind: 'plan', title: '카드', state: 'in_progress', selected: false, cardState: cardStateView(facts)! }
    wrapper = mount(FlowNodeCard, { props: { id: 'gn_failed', data }, global: { plugins: [i18n], stubs: { Handle: true } } })
    return wrapper.get('[data-test="designer-canvas-node-card-state"]').attributes('title')
  }

  it('ko: 실행 미완료 (모델 수용량 초과로 호출이 종료되었습니다.)', () => {
    i18n.global.locale.value = 'ko'
    expect(titleOf(failed({ dispatchErrorDetail: 'provider_capacity' }))).toBe('실행 미완료 (모델 수용량 초과로 호출이 종료되었습니다.)')
  })

  it.each(LOCALES)('%s: every known cause is its sentence', locale => {
    i18n.global.locale.value = locale
    for (const reason of REASONS) {
      expect(titleOf(failed({ dispatchErrorDetail: reason }))).toBe(`${incompleteLabel(locale)} (${sentence(locale, reason)})`)
    }
  })

  it('no cause: 실행 미완료 as before', () => {
    i18n.global.locale.value = 'ko'
    expect(titleOf(failed())).toBe('실행 미완료')
  })

  it('an unknown cause is shown as sent', () => {
    i18n.global.locale.value = 'ko'
    expect(titleOf(failed({ dispatchErrorDetail: 'something_new' }))).toBe('실행 미완료 (something_new)')
  })
})

describe('the Inspector state pill words the cause as the Canvas card does (InspectorHeader)', () => {
  let wrapper: VueWrapper<any> | null = null
  afterEach(() => {
    wrapper?.unmount()
    wrapper = null
    i18n.global.locale.value = 'ko'
  })
  const pillOf = (nodeRun: FlowGraphNodeRun) => {
    wrapper?.unmount()
    const node = { ...GRAPH_TEST_NODE, nodeRun }
    const graph = { ...GRAPH_GRAPH, nodes: [node] }
    wrapper = mount(InspectorHeader, { props: { target: targetOf(selectionOf(graph, node.id)) }, global: { plugins: [i18n] } })
    return wrapper.get('[data-test="designer-inspector-pill"]')
  }
  const failedRun = (fields: Partial<FlowGraphNodeRun> = {}): FlowGraphNodeRun => ({
    status: 'open', openedAt: '2026-11-03T07:00:00Z', finishedAt: null, resultDocId: null, stage: 'result',
    dispatchErrorCode: 'ai_invoke_failed', nodeRunning: false, ...fields,
  })

  it.each(LOCALES)('%s: a capacity failure shows 실행 미완료 and its sentence, never the raw code', locale => {
    i18n.global.locale.value = locale
    const pill = pillOf(failedRun({ dispatchErrorDetail: 'provider_capacity' }))
    expect(pill.attributes('data-card-state')).toBe('incomplete')
    expect(pill.get('[data-test="designer-inspector-pill-label"]').text()).toBe(incompleteLabel(locale))
    const title = pill.attributes('title') ?? ''
    expect(title.endsWith(`\n${incompleteLabel(locale)} (${sentence(locale, 'provider_capacity')})`)).toBe(true)
    expect(title).not.toContain('provider_capacity')
  })

  it.each(LOCALES)('%s: every known cause is its sentence in the tooltip', locale => {
    i18n.global.locale.value = locale
    for (const reason of REASONS) {
      const title = pillOf(failedRun({ dispatchErrorDetail: reason })).attributes('title') ?? ''
      expect(title.endsWith(`${incompleteLabel(locale)} (${sentence(locale, reason)})`)).toBe(true)
    }
  })

  it('no cause: the tooltip has no reason line; an unknown cause is shown as sent', () => {
    i18n.global.locale.value = 'ko'
    expect(pillOf(failedRun()).attributes('title')).not.toContain('실행 미완료 (')
    expect(pillOf(failedRun({ dispatchErrorDetail: 'something_new' })).attributes('title')).toContain('실행 미완료 (something_new)')
  })

  it('another state’s reason stays as sent (an intervention, an approval wait)', () => {
    i18n.global.locale.value = 'ko'
    const intervention = pillOf({ status: 'review', openedAt: null, finishedAt: null, resultDocId: 'result',
      intervention: { kind: 'needs_intervention', reason: 'run_not_clean:timeout' } })
    expect(intervention.attributes('title')).toContain('(run_not_clean:timeout)')
    const waiting = pillOf({ status: 'review', openedAt: null, finishedAt: null, resultDocId: 'result', awaitingApproval: true,
      approvalWaitReason: 'approval_refused' })
    expect(waiting.attributes('title')).toContain('(approval_refused)')
  })
})
