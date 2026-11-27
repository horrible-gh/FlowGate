/**
 * flowgate.v02test.F98-4: a card in intervention shows why its review loop closed — the intervention's added
 * `stop_detail` (the loop's failure cause). A known code is worded (ko / en / ja) as `<문구>(<code>)`, any other value as
 * sent; no cause, the same view and text as before. The Canvas card title and the Inspector pill share the rule.
 */
import { mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, describe, expect, it } from 'vitest'
import en from '@/app/i18n/en'
import ja from '@/app/i18n/ja'
import ko from '@/app/i18n/ko'
import FlowNodeCard from '@/features/designer/center/canvas/FlowNodeCard.vue'
import {
  CARD_INTERVENTION_CAUSES, cardInterventionCauseText, cardStateReasonText, cardStateView,
} from '@/features/designer/center/graph/cardStateView'
import { toFlowRun } from '@/features/project/flowRunApi'
import { prepareInspectorI18n } from './inspector/support/inspectorSupport'

const i18n = prepareInspectorI18n()
const wrappers: VueWrapper[] = []
afterEach(() => { wrappers.splice(0).forEach(w => w.unmount()); i18n.global.locale.value = 'ko' })
const t = (key: string) => `<${key}>`
const REASON = 'review_not_passed:retry_exhausted'
const facts = (stop_detail?: string | null) => ({
  status: 'review',
  intervention: { kind: 'needs_intervention', reason: REASON, ...(stop_detail === undefined ? {} : { stop_detail }) },
})

describe('cardStateView carries the cause', () => {
  it('adds `cause` only when the server sent one', () => {
    expect(cardStateView(facts('model_not_found'))).toMatchObject({ key: 'needs_intervention', reason: REASON, cause: 'model_not_found' })
    for (const none of [undefined, null, '', '   ']) expect(cardStateView(facts(none))).not.toHaveProperty('cause')
    expect(cardStateView(facts())).toEqual(cardStateView({ status: 'review', intervention: { kind: 'needs_intervention', reason: REASON } }))
  })
  it('a system error carries its cause too', () => {
    expect(cardStateView({ status: 'review', intervention: { kind: 'system_error', reason: 'x', stop_detail: 'timeout' } }))
      .toMatchObject({ key: 'system_error', cause: 'timeout' })
  })
})

describe('the cause text', () => {
  it('words a known code as `<sentence>(<code>)` and keeps others as sent', () => {
    expect(cardInterventionCauseText('model_not_found', t)).toBe('<gui.designer.center.card_intervention_causes.model_not_found>(model_not_found)')
    expect(cardInterventionCauseText('model_not_found: gpt-x', t))
      .toBe('<gui.designer.center.card_intervention_causes.model_not_found>(model_not_found: gpt-x)')
    expect(cardInterventionCauseText('remote final failure: model_not_found', t))
      .toBe('<gui.designer.center.card_intervention_causes.model_not_found>(remote final failure: model_not_found)')
    expect(cardInterventionCauseText('remote final failure: model_not_found: gpt-x', t))
      .toBe('<gui.designer.center.card_intervention_causes.model_not_found>(remote final failure: model_not_found: gpt-x)')
    expect(cardInterventionCauseText('remote final failure: unknown', t)).toBe('remote final failure: unknown')
    expect(cardInterventionCauseText('reviewer returned hold', t)).toBe('reviewer returned hold')
    expect(cardInterventionCauseText('toString', t)).toBe('toString')
    for (const none of [undefined, null, '', ' ']) expect(cardInterventionCauseText(none, t)).toBeNull()
  })
  it('follows the reason after ` · `, and no cause keeps the text as before', () => {
    expect(cardStateReasonText(cardStateView(facts('model_not_found')), t))
      .toBe(`${REASON} · <gui.designer.center.card_intervention_causes.model_not_found>(model_not_found)`)
    expect(cardStateReasonText(cardStateView(facts()), t)).toBe(REASON)
    expect(cardStateReasonText({ key: 'needs_intervention', reason: null, cause: 'odd' }, t)).toBe('odd')
  })
  it('every known code has a ko / en / ja sentence; ko says the model is not supported', () => {
    for (const messages of [ko, en, ja]) {
      for (const code of CARD_INTERVENTION_CAUSES) expect(messages.designer.center.card_intervention_causes[code].length).toBeGreaterThan(1)
    }
    expect(ko.designer.center.card_intervention_causes.model_not_found).toBe('모델을 지원하지 않음')
  })
})

describe('the screen', () => {
  it('the run read keeps stop_detail on the node run', () => {
    const run = toFlowRun({ run_id: 'r1', status: 'idle', node_runs: [{ node_key: 'n1', status: 'review', result_doc_id: 'd', intervention: facts('model_not_found').intervention }] })!
    expect(run.nodeRuns![0].intervention).toEqual({ kind: 'needs_intervention', reason: REASON, stop_detail: 'model_not_found' })
  })
  it('the Canvas card title names the cause in Korean (ko)', () => {
    const w = mount(FlowNodeCard, { props: { id: 'n1', data: { kind: 'task', title: 'Card', selected: false, state: null, cardState: cardStateView(facts('model_not_found')) } }, global: { plugins: [i18n], stubs: { Handle: true } } })
    wrappers.push(w)
    const title = w.get('[data-test="designer-canvas-node-card-state"]').attributes('title') ?? ''
    expect(title).toContain('모델을 지원하지 않음(model_not_found)')
    expect(title).toContain(REASON)
  })
  it('no cause: the Canvas card title is unchanged', () => {
    const w = mount(FlowNodeCard, { props: { id: 'n1', data: { kind: 'task', title: 'Card', selected: false, state: null, cardState: cardStateView(facts()) } }, global: { plugins: [i18n], stubs: { Handle: true } } })
    wrappers.push(w)
    const title = w.get('[data-test="designer-canvas-node-card-state"]').attributes('title') ?? ''
    expect(title).toBe(`${i18n.global.t('gui.designer.center.card_states.needs_intervention')} (${REASON})`)
  })
})
