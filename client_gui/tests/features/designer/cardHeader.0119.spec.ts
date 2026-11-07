import { mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, describe, expect, it } from 'vitest'
import FlowNodeCard from '@/features/designer/center/canvas/FlowNodeCard.vue'
import { gateHeaderState as rawGateHeaderState } from '@/features/designer/center/canvas/gateHeaderState'
import { cardStateView, CARD_STATE_KEYS } from '@/features/designer/center/graph/cardStateView'
import { toFlowCanvas } from '@/features/project/flowCanvasApi'
import { toFlowRun } from '@/features/project/flowRunApi'
import { toFlowGraph } from '@/features/designer/center/graph/serverFlowGraphSource'
import { toVueFlowNodes } from '@/features/designer/center/canvas/vueFlowMapping'
import { prepareInspectorI18n } from './inspector/support/inspectorSupport'
const i18n = prepareInspectorI18n()
const wrappers: VueWrapper[] = []
afterEach(() => { wrappers.splice(0).forEach(w => w.unmount()); i18n.global.locale.value = 'ko' })
const render = (data: any) => { const w = mount(FlowNodeCard, { props: { id: 'n1', data: { kind: 'task', title: 'Card', selected: false, state: null, ...data } }, global: { plugins: [i18n], stubs: { Handle: true } } }); wrappers.push(w); return w }
const gateHeaderState = (gate: any) => rawGateHeaderState(gate, { status: gate?.decision?.status === 'succeeded' ? 'done' : 'open', attemptId: 'a1' })
const gate = (extra = {}) => ({ decision: null, job: null, recovery: null, can_decide: true, decision_in_progress: false, recovery_reset_required: false, settings_locked: false, archived: false, ...extra })
describe('0119 Canvas header accessibility', () => {
  it.each(['in_progress', 'review', 'done', 'blocked'])('generic fallback %s is an icon with the existing text as its name and tooltip', state => {
    const w = render({ state }); const icon = w.get('[data-test="designer-canvas-node-state"]')
    expect(icon.attributes('role')).toBe('img')
    expect(icon.attributes('aria-label')).toBe(i18n.global.t(`gui.designer.center.node_states.${state}`))
    expect(icon.attributes('title')).toBe(icon.attributes('aria-label'))
    expect(icon.text()).toBe(''); expect(icon.find('svg path').exists()).toBe(true)
  })
  it.each(['ko', 'en', 'ja'])('intervention and system errors have a reason tooltip, no action and no header text (%s)', locale => {
    i18n.global.locale.value = locale as 'ko' | 'en' | 'ja'
    for (const kind of ['needs_intervention', 'system_error']) {
      const w = render({ cardState: cardStateView({ status: 'review', intervention: { kind, reason: 'recorded_reason' } }), approvable: true })
      const icon = w.get('[data-test="designer-canvas-node-card-state"]')
      expect(icon.attributes('role')).toBe('img')
      expect(icon.attributes('aria-label')).toContain(i18n.global.t(`gui.designer.center.card_states.${kind}`))
      expect(icon.attributes('aria-label')).toContain('recorded_reason')
      expect(icon.attributes('title')).toContain('recorded_reason')
      expect(icon.text()).toBe(''); expect(w.find('button').exists()).toBe(false)
    }
  })
  it('snapshots gate icon names and keeps four different glyphs', () => {
    const inputs = [gate(), gate({ decision_in_progress: true }), gate({ job: { phase_note: 'gate_conflict' }, decision_in_progress: true }), gate({ recovery_reset_required: true, job: { last_error_code: 'probe_failed' } })]
    const glyphs = new Set<string>()
    const rows = inputs.map(value => {
      const w = render({ state: 'done', gateState: gateHeaderState(value as any) })
      const icon = w.get('[data-test="designer-canvas-node-gate-state"]')
      glyphs.add(icon.get('path').attributes('d'))
      return [icon.attributes('data-state'), icon.attributes('role'), icon.attributes('aria-label'), icon.attributes('title'), icon.text()]
    })
    expect(glyphs.size).toBe(4)
    expect(rows).toMatchInlineSnapshot(`
      [
        [
          "waiting_confirm",
          "img",
          "확인 대기",
          "확인 대기",
          "",
        ],
        [
          "publishing",
          "img",
          "반영 중",
          "반영 중",
          "",
        ],
        [
          "conflict_review",
          "img",
          "충돌 검수 대기",
          "충돌 검수 대기",
          "",
        ],
        [
          "recovery_required",
          "img",
          "복구 필요 (probe_failed)",
          "복구 필요 (probe_failed)",
          "",
        ],
      ]
    `)
  })
  it('retains the lower integration badge, hides pending and keeps the hold mark', () => {
    const w = render({ cardState: cardStateView({ status: 'pending' }), state: 'draft', integration: 'failed' })
    expect(w.find('[data-test="designer-canvas-node-card-state"]').exists()).toBe(false)
    expect(w.find('[data-test="designer-canvas-node-state"]').exists()).toBe(false)
    expect(w.get('[data-test="designer-canvas-node-integration"]').text()).toBe(i18n.global.t('gui.designer.center.integration.failed'))
    const held = render({ hold: true, gateState: gateHeaderState(gate()) })
    expect(held.find('[data-test="designer-canvas-node-gate-state"]').exists()).toBe(false)
    expect(held.find('[data-test="designer-canvas-node-hold-icon"]').exists()).toBe(true)
  })
  it('all general state labels and every gate state label exist in three locales', () => {
    for (const locale of ['ko', 'en', 'ja']) {
      for (const key of [...CARD_STATE_KEYS, 'waiting_confirm', 'publishing', 'conflict_review', 'recovery_required', 'confirmed']) {
        expect(i18n.global.te(`gui.designer.center.card_states.${key}`, locale)).toBe(true)
      }
    }
  })
})
describe('0119 gate evidence and transport', () => {
  it.each([
    [gate(), 'waiting_confirm'],
    [gate({ job: { status: 'running' } }), 'publishing'],
    [gate({ job: { status: 'running', phase_note: 'gate_conflict' } }), 'conflict_review'],
    [gate({ job: { status: 'running', phase_note: 'gate_failed' } }), 'recovery_required'],
    [gate({ recovery_reset_required: true }), 'recovery_required'],
    [gate({ decision: { status: 'succeeded', result: 'confirmed', attempt_id: 'a1' } }), 'confirmed'],
  ])('reads only gate evidence (%j)', (facts, key) => { expect(gateHeaderState(facts as any).key).toBe(key) })
  it('run and Canvas reads preserve intervention/gate through graph mapping; null clears stale evidence', () => {
    const facts = { kind: 'needs_intervention', reason: 'review_not_passed:hold' }
    const row = { node_key: 'n1', status: 'review', result_doc_id: 'result', intervention: facts, gate: gate({ decision_in_progress: true }) }
    const run = toFlowRun({ run_id: 'r1', flow_id: 'f1', status: 'idle', node_runs: [row] })!
    expect(run.nodeRuns![0].intervention).toEqual(facts)
    expect(run.nodeRuns![0].gate?.decision_in_progress).toBe(true)
    const canvas = toFlowCanvas({ flow_id: 'f1', ownership: { state: 'graph' }, nodes: [{ node_key: 'n1', kind: 'task', title: 'Card', graph_node: { node_type: 'document' }, node_run: row }], edges: [] })
    expect(toVueFlowNodes(toFlowGraph(canvas), null)[0].data.cardState?.key).toBe('needs_intervention')
    const cleared = toFlowRun({ run_id: 'r1', status: 'idle', node_runs: [{ ...row, intervention: null, gate: null }] })!
    expect(cleared.nodeRuns![0].intervention).toBeNull(); expect(cleared.nodeRuns![0].gate).toBeNull()
  })
})
