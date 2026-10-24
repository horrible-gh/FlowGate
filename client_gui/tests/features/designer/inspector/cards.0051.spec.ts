/**
 * flowgate.v02.0051 stage 2 — the per-card parts (0051-R §2.3): the rules that turn the target into each
 * card's v7 lines, actions and settings (pure), and what the parts draw for them — the lines with their
 * names, pills and buttons; the disabled header actions and menu items with their reason; the settings
 * blocks drawn inactive with the reason. None of these parts reaches the server; a Node without a
 * document still shows the card's structure.
 * flowgate.v02.0053 T#5: the lines are `name · state dot · [document icon] · ✎` with no type code, no
 * "아직 없음" and no ＋; the 설계 card is 지시서 · 레포트 without the "설명 대기" note; a Custom card is 지시서 ·
 * 레포트. The cards built from card lines are checked in cardLines.0053.spec.ts.
 * flowgate.v02.0074 T#3 (R §3): the 설계 card's two lines are named 설계 지시 · 설계서; 조사 · 작업 keep 지시서 · 레포트.
 * flowgate.v02.0074 T#5: the 작업계획 card's header buttons deploy the plan (workPlanDeployActions over the status read; the
 * status read is mocked here, the deploy flow itself is checked in workPlan/workPlanDeploy.0074.spec.ts).
 */
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import CheckSettings from '@/features/designer/inspector/features/checkSettings/CheckSettings.vue'
import { checkSettingsView } from '@/features/designer/inspector/features/checkSettings/checkSettingsModel'
import DesignSlots from '@/features/designer/inspector/features/designCard/DesignSlots.vue'
import { designCardSlots } from '@/features/designer/inspector/features/designCard/designCardModel'
import GateActions from '@/features/designer/inspector/features/gateCard/GateActions.vue'
import GateSlots from '@/features/designer/inspector/features/gateCard/GateSlots.vue'
import { gateActions, gateCardShape, gateCardSlots, gateFailureKey } from '@/features/designer/inspector/features/gateCard/gateCardModel'
import RequirementSlots from '@/features/designer/inspector/features/requirementCard/RequirementSlots.vue'
import { requirementCardSlots } from '@/features/designer/inspector/features/requirementCard/requirementCardModel'
import RequirementSettings from '@/features/designer/inspector/features/requirementSettings/RequirementSettings.vue'
import { requirementSettingsView, type RequirementSettingsView } from '@/features/designer/inspector/features/requirementSettings/requirementSettingsModel'
import ResearchSlots from '@/features/designer/inspector/features/researchCard/ResearchSlots.vue'
import { researchCardSlots, researchDocumentState } from '@/features/designer/inspector/features/researchCard/researchCardModel'
import TaskSlots from '@/features/designer/inspector/features/taskCard/TaskSlots.vue'
import { taskCardSlots } from '@/features/designer/inspector/features/taskCard/taskCardModel'
import TestRunSettings from '@/features/designer/inspector/features/testRunSettings/TestRunSettings.vue'
import { testRunSettingsView } from '@/features/designer/inspector/features/testRunSettings/testRunSettingsModel'
import { testSpecSlots, type TestSpecViewFacts } from '@/features/designer/inspector/features/testSpec/testSpecModel'
import WorkPlanActions from '@/features/designer/inspector/features/workPlan/WorkPlanActions.vue'
import { workPlanDeployActions, workPlanSlotState } from '@/features/designer/inspector/features/workPlan/workPlanModel'
import type { InspectorTarget } from '@/features/designer/inspector/inspectorTarget'
import InspectorRunMenu from '@/features/designer/inspector/parts/InspectorRunMenu.vue'
import { prepareInspectorI18n, SELECTIONS, targetOf } from './support/inspectorSupport'

const deployStatusRead = vi.hoisted(() => vi.fn())
vi.mock('@/features/document', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/features/document')>()
  return { ...actual, workPlanDeployStatus: (...args: unknown[]) => deployStatusRead(...args) }
})

const i18n = prepareInspectorI18n()

beforeEach(() => {
  i18n.global.locale.value = 'ko'
})

const global = { plugins: [i18n] }

const V01_TR = targetOf(SELECTIONS.v01)
const GRAPH_TS = targetOf(SELECTIONS.graph)
const GATE = targetOf(SELECTIONS.graphGate)
const REQ = targetOf(SELECTIONS.unassigned)
const DESIGN_CONFLICT = targetOf(SELECTIONS.conflict)
const DESIGN_NO_DOCUMENT = targetOf(SELECTIONS.v01NoDocument)

