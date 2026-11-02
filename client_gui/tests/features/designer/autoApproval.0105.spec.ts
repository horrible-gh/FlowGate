import { describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import { cardStateView } from '@/features/designer/center/graph/cardStateView'
import { toFlowRun } from '@/features/project/flowRunApi'
import { createServerFlowGraphSource, type FlowCanvasLoaders } from '@/features/designer/center/graph/serverFlowGraphSource'
import InspectorHeader from '@/features/designer/inspector/InspectorHeader.vue'
import { GRAPH_GRAPH, GRAPH_TEST_NODE, prepareInspectorI18n, selectionOf, targetOf } from './inspector/support/inspectorSupport'
const i18n = prepareInspectorI18n()
describe('v0.2 intervention presentation replaces automatic approval failure', () => {
  it.each(['review_not_passed:review_count_exhausted', 'run_not_clean:timeout', 'issuer_lacks_approve', 'unknown'])('uses the recorded intervention reason: %s', reason => {
    expect(cardStateView({ status: 'review', intervention: { kind: 'needs_intervention', reason } })).toMatchObject({ key: 'needs_intervention', tone: 'warning', reason, approvable: false })
  })
  it.each(['issuer_missing', 'approval_refused', 'review_not_started', 'review_result_unknown', 'assignment_unreadable', 'stage_not_person'])('shows automatic path failures as system error: %s', reason => {
    expect(cardStateView({ status: 'open', stage: 'instruction', intervention: { kind: 'system_error', reason } })).toMatchObject({ key: 'system_error', tone: 'danger', reason, approvable: false })
  })
  it('a done node stays complete even if an older intervention is present', () => {
    expect(cardStateView({ status: 'done', intervention: { kind: 'system_error', reason: 'old' } })?.key).toBe('approved')
  })
  it.each(['ko', 'en', 'ja'])('the Inspector names intervention and its reason without an approval action (%s)', locale => {
    i18n.global.locale.value = locale as 'ko' | 'en' | 'ja'
    const node = { ...GRAPH_TEST_NODE, nodeRun: { status: 'review', openedAt: null, finishedAt: null, resultDocId: 'result', intervention: { kind: 'needs_intervention', reason: 'review_not_passed:hold' } } }
    const graph = { ...GRAPH_GRAPH, nodes: [node] }
    const w = mount(InspectorHeader, { props: { target: targetOf(selectionOf(graph, node.id)) }, global: { plugins: [i18n] } })
    expect(w.get('[data-test="designer-inspector-intervention"]').attributes('aria-label')).toBe(i18n.global.t('gui.designer.center.card_states.needs_intervention'))
    expect(w.get('[data-test="designer-inspector-pill"]').attributes('title')).toContain('review_not_passed:hold')
    expect(w.find('[data-test="card-approval-approve"]').exists()).toBe(false)
    w.unmount(); i18n.global.locale.value = 'ko'
  })
})

describe('the transport keeps the reason and the viewer only when sent (B-2 / B-6)', () => {
  const raw = (extra: Record<string, unknown> = {}) => ({
    run_id: 'run_1',
    flow_id: 'p.ui.0105',
    status: 'idle',
    node_runs: [{ node_key: 'gn_1', status: 'review', result_doc_id: 'd', awaiting_approval: true, approval_wait_reason: 'approval_refused' }],
    ...extra,
  })

  it('maps viewer.can_approve true / false and leaves it out when absent', () => {
    expect(toFlowRun(raw({ viewer: { can_approve: true } }))!.viewerCanApprove).toBe(true)
    expect(toFlowRun(raw({ viewer: { can_approve: false } }))!.viewerCanApprove).toBe(false)
    expect('viewerCanApprove' in toFlowRun(raw())!).toBe(false)
    expect('viewerCanApprove' in toFlowRun(raw({ viewer: { can_approve: 'yes' } }))!).toBe(false)
  })

  it('maps each node run’s approval_wait_reason', () => {
    expect(toFlowRun(raw())!.nodeRuns![0].approvalWaitReason).toBe('approval_refused')
    const none = toFlowRun({ ...raw(), node_runs: [{ node_key: 'gn_1', status: 'review' }] })!
    expect('approvalWaitReason' in none.nodeRuns![0]).toBe(false)
  })

  it('the server Graph source hands the viewer on with the run (absent stays absent)', async () => {
    const loaders = (run: ReturnType<typeof toFlowRun>) =>
      ({
        loadFlowCanvas: async () => {
          throw new Error('not read here')
        },
        saveFlowCanvasLayout: async () => ({ ignored: [] }),
        loadFlowRun: async () => run,
        startFlowRun: async () => run!,
        stopFlowRun: async () => run!,
        resumeFlowRun: async () => run!,
      }) as unknown as FlowCanvasLoaders
    const allowed = await createServerFlowGraphSource(loaders(toFlowRun(raw({ viewer: { can_approve: true } })))).loadRun!('p', 'p.ui.0105')
    expect(allowed!.viewerCanApprove).toBe(true)
    const older = await createServerFlowGraphSource(loaders(toFlowRun(raw()))).loadRun!('p', 'p.ui.0105')
    expect(older && 'viewerCanApprove' in older).toBe(false)
  })
})
