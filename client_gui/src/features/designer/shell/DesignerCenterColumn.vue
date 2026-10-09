<template>
  <!-- Center column: Workspace over the Bottom Dock. The Bottom Dock is a child of this
       column (not of the Body), so it can never reach under the Left / Right Docks. The
       Designer Center session passes through to the Workspace; the Bottom Inspector
       (flowgate.v02.0036) gets the session's selected Node (later, the Minimap its Viewport).
       flowgate.v02.0039 T#3: `unselected` (no work target yet) passes to both.
       flowgate.v02.0046: a resize handle on the Workspace ↔ Bottom Dock line asks the Shell for
       a new Bottom Dock height; the Workspace (and its Canvas) takes the rest.
       flowgate.v02.0048 T#1 (D#1 §4.2 / §5.7): the Inspector gets the session's selection context.
       Its Host intents are carried out here — select another Node, read the Flow again quietly —
       or, for a Tool Panel, passed up to the Shell. -->
  <div ref="columnRef" class="designer-center-column" data-test="designer-center-column">
    <DesignerWorkspace :center="center" :unselected="unselected" />
    <DesignerResizeHandle
      class="designer-center-resize"
      axis="y"
      :size="bottomHeight"
      :grow="-1"
      data-test="designer-bottom-resize"
      @resize="emit('resize-bottom', $event, columnRef?.clientHeight ?? 0)"
    />
    <DesignerBottomDock :height="bottomHeight" :selected-node="center.activeSelectedNode.value" :unselected="unselected" />
  </div>
</template>

<script setup lang="ts">
import { ref } from 'vue'
import type { DesignerCenterSession } from '../center/designerCenterSession'
import DesignerBottomDock from './DesignerBottomDock.vue'
import DesignerResizeHandle from './DesignerResizeHandle.vue'
import { provide } from 'vue'
import DesignerWorkspace from './DesignerWorkspace.vue'

const props = withDefaults(
  defineProps<{
    center: DesignerCenterSession
    bottomHeight: number
    unselected?: boolean
  }>(),
  { unselected: false },
)

// Each drag step carries the column's own height, which the Shell's clamp keeps the Workspace's room in.
const emit = defineEmits<{ 'resize-bottom': [height: number, columnHeight: number]; 'show-tool': [tool: 'git'] }>()

// flowgate.v02.0048 T#1: the Bottom Dock's Inspector reads the session's selection context, and its Host
// intents (inspector/inspectorHost.ts) are carried out here, always about the active Flow. Provided
// (the Dock injects the same key) so the Dock's props and events stay as they are.
provide(Symbol.for('flowgate.designer.bottomDockInspector'), {
  selection: () => props.center.activeSelection.value,
  host(intent: { readonly kind: string; readonly nodeId?: string; readonly tool?: string }) {
    const flowId = props.center.state.activeFlowId
    if (intent.kind === 'select-node' && flowId !== null && intent.nodeId) props.center.selectNode(flowId, intent.nodeId)
    else if (intent.kind === 'refresh-flow' && flowId !== null) props.center.refreshFlow(flowId)
    else if (intent.kind === 'show-tool' && intent.tool === 'git') emit('show-tool', intent.tool)
  },
})

const columnRef = ref<HTMLElement | null>(null)
</script>

<style scoped>
.designer-center-column {
  display: flex;
  flex-direction: column;
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