/** A target of the given document type and states, with or without a document. */
function facts(base: InspectorTarget, docType: string | null, extra: Partial<InspectorTarget> = {}): InspectorTarget {
  return { ...base, docType, ...extra }
}

const NO_DOCUMENT = { docId: null, docReviewStatus: null, docStatus: null, nodeRun: null } as const

const slotRows = (w: ReturnType<typeof mount>) => w.findAll('[data-test="inspector-slot"]')

// --- Rules ---------------------------------------------------------------------------------------------

describe('조사 card lines (researchCardModel)', () => {
  it('a bound N: the instruction line with its state, the report "실행 후"', () => {
    const [instruction, report] = researchCardSlots(facts(V01_TR, 'N', { docReviewStatus: 'approved', docStatus: 'closed' }))
    expect(instruction).toMatchObject({ key: 'instruction', id: V01_TR.docId, empty: false, open: 'enabled', focus: false })
    expect(instruction.state).toEqual({ key: 'review_status.approved', raw: 'approved', tone: 'success' })
    expect(report).toMatchObject({ key: 'report', empty: true, emptyKey: 'empty.after_run', open: 'none' })
  })

  it('a bound NR under review: the report line is the one to look at; the instruction is not linked', () => {
    const [instruction, report] = researchCardSlots(facts(V01_TR, 'NR'))
    expect(instruction).toMatchObject({ empty: true, emptyKey: 'empty.unconnected', open: 'none' })
    expect(report).toMatchObject({ id: V01_TR.docId, focus: true, open: 'enabled' })
    expect(report.state).toMatchObject({ raw: 'pending_review', tone: 'warning' })
  })

  it('a Graph N whose run produced a result: the report line names the result document, state not read', () => {
    const [, report] = researchCardSlots(facts(GRAPH_TS, 'N', { nodeRun: { status: 'completed', openedAt: null, finishedAt: null, resultDocId: 'flowgate.v02.0049.0008-NR' } }))
    expect(report).toMatchObject({ id: 'flowgate.v02.0049.0008-NR', empty: false, state: null, open: 'enabled' })
  })

  it('a Node without a document or card lines keeps both lines without a document, never "아직 없음" or ＋', () => {
    const [instruction, report] = researchCardSlots(facts(V01_TR, 'N', NO_DOCUMENT))
    expect(instruction).toMatchObject({ empty: true, emptyKey: null, open: 'none' })
    expect(report).toMatchObject({ empty: true, emptyKey: 'empty.after_run' })
    expect(instruction).not.toHaveProperty('write')
  })

  it('the document state: review status first, else the document status, unknown values as sent', () => {
    expect(researchDocumentState(null, null)).toBeNull()
    // flowgate.v02.0056 F1: a document being written is a neutral state (the line draws no badge for it).
    expect(researchDocumentState(null, 'draft')).toEqual({ key: 'slot_state.writing', raw: 'draft', tone: 'neutral' })
    expect(researchDocumentState(null, 'closed')).toEqual({ key: 'slot_state.closed', raw: 'closed', tone: 'neutral' })
    expect(researchDocumentState('rejected', 'open')).toEqual({ key: 'review_status.rejected', raw: 'rejected', tone: 'danger' })
    expect(researchDocumentState('wf_done', 'open')).toEqual({ key: 'review_status.wf_done', raw: 'wf_done', tone: 'neutral' })
  })
})

describe('작업 card lines (taskCardModel)', () => {
  it('T / T2 fill the instruction line, TR / TR2 the report line', () => {
    expect(taskCardSlots(facts(V01_TR, 'T2'))[0]).toMatchObject({ key: 'instruction', id: V01_TR.docId })
    expect(taskCardSlots(facts(V01_TR, 'TR2'))[1]).toMatchObject({ key: 'report', id: V01_TR.docId, focus: true })
    expect(taskCardSlots(V01_TR)[0]).toMatchObject({ empty: true, emptyKey: 'empty.unconnected' })
  })
})

