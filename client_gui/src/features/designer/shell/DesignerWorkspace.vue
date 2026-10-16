<template>
  <!-- Final container for the center work area. flowgate.v02.0026: holds the Designer Center
       (Flow Tabs + read-only Canvas); the tab strip and the Canvas scroll / pan themselves.
       flowgate.v02.0039 T#3: with no work target the work area is an empty frame — no Flow
       tabs, no guide text, no Canvas (confirmed initial-state design). The Center session
       lives in the Shell, so nothing is lost while the frame is shown.
       flowgate.v02.0066 T#6: while the Shell's Center Diff has a file open, the Workspace draws it and only hides the
       Designer Center (still mounted, so the Flow tabs and Canvas come back as they were). No Center Diff given, or
       none open, draws exactly as before; with no work target the Diff is never drawn. -->
  <section class="designer-workspace" data-test="designer-workspace">
    <div v-if="unselected" class="designer-workspace-frame" data-test="designer-center-unselected" />
    <template v-else>
      <GitCenterDiffView v-if="diffOpen && centerDiff" :session="centerDiff" />
      <DesignerCenter v-show="!diffOpen" :center="center" :placing="placing" :move-step="moveStep" @canvas-place="(flowId, x, y) => emit('canvas-place', flowId, x, y)" @cancel-placement="emit('cancel-placement')" @show-tool="emit('show-tool', $event)" />
    </template>
  </section>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { GitCenterDiffView, type GitCenterDiffSession } from '@/features/git'
import DesignerCenter from '../center/DesignerCenter.vue'
import type { DesignerCenterSession } from '../center/designerCenterSession'

const props = withDefaults(
  defineProps<{
    center: DesignerCenterSession
    placing?: boolean
    /** No work target yet (0039 T#3): show the empty frame instead of the Designer Center. */
    unselected?: boolean
    /** flowgate.v02.0059 0017-T: the density's card drag step, passed to the Canvas; null = 자율형. */
    moveStep?: number | null
    /** flowgate.v02.0066 T#6: the Shell's Center Diff; absent = the Workspace as before. */
    centerDiff?: GitCenterDiffSession | null
  }>(),
  { unselected: false, placing: false, moveStep: null, centerDiff: null },
)

const diffOpen = computed(() => !props.unselected && !!props.centerDiff?.state.open)

const emit = defineEmits<{
  'canvas-place': [flowId: string, x: number, y: number]
  'cancel-placement': []
  /** flowgate.v02.0061 T#14: a card's merge conflict asks for the Git tool panel. */
  'show-tool': [tool: 'git']
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
