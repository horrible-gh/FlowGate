<template>
  <!-- Final container for the center work area. flowgate.v02.0026: holds the Designer Center
       (Flow Tabs + read-only Canvas); the tab strip and the Canvas scroll / pan themselves. -->
  <section class="designer-workspace" data-test="designer-workspace">
    <DesignerCenter :center="center" />
  </section>
</template>

<script setup lang="ts">
import DesignerCenter from '../center/DesignerCenter.vue'
import type { DesignerCenterSession } from '../center/designerCenterSession'

defineProps<{
  center: DesignerCenterSession
}>()
</script>

<style scoped>
.designer-workspace {
  display: flex;
  flex: 1 1 auto;
  flex-direction: column;
  min-width: 0;
  min-height: 0;
  overflow: hidden;
}
</style>