describe('설계 card lines (designCardModel)', () => {
  it('instruction · report lines like 조사 · 작업: a bound D fills the report line; the DS instruction is not linked', () => {
    const [instruction, report] = designCardSlots(DESIGN_CONFLICT)
    expect(instruction).toMatchObject({ key: 'instruction', empty: true, emptyKey: 'empty.unconnected', open: 'none' })
    expect(report).toMatchObject({ key: 'report', id: DESIGN_CONFLICT.docId, focus: true, open: 'enabled' })
    expect(report.state).toMatchObject({ raw: 'rejected', tone: 'danger' })
    expect(designCardSlots(facts(DESIGN_CONFLICT, 'DS'))[0]).toMatchObject({ key: 'instruction', id: DESIGN_CONFLICT.docId })
    expect(designCardSlots(DESIGN_NO_DOCUMENT)).toEqual([
      { key: 'instruction', kind: 'document', id: null, empty: true, emptyKey: null, state: null, focus: false, open: 'none' },
      { key: 'report', kind: 'document', id: null, empty: true, emptyKey: 'empty.after_run', state: null, focus: false, open: 'none' },
    ])
  })
})

describe('요구사항 / 버그 card line (requirementCardModel)', () => {
  it('R is the 요구사항 line, B the 버그 line (0015-T T3); without a document the line has none (no ＋)', () => {
    expect(requirementCardSlots(REQ)[0]).toMatchObject({ name: 'requirement', id: REQ.docId, open: 'enabled' })
    expect(requirementCardSlots(REQ)[0].state).toEqual({ key: 'slot_state.writing', raw: 'draft', tone: 'neutral' })
    expect(requirementCardSlots(facts(REQ, 'B'))[0].name).toBe('bug')
    expect(requirementCardSlots(facts(REQ, 'R', NO_DOCUMENT))).toEqual([
      { key: 'document', kind: 'document', name: 'requirement', id: null, empty: true, emptyKey: null, state: null, focus: false, open: 'none' },
    ])
  })

  it('a root in its workflow shows the plain wording, never wf_in_progress / wf_done (0052)', () => {
    expect(requirementCardSlots(facts(REQ, 'R', { docReviewStatus: 'wf_in_progress', docStatus: 'open' }))[0].state).toEqual({ key: 'slot_state.in_progress', raw: 'wf_in_progress', tone: 'running' })
    expect(requirementCardSlots(facts(REQ, 'R', { docReviewStatus: 'wf_done', docStatus: 'open' }))[0].state).toEqual({ key: 'slot_state.closed', raw: 'wf_done', tone: 'success' })
    const w = mount(RequirementSlots, { props: { target: facts(REQ, 'R', { docReviewStatus: 'wf_in_progress', docStatus: 'open' }) }, global })
    expect(w.get('[data-test="inspector-slot-state"]').attributes('title')).toBe('진행 중')
    expect(w.html()).not.toContain('wf_in_progress')
  })
})

describe('Graph gate (gateCardModel)', () => {
  it('a gate: the 확인 결과 result line (no document) and two actions shown, waiting for a running run', () => {
    expect(gateCardShape(GATE)).toBe('gate')
    expect(gateCardSlots(GATE)).toEqual([{ key: 'result', kind: 'result', id: null, empty: true, emptyKey: 'empty.before', state: null, focus: false, open: 'none' }])
    expect(gateActions(GATE)).toEqual({
      confirm: { visible: true, enabled: false, reason: 'reason.not_running' },
      reject: { visible: true, enabled: false, reason: 'reason.not_running' },
    })
  })

  // flowgate.v02.0053 T#9: the gate decision of the Flow's run (P0008 S6-1).
  it('a gate decides while it is open in a running run; a stopped run, another turn, a decision in flight or a conflict wait', () => {
    const open = { status: 'running', nodes: [{ nodeKey: GATE.nodeId, status: 'open', gate: { can_decide: true } as any }] }
    expect(gateActions(GATE, open).confirm).toEqual({ visible: true, enabled: true, reason: null })
    expect(gateActions(GATE, open).reject.enabled).toBe(true)
    expect(gateActions(GATE, { ...open, status: 'stopped' }).confirm.reason).toBe('reason.not_running')
    expect(gateActions(GATE, { status: 'running', nodes: [{ nodeKey: GATE.nodeId, status: 'pending' }] }).confirm.reason).toBe('reason.not_open')
    expect(gateActions(GATE, { status: 'running', nodes: [] }).confirm.reason).toBe('reason.not_open')
    expect(gateActions(GATE, open, true).confirm.reason).toBe('reason.busy')
    expect(gateActions({ ...GATE, readOnly: 'conflict' }, open).confirm.reason).toBe('reason.read_only_conflict')
    // The revision view a run shows does not stop the decision (it is the run's own view).
    expect(gateActions({ ...GATE, readOnly: 'revision' }, open).confirm.enabled).toBe(true)
  })

  it('a refused gate decision names its reason by the server code, never the code itself', () => {
    const of = (code: string | null, status: number | null = 409, kind = 'conflict') => gateFailureKey({ kind, status, code })
    expect(of('run_not_running')).toBe('failure.not_running')
    expect(of('node_not_open')).toBe('failure.not_open')
    expect(of('run_policy_undecided')).toBe('failure.undecided')
    expect(of(null, 403, 'forbidden')).toBe('failure.forbidden')
    expect(of(null, null, 'network')).toBe('failure.network')
    expect(of('internal_error', 500, 'other')).toBe('failure.other')
  })

  it('an `other` document Node: its one document line; a Node with nothing: no line, no action', () => {
    const other = facts(V01_TR, 'XX', { nodeType: null })
    expect(gateCardShape(other)).toBe('document')
    expect(gateCardSlots(other)[0]).toMatchObject({ key: 'document', id: V01_TR.docId, open: 'enabled' })
    const none = facts(V01_TR, null, { ...NO_DOCUMENT, nodeType: null })
    expect(gateCardShape(none)).toBe('none')
    expect(gateCardSlots(none)).toEqual([])
    expect(gateActions(none).confirm.visible).toBe(false)
  })

  it('a Custom card (Graph `custom` node): 지시서 · 레포트 even before its documents are known', () => {
    const custom = facts(V01_TR, null, { ...NO_DOCUMENT, nodeType: 'custom' })
    expect(gateCardShape(custom)).toBe('custom')
    expect(gateCardSlots(custom).map((line) => [line.key, line.empty, line.emptyKey])).toEqual([['instruction', true, null], ['report', true, 'empty.after_run']])
    expect(gateActions(custom).confirm.visible).toBe(false)
  })
})

