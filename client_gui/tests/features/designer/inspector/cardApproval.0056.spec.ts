import { describe, expect, it, vi } from 'vitest'
import { computed, ref } from 'vue'
import { mount } from '@vue/test-utils'
import { inspectorRegistry } from '@/features/designer/inspector/inspectorRegistry'
import { inspectorCardState } from '@/features/designer/inspector/inspectorTarget'
import GateActions from '@/features/designer/inspector/features/gateCard/GateActions.vue'
import { gateActions } from '@/features/designer/inspector/features/gateCard/gateCardModel'
import { SELECTIONS, prepareInspectorI18n, targetOf } from './support/inspectorSupport'
const i18n = prepareInspectorI18n()
describe('v0.2 removes human card approval and gate rejection', () => {
  it('has no approval registration or document API request', async () => {
    expect(inspectorRegistry.entries.some(item => item.key === 'cardApproval')).toBe(false)
    expect(await import('@/features/document')).not.toHaveProperty('runCardApprove')
  })
  it('the general card exposes intervention without approval', () => {
    const target = { ...targetOf(SELECTIONS.graph), nodeRun: { status: 'review', intervention: { kind: 'needs_intervention', reason: 'review_not_passed:hold' } } }
    expect(inspectorCardState(target as any)).toMatchObject({ key: 'needs_intervention', approvable: false, reason: 'review_not_passed:hold' })
  })
  it('renders only confirmation, even if a supplied old state says reject is visible', async () => {
    const target = targetOf(SELECTIONS.graphGate)
    const decide = vi.fn()
    const item = { visible: true, enabled: true, reason: null }
    const w = mount(GateActions, { props: { target, state: { actions: computed(() => ({ confirm: item, reject: item })), busy: ref(null), failureKey: ref(null), decide } as any }, global: { plugins: [i18n] } })
    expect(w.find('[data-test="gate-reject"]').exists()).toBe(false)
    await w.get('[data-test="gate-confirm"]').trigger('click')
    expect(decide).toHaveBeenCalledWith('confirm')
    w.unmount()
  })
  it('gate model never enables or shows rejection', () => {
    const target = targetOf(SELECTIONS.graphGate)
    const actions = gateActions(target, { status: 'idle', nodes: [{ nodeKey: target.nodeId, status: 'open', gate: { can_decide: true } as any }] })
    expect(actions.confirm.enabled).toBe(true)
    expect(actions.reject).toEqual({ visible: false, enabled: false, reason: null })
  })
})
