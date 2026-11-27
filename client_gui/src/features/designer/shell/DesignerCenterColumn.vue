<template>
  <!-- Center column: Workspace over the Bottom Dock. The Bottom Dock is a child of this
       column (not of the Body), so it can never reach under the Left / Right Docks. The
       Designer Center session passes through to the Workspace; the Bottom Inspector
       gets the session's selected Node; the earlier change: the Minimap gets the
       active Flow's ready Graph, Viewport and Canvas size, and its moves go back to the session.
       `unselected` (no work target yet) passes to both.
       a resize handle on the Workspace ↔ Bottom Dock line asks the Shell for
       a new Bottom Dock height; the Workspace (and its Canvas) takes the rest.
       the Inspector gets the session's selection context.
       Its Host intents are carried out here — select another Node, read the Flow again quietly —
       or, for a Tool Panel, passed up to the Shell.
       (B1): the Inspector header's card title save goes to the session's saveNodeTitle for the
       Flow and card the edit was opened on (never the active Flow at answer time).
       `bottomHeight` is the height the person chose. When the column is too low
       for it and the Workspace minimum, the Bottom Dock is drawn lower here (fitBottomHeight) and the
       chosen height is left alone. -->
  <div ref="columnRef" class="designer-center-column" data-test="designer-center-column">
    <DesignerWorkspace :center="center" :unselected="unselected" :placing="placing" :move-step="moveStep" @canvas-place="(flowId, x, y) => emit('canvas-place', flowId, x, y)" @cancel-placement="emit('cancel-placement')" @show-tool="emit('show-tool', $event)" @open-document="openCardDocument" />
    <DesignerResizeHandle
      class="designer-center-resize"
      axis="y"
      :size="shownBottomHeight"
      :grow="-1"
      data-test="designer-bottom-resize"
      @resize="emit('resize-bottom', $event, columnRef?.clientHeight ?? 0)"
      @resize-end="emit('resize-end')"
    />
    <DesignerBottomDock
      :height="shownBottomHeight"
      :selected-node="center.activeSelectedNode.value"
      :unselected="unselected"
      :minimap="minimap"
      @minimap-viewport="onMiniMapViewport"
    />
    <!-- 0078 · 0015-T A-2: the Flow terminal in 디자이너 mode fills everything under the Flow tab strip — the Canvas and
         the Inspector · Minimap places — as one area (no split, no height bar). It lies over them; they stay mounted and
         come back as they were. The terminal's screen is moved in here by its window (Teleport). -->
    <div
      v-show="terminalFillActive"
      ref="terminalFillPlace"
      class="designer-terminal-fill"
      :style="{ top: `${tabBarBottom}px` }"
      data-test="designer-terminal-fill"
    />
  </div>
</template>

<script setup lang="ts">
import { computed, inject, nextTick, onBeforeUnmount, onMounted, ref, watch, watchEffect } from 'vue'
import { toInspectorTarget } from '../inspector/inspectorTarget'
import { openMergeWindow } from '../inspector/features/merge/mergeWindows'
import { OPEN_CARD_EVENT } from '../inspector/features/verification/verificationModel'
import { openDocument } from '../inspector/openDocument'
import { requestTestCardScenario } from '../inspector/features/testSpec/testCardSession'
import { cardDocumentButtons, type CardDocumentOpen } from '../center/canvas/cardDocumentButtons'
import type { CanvasViewport } from '../center/canvasView'
import type { DesignerCenterSession } from '../center/designerCenterSession'
import type { FlowGraph, FlowGraphNode, FlowGraphNodeRun } from '../center/graph/flowGraph'
import DesignerBottomDock from './DesignerBottomDock.vue'
import DesignerResizeHandle from './DesignerResizeHandle.vue'
import { provide } from 'vue'
import DesignerWorkspace from './DesignerWorkspace.vue'
import { fitBottomHeight } from './designerLayout'

const props = withDefaults(
  defineProps<{
    center: DesignerCenterSession
    bottomHeight: number
    placing?: boolean
    unselected?: boolean
    /** the density's card drag step (the Shell's), for the Canvas; null = freeform. */
    moveStep?: number | null
  }>(),
  { unselected: false, placing: false, moveStep: null },
)

// Each drag step carries the column's own height, which the Shell's clamp keeps the Workspace's room in.
const emit = defineEmits<{
  'resize-bottom': [height: number, columnHeight: number]
  /** The bottom handle was let go. */
  'resize-end': []
  'canvas-place': [flowId: string, x: number, y: number]
  'cancel-placement': []
  'show-tool': [tool: 'git']
}>()

