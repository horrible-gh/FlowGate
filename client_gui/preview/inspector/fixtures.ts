// flowgate.v02.0051 — preview fixtures: the 0035 v7 mockup's example values (insp.js NODES / V),
// shaped as the Center session hands them to the Inspector (FlowGraphSelection -> InspectorTarget).
// Not server data. Ids and titles are the mockup's (group 0008).
// Stage 2 adds one target per card kind × document state for the card parts, fixture views for the
// settings parts' shapes that have no server side yet (AI 위임 ON, test counts, Git choice), and fixed
// v7 example slot lines for the cards whose real lines need a server read (WP · test · check).
import type { FlowGraph, FlowGraphNode, FlowGraphSelection } from '@/features/designer/center/graph/flowGraph'
import type { CheckSettingsView } from '@/features/designer/inspector/features/checkSettings/checkSettingsModel'
import type { RequirementSettingsView } from '@/features/designer/inspector/features/requirementSettings/requirementSettingsModel'
import type { TestRunSettingsView } from '@/features/designer/inspector/features/testRunSettings/testRunSettingsModel'
import { toInspectorTarget, type InspectorTarget } from '@/features/designer/inspector/inspectorTarget'
import type { InspectorRunSettingsFields } from '@/features/designer/inspector/parts/InspectorRunSettings.vue'
import type { InspectorSlotView } from '@/features/designer/inspector/parts/InspectorSlotList.vue'
import type { InspectorSlotState } from '@/features/designer/inspector/parts/InspectorSlotRow.vue'

const PROJECT_ID = 'preview'
const FLOW = 'flowgate.v02.0008'

function graphOf(flowId: string, ownership: string, node: FlowGraphNode, viewMode: string | null = null): FlowGraph {
  return { flowId, nodes: [node], edges: [], ownership, viewMode, draft: null }
}

function selectionOf(graph: FlowGraph): FlowGraphSelection {
  return { projectId: PROJECT_ID, graph, node: graph.nodes[0] }
}

function target(graph: FlowGraph): InspectorTarget {
  const result = toInspectorTarget(selectionOf(graph))
  if (!result) throw new Error('fixture without a target')
  return result
}

/** A v0.1 document Node (bound document + sequence slot). */
function v01Node(kind: string, seq: number, type: string, title: string, state: string, review: string | null, status: string): FlowGraphNode {
  const docId = `${FLOW}.${String(seq).padStart(4, '0')}-${type}`
  return {
    id: `doc:${docId}`,
    kind,
    title,
    state,
    docType: type,
    x: 0,
    y: 0,
    document: { docId, typeCode: type, seq, title, docReviewStatus: review, status, supersedes: [] },
    slot: { itemId: seq, itemSeq: seq, order: seq, type, label: title, derivedStatus: state, protected: false, sourceWpCardId: null },
    nodeRun: null,
  }
}

/** A Graph document Node without a document yet (the card's shape must still show, 0051-R §2.3). */
function graphEmptyNode(id: string, kind: string, type: string, title: string, rootBound = false): FlowGraphNode {
  return {
    id,
    kind,
    title,
    state: 'draft',
    docType: type,
    x: 0,
    y: 0,
    definition: { nodeType: 'document', docType: type, paletteItemId: `doc_${type.toLowerCase()}`, label: title, assignee: null, rootBound },
    document: null,
    slot: null,
    nodeRun: null,
  }
}

/** 요구사항 · 작성 중 (unassigned Flow): the R card before the group's first run. */
export const REQ_DRAFT = target(
  graphOf(FLOW, 'unassigned', {
    id: `doc:${FLOW}.0001-R`,
    kind: 'requirement',
    title: '로그인 오류 수정',
    state: 'draft',
    docType: 'R',
    x: 0,
    y: 0,
    document: { docId: `${FLOW}.0001-R`, typeCode: 'R', seq: 1, title: '로그인 오류 수정', docReviewStatus: null, status: 'draft', supersedes: [] },
  }),
)