describe('작업계획 card (workPlanModel, stage 2)', () => {
  it('⇉ 전개 · ⇉ 재전개 ▾ are drawn and disabled with the reason; the plan state follows the document', () => {
    const status = { ownership: 'unassigned', deployable: true, reason: null, deployedCount: 0, targetCount: 2 }
    const target = facts(REQ, 'WP')
    // flowgate.v02.0074 T#5: not deployed, two targets -> [전개] only; [재전개 ▾] and its items say why not.
    const fresh = workPlanDeployActions({ target, status, statusFailed: false, busy: false })
    expect(fresh.deploy).toEqual({ visible: true, enabled: true, reason: null })
    expect(fresh.redeploy).toEqual({ visible: true, enabled: false, reason: 'reason.not_deployed' })
    expect(fresh.add.enabled).toBe(false)
    expect(fresh.cleanup.enabled).toBe(false)
    // deployed -> [재전개 ▾] with both items; [전개] says it is deployed.
    const deployed = workPlanDeployActions({ target, status: { ...status, deployedCount: 2 }, statusFailed: false, busy: false })
    expect(deployed.deploy).toEqual({ visible: true, enabled: false, reason: 'reason.deployed' })
    expect([deployed.redeploy.enabled, deployed.add.enabled, deployed.cleanup.enabled]).toEqual([true, true, true])
    // no document: every one off with the same reason (a Node without a plan keeps the buttons drawn).
    const none = workPlanDeployActions({ target: facts(REQ, 'WP', NO_DOCUMENT), status: null, statusFailed: false, busy: false })
    for (const item of Object.values(none)) expect(item).toEqual({ visible: true, enabled: false, reason: 'reason.no_document' })
    expect(workPlanSlotState('approved', 'open')).toMatchObject({ raw: 'approved', tone: 'success' })
    expect(workPlanSlotState(null, 'draft')).toMatchObject({ key: 'slot_state.writing', tone: 'neutral' })
  })
})