// the Bottom Dock's Inspector reads the session's selection context, and its Host
// intents (inspector/inspectorHost.ts) are carried out here, about the active Flow — or, for a
// refresh-flow naming its Flow, that open Flow even when another tab is active. Provided
// (the Dock injects the same key) so the Dock's props and events stay as they are.
// flowgate.v02test.0042 (mockup v7): "open this card's '병합 · 충돌' tab" — the Canvas card's 병합 충돌, a notification's [열기],
// the Git panel's [카드로 이동] and the tab's own [‘…’ 카드로 이동] send one window event; here the card's Flow is shown
// (opened when it is not), the card selected and the Inspector asked for the tab. A Git session names its merge id
// instead of a card: the card is the one whose node run's integration reason is that session (`merge_session:<id>`).
const pendingMerge = ref<{ flowId: string; nodeKey?: string; mergeId?: number } | null>(null)

function cardOfMergeSession(flowId: string, mergeId: number): string | null {
  const load = props.center.state.graphs?.[flowId]
  if (load?.status !== 'ready') return null
  const reason = `merge_session:${mergeId}`
  const nodes = (load.graph as FlowGraph).nodes
  const marks = props.center.runMarks(flowId).nodeRuns
  const node = nodes.find((item) => {
    const run = marks.get(item.id) as { integrationReason?: string | null } | undefined
    return (run && 'integrationReason' in run ? run.integrationReason : item.nodeRun?.integrationReason) === reason
  })
  return node?.id ?? null
}

function onOpenCardMerge(event: Event): void {
  const detail = (event as CustomEvent<{ flowId?: unknown; nodeKey?: unknown; mergeId?: unknown; flowName?: unknown }>).detail ?? {}
  const flowId = typeof detail.flowId === 'string' && detail.flowId ? detail.flowId : null
  const projectId = props.center.state.projectId
  if (!flowId || projectId === null) return
  if (props.center.state.tabs.some((tab) => tab.flowId === flowId)) props.center.activateFlow(flowId)
  else props.center.openFlow({ projectId, flowId, flowName: typeof detail.flowName === 'string' ? detail.flowName : flowId })
  pendingMerge.value = { flowId, nodeKey: typeof detail.nodeKey === 'string' ? detail.nodeKey : undefined,
    mergeId: typeof detail.mergeId === 'number' ? detail.mergeId : undefined }
}

watchEffect(() => {
  const request = pendingMerge.value
  const projectId = props.center.state.projectId
  if (!request || projectId === null) return
  const { flowId } = request
  const nodeId = request.nodeKey || (request.mergeId !== undefined ? cardOfMergeSession(flowId, request.mergeId) : null)
  const load = props.center.state.graphs?.[flowId]
  if (!nodeId || load?.status !== 'ready') return
  const node = load.graph.nodes.find(item => item.id === nodeId)
  if (!node) return
  const initial = toInspectorTarget({ projectId, graph: load.graph as FlowGraph, node: node as FlowGraphNode })!
  props.center.selectNode(flowId, nodeId)
  openMergeWindow(() => {
    const latest = props.center.state.graphs?.[flowId]
    const current = latest?.status === 'ready' ? latest.graph.nodes.find(item => item.id === nodeId) : null
    if (!current || latest?.status !== 'ready') return initial
    const run = props.center.runMarks(flowId).nodeRuns.get(nodeId)
    return toInspectorTarget({ projectId, graph: latest.graph as FlowGraph, node: { ...current, nodeRun: (run ? { ...current.nodeRun, ...run } : current.nodeRun) as FlowGraphNodeRun | null } })!
  }, () => props.center.refreshFlow(flowId))
  pendingMerge.value = null
})

// flowgate.v02test.0052 (0008 task 1·4·6): the Git tab's [확인 카드로 이동] — show the card's Flow (opened when it is not) and
// select its check card, whose Inspector holds [승인]. Same event name as `SELECT_GATE_NODE_EVENT` of the Git part (its gateOperations).
function onSelectGateNode(event: Event): void {
  const detail = (event as CustomEvent<{ flowId?: unknown; nodeKey?: unknown; flowName?: unknown }>).detail ?? {}
  const flowId = typeof detail.flowId === 'string' && detail.flowId ? detail.flowId : null
  const nodeKey = typeof detail.nodeKey === 'string' && detail.nodeKey ? detail.nodeKey : null
  const projectId = props.center.state.projectId
  if (!flowId || !nodeKey || projectId === null) return
  if (props.center.state.tabs.some((tab) => tab.flowId === flowId)) props.center.activateFlow(flowId)
  else props.center.openFlow({ projectId, flowId, flowName: typeof detail.flowName === 'string' ? detail.flowName : flowId })
  props.center.selectNode(flowId, nodeKey)
}