/** 버그 · 작성 중 (unassigned Flow): the same root as a B document — bug glyph, red accent, 버그 리포트 line. */
export const REQ_BUG = target(
  graphOf(FLOW, 'unassigned', {
    id: `doc:${FLOW}.0001-B`,
    kind: 'requirement',
    title: '로그인 오류 수정',
    state: 'draft',
    docType: 'B',
    x: 0,
    y: 0,
    document: { docId: `${FLOW}.0001-B`, typeCode: 'B', seq: 1, title: '로그인 오류 수정', docReviewStatus: null, status: 'draft', supersedes: [] },
  }),
)

const REQ_NO_DOCUMENT_GRAPH = graphOf(FLOW, 'graph', graphEmptyNode('gn_req', 'requirement', 'R', '로그인 오류 수정', true), 'draft')
/** 요구사항 (Graph Node, no document): the card's shape with empty lines. */
export const REQ_NO_DOCUMENT = target(REQ_NO_DOCUMENT_GRAPH)
export const SELECTION_REQ_NO_DOCUMENT = selectionOf(REQ_NO_DOCUMENT_GRAPH)

/** 조사 · 지시서 (N) 승인 (v0.1): the instruction line is the bound document, the report waits. */
export const RESEARCH_N = target(graphOf(FLOW, 'v01', v01Node('research', 2, 'N', '원인 조사', 'done', 'approved', 'closed')))
/** 조사 · 레포트 (NR) 검수 대기 (v0.1): the report line is the bound document and the one to look at. */
export const RESEARCH_NR_REVIEW = target(graphOf(FLOW, 'v01', v01Node('research', 3, 'NR', '원인 조사 결과', 'review', 'pending_review', 'open')))
/** 조사 · 반려됨 (v0.1). */
export const RESEARCH_NR_REJECTED = target(graphOf(FLOW, 'v01', v01Node('research', 3, 'NR', '원인 조사 결과', 'blocked', 'rejected', 'open')))
/** 조사 · Graph N whose run produced the NR: the report line names the result document. */
export const RESEARCH_N_RUN = target(
  graphOf(
    FLOW,
    'graph',
    {
      ...graphEmptyNode('gn_n', 'research', 'N', '원인 조사'),
      state: 'done',
      document: { docId: `${FLOW}.0002-N`, typeCode: 'N', seq: 2, title: '원인 조사', docReviewStatus: 'approved', status: 'closed', supersedes: [] },
      nodeRun: { status: 'completed', openedAt: '2026-09-26T01:00:00Z', finishedAt: '2026-09-26T01:20:00Z', resultDocId: `${FLOW}.0003-NR` },
    },
    'draft',
  ),
)
const RESEARCH_NO_DOCUMENT_GRAPH = graphOf(FLOW, 'graph', graphEmptyNode('gn_n2', 'research', 'N', '원인 조사'), 'draft')
/** 조사 (Graph Node, no document). */
export const RESEARCH_NO_DOCUMENT = target(RESEARCH_NO_DOCUMENT_GRAPH)
export const SELECTION_RESEARCH_NO_DOCUMENT = selectionOf(RESEARCH_NO_DOCUMENT_GRAPH)

/** 작업 · 지시서 (T) 승인 (v0.1). */
export const TASK_T = target(graphOf(FLOW, 'v01', v01Node('task', 6, 'T', '토큰 갱신 로직 수정', 'in_progress', 'approved', 'open')))

/** 작업 · 검수 대기 (Graph Flow, AI delegation expanded it): the card with the most header items. */
const TASK_REVIEW_GRAPH = graphOf(
  FLOW,
  'graph',
  {
    id: 'gn_work',
    kind: 'task',
    title: '토큰 갱신 로직 수정',
    state: 'review',
    docType: 'TR',
    x: 0,
    y: 0,
    definition: { nodeType: 'document', docType: 'TR', paletteItemId: 'doc_tr', label: '토큰 갱신 로직 수정', assignee: null, rootBound: false },
    document: { docId: `${FLOW}.0007-TR`, typeCode: 'TR', seq: 7, title: '토큰 갱신 로직 수정', docReviewStatus: 'pending_review', status: 'open', supersedes: [] },
    nodeRun: { status: 'waiting_review', openedAt: '2026-09-26T02:05:00Z', finishedAt: null, resultDocId: `${FLOW}.0007-TR` },
  },
  'draft',
)