describe('테스트 card lines (testSpecModel, stage 2)', () => {
  const view: TestSpecViewFacts = {
    docId: GRAPH_TS.docId!,
    kind: 'TS',
    contractVersion: 2,
    tsDocId: GRAPH_TS.docId,
    tsReviewStatus: 'approved',
    tsrDocId: null,
    tsrReviewStatus: null,
    cases: [{ caseId: 'c1', executionMode: 'automated', required: true }],
    caseCapabilities: { c1: 'case_selectable' },
    specErrors: 0,
    latest: null,
    activeRun: null,
    lastExecution: null,
    history: [],
    effective: null,
    basisValid: null,
    basisState: null,
    gate: null,
  }

  it('a TS view: 지시서 with its state, 시나리오 (the same TS) with the tally, 레포트 "실행 후"', () => {
    const [instruction, scenario, report] = testSpecSlots(view, GRAPH_TS)
    expect(instruction).toMatchObject({ key: 'instruction', id: GRAPH_TS.docId, open: 'enabled' })
    expect(instruction.state).toMatchObject({ raw: 'approved', tone: 'success' })
    expect(scenario).toMatchObject({ key: 'scenario', id: GRAPH_TS.docId, empty: false, summary: 'tally', open: 'enabled' })
    expect(report).toMatchObject({ key: 'report', kind: 'document', empty: true, emptyKey: 'empty.after_run' })
  })

  it('no view yet: the selected TSR fills the report line; 지시서 and 시나리오 have no document', () => {
    const [instruction, scenario, report] = testSpecSlots(null, facts(V01_TR, 'TSR'))
    expect(instruction).toMatchObject({ empty: true, emptyKey: null, open: 'none' })
    expect(scenario).toMatchObject({ empty: true, emptyKey: 'empty.after_run' })
    expect(report).toMatchObject({ id: V01_TR.docId, empty: false, open: 'enabled' })
    expect(report.state).toMatchObject({ raw: 'pending_review', tone: 'warning' })
  })
})

// flowgate.v02.0053 T#7: a Graph card keeps these settings (connected, cardSettings.0053); a Node without a card
// (a v0.1 / unassigned document Node) still draws them, off, with the reason as a tooltip.
describe('settings views of a Node without a card', () => {
  const NONE = { card: false, node: null, pending: {}, readOnly: null, placing: false, disposed: false, failure: null } as const
  it('AI 위임 is OFF and not changeable; the test 실행 block knows no counts; check settings know nothing', () => {
    expect(requirementSettingsView({ ...NONE, teams: [] })).toMatchObject({ delegation: 'off', connected: false, locked: false, reason: 'delegation.unconnected' })
    expect(requirementSettingsView({ ...NONE, teams: [] }).limits.map((limit) => limit.key)).toEqual(['max_nodes', 'total_reviews', 'retries_per_card', 'replans'])
    expect(testRunSettingsView(null)).toEqual({ modes: { automated: null, manual: null, external: null } })
    expect(checkSettingsView({ ...NONE, branches: [] })).toEqual({
      usage: null,
      scope: null,
      target: null,
      targetOptions: ['origin'],
      archive: false,
      push: null,
      keepBranch: null,
      connected: false,
      reason: 'git.unconnected',
      failures: {},
    })
  })
})

// --- What the parts draw -------------------------------------------------------------------------------

