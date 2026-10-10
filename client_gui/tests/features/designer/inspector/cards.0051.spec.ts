/**
 * flowgate.v02.0051 stage 2 — the per-card parts (0051-R §2.3): the rules that turn the target into each
 * card's v7 lines, actions and settings (pure), and what the parts draw for them — the lines with their
 * names, pills and buttons; the disabled header actions and menu items with their reason; the settings
 * blocks drawn inactive with the reason. None of these parts reaches the server; a Node without a
 * document still shows the card's structure.
 */
import { mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it } from 'vitest'
import CheckSettings from '@/features/designer/inspector/features/checkSettings/CheckSettings.vue'
import { checkSettingsView } from '@/features/designer/inspector/features/checkSettings/checkSettingsModel'
import DesignSlots from '@/features/designer/inspector/features/designCard/DesignSlots.vue'
import { designCardSlots } from '@/features/designer/inspector/features/designCard/designCardModel'
import GateActions from '@/features/designer/inspector/features/gateCard/GateActions.vue'
import GateSlots from '@/features/designer/inspector/features/gateCard/GateSlots.vue'
import { gateActions, gateCardShape, gateCardSlots } from '@/features/designer/inspector/features/gateCard/gateCardModel'
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
import WorkPlanOrigin from '@/features/designer/inspector/features/workPlan/WorkPlanOrigin.vue'
import { workPlanDeployActions, workPlanSlotState } from '@/features/designer/inspector/features/workPlan/workPlanModel'
import type { InspectorTarget } from '@/features/designer/inspector/inspectorTarget'
import InspectorRunMenu from '@/features/designer/inspector/parts/InspectorRunMenu.vue'
import { prepareInspectorI18n, SELECTIONS, targetOf } from './support/inspectorSupport'

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
  it('a bound N: the instruction line with its pill, the report "실행 후"', () => {
    const [instruction, report] = researchCardSlots(facts(V01_TR, 'N', { docReviewStatus: 'approved', docStatus: 'closed' }))
    expect(instruction).toMatchObject({ key: 'instruction', id: V01_TR.docId, empty: false, open: 'enabled', focus: false })
    expect(instruction.state).toEqual({ key: 'review_status.approved', raw: 'approved', tone: 'success' })
    expect(report).toMatchObject({ key: 'report', empty: true, emptyKey: 'empty.after_run', open: 'none' })
  })

  it('a bound NR under review: the report line is the one to look at; the instruction is not linked', () => {
    const [instruction, report] = researchCardSlots(facts(V01_TR, 'NR'))
    expect(instruction).toMatchObject({ empty: true, emptyKey: 'empty.unconnected', open: 'none', write: 'none' })
    expect(report).toMatchObject({ id: V01_TR.docId, focus: true, open: 'enabled' })
    expect(report.state).toMatchObject({ raw: 'pending_review', tone: 'warning' })
  })

  it('a Graph N whose run produced a result: the report line names the result document, state not read', () => {
    const [, report] = researchCardSlots(facts(GRAPH_TS, 'N', { nodeRun: { status: 'completed', openedAt: null, finishedAt: null, resultDocId: 'flowgate.v02.0049.0008-NR' } }))
    expect(report).toMatchObject({ id: 'flowgate.v02.0049.0008-NR', empty: false, state: null, summaryKey: 'summary.not_read', open: 'enabled' })
  })

  it('a Node without a document keeps both lines: the instruction waits to be written (＋ disabled)', () => {
    const [instruction, report] = researchCardSlots(facts(V01_TR, 'N', NO_DOCUMENT))
    expect(instruction).toMatchObject({ empty: true, emptyKey: 'empty.not_yet', write: 'disabled', open: 'none' })
    expect(report).toMatchObject({ empty: true, emptyKey: 'empty.after_run' })
  })

  it('the document pill: review status first, else the document status, unknown values as sent', () => {
    expect(researchDocumentState(null, null)).toBeNull()
    expect(researchDocumentState(null, 'draft')).toEqual({ key: 'slot_state.writing', raw: 'draft', tone: 'info' })
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

describe('설계 card line (designCardModel)', () => {
  it('the bound document with its type; a Node without one gets the empty line with ＋ disabled', () => {
    expect(designCardSlots(DESIGN_CONFLICT)[0]).toMatchObject({ type: 'D', id: DESIGN_CONFLICT.docId, focus: true, open: 'enabled' })
    expect(designCardSlots(DESIGN_CONFLICT)[0].state).toMatchObject({ raw: 'rejected', tone: 'danger' })
    expect(designCardSlots(DESIGN_NO_DOCUMENT)[0]).toMatchObject({ type: 'D', empty: true, emptyKey: 'empty.not_yet', write: 'disabled' })
    expect(designCardSlots(facts(DESIGN_CONFLICT, 'X'))[0].type).toBeNull()
  })
})

describe('요구사항 / 버그 card line (requirementCardModel)', () => {
  it('R is the 요구사항 line, B the 버그 리포트 line; without a document the line is empty', () => {
    expect(requirementCardSlots(REQ)[0]).toMatchObject({ name: 'requirement', id: REQ.docId, open: 'enabled' })
    expect(requirementCardSlots(REQ)[0].state).toEqual({ key: 'slot_state.writing', raw: 'draft', tone: 'info' })
    expect(requirementCardSlots(facts(REQ, 'B'))[0].name).toBe('bug')
    expect(requirementCardSlots(facts(REQ, 'R', NO_DOCUMENT))[0]).toMatchObject({ empty: true, write: 'disabled' })
  })
})

describe('Graph gate (gateCardModel)', () => {
  it('a gate: the 확인 결과 result line (empty) and two actions shown but never usable yet', () => {
    expect(gateCardShape(GATE)).toBe('gate')
    expect(gateCardSlots(GATE)).toEqual([{ key: 'result', kind: 'result', type: null, id: null, empty: true, emptyKey: 'empty.before', state: null, focus: false, open: 'none' }])
    expect(gateActions(GATE)).toEqual({
      confirm: { visible: true, enabled: false, reason: 'reason.unconnected' },
      reject: { visible: true, enabled: false, reason: 'reason.unconnected' },
    })
  })

  it('an `other` document Node: its one document line; a Node with nothing: no line, no action', () => {
    const other = facts(V01_TR, 'XX', { nodeType: null })
    expect(gateCardShape(other)).toBe('document')
    expect(gateCardSlots(other)[0]).toMatchObject({ key: 'document', type: 'XX', id: V01_TR.docId, open: 'enabled' })
    const none = facts(V01_TR, null, { ...NO_DOCUMENT, nodeType: 'custom' })
    expect(gateCardShape(none)).toBe('none')
    expect(gateCardSlots(none)).toEqual([])
    expect(gateActions(none).confirm.visible).toBe(false)
  })
})

describe('작업계획 card (workPlanModel, stage 2)', () => {
  it('⇉ 전개 · ⇉ 재전개 ▾ are drawn and disabled with the reason; the plan pill follows the document', () => {
    const actions = workPlanDeployActions({ hasDocument: true })
    expect(actions.deploy).toEqual({ visible: true, enabled: false, reason: 'deploy.unconnected' })
    expect(actions.redeploy).toEqual({ visible: true, enabled: false, reason: 'deploy.unconnected' })
    expect(workPlanSlotState('approved', 'open')).toMatchObject({ raw: 'approved', tone: 'success' })
    expect(workPlanSlotState(null, 'draft')).toMatchObject({ key: 'slot_state.writing', tone: 'info' })
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

  it('a TS view: the instruction with its pill, the Cases with the tally, the result "실행 후"', () => {
    const [instruction, cases, result] = testSpecSlots(view, GRAPH_TS)
    expect(instruction).toMatchObject({ key: 'instruction', id: GRAPH_TS.docId, open: 'enabled' })
    expect(instruction.state).toMatchObject({ raw: 'approved', tone: 'success' })
    expect(cases).toMatchObject({ key: 'cases', form: true, empty: false, summary: 'tally', open: 'none' })
    expect(result).toMatchObject({ key: 'result', kind: 'result', empty: true, emptyKey: 'empty.after_run' })
  })

  it('no view yet: the selected TSR fills the result line, the instruction waits to be written', () => {
    const [instruction, cases, result] = testSpecSlots(null, facts(V01_TR, 'TSR'))
    expect(instruction).toMatchObject({ empty: true, emptyKey: 'empty.write', write: 'disabled' })
    expect(cases).toMatchObject({ empty: true, emptyKey: 'empty.after_run' })
    expect(result).toMatchObject({ id: V01_TR.docId, empty: false, state: null, open: 'enabled' })
  })
})

describe('settings views with no server side yet', () => {
  it('AI 위임 is OFF and not changeable; test settings lock while the run is live; check settings know nothing', () => {
    expect(requirementSettingsView(REQ)).toMatchObject({ delegation: 'off', connected: false, locked: false })
    expect(requirementSettingsView(REQ).limits.map((limit) => limit.key)).toEqual(['max_nodes', 'total_reviews', 'retries_per_card', 'replans'])
    expect(testRunSettingsView(GRAPH_TS)).toMatchObject({ connected: false, locked: false, modes: { automated: null, manual: null, external: null } })
    expect(testRunSettingsView({ ...GRAPH_TS, nodeRun: { status: 'running', openedAt: null, finishedAt: null, resultDocId: null } }).locked).toBe(true)
    expect(checkSettingsView(V01_TR)).toEqual({ usage: null, scope: null, target: null, push: null, keepBranch: null, connected: false })
  })
})

// --- What the parts draw -------------------------------------------------------------------------------

describe('card documents tab parts', () => {
  it('ResearchSlots: two lines — 지시서 "연결 전", 레포트 with the 검수 대기 pill and ↗', () => {
    const w = mount(ResearchSlots, { props: { target: facts(V01_TR, 'NR') }, global })
    const rows = slotRows(w)
    expect(rows).toHaveLength(2)
    expect(rows[0].get('[data-test="inspector-slot-name"]').text()).toBe('지시서')
    expect(rows[0].get('[data-test="inspector-slot-empty"]').text()).toBe('연결 전')
    expect(rows[1].get('[data-test="inspector-slot-name"]').text()).toBe('레포트')
    expect(rows[1].get('[data-test="inspector-status-chip"]').text()).toBe('검수 대기')
    expect(rows[1].classes()).toContain('is-focus')
    expect(rows[1].find('[data-test="inspector-slot-open"]').exists()).toBe(true)
    expect(rows[0].find('[data-test="inspector-slot-open"]').exists()).toBe(false)
  })

  it('TaskSlots on a Node without a document: both lines empty, ＋ shown disabled with its reason', () => {
    const w = mount(TaskSlots, { props: { target: facts(V01_TR, 'T', NO_DOCUMENT) }, global })
    const rows = slotRows(w)
    expect(rows.map((row) => row.get('[data-test="inspector-slot-empty"]').text())).toEqual(['아직 없음', '실행 후'])
    const write = rows[0].get('[data-test="inspector-slot-write"]')
    expect(write.attributes('disabled')).toBeDefined()
    expect(write.attributes('title')).toBe('작성 — 아직 연결되지 않음 (v0.1 문서 화면에서 작성)')
  })

  it('RequirementSlots: 요구사항 for R with the 작성 중 pill; 버그 리포트 for B', () => {
    const w = mount(RequirementSlots, { props: { target: REQ }, global })
    expect(w.get('[data-test="inspector-slot-name"]').text()).toBe('요구사항')
    expect(w.get('[data-test="inspector-status-chip"]').text()).toBe('작성 중')
    expect(mount(RequirementSlots, { props: { target: facts(REQ, 'B') }, global }).get('[data-test="inspector-slot-name"]').text()).toBe('버그 리포트')
  })

  it('DesignSlots: the "설명 대기" note and the typed document line', () => {
    const w = mount(DesignSlots, { props: { target: DESIGN_CONFLICT }, global })
    expect(w.get('[data-test="inspector-tbd"]').text()).toContain('설계 카드는 아직 비워 두었습니다.')
    expect(w.get('[data-test="inspector-slot-name"]').text()).toBe('기본 설계')
    expect(w.get('[data-test="inspector-status-chip"]').text()).toBe('반려됨')
    expect(w.get('[data-test="inspector-slot"]').classes()).toContain('is-danger')
  })

  it('GateSlots / GateActions: the 확인 결과 line and the two disabled actions with the reason; nothing on a non-gate without a document', () => {
    const slots = mount(GateSlots, { props: { target: GATE }, global })
    expect(slots.get('[data-test="inspector-slot-name"]').text()).toBe('확인 결과')
    expect(slots.get('[data-test="inspector-slot-empty"]').text()).toBe('확인 전')
    const actions = mount(GateActions, { props: { target: GATE }, global })
    const confirm = actions.get('[data-test="gate-confirm"]')
    expect(confirm.text()).toBe('✓ 확인')
    expect(confirm.attributes('disabled')).toBeDefined()
    expect(confirm.attributes('title')).toContain('아직 연결되지 않았습니다')
    expect(actions.get('[data-test="gate-reject"]').attributes('disabled')).toBeDefined()
    const none = facts(V01_TR, null, { ...NO_DOCUMENT, nodeType: 'custom' })
    expect(mount(GateSlots, { props: { target: none }, global }).find('[data-test="inspector-gate-card"]').exists()).toBe(false)
    expect(mount(GateActions, { props: { target: none }, global }).find('[data-test="inspector-gate-actions"]').exists()).toBe(false)
  })
})

describe('card header parts', () => {
  it('WorkPlanActions: ⇉ 전개 · ⇉ 재전개 ▾ disabled with the reason; WorkPlanOrigin: 작성 사람', () => {
    const w = mount(WorkPlanActions, { props: { target: facts(REQ, 'WP') }, global })
    const buttons = w.findAll('button')
    expect(buttons.map((button) => button.text())).toEqual(['⇉ 전개', '⇉ 재전개 ▾'])
    for (const button of buttons) {
      expect(button.attributes('disabled')).toBeDefined()
      expect(button.attributes('title')).toBe('WP 전개는 아직 연결되지 않았습니다 (0048 D#1 §8.4).')
    }
    const origin = mount(WorkPlanOrigin, { props: { target: facts(REQ, 'WP') }, global })
    expect(origin.get('[data-test="work-plan-author"]').text()).toBe('작성 사람')
  })

  it('InspectorRunMenu: 실행 이력 · ■ 중단 · ↻ 재실행, each disabled with the common reason', () => {
    const w = mount(InspectorRunMenu, { global })
    const items = w.findAll('[role="menuitem"]')
    expect(items.map((item) => item.text())).toEqual(['실행 이력', '■ 중단', '↻ 재실행'])
    for (const item of items) {
      expect(item.attributes('disabled')).toBeDefined()
      expect(item.attributes('title')).toBe('아직 연결되지 않음')
    }
  })
})

describe('card settings parts', () => {
  it('RequirementSettings: the OFF explanation, the switch disabled, the reason; ON (fixture) draws 범위 · AI Team · 안전 한도', () => {
    const w = mount(RequirementSettings, { props: { target: REQ }, global })
    expect(w.get('[data-test="inspector-requirement-settings"]').attributes('data-delegation')).toBe('off')
    expect(w.get('[data-test="requirement-delegation-off"]').text()).toContain('꺼져 있으면 직접 구성하거나')
    for (const option of w.findAll('[data-test="inspector-segment-option"]')) expect(option.attributes('disabled')).toBeDefined()
    expect(w.get('[data-test="requirement-settings-unconnected"]').text()).toContain('AI 위임은 아직 연결되지 않았습니다')
    expect(w.find('[data-test="requirement-limits"]').exists()).toBe(false)

    const on: RequirementSettingsView = {
      delegation: 'on',
      scope: 'confirm',
      team: 'auth',
      teamOptions: [{ value: 'auth', label: '인증팀' }],
      limits: [
        { key: 'max_nodes', value: 12, hot: false },
        { key: 'total_reviews', value: 30, hot: true },
        { key: 'retries_per_card', value: 2, hot: false },
        { key: 'replans', value: 3, hot: false },
      ],
      locked: true,
      connected: true,
    }
    const fixture = mount(RequirementSettings, { props: { target: REQ, view: on }, global })
    expect(fixture.get('[data-test="inspector-requirement-settings"]').attributes('data-delegation')).toBe('on')
    expect(fixture.get('[data-test="inspector-lock-note"]').text()).toContain('위임 진행 중')
    expect(fixture.findAll('[data-test^="requirement-limit-"]')).toHaveLength(4)
    expect(fixture.get('[data-test="requirement-limits"] [data-test="inspector-status-chip"]').text()).toBe('총 검수 · 재작업 30/30')
    expect(fixture.find('[data-test="requirement-settings-unconnected"]').exists()).toBe(false)
  })

  it('TestRunSettings: the three v7 blocks, counts "—", the controls disabled, the TBD tag; locked while running', () => {
    const w = mount(TestRunSettings, { props: { target: GRAPH_TS }, global })
    expect(w.get('[data-test="test-run-mode-automated"]').text()).toBe('automated —')
    expect(w.get('[data-test="inspector-tbd"]').text()).toBe('manual · external 흐름 그리지 않음')
    for (const option of w.findAll('[data-test="inspector-segment-option"]')) expect(option.attributes('disabled')).toBeDefined()
    expect(w.get('[data-test="test-run-settings-unconnected"]').text()).toContain('아직 Node에 저장되지 않습니다')
    expect(w.find('[data-test="inspector-lock-note"]').exists()).toBe(false)
    const running = mount(TestRunSettings, { props: { target: { ...GRAPH_TS, nodeRun: { status: 'running', openedAt: null, finishedAt: null, resultDocId: null } } }, global })
    expect(running.get('[data-test="inspector-lock-note"]').text()).toContain('실행 중')
  })

  it('CheckSettings: 용도 "—" with the 토론 중 tag, the Git card disabled, the reason as the summary', () => {
    const w = mount(CheckSettings, { props: { target: V01_TR }, global })
    expect(w.get('[data-test="check-settings-usage-value"]').text()).toBe('—')
    expect(w.get('[data-test="inspector-tbd"]').text()).toBe('토론 중')
    for (const option of w.findAll('[data-test="inspector-segment-option"]')) expect(option.attributes('disabled')).toBeDefined()
    expect(w.get('[data-test="check-settings-summary"]').text()).toBe('Node에 저장되는 설정이 아직 없습니다. 반영 방식은 헤더의 최종 확인 대화상자에서 고릅니다.')
    const fixture = mount(CheckSettings, { props: { target: V01_TR, view: { usage: 'end', scope: 'merge', target: 'main', push: true, keepBranch: false, connected: true } }, global })
    expect(fixture.get('[data-test="check-settings-usage-value"]').text()).toBe('그룹 종료')
    expect(fixture.get('[data-test="check-settings-summary"]').text()).toBe('작업 브랜치를 main에 병합하고 원격 반영')
  })

  it('follows the locale', () => {
    i18n.global.locale.value = 'en'
    expect(mount(ResearchSlots, { props: { target: facts(V01_TR, 'NR') }, global }).findAll('[data-test="inspector-slot-name"]').map((n) => n.text())).toEqual(['Instruction', 'Report'])
    expect(mount(GateActions, { props: { target: GATE }, global }).get('[data-test="gate-confirm"]').text()).toBe('✓ Confirm')
    expect(mount(InspectorRunMenu, { global }).findAll('[role="menuitem"]').map((item) => item.text())).toEqual(['Run history', '■ Stop', '↻ Run again'])
  })
})