export const TASK_REVIEW = target(TASK_REVIEW_GRAPH)

/** The same card as the Frame's selection context (the frame sample). */
export const SELECTION_TASK_REVIEW = selectionOf(TASK_REVIEW_GRAPH)

const TASK_NO_DOCUMENT_GRAPH = graphOf(FLOW, 'graph', graphEmptyNode('gn_t2', 'task', 'T', '토큰 갱신 로직 수정'), 'draft')
/** 작업 (Graph Node, no document). */
export const TASK_NO_DOCUMENT = target(TASK_NO_DOCUMENT_GRAPH)
export const SELECTION_TASK_NO_DOCUMENT = selectionOf(TASK_NO_DOCUMENT_GRAPH)

/** 설계 · 읽기 전용 (ownership conflict): the read-only tag. */
export const DESIGN_READ_ONLY = target(
  graphOf(
    FLOW,
    'conflict',
    {
      id: `doc:${FLOW}.0004-D`,
      kind: 'design',
      title: '토큰 갱신 설계',
      state: 'in_progress',
      docType: 'D',
      x: 0,
      y: 0,
      document: { docId: `${FLOW}.0004-D`, typeCode: 'D', seq: 4, title: '토큰 갱신 설계', docReviewStatus: 'rejected', status: 'open', supersedes: [] },
    },
    'revision',
  ),
)
/** 설계 (Graph Node, no document). */
export const DESIGN_NO_DOCUMENT = target(graphOf(FLOW, 'graph', graphEmptyNode('gn_d', 'design', 'P', '토큰 갱신 프로토콜'), 'draft'))

/** 테스트 · 지시 (TS) 실행 중 (Graph): the test settings lock while the run is live. */
export const TEST_TS_RUNNING = target(
  graphOf(
    FLOW,
    'graph',
    {
      ...graphEmptyNode('gn_ts', 'test', 'TS', '인증 통합 검증'),
      state: 'in_progress',
      document: { docId: `${FLOW}.0008-TS`, typeCode: 'TS', seq: 8, title: '인증 통합 검증', docReviewStatus: 'approved', status: 'open', supersedes: [] },
      nodeRun: { status: 'running', openedAt: '2026-09-26T03:00:00Z', finishedAt: null, resultDocId: null },
    },
    'draft',
  ),
)

/** 작업계획 (WP) · 작성 중 (unassigned). */
export const PLAN_WP = target(
  graphOf(FLOW, 'unassigned', {
    id: `doc:${FLOW}.0004-WP`,
    kind: 'plan',
    title: '수정 계획',
    state: 'draft',
    docType: 'WP',
    x: 0,
    y: 0,
    document: { docId: `${FLOW}.0004-WP`, typeCode: 'WP', seq: 4, title: '수정 계획', docReviewStatus: null, status: 'draft', supersedes: [] },
  }),
)

/** 확인 (AC) · 확인 대기 (v0.1). */
export const CHECK_AC = target(graphOf(FLOW, 'v01', v01Node('approval', 11, 'AC', '최종 확인', 'review', 'pending_review', 'open')))

const GATE_GRAPH = graphOf(
  FLOW,
  'graph',
  {
    id: 'gn_gate',
    kind: 'approval',
    title: '결과 확인',
    state: 'draft',
    docType: null,
    x: 0,
    y: 0,
    definition: { nodeType: 'gate', docType: null, paletteItemId: 'gate', label: '결과 확인', assignee: null, rootBound: false },
    document: null,
    slot: null,
    nodeRun: null,
  },
  'draft',
)
/** 확인 (Graph gate) — no document: the header must still carry the whole v7 line. */
export const GATE_NO_DOCUMENT = target(GATE_GRAPH)
export const SELECTION_GATE = selectionOf(GATE_GRAPH)

