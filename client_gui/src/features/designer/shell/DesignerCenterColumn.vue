<template>
  <!-- Center column: Workspace over the Bottom Dock. The Bottom Dock is a child of this
       column (not of the Body), so it can never reach under the Left / Right Docks. The
       Designer Center session passes through to the Workspace (and, later, to the Bottom
       Inspector / Minimap). -->
  <div class="designer-center-column" data-test="designer-center-column">
    <DesignerWorkspace :center="center" />
    <DesignerBottomDock :height="bottomHeight" />
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
