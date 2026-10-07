<template>
  <!-- Center column: Workspace over the Bottom Dock. The Bottom Dock is a child of this
       column (not of the Body), so it can never reach under the Left / Right Docks. The
       Designer Center session passes through to the Workspace; the Bottom Inspector
       (flowgate.v02.0036) gets the session's selected Node (later, the Minimap its Viewport).
       flowgate.v02.0039 T#3: `unselected` (no work target yet) passes to both. -->
  <div class="designer-center-column" data-test="designer-center-column">
    <DesignerWorkspace :center="center" :unselected="unselected" />
    <DesignerBottomDock :height="bottomHeight" :selected-node="center.activeSelectedNode.value" :unselected="unselected" />
  </div>
</template>

<script setup lang="ts">
import type { DesignerCenterSession } from '../center/designerCenterSession'
import DesignerBottomDock from './DesignerBottomDock.vue'
import DesignerWorkspace from './DesignerWorkspace.vue'

withDefaults(
  defineProps<{
    center: DesignerCenterSession
    bottomHeight: number
    unselected?: boolean
  }>(),
  { unselected: false },
)
</script>

<style scoped>
.designer-center-column {
  display: flex;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  overflow: hidden;
}
</style>