// --- Slot lines (insp.js V[...].slots) ----------------------------------------------------------

export interface SlotFixture {
  readonly kind?: 'document' | 'result'
  readonly name: string
  readonly id?: string
  readonly empty?: boolean
  readonly emptyText?: string
  readonly state?: InspectorSlotState
  readonly summary?: string
  readonly progress?: number
  readonly focus?: boolean
  readonly danger?: boolean
  /** Buttons on the right: review badge (count / state / remain), nav label, open (document icon · ✎). */
  readonly review?: { count: number; state: 'pass' | 'warn' | 'run' | 'limit'; remain?: number }
  readonly open?: boolean
  readonly nav?: string
  readonly caption: string
}

export const SLOT_FIXTURES: readonly SlotFixture[] = [
  { name: '지시서', id: '0008.0006-T', state: { label: '승인', tone: 'success' }, open: true, caption: '문서 슬롯 · 승인 · 문서 아이콘 · ✎' },
  { name: '레포트', id: '0008.0007-TR', state: { label: '검수 대기', tone: 'warning' }, review: { count: 2, state: 'pass', remain: 0 }, open: true, focus: true, caption: '문서 슬롯 · 검수 대기 · 🛡 2 · focus' },
  { name: '레포트', id: '0008.0007-TR', state: { label: '반려됨', tone: 'danger' }, review: { count: 1, state: 'warn' }, open: true, focus: true, caption: '문서 슬롯 · 반려됨 · 🛡 1 (대응 필요)' },
  { name: '레포트', id: '0008.0007-TR', state: { label: '검수 한도 초과', tone: 'danger' }, review: { count: 2, state: 'limit', remain: 1 }, open: true, focus: true, caption: '문서 슬롯 · 검수 한도 초과 · 🛡 2 (빨강)' },
  { name: '레포트', id: '0008.0003-NR', state: { label: '생성 중', tone: 'running' }, review: { count: 1, state: 'run' }, open: true, caption: '문서 슬롯 · 생성 중 · 🛡 1 (보라)' },
  { name: '레포트', empty: true, emptyText: '실행 후', caption: '문서 없는 줄 · 점선 · “실행 후” 문구' },
  { name: '레포트', empty: true, emptyText: '실패 · 제한 시간 초과', danger: true, caption: '문서 없는 줄 · 실패 (danger)' },
  { name: '시나리오', id: '0008.0008-TS', state: { label: '승인', tone: 'success' }, summary: '케이스 57 · automated 49 · manual 6 · external 2', open: true, caption: '문서 슬롯 · 요약 줄임' },
  { name: '작업계획', id: '0008.0004-WP', state: { label: '스냅샷', tone: 'neutral' }, open: true, caption: '문서 슬롯 · 스냅샷 (idle · 배지 없음)' },
  { name: '레포트', id: '0008.0010-TSR', state: { label: '진행 중', tone: 'running' }, summary: 'automated 12/49', progress: 24, open: true, caption: '문서 슬롯 · 진행도' },
  { name: '레포트', id: '0008.0010-TSR', state: { label: '결함 3', tone: 'danger' }, summary: '통과 54 · 실패 3', open: true, focus: true, caption: '문서 슬롯 · 결함 (focus)' },
  { kind: 'result', name: '확인 결과', state: { label: '보류', tone: 'hold' }, summary: '확인 완료 · Git 반영 보류', nav: 'Git 탭', open: true, caption: '결과 슬롯 · 보류 · 이동 버튼' },
  { kind: 'result', name: '전개', state: { label: '계획과 다름 1', tone: 'hold' }, summary: 'Node 4 · 작업: 검수 횟수 2→1', open: true, caption: '결과 슬롯 · 계획과 다름' },
  { kind: 'result', name: 'AI 위임', state: { label: '한도 도달', tone: 'danger' }, summary: '총 검수/재작업 30/30', progress: 50, open: true, focus: true, danger: true, caption: '결과 슬롯 · 한도 도달 (danger + 진행도)' },
  { kind: 'result', name: '전개', empty: true, emptyText: '전개 전', caption: '빈 결과 슬롯' },
]