onMounted(() => {
  window.addEventListener('fg:open_card_merge', onOpenCardMerge)
  window.addEventListener('fg:select_gate_node', onSelectGateNode)
})
onBeforeUnmount(() => {
  window.removeEventListener('fg:open_card_merge', onOpenCardMerge)
  window.removeEventListener('fg:select_gate_node', onSelectGateNode)
})

// flowgate.v02test.0053 1-D: "show this card" — the `검증` line's [→] to the card ahead when it is in another Flow of the
// open project (the same Flow goes through the Inspector's select-node). The Flow is activated (opened when it is not)
// and the card selected once its Graph is shown; a card of another project is not shown here.
const pendingCard = ref<{ flowId: string; nodeKey: string } | null>(null)

function onOpenCard(event: Event): void {
  const detail = (event as CustomEvent<{ projectId?: unknown; flowId?: unknown; nodeKey?: unknown; flowName?: unknown }>).detail ?? {}
  const projectId = props.center.state.projectId
  const flowId = typeof detail.flowId === 'string' && detail.flowId ? detail.flowId : null
  const nodeKey = typeof detail.nodeKey === 'string' && detail.nodeKey ? detail.nodeKey : null
  if (!flowId || !nodeKey || projectId === null) return
  if (typeof detail.projectId === 'string' && detail.projectId !== projectId) return
  if (props.center.state.tabs.some((tab) => tab.flowId === flowId)) props.center.activateFlow(flowId)
  else props.center.openFlow({ projectId, flowId, flowName: typeof detail.flowName === 'string' ? detail.flowName : flowId })
  pendingCard.value = { flowId, nodeKey }
}

watchEffect(() => {
  const request = pendingCard.value
  if (!request) return
  const load = props.center.state.graphs?.[request.flowId]
  if (!load || load.status === 'loading') return
  if (load.status === 'ready' && load.graph.nodes.some((item) => item.id === request.nodeKey)) {
    props.center.selectNode(request.flowId, request.nodeKey)
  }
  pendingCard.value = null
})

onMounted(() => window.addEventListener(OPEN_CARD_EVENT, onOpenCard))
onBeforeUnmount(() => window.removeEventListener(OPEN_CARD_EVENT, onOpenCard))

provide(Symbol.for('flowgate.designer.bottomDockInspector'), {
  selection: () => props.center.activeSelection.value,
  saveTitle: props.center.canEditNodeTitles
    ? (flowId: string, nodeId: string, title: string) => props.center.saveNodeTitle(flowId, nodeId, title)
    : null,
  // a work plan window follows its own Flow's run lock, also when another Flow is active.
  flowRunLock: (flowId: string) => props.center.runMarks(flowId).lock,
  // and its WP card's latest node run — the run read's (the latest) over the Canvas read's, the
  // bound result and the plan line from the Canvas read. Null while the Flow's Graph is not shown.
  cardRun(flowId: string, nodeKey: string) {
    const load = props.center.state.graphs?.[flowId]
    const node = load?.status === 'ready' ? (load.graph as FlowGraph).nodes.find((item) => item.id === nodeKey) : undefined
    if (!node) return null
    const run = props.center.runMarks(flowId).nodeRuns.get(nodeKey)
    return {
      status: run?.status ?? node.nodeRun?.status ?? null,
      resultDocId: node.nodeRun?.resultDocId ?? null,
      runningExecutionCount: run?.runningExecutionCount ?? node.nodeRun?.runningExecutionCount ?? 0,
      lineDocId: node.cardDocuments?.find((line) => line.slot === 'plan')?.docId ?? null,
    }
  },
  host(intent: { readonly kind: string; readonly nodeId?: string; readonly tool?: string; readonly flowId?: string; readonly originClientId?: string }) {
    const flowId = props.center.state.activeFlowId
    if (intent.kind === 'select-node' && flowId !== null && intent.nodeId) props.center.selectNode(flowId, intent.nodeId)
    else if (intent.kind === 'refresh-flow') {
      const readFlowId = intent.flowId ?? flowId
      if (readFlowId !== null) {
        if (intent.originClientId) props.center.refreshFlow(readFlowId, { originClientId: intent.originClientId })
        else props.center.refreshFlow(readFlowId)
      }
    } else if (intent.kind === 'show-tool' && intent.tool === 'git') emit('show-tool', intent.tool)
  },
})

