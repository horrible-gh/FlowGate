<template>
  <!-- Center column: Workspace over the Bottom Dock. The Bottom Dock is a child of this
       column (not of the Body), so it can never reach under the Left / Right Docks. -->
  <div class="designer-center-column" data-test="designer-center-column">
    <DesignerWorkspace :active-flow="activeFlow" />
    <DesignerBottomDock :height="bottomHeight" />
  </div>
</template>

<script setup lang="ts">
import type { FlowRef } from '@/features/project'
import DesignerBottomDock from './DesignerBottomDock.vue'
import DesignerWorkspace from './DesignerWorkspace.vue'

defineProps<{
  activeFlow: FlowRef | null
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