// --- Run settings (insp.js execSettings) ---------------------------------------------------------

const AI_OPTIONS = [
  { value: 'sonnet5', label: 'Claude Sonnet 5' },
  { value: 'opus5', label: 'Claude Opus 5' },
  { value: 'sol', label: 'GPT-5.6 Sol' },
]

export const RUN_SETTINGS_SAMPLE: InspectorRunSettingsFields = {
  run_ai: { value: 'sonnet5', options: AI_OPTIONS },
  restart: { value: '1', options: [{ value: '0', label: '안 함' }, { value: '1', label: '1회' }, { value: '2', label: '2회' }] },
  review_ai: { value: 'sol', options: AI_OPTIONS },
  review_count: { value: '2', options: [{ value: '0', label: '안 함' }, { value: '1', label: '1회' }, { value: '2', label: '2회' }, { value: '-1', label: '통과할 때까지' }] },
  time_limit: { value: '60', options: [{ value: '30', label: '30분' }, { value: '60', label: '60분' }] },
  review_basis: { value: 'type', options: [{ value: 'type', label: '문서 타입 기본' }, { value: 'last', label: '직전 반려 사유만' }] },
}

/** Only the three settings 0048 stores on a v0.1 sequence row; the rest is not connected. */
export const RUN_SETTINGS_PARTIAL: InspectorRunSettingsFields = {
  run_ai: { value: 'sonnet5', options: AI_OPTIONS },
  review_ai: { value: 'sol', options: AI_OPTIONS },
  review_count: { value: '2', options: [{ value: '0', label: '안 함' }, { value: '1', label: '1회' }, { value: '2', label: '2회' }] },
  restart: { value: '', options: [], disabled: true, reason: '재시작은 아직 저장되지 않습니다' },
  time_limit: { value: '', options: [], disabled: true, reason: '실행 제한 시간은 아직 저장되지 않습니다' },
  review_basis: { value: '', options: [], disabled: true, reason: '검수 기준은 아직 저장되지 않습니다' },
}

// --- Stage 2: fixture views of the settings parts' shapes that have no server side yet -------------

const TEAM_OPTIONS = [
  { value: 'auth', label: '인증팀 · Supervisor Claude Opus 5' },
  { value: 'basic', label: '기본팀 · Supervisor 없음' },
]

/** AI 위임 ON (v7 reqSettings, `F.plan === 'ai'`): the example values of a requirement card's settings. */
export const REQ_SETTINGS_ON: RequirementSettingsView = {
  delegation: 'on',
  scope: 'end',
  team: 'auth',
  teamOptions: TEAM_OPTIONS,
  limits: [
    { key: 'max_nodes', value: 12, isDefault: true, hot: false },
    { key: 'total_reviews', value: 30, isDefault: true, hot: false },
    { key: 'retries_per_card', value: 2, isDefault: true, hot: false },
    { key: 'replans', value: 3, isDefault: true, hot: false },
  ],
  locked: false,
  connected: true,
  reason: null,
  failures: {},
}

/** AI 위임 · 한도 도달 (v7 req.d.limit): the reached limit red, the settings locked. */
export const REQ_SETTINGS_LIMIT: RequirementSettingsView = {
  ...REQ_SETTINGS_ON,
  limits: REQ_SETTINGS_ON.limits.map((limit) => (limit.key === 'total_reviews' ? { ...limit, hot: true } : limit)),
  locked: true,
  reason: 'reason.locked',
}

/** 테스트 설정 (v7 testSettings) — the 실행 block with the example counts. */
export const TEST_RUN_SETTINGS_SAMPLE: TestRunSettingsView = {
  modes: { automated: 49, manual: 6, external: 2 },
}

