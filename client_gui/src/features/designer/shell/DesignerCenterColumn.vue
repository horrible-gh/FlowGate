<template>
  <!-- Center column: Workspace over the Bottom Dock. The Bottom Dock is a child of this
       column (not of the Body), so it can never reach under the Left / Right Docks. The
       Designer Center session passes through to the Workspace; the Bottom Inspector
       (flowgate.v02.0036) gets the session's selected Node (later, the Minimap its Viewport). -->
  <div class="designer-center-column" data-test="designer-center-column">
    <DesignerWorkspace :center="center" />
    <DesignerBottomDock :height="bottomHeight" :selected-node="center.activeSelectedNode.value" />
  </div>
</template>

<script setup lang="ts">
import type { DesignerCenterSession } from '../center/designerCenterSession'
import DesignerBottomDock from './DesignerBottomDock.vue'
import DesignerWorkspace from './DesignerWorkspace.vue'

defineProps<{
  center: DesignerCenterSession
  bottomHeight: number
}>()
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
