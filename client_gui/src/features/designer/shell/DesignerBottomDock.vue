<template>
  <!-- Final container for the bottom Dock. Lives inside the CenterColumn, so it is only ever
       as wide as the Center. flowgate.v02.0036 (0035 v7): the Inspector on the left and the
       Minimap on the right, 7 : 3. flowgate.v02.0049: the real Minimap of the active Flow.
       flowgate.v02.0039 T#3: with no work target the Inspector keeps only its frame and the
       Minimap stays on screen dimmed and unavailable (confirmed initial-state design).
       flowgate.v02.0048 T#1 (D#1 §4.2 / §5.7): the Inspector gets the selection context and its
       Host intents are passed up unchanged — the Dock knows no Inspector feature.
       flowgate.v02.0070 T#1: so is the card title save the Center column gives (the Inspector header's).
       flowgate.v02.0072 TR0012 rev1: and the session's run lock of any Flow (the work plan windows').
       flowgate.v02.0083 T#3: and the session's latest node run of any shown card (a WP card's plan window). -->
  <section
    class="designer-bottom-dock"
    data-test="designer-bottom-dock"
    :style="{ height: `${height}px`, gridTemplateColumns: DESIGNER_BOTTOM_DOCK_COLUMNS }"
  >
    <DesignerInspector
      :selection="inspector?.selection() ?? null"
      :unselected="unselected"
      :save-title="inspector?.saveTitle ?? null"
      :flow-run-lock="inspector?.flowRunLock ?? null"
      :card-run="inspector?.cardRun ?? null"
      @host="inspector?.host($event)"
    />
    <aside
      class="designer-bottom-minimap"
      :class="{ 'is-unavailable': unselected }"
      :aria-disabled="unselected ? 'true' : undefined"
      data-test="designer-bottom-minimap"
    >
      <DesignerMiniMap
        :flow-id="minimap.flowId"
        :graph="minimap.graph"
        :viewport="minimap.viewport"
        :canvas-size="minimap.canvasSize"
        :node-sizes="minimap.nodeSizes ?? null"
        :selected-node-id="selectedNode?.id ?? null"
        @viewport-change="emit('minimap-viewport', $event, false)"
        @viewport-commit="emit('minimap-viewport', $event, true)"
      />
    </aside>
  </section>
</template>

<script setup lang="ts">
import type { CanvasSize, CanvasViewport } from '../center/canvasView'
import type { FlowGraph, FlowGraphNode } from '../center/graph/flowGraph'
import DesignerInspector from '../inspector/DesignerInspector.vue'
import DesignerMiniMap from '../minimap/DesignerMiniMap.vue'
import { DESIGNER_BOTTOM_DOCK_COLUMNS } from './designerLayout'

/** flowgate.v02.0049: what the Minimap draws — the active Flow's ready Graph and Canvas view. */
interface BottomDockMiniMap {
  flowId: string | null
  graph: FlowGraph | null
  viewport: CanvasViewport | null
  canvasSize: CanvasSize | null
  /** flowgate.v02.0068 T#2: the cards' measured sizes (absent / null: the card size). */
  nodeSizes?: Readonly<Record<string, { readonly width: number; readonly height: number }>> | null
}

withDefaults(
  defineProps<{
    /** bottomHeight from the Shell's layout state. */
    height: number
    /** The selected Node of the active Flow (Center session), or null. */
    selectedNode: FlowGraphNode | null
    /** No work target yet (0039 T#3). */
    unselected?: boolean
    /** flowgate.v02.0049: the Minimap's input (nothing to draw when omitted). */
    minimap?: BottomDockMiniMap
  }>(),
  { unselected: false, minimap: () => ({ flowId: null, graph: null, viewport: null, canvasSize: null }) },
)
// flowgate.v02.0048 T#1: the Center column provides the Inspector's selection context and the place
// its Host intents go (inspector/inspectorHost.ts, which the Shell does not import — structure.0025),
// so the Dock's props and events stay as they are. Null when the Dock is mounted without it.
import { inject } from 'vue'
import type { DesignerCenterSession } from '../center/designerCenterSession'
import type { FlowGraphSelection } from '../center/graph/flowGraph'
interface BottomDockInspector {
  readonly selection: () => FlowGraphSelection | null
  readonly host: (intent: { readonly kind: string; readonly nodeId?: string; readonly tool?: string }) => void
  /** flowgate.v02.0070 T#1: the card title save (the session's, for the Flow and card given); null = none. */
  readonly saveTitle?: DesignerCenterSession['saveNodeTitle'] | null
  /** flowgate.v02.0072 TR0012 rev1: any open Flow's run lock now (the session's run marks), for the work plan windows. */
  readonly flowRunLock?: ((flowId: string) => 'run' | 'unknown' | null) | null
  /** flowgate.v02.0083 T#3: any shown card's latest node run (the session's), for a WP card's plan window. */
  readonly cardRun?:
    | ((flowId: string, nodeKey: string) => {
        readonly status: string | null
        readonly resultDocId: string | null
        readonly runningExecutionCount: number
        readonly lineDocId: string | null
      } | null)
    | null
}
const inspector = inject<BottomDockInspector | null>(Symbol.for('flowgate.designer.bottomDockInspector'), null)

// flowgate.v02.0049: the Minimap moved the shown area (`commit`: the press ended).
const emit = defineEmits<{ 'minimap-viewport': [viewport: CanvasViewport, commit: boolean] }>()
</script>

<style scoped>
.designer-bottom-dock {
  display: grid;
  flex: none;
  grid-template-rows: minmax(0, 1fr);
  min-width: 0;
  min-height: 0;
  overflow: hidden;
  border-top: 1px solid var(--fg2-line-strong);
  background: var(--fg2-panel);
}

.designer-bottom-minimap {
  min-width: 0;
  min-height: 0;
  overflow: hidden;
  border-left: 1px solid var(--fg2-line);
}

.designer-bottom-minimap.is-unavailable {
  opacity: 0.46;
  filter: saturate(0.55);
  pointer-events: none;
  user-select: none;
}
</style>
