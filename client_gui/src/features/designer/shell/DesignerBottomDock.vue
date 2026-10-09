<template>
  <!-- Final container for the bottom Dock. Lives inside the CenterColumn, so it is only ever
       as wide as the Center. flowgate.v02.0036 (0035 v7): the Inspector on the left and the
       Minimap on the right, 7 : 3. flowgate.v02.0049: the real Minimap of the active Flow.
       flowgate.v02.0039 T#3: with no work target the Inspector keeps only its frame and the
       Minimap stays on screen dimmed and unavailable (confirmed initial-state design). -->
  <section
    class="designer-bottom-dock"
    data-test="designer-bottom-dock"
    :style="{ height: `${height}px`, gridTemplateColumns: DESIGNER_BOTTOM_DOCK_COLUMNS }"
  >
    <DesignerInspector :node="selectedNode" :unselected="unselected" />
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
