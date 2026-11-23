<template>
  <!-- Final container for the bottom Dock. Lives inside the CenterColumn, so it is only ever
       as wide as the Center. the earlier change (v7): the Inspector on the left and the
       Minimap on the right, 7: 3. the earlier change: the real Minimap of the active Flow.
       with no work target the Inspector keeps only its frame and the
       Minimap stays on screen dimmed and unavailable (confirmed initial-state design).
       the Inspector gets the selection context and its
       Host intents are passed up unchanged — the Dock knows no Inspector feature.
       so is the card title save the Center column gives (the Inspector header's).
       and the session's run lock of any Flow (the work plan windows').
       and the session's latest node run of any shown card (a WP card's plan window).
       0078 · 0009-T: the left 7 is shared by the Inspector and the open Flow's docked terminal — when that Flow's
       terminal is docked an Inspector | 터미널 switch shows above them (the Minimap keeps its 3). The terminal itself
       is moved into `terminalPlace` by its window (Teleport); the Dock only gives the place and the switch, through
       the terminal bridge the Shell provides (no terminal import here). -->
  <section
    class="designer-bottom-dock"
    data-test="designer-bottom-dock"
    :style="{ height: `${height}px`, gridTemplateColumns: DESIGNER_BOTTOM_DOCK_COLUMNS }"
  >
    <div class="designer-bottom-main" data-test="designer-bottom-main" :data-pane="pane ?? 'inspector'">
      <div v-if="pane" class="designer-bottom-switch" role="tablist" :aria-label="labels?.switchLabel" data-test="designer-bottom-switch">
        <button
          v-for="item in (['inspector', 'terminal'] as const)"
          :key="item"
          type="button"
          role="tab"
          class="designer-bottom-switch-tab"
          :class="{ 'is-active': pane === item }"
          :aria-selected="pane === item ? 'true' : 'false'"
          :data-test="`designer-bottom-tab-${item}`"
          @click="choose(item)"
        >{{ labels?.[item] }}</button>
      </div>
      <div v-show="pane !== 'terminal'" class="designer-bottom-pane" data-test="designer-bottom-inspector-pane">
        <DesignerInspector
          :selection="inspector?.selection() ?? null"
          :unselected="unselected"
          :save-title="inspector?.saveTitle ?? null"
          :flow-run-lock="inspector?.flowRunLock ?? null"
          :card-run="inspector?.cardRun ?? null"
          @host="inspector?.host($event)"
        />
      </div>
      <div v-show="pane === 'terminal'" ref="terminalPlace" class="designer-bottom-pane designer-bottom-terminal" data-test="designer-bottom-terminal" />
    </div>
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

/** what the Minimap draws — the active Flow's ready Graph and Canvas view. */
interface BottomDockMiniMap {
  flowId: string | null
  graph: FlowGraph | null
  viewport: CanvasViewport | null
  canvasSize: CanvasSize | null
  /** the cards' measured sizes (absent / null: the card size). */
  nodeSizes?: Readonly<Record<string, { readonly width: number; readonly height: number }>> | null
}

const props = withDefaults(
  defineProps<{
    /** bottomHeight from the Shell's layout state. */
    height: number
    /** The selected Node of the active Flow (Center session), or null. */
    selectedNode: FlowGraphNode | null
    /** No work target yet. */
    unselected?: boolean
    /** the Minimap's input (nothing to draw when omitted). */
    minimap?: BottomDockMiniMap
  }>(),
  { unselected: false, minimap: () => ({ flowId: null, graph: null, viewport: null, canvasSize: null }) },
)
// the Center column provides the Inspector's selection context and the place
// its Host intents go (inspector/inspectorHost.ts, which the Shell does not import — structure.0025),
// so the Dock's props and events stay as they are. Null when the Dock is mounted without it.
import { computed, inject, onBeforeUnmount, onMounted, ref } from 'vue'
import type { DesignerCenterSession } from '../center/designerCenterSession'
import type { FlowGraphSelection } from '../center/graph/flowGraph'
interface BottomDockInspector {
  readonly selection: () => FlowGraphSelection | null
  /** flowgate.v02test.0042: the last request to show one card's Inspector tab (its '병합 · 충돌'), or null. */
  readonly host: (intent: { readonly kind: string; readonly nodeId?: string; readonly tool?: string }) => void
  /** the card title save (the session's, for the Flow and card given); null = none. */
  readonly saveTitle?: DesignerCenterSession['saveNodeTitle'] | null
  /** any open Flow's run lock now (the session's run marks), for the work plan windows. */
  readonly flowRunLock?: ((flowId: string) => 'run' | 'unknown' | 'archived' | null) | null
  /** any shown card's latest node run (the session's), for a WP card's plan window. */
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

// 0078 · 0009-T: the Flow terminal's bridge (the Shell provides it over the terminal Host). Null when the Dock is
// mounted without it: the Inspector alone, as before.
type BottomDockPane = 'inspector' | 'terminal'
interface BottomDockTerminal {
  /** What the left part shows for the Flow: null = no docked terminal (the Inspector alone, no switch). */
  readonly pane: (flowId: string | null) => BottomDockPane | null
  readonly select: (flowId: string, pane: BottomDockPane) => void
  /** The terminal place of this Dock (null when it goes away). */
  readonly setTarget: (el: HTMLElement | null) => void
  readonly labels: () => { readonly inspector: string; readonly terminal: string; readonly switchLabel: string } | null
}
const terminal = inject<BottomDockTerminal | null>(Symbol.for('flowgate.designer.bottomDockTerminal'), null)
const terminalPlace = ref<HTMLElement | null>(null)
const activeFlowId = computed(() => props.minimap.flowId)
const pane = computed(() => terminal?.pane(activeFlowId.value) ?? null)
const labels = computed(() => terminal?.labels() ?? null)
function choose(next: BottomDockPane) {
  const flowId = activeFlowId.value
  if (flowId) terminal?.select(flowId, next)
}
onMounted(() => terminal?.setTarget(terminalPlace.value))
onBeforeUnmount(() => terminal?.setTarget(null))

// the Minimap moved the shown area (`commit`: the press ended).
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

.designer-bottom-main {
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  overflow: hidden;
}

/* The Inspector | 터미널 switch (only while the open Flow's terminal is docked). */
.designer-bottom-switch {
  display: flex;
  flex: none;
  gap: 2px;
  height: 26px;
  padding: 0 6px;
  align-items: flex-end;
  border-bottom: 1px solid var(--fg2-line);
  background: var(--fg2-panel);
}

.designer-bottom-switch-tab {
  height: 22px;
  padding: 0 12px;
  border: 1px solid transparent;
  border-bottom: none;
  border-radius: 5px 5px 0 0;
  background: transparent;
  color: var(--fg2-text-muted, var(--fg2-control-fg));
  font: inherit;
  font-size: 11px;
  cursor: pointer;
}

.designer-bottom-switch-tab.is-active {
  border-color: var(--fg2-line);
  background: var(--fg2-surface, var(--fg2-panel));
  color: var(--fg2-text);
  font-weight: 650;
}

/* Each pane takes the rest of the left part as the Inspector took its grid cell before. */
.designer-bottom-pane {
  display: grid;
  flex: 1 1 auto;
  grid-template-rows: minmax(0, 1fr);
  grid-template-columns: minmax(0, 1fr);
  min-width: 0;
  min-height: 0;
  overflow: hidden;
}

/* The docked terminal fills it (absolutely placed, so its size follows the Dock's height and the column's width). */
.designer-bottom-terminal {
  position: relative;
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