function openCardDocument(flowId: string, requested: CardDocumentOpen) {
  const load = props.center.state.graphs[flowId]
  if (load?.status !== 'ready') return
  const node = load.graph.nodes.find(item => item.id === requested.nodeId)
  if (!node) return
  const doc = cardDocumentButtons(node).find(item => item.docId === requested.docId && !!item.scenario === !!requested.scenario)
  if (!doc) return
  // F81-9: a TS document always opens as the scenario — the card is selected and its scenario window opens (the
  // Inspector's TestCardWindowHost), never a 지시서 document window.
  if (doc.scenario) {
    const projectId = props.center.state.projectId
    if (projectId) requestTestCardScenario({ projectId, flowId, nodeKey: node.id })
    props.center.selectNode(flowId, node.id)
    return
  }
  const ownerSlot = doc.ownerSlot
  const card = node.definition && ownerSlot ? {
    projectId: props.center.state.projectId!, flowId, nodeKey: node.id,
    ownerSlot, openedDocId: doc.docId,
  } : null
  openDocument(doc.docId, 'view', { card })
}

const columnRef = ref<HTMLElement | null>(null)

// The column's own height, followed while the window changes size. 0 until it is
// measured (and where there is no layout), which draws the chosen height as it is.
const measuredHeight = ref(0)
let columnObserver: ResizeObserver | null = null

function measureColumn(): void {
  measuredHeight.value = columnRef.value?.clientHeight ?? 0
  measureTabBar()
}

// 0078 · 0015-T A: the place a Flow terminal fills in 디자이너 mode, under the Flow tab strip. The bridge is the Shell's
// (null when the column is mounted without it: no place is given).
interface TerminalFillBridge {
  readonly active: (flowId: string | null) => boolean
  readonly setTarget: (el: HTMLElement | null) => void
}
const terminalFill = inject<TerminalFillBridge | null>(Symbol.for('flowgate.designer.terminalFill'), null)
const terminalFillPlace = ref<HTMLElement | null>(null)
const terminalFillActive = computed(() => terminalFill?.active(props.center.state.activeFlowId) ?? false)
/** Where the Flow tab strip ends, from the column's top (0 while it is not drawn). */
const tabBarBottom = ref(0)
function measureTabBar(): void {
  const column = columnRef.value
  const bar = column?.querySelector<HTMLElement>('[data-test="designer-flow-tab-bar"]')
  tabBarBottom.value = column && bar ? Math.max(0, Math.round(bar.getBoundingClientRect().bottom - column.getBoundingClientRect().top)) : 0
}
watch(terminalFillActive, (on) => { if (on) void nextTick(measureTabBar) })
onMounted(() => terminalFill?.setTarget(terminalFillPlace.value))
onBeforeUnmount(() => terminalFill?.setTarget(null))

onMounted(() => {
  measureColumn()
  if (typeof ResizeObserver !== 'undefined' && columnRef.value) {
    columnObserver = new ResizeObserver(measureColumn)
    columnObserver.observe(columnRef.value)
  }
})
onBeforeUnmount(() => {
  columnObserver?.disconnect?.()
  columnObserver = null
})

const shownBottomHeight = computed(() => fitBottomHeight(props.bottomHeight, measuredHeight.value))

// the Minimap reads the session's active Flow only — a Graph that is not ready
// (loading, error, no tab) draws nothing, so another Flow's map never stays on screen.
const minimap = computed(() => {
  const { center } = props
  const load = center.activeGraphLoad.value
  return {
    flowId: center.state.activeFlowId,
    graph: load?.status === 'ready' ? (load.graph as FlowGraph) : null,
    viewport: center.activeView.value?.viewport ?? null,
    canvasSize: center.canvasSize.value,
    // the lines join the sides the Canvas joins, at the cards' measured sizes.
    nodeSizes: center.activeNodeSizes.value,
  }
})

function onMiniMapViewport(viewport: CanvasViewport, commit: boolean) {
  const flowId = props.center.state.activeFlowId
  if (flowId === null) return
  if (commit) props.center.commitViewport(flowId, viewport)
  else props.center.recordViewport(flowId, viewport)
}
</script>

<style scoped>
.designer-center-column {
  position: relative;
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  overflow: hidden;
}

/* 0015-T A-2: the Flow terminal's 디자이너 place — everything under the Flow tabs, over the Canvas and the bottom Dock. */
.designer-terminal-fill {
  position: absolute;
  right: 0;
  bottom: 0;
  left: 0;
  /* above the Canvas (Vue Flow layers), the resize handle and the bottom Dock; floating windows stay above it. */
  z-index: 30;
  min-width: 0;
  min-height: 0;
  overflow: hidden;
}

/* Takes no height of its own: it straddles the Bottom Dock's top border. */
.designer-center-column > .designer-center-resize {
  flex: none;
  margin: calc(var(--fg2-splitter-size) / -2) 0;
}
</style>
