// flowgate.v02.0026 — sample Graph source (0003-D §3.2.3), the Center session's default until
// a server Graph API exists. Any Flow id (real project Flows included) gets one of a few
// differently shaped sample Graphs, picked from the Flow id so the same Flow always shows the
// same Graph and switching tabs shows a different one. The samples are built directly as
// FlowGate Graph Model values — no imitation of a server response, so no server format is
// decided here. Node / Edge ids carry the Flow id as a prefix so tabs never share ids.
//
// Titles are Korean UI text: the Flow object is never called Flow / 흐름 / 플로 (T0006 §3).
import type { FlowGraph, FlowGraphEdge, FlowGraphNode } from './flowGraph'
import type { FlowGraphSource } from './flowGraphSource'

interface SampleNode {
  key: string
  kind: string
  title: string
  state?: string
  x: number
  y: number
}

interface SampleGraph {
  nodes: SampleNode[]
  edges: Array<[from: string, to: string]>
}

// Free placement with branches and joins — never one row (R0001 §4: no sequence-first UI).
// Columns are 260px apart like 03-designer-shell (176px card + 84px Edge).
const SAMPLES: readonly SampleGraph[] = [
  // Branching progress: one requirement, two investigations, one plan fanning out again.
  {
    nodes: [
      { key: 'n1', kind: 'requirement', title: '로그인 실패 요구사항', state: 'done', x: 0, y: 120 },
      { key: 'n2', kind: 'research', title: '세션 만료 원인 조사', state: 'in_progress', x: 260, y: 10 },
      { key: 'n3', kind: 'research', title: '토큰 갱신 경로 조사', state: 'review', x: 260, y: 230 },
      { key: 'n4', kind: 'plan', title: '수정 계획', state: 'draft', x: 520, y: 120 },
      { key: 'n5', kind: 'task', title: '세션 갱신 수정', x: 780, y: 20 },
      { key: 'n6', kind: 'test', title: '재현 테스트', x: 780, y: 240 },
    ],
    edges: [['n1', 'n2'], ['n1', 'n3'], ['n2', 'n4'], ['n3', 'n4'], ['n4', 'n5'], ['n4', 'n6']],
  },
  // Two tracks joining: server and screen work merge into one test and one approval.
  {
    nodes: [
      { key: 'n1', kind: 'requirement', title: '알림 요구사항', state: 'done', x: 0, y: 0 },
      { key: 'n2', kind: 'design', title: '알림 화면 설계', state: 'review', x: 0, y: 260 },
      { key: 'n3', kind: 'task', title: '알림 서버 작업', state: 'in_progress', x: 260, y: 0 },
      { key: 'n4', kind: 'task', title: '알림 화면 작업', state: 'in_progress', x: 260, y: 260 },
      { key: 'n5', kind: 'test', title: '통합 테스트', state: 'blocked', x: 520, y: 130 },
      { key: 'n6', kind: 'approval', title: '배포 승인', x: 780, y: 130 },
    ],
    edges: [['n1', 'n3'], ['n1', 'n4'], ['n2', 'n4'], ['n3', 'n5'], ['n4', 'n5'], ['n5', 'n6']],
  },
  // A plan splitting into parallel work plus a side memo, joined again for approval.
  {
    nodes: [
      { key: 'n1', kind: 'requirement', title: '검색 개선 요구사항', x: 0, y: 140 },
      { key: 'n2', kind: 'plan', title: '작업계획 수립', state: 'done', x: 260, y: 140 },
      { key: 'n3', kind: 'task', title: '색인 작업', state: 'in_progress', x: 520, y: 0 },
      { key: 'n4', kind: 'task', title: '검색 화면 작업', state: 'draft', x: 520, y: 200 },
      { key: 'n5', kind: 'memo', title: '성능 확인 메모', x: 520, y: 390 },
      { key: 'n6', kind: 'approval', title: '검토 승인', state: 'review', x: 780, y: 100 },
    ],
    edges: [['n1', 'n2'], ['n2', 'n3'], ['n2', 'n4'], ['n2', 'n5'], ['n3', 'n6'], ['n4', 'n6']],
  },
]

/** Stable string hash: the same Flow id always picks the same sample. */
function sampleIndex(flowId: string): number {
  let hash = 0
  for (let i = 0; i < flowId.length; i += 1) hash = (hash * 31 + flowId.charCodeAt(i)) >>> 0
  return hash % SAMPLES.length
}

/** The sample Graph of a Flow, as a fresh FlowGate Graph Model value. */
export function buildFixtureFlowGraph(flowId: string): FlowGraph {
  const sample = SAMPLES[sampleIndex(flowId)]
  const nodeId = (key: string) => `${flowId}:${key}`
  const nodes: FlowGraphNode[] = sample.nodes.map((node) => ({
    id: nodeId(node.key),
    kind: node.kind,
    title: node.title,
    state: node.state ?? null,
    x: node.x,
    y: node.y,
  }))
  const edges: FlowGraphEdge[] = sample.edges.map(([from, to], index) => ({
    id: `${flowId}:e${index + 1}`,
    source: nodeId(from),
    target: nodeId(to),
  }))
  return { flowId, nodes, edges }
}

export const fixtureFlowGraphSource: FlowGraphSource = {
  isSample: true,
  // Async like a real source (same loading path), but with no timer delay.
  loadGraph: async (_projectId: string, flowId: string) => buildFixtureFlowGraph(flowId),
}