describe('card documents tab parts', () => {
  it('ResearchSlots: two lines — 지시서 (hollow dot "연결 전"), 레포트 with the 검수 대기 dot, the document icon and ✎', () => {
    const w = mount(ResearchSlots, { props: { target: facts(V01_TR, 'NR') }, global })
    const rows = slotRows(w)
    expect(rows).toHaveLength(2)
    expect(rows[0].get('[data-test="inspector-slot-name"]').text()).toBe('지시서')
    expect(rows[0].get('[data-test="inspector-slot-state"]').attributes('title')).toBe('연결 전')
    expect(rows[1].get('[data-test="inspector-slot-name"]').text()).toBe('레포트')
    expect(rows[1].get('[data-test="inspector-slot-state"]').attributes('title')).toBe('검수 대기')
    expect(rows[1].classes()).toContain('is-focus')
    expect(rows[1].get('[data-test="inspector-slot-open"]').attributes('data-icon')).toBe('document')
    expect(rows[1].find('[data-test="inspector-slot-edit"]').exists()).toBe(true)
    expect(rows[0].find('[data-test="inspector-slot-open"]').exists()).toBe(false)
  })

  it('TaskSlots on a Node without a document: both lines without a document, no "아직 없음", no ＋', () => {
    const w = mount(TaskSlots, { props: { target: facts(V01_TR, 'T', NO_DOCUMENT) }, global })
    const rows = slotRows(w)
    expect(rows.map((row) => row.get('[data-test="inspector-slot-state"]').attributes('title'))).toEqual(['문서 없음', '실행 후'])
    expect(w.text()).not.toContain('아직 없음')
    expect(w.find('[data-test="inspector-slot-write"]').exists()).toBe(false)
    expect(w.text()).not.toContain('＋')
  })

  it('RequirementSlots: 요구사항 for R, no badge while it is being written (0056 F1); 버그 for B (0015-T T3)', () => {
    const w = mount(RequirementSlots, { props: { target: REQ }, global })
    expect(w.get('[data-test="inspector-slot-name"]').text()).toBe('요구사항')
    expect(w.find('[data-test="inspector-slot-state"]').exists()).toBe(false)
    expect(mount(RequirementSlots, { props: { target: facts(REQ, 'B') }, global }).get('[data-test="inspector-slot-name"]').text()).toBe('버그')
    i18n.global.locale.value = 'en'
    expect(mount(RequirementSlots, { props: { target: facts(REQ, 'B') }, global }).get('[data-test="inspector-slot-name"]').text()).toBe('Bug')
    i18n.global.locale.value = 'ja'
    expect(mount(RequirementSlots, { props: { target: facts(REQ, 'B') }, global }).get('[data-test="inspector-slot-name"]').text()).toBe('バグ')
    i18n.global.locale.value = 'ko'
  })

  it('DesignSlots: no "설명 대기" note; 설계 지시 · 설계서 with the rejected report in red', () => {
    const w = mount(DesignSlots, { props: { target: DESIGN_CONFLICT }, global })
    expect(w.find('[data-test="inspector-tbd"]').exists()).toBe(false)
    expect(w.text()).not.toContain('설계 카드는 아직 비워 두었습니다.')
    const rows = slotRows(w)
    expect(rows.map((row) => row.get('[data-test="inspector-slot-name"]').text())).toEqual(['설계 지시', '설계서'])
    expect(rows[1].get('[data-test="inspector-slot-state"]').attributes('title')).toBe('반려됨')
    expect(rows[1].classes()).toContain('is-danger')
  })

  // flowgate.v02.0074 T#3 (R §3): the 설계 card names its two documents as the work plan edit window does; the
  // 조사 · 작업 cards keep 지시서 · 레포트, in every locale.
  it.each([
    ['ko', ['설계 지시', '설계서'], ['지시서', '레포트']],
    ['en', ['Design instruction', 'Design document'], ['Instruction', 'Report']],
    ['ja', ['設計指示', '設計書'], ['指示書', 'レポート']],
  ] as const)('%s: DesignSlots names its lines %j; ResearchSlots · TaskSlots keep %j', (locale, design, other) => {
    i18n.global.locale.value = locale
    const names = (w: ReturnType<typeof mount>) => slotRows(w).map((row) => row.get('[data-test="inspector-slot-name"]').text())
    expect(names(mount(DesignSlots, { props: { target: DESIGN_CONFLICT }, global }))).toEqual([...design])
    expect(names(mount(DesignSlots, { props: { target: facts(DESIGN_CONFLICT, 'D', NO_DOCUMENT) }, global }))).toEqual([...design])
    expect(names(mount(ResearchSlots, { props: { target: facts(V01_TR, 'NR') }, global }))).toEqual([...other])
    expect(names(mount(TaskSlots, { props: { target: facts(V01_TR, 'T') }, global }))).toEqual([...other])
    for (const word of ['설계 문서', '설계 지시서', '設計指示書']) expect(mount(DesignSlots, { props: { target: DESIGN_CONFLICT }, global }).text()).not.toContain(word)
  })

  it('GateSlots / GateActions: the result box and disabled decisions before a run', () => {
    const slots = mount(GateSlots, { props: { target: GATE }, global })
    expect(slots.get('[data-test="gate-result"]').text()).toContain('확인 결과')
    expect(slots.get('[data-test="gate-result-value"]').text()).toBe('확인 전')
    expect(slots.get('[data-test="gate-confirm"]').attributes('disabled')).toBeDefined()
    const none = facts(GATE, null, { ...NO_DOCUMENT, nodeType: null })
    expect(mount(GateSlots, { props: { target: none }, global }).find('[data-test="inspector-gate-card"]').exists()).toBe(false)
  })

  it('GateSlots on an open-type document Node: the 문서 line has no type code', () => {
    const w = mount(GateSlots, { props: { target: facts(V01_TR, 'XX', { nodeType: null }) }, global })
    expect(w.get('[data-test="inspector-slot-name"]').text()).toBe('문서')
    expect(w.text()).not.toContain('XX')
  })
})