/** 확인 설정 (v7 confirmSettings) with the example choice. */
export const CHECK_SETTINGS_SAMPLE: CheckSettingsView = {
  usage: 'end',
  scope: 'merge',
  target: 'main',
  targetOptions: ['main', 'release'],
  push: true,
  keepBranch: false,
  connected: true,
  reason: null,
  failures: {},
}

// --- Stage 2: fixed v7 example lines of the cards whose real lines come from a server read ---------

export interface CardSlotFixture {
  readonly caption: string
  readonly lines: readonly InspectorSlotView[]
}

export const CARD_SLOT_FIXTURES: readonly CardSlotFixture[] = [
  {
    caption: '작업계획 · 사람 WP · 작성 중 · 전개 전 (v7 plan.m.draft)',
    lines: [
      { key: 'plan', name: '작업계획', id: '0008.0004-WP', state: { label: '작성 중', tone: 'info' }, summary: 'rev 1 · Step 4', focus: true, open: 'enabled' },
      { key: 'deploy', kind: 'result', name: '전개', empty: true, emptyText: '전개 전' },
    ],
  },
  {
    caption: '작업계획 · 전개됨 · 계획과 다름 (v7 plan.m.diff)',
    lines: [
      { key: 'plan', name: '작업계획', id: '0008.0004-WP', state: { label: '스냅샷', tone: 'neutral' }, summary: 'rev 1 · Step 4', open: 'enabled' },
      { key: 'deploy', kind: 'result', name: '전개', state: { label: '계획과 다름 1', tone: 'hold' }, summary: 'Node 4 · 작업: 검수 횟수 2→1', open: 'enabled' },
    ],
  },
  {
    caption: '테스트 · 실행 중 12/57 (v7 test.run)',
    lines: [
      { key: 'instruction', name: '지시서', id: '0008.0008-TS', state: { label: '승인', tone: 'success' }, open: 'enabled' },
      { key: 'scenario', name: '시나리오', id: '0008.0008-TS', state: { label: '승인', tone: 'success' }, summary: '케이스 57 · automated 49 · manual 6 · external 2', open: 'enabled' },
      { key: 'report', name: '레포트', id: '0008.0010-TSR', state: { label: '진행 중', tone: 'running' }, summary: 'automated 12/49', progress: 24, open: 'enabled' },
    ],
  },
  {
    caption: '테스트 · 검수 대기 · 결함 3 (v7 test.wait)',
    lines: [
      { key: 'instruction', name: '지시서', id: '0008.0008-TS', state: { label: '승인', tone: 'success' }, open: 'enabled' },
      { key: 'scenario', name: '시나리오', id: '0008.0008-TS', state: { label: '승인', tone: 'success' }, summary: '케이스 57 · automated 49 · manual 6 · external 2', open: 'enabled' },
      { key: 'report', name: '레포트', id: '0008.0010-TSR', state: { label: '결함 3', tone: 'danger' }, summary: '통과 54 · 실패 3', focus: true, open: 'enabled' },
    ],
  },
  {
    caption: '확인 · 반영 보류 (v7 cmid.hold)',
    lines: [{ key: 'result', kind: 'result', name: '확인 결과', state: { label: '보류', tone: 'hold' }, summary: '확인 완료 · Git 반영 보류', nav: { label: 'Git 탭' }, open: 'enabled' }],
  },
  {
    caption: '요구사항 · AI 위임 진행 중 (v7 req.d.run — ON은 시안 가정, API 없음)',
    lines: [
      { key: 'document', name: '요구사항', id: '0008.0001-R', state: { label: '작성 중', tone: 'info' }, open: 'enabled' },
      { key: 'delegation', kind: 'result', name: 'AI 위임', state: { label: '진행 중', tone: 'running' }, summary: 'Node 2/4 · 팀 내 질문 3 해결', progress: 50, open: 'enabled' },
    ],
  },
]
