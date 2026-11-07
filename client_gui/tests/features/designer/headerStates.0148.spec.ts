import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import FlowNodeCard from '@/features/designer/center/canvas/FlowNodeCard.vue'
import { CARD_STATE_KEYS, cardStateView } from '@/features/designer/center/graph/cardStateView'
import { GATE_HEADER_KEYS, gateHeaderState } from '@/features/designer/center/canvas/gateHeaderState'
import { FLOW_NODE_STATES, HEADER_ICON_KEYS, HEADER_GLYPHS, NO_ICON_KEYS, flowNodeHeaderIcon } from '@/features/designer/center/canvas/flowNodeKinds'
import { toVueFlowNodes } from '@/features/designer/center/canvas/vueFlowMapping'
import { toRunGateState } from '@/features/document/api/run'
import { gateResult } from '@/features/designer/inspector/features/gateCard/gateCardModel'
import { prepareInspectorI18n, SELECTIONS } from './inspector/support/inspectorSupport'
const i18n = prepareInspectorI18n()
const facts = (extra: any = {}) => toRunGateState({ can_decide: true, ...extra })!
const node = (extra: any = {}) => ({ status: 'open', attemptId: 'a1', ...extra })
describe('0148 explicit state icons and B3 regression', () => {
  it.each([...new Set([...CARD_STATE_KEYS, ...GATE_HEADER_KEYS, ...FLOW_NODE_STATES])])('%s has an explicit glyph or an explicit no-icon entry', key => {
    expect(NO_ICON_KEYS.includes(key) || Object.hasOwn(HEADER_ICON_KEYS, key)).toBe(true)
  })
  it.each(['warning', 'danger'] as const)('never hides a %s state, even an unknown or initial key', tone => {
    for (const key of ['future_state', ...NO_ICON_KEYS]) expect(flowNodeHeaderIcon({ key, tone })).not.toBeNull()
  })
  it('keeps a stopped yellow clock for work and publishing and distinguishes rework from errors', () => {
    for (const key of ['working', 'writing_instruction', 'writing_result', 'publishing']) expect(flowNodeHeaderIcon({ key, tone: 'info' })).toEqual({ path: HEADER_GLYPHS.clock, tone: 'warning' })
    expect(new Set(['rejected', 'revised', 'system_error'].map(key => flowNodeHeaderIcon({ key, tone: 'danger' })!.path)).size).toBe(3)
    expect(flowNodeHeaderIcon({ key: 'ai_setup_required', tone: 'warning' })!.path).not.toBe(HEADER_GLYPHS.intervention)
  })
  it('isolates the actual accepted-recovery wire shape: old predicate is red, new Canvas and Inspector are publishing', () => {
    const gate = facts({ decision_in_progress: true, settings_locked: true, recovery: { decision_id: 'd1', status: 'accepted', error_code: null }, job: { job_id: 'j1', status: 'running', phase_note: null } })
    const oldRecoveryRequired = gate.recovery && gate.recovery.status !== 'succeeded' && gate.recovery.status !== 'resolved'
    expect(oldRecoveryRequired).toBe(true)
    const state = gateHeaderState(gate, node())
    expect(state.key).toBe('publishing')
    expect(flowNodeHeaderIcon(state)).toEqual({ path: HEADER_GLYPHS.clock, tone: 'warning' })
    expect(gateResult({ ...node(), gate }).key).toBe('publishing')
    expect(gateHeaderState(facts({ git_active: true, recovery: { status: 'accepted', error_code: 'previous_error' } }), node()).key).toBe('publishing')
  })
  it.each([
    [{ recovery: { status: 'failed' } }, 'recovery_required'],
    [{ recovery: { status: 'recovery_required' } }, 'recovery_required'],
    [{ recovery: { status: 'conflict' } }, 'conflict_review'],
    [{ job: { status: 'failed' } }, 'recovery_required'],
    [{ recovery_reset_required: true }, 'recovery_required'],
  ])('preserves actual errors and conflict (%j)', (extra, key) => expect(gateHeaderState(facts(extra), node()).key).toBe(key))
  it('unreached gates and historical receipts cannot appear ready or complete', () => {
    expect(gateHeaderState(null).key).toBe('pending')
    expect(gateHeaderState(facts(), node({ status: 'pending' })).key).toBe('pending')
    const gate = facts({ decision: { status: 'succeeded', result: 'confirmed', attempt_id: 'a0' } })
    expect(gateHeaderState(gate, node()).key).toBe('waiting_confirm')
    expect(gateHeaderState(gate, node({ status: 'done' })).key).toBe('pending')
    expect(gateHeaderState(gate, node({ status: 'done', attemptId: 'a0' })).key).toBe('confirmed')
  })
  it('review facts derive revised/rejected glyphs while actual work, AI waits and intervention retain priority', () => {
    expect(cardStateView(null, 'revised')?.key).toBe('revised')
    expect(cardStateView(null, 'rejected')?.key).toBe('rejected')
    expect(cardStateView({ status: 'open', nodeRunning: true }, 'revised')?.key).toBe('working')
    expect(cardStateView({ status: 'open', waitReason: { code: 'ai_unavailable', role: 'execution', detail: 'offline' } }, 'revised')?.key).toBe('ai_unavailable')
  })
  it.each(['ko', 'en', 'ja'] as const)('preserves AI labels and reasons in accessible names (%s)', locale => {
    i18n.global.locale.value = locale
    for (const code of ['ai_setup_required', 'ai_unavailable']) {
      const cardState = cardStateView({ status: 'open', waitReason: { code, role: 'execution', detail: 'recorded_reason' } })!
      const w = mount(FlowNodeCard, { props: { id: 'n1', data: { kind: 'task', title: 'Card', selected: false, state: null, cardState } }, global: { plugins: [i18n], stubs: { Handle: true } } })
      const icon = w.get('[role="img"]'); expect(icon.attributes('title')).toContain('recorded_reason'); expect(icon.attributes('aria-label')).toContain('recorded_reason')
      expect(icon.attributes('aria-label')).toContain(i18n.global.t(cardState.labelKey)); expect(icon.get('path').attributes('d')).not.toBe(HEADER_GLYPHS.clock)
      w.unmount()
    }
    for (const key of CARD_STATE_KEYS) expect(i18n.global.te(`gui.designer.center.card_states.${key}`, locale)).toBe(true)
  })
  it('maps only reachable gates to approval controls and never offers one on ordinary cards', () => {
    const selection = SELECTIONS.graphGate
    const graph = { ...selection.graph, nodes: [{ ...selection.node, nodeRun: { ...selection.node.nodeRun, ...node(), gate: facts() } }] } as any
    const data = toVueFlowNodes(graph, null, new Map(), { canApprove: true })[0].data
    expect(data.approvable).toBe(true); expect(data.selected).toBe(false)
    expect(toVueFlowNodes({ ...graph, ownership: 'conflict' }, null, new Map(), { canApprove: true })[0].data.approvable).toBe(false)
  })
})