describe('card header parts', () => {
  // flowgate.v02.0074 T#5: two icon buttons of the ⋯ kind (no text label; the name and the reason in the tooltip).
  it('WorkPlanActions: ⇉ 전개 · ⇉ 재전개 ▾ disabled with the reason', async () => {
    deployStatusRead.mockReset()
    deployStatusRead.mockResolvedValue({
      docId: REQ.docId, flowId: REQ.flowId, wpNodeKey: 'gn_wp', ownership: 'v01', wpRevisionNo: 1, deployable: false, reason: 'flow_owned_by_v01',
      deployedCount: 0, targetCount: 2, missingCount: 2, orphanCount: 0, mismatchCount: 0, cards: [], modes: { first: false, add: false, cleanup: false },
    })
    const w = mount(WorkPlanActions, { props: { target: facts(REQ, 'WP') }, global })
    await flushPromises()
    expect(deployStatusRead).toHaveBeenCalledWith(REQ.docId)
    const buttons = [w.get('[data-test="work-plan-deploy"]'), w.get('[data-test="work-plan-redeploy"]')]
    expect(buttons.map((button) => button.classes())).toEqual([expect.arrayContaining(['ui-icon-btn', 'ui-icon-btn--sm', 'ui-icon-btn--secondary']), expect.arrayContaining(['ui-icon-btn--sm', 'ui-icon-btn--secondary'])])
    expect(buttons.map((button) => button.attributes('aria-label'))).toEqual(['전개', '재전개'])
    for (const button of buttons) {
      expect(button.text()).not.toMatch(/전개/)
      expect(button.attributes('disabled')).toBeDefined()
      expect(button.attributes('title')).toMatch(/ — v0\.1이 진행하는 흐름입니다$/)
    }
  })

  // flowgate.v02.0052 T#3: run control lives in the top Menu Bar / Toolbar (실행 중지 · 재실행); the card
  // no longer repeats ■ 중단 / ↻ 재실행. 실행 이력 has no top counterpart and stays.
  it('InspectorRunMenu: 실행 이력 only, disabled with the common reason', () => {
    const w = mount(InspectorRunMenu, { global })
    const items = w.findAll('[role="menuitem"]')
    expect(items.map((item) => item.text())).toEqual(['실행 이력'])
    expect(w.text()).not.toContain('중단')
    expect(w.text()).not.toContain('재실행')
    for (const item of items) {
      expect(item.attributes('disabled')).toBeDefined()
      expect(item.attributes('title')).toBe('아직 연결되지 않음')
    }
  })
})

