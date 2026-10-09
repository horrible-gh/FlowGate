<template>
  <!-- Final container for the center work area. flowgate.v02.0026: holds the Designer Center
       (Flow Tabs + read-only Canvas); the tab strip and the Canvas scroll / pan themselves.
       flowgate.v02.0039 T#3: with no work target the work area is an empty frame — no Flow
       tabs, no guide text, no Canvas (confirmed initial-state design). The Center session
       lives in the Shell, so nothing is lost while the frame is shown. -->
  <section class="designer-workspace" data-test="designer-workspace">
    <div v-if="unselected" class="designer-workspace-frame" data-test="designer-center-unselected" />
    <DesignerCenter v-else :center="center" :placing="placing" @canvas-place="(flowId, x, y) => emit('canvas-place', flowId, x, y)" @cancel-placement="emit('cancel-placement')" />
  </section>
</template>

<script setup lang="ts">
import DesignerCenter from '../center/DesignerCenter.vue'
import type { DesignerCenterSession } from '../center/designerCenterSession'

withDefaults(
  defineProps<{
    center: DesignerCenterSession
    placing?: boolean
    /** No work target yet (0039 T#3): show the empty frame instead of the Designer Center. */
    unselected?: boolean
  }>(),
  { unselected: false, placing: false },
)

const emit = defineEmits<{
  'canvas-place': [flowId: string, x: number, y: number]
  'cancel-placement': []
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

.designer-workspace-frame {
  flex: 1 1 auto;
  min-width: 0;
  min-height: 0;
  background: #081421;
}
</style>