describe('card settings parts', () => {
  it('RequirementSettings (no card): the OFF line, the switch disabled, the 미연결 tag; ON (fixture) draws 범위 · AI Team · 안전 한도', () => {
    const w = mount(RequirementSettings, { props: { target: REQ }, global })
    expect(w.get('[data-test="inspector-requirement-settings"]').attributes('data-delegation')).toBe('off')
    // flowgate.v02.0052 T#2: one short OFF line; the v7 explanation stays as its tooltip (flowgate.v02.0053 T#7: no code).
    const off = w.get('[data-test="requirement-delegation-off"]')
    expect(off.text()).toBe('꺼짐 — 직접 구성하거나 작업계획으로 전개')
    expect(off.attributes('title')).toBe('전체 실행은 AI 위임과 무관 · 각 Node 설정으로 실행')
    for (const option of w.findAll('[data-test="inspector-segment-option"]')) {
      expect(option.attributes('disabled')).toBeDefined()
      expect(option.attributes('title')).toBe('이 Flow에는 카드 설정이 없습니다')
    }
    // "Not connected" is a tag beside the switch (reason in its tooltip), not a sentence in the body.
    const unconnected = w.get('[data-test="requirement-settings-unconnected"] [data-test="inspector-tbd"]')
    expect(unconnected.text()).toBe('미연결')
    expect(unconnected.attributes('title')).toBe('이 Flow에는 카드 설정이 없습니다')
    expect(w.get('[data-test="requirement-delegation"] .inspector-block-head').find('[data-test="requirement-settings-unconnected"]').exists()).toBe(true)
    expect(w.text()).not.toContain('0051-R')
    expect(w.find('[data-test="requirement-limits"]').exists()).toBe(false)

    const on: RequirementSettingsView = {
      delegation: 'on',
      scope: 'confirm',
      team: 'auth',
      teamOptions: [{ value: 'auth', label: '인증팀' }],
      limits: [
        { key: 'max_nodes', value: 12, isDefault: false, hot: false },
        { key: 'total_reviews', value: 30, isDefault: false, hot: true },
        { key: 'retries_per_card', value: 2, isDefault: true, hot: false },
        { key: 'replans', value: 3, isDefault: true, hot: false },
      ],
      locked: true,
      connected: true,
      reason: 'reason.locked',
      failures: {},
    }
    const fixture = mount(RequirementSettings, { props: { target: REQ, view: on }, global })
    expect(fixture.get('[data-test="inspector-requirement-settings"]').attributes('data-delegation')).toBe('on')
    expect(fixture.get('[data-test="inspector-lock-note"]').text()).toContain('위임 진행 중')
    expect(fixture.findAll('input[data-test^="requirement-limit-"]')).toHaveLength(4)
    expect(fixture.get('[data-test="requirement-limits"] [data-test="inspector-status-chip"]').text()).toBe('총 검수 · 재작업 30/30')
    expect(fixture.find('[data-test="requirement-settings-unconnected"]').exists()).toBe(false)
    // v7 `기본`: only on a limit with no value of its own; the Supervisor note is the AI Team label's tooltip.
    expect(fixture.findAll('[data-test$="-default"]').map((mark) => mark.attributes('data-test'))).toEqual(['requirement-limit-retries_per_card-default', 'requirement-limit-replans-default'])
    expect(fixture.text()).not.toContain('Supervisor 없는 팀을')
    const teamRow = fixture.get('[data-test="requirement-delegation-team"]').element.closest('[data-test="inspector-control-row"]')!
    expect(teamRow.querySelector('.inspector-control-label')!.getAttribute('title')).toBe('계획 · 질문 판단 · 진행 판단은 팀의 Supervisor')
  })

  // flowgate.v02.0053 T#7: the 실행 block only (지시서 · 시나리오 작성 are the settings feature's, in
  // v7 order before it); no code on screen (TSC / TSR, automated / manual / external).
  it('TestRunSettings: the 실행 block — 케이스별, the counts by mode ("—" while unknown), the TBD tag; no code, no control', () => {
    const w = mount(TestRunSettings, { props: { target: { ...GRAPH_TS, docId: null, cardDocuments: [] } }, global })
    expect(w.get('[data-test="inspector-block-title"]').text()).toBe('실행')
    expect(w.find('[data-test="inspector-block-sub"]').exists()).toBe(false)
    expect(w.get('[data-test="test-run-modes"]').text()).toContain('케이스별 — 시나리오에서 정함')
    expect(w.findAll('[data-test^="test-run-mode-"]').map((mode) => mode.text())).toEqual(['자동 —', '수동 —', '외부 —'])
    expect(w.get('[data-test="inspector-tbd"]').text()).toBe('수동 · 외부 흐름 그리지 않음')
    expect(w.find('[data-test="inspector-segment-option"]').exists()).toBe(false)
    expect(w.find('[data-test="test-run-settings-unconnected"]').exists()).toBe(false)
    for (const code of ['TSC', 'TSR', 'automated', 'manual', 'external']) expect(w.text()).not.toContain(code)
    const fixture = mount(TestRunSettings, { props: { target: GRAPH_TS, view: { modes: { automated: 49, manual: 6, external: 2 } } }, global })
    expect(fixture.findAll('[data-test^="test-run-mode-"]').map((mode) => mode.text())).toEqual(['자동 49', '수동 6', '외부 2'])
  })

  it('CheckSettings: archive and Git only, with the supported remote choices', () => {
    const w = mount(CheckSettings, { props: { target: GATE }, global })
    expect(w.find('[data-test="check-settings-usage"]').exists()).toBe(false)
    expect(w.find('[data-test="check-settings-summary"]').exists()).toBe(false)
    expect(w.find('[data-test="check-settings-hold-block"]').exists()).toBe(false)
    expect(w.get('[data-test="check-settings-archive-value"]').attributes('disabled')).toBeDefined()
    expect(w.findAll('[data-test="check-settings-target"] option').map(option => option.text())).toEqual(['—', 'origin'])
  })

  it('follows the locale', () => {
    i18n.global.locale.value = 'en'
    expect(mount(ResearchSlots, { props: { target: facts(V01_TR, 'NR') }, global }).findAll('[data-test="inspector-slot-name"]').map((n) => n.text())).toEqual(['Instruction', 'Report'])
    expect(mount(GateActions, { props: { target: GATE }, global }).get('[data-test="gate-confirm"]').text()).toBe('✓ Confirm')
    expect(mount(InspectorRunMenu, { global }).findAll('[role="menuitem"]').map((item) => item.text())).toEqual(['Run history'])
  })
})
