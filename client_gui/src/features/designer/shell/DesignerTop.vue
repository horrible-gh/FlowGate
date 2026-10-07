<template>
  <!-- Final container for the Designer's top band. flowgate.v02.0038 (0007-T §5): stacks the
       real Header / Menu Bar / Toolbar parts and owns only their order and row heights. What
       each part shows comes from the Shell as props, and every part event passes straight up
       to the Shell — this container handles none of them itself. -->
  <header class="designer-top" data-test="designer-top">
    <DesignerHeader @command="emit('command', $event)" @search="emit('search', $event)" />
    <DesignerMenuBar
      :run-mode="runMode"
      :density="density"
      :window-state="windowState"
      @command="emit('command', $event)"
      @toggle-panel="emit('toggle-panel', $event)"
      @show-tab="emit('show-tab', $event)"
      @update:density="emit('update:density', $event)"
      @open-recent="emit('open-recent', $event)"
    />
    <DesignerToolbar
      :run-mode="runMode"
      :density="density"
      :zoom="zoom"
      @command="emit('command', $event)"
      @update:density="emit('update:density', $event)"
    />
  </header>
</template>

<script setup lang="ts">
import DesignerHeader from '../top/DesignerHeader.vue'
import DesignerMenuBar from '../top/DesignerMenuBar.vue'
import DesignerToolbar from '../top/DesignerToolbar.vue'
import type {
  DesignerDensity,
  DesignerRecentRequest,
  DesignerRunMode,
  DesignerSearchRequest,
  DesignerTopCommand,
  DesignerWindowPanel,
  DesignerWindowState,
  DesignerWindowTab,
} from '../top/designerTop'

defineProps<{
  runMode: DesignerRunMode
  density: DesignerDensity
  /** Zoom of the active Flow's Canvas, in percent. */
  zoom: number
  windowState: DesignerWindowState
}>()

const emit = defineEmits<{
  command: [command: DesignerTopCommand]
  search: [request: DesignerSearchRequest]
  'toggle-panel': [panel: DesignerWindowPanel]
  'show-tab': [tab: DesignerWindowTab]
  'update:density': [density: DesignerDensity]
  'open-recent': [request: DesignerRecentRequest]
}>()
</script>

<style scoped>
/* Header 52px / Menu Bar 34px / Toolbar 66px, as drawn in 03-designer-shell-menubar-v3. The
   band sits above the Body so the open menus and the search panel draw over the work area;
   inside it, earlier rows sit above later ones (the search panel over the Menu Bar, a menu
   over the Toolbar). */
.designer-top {
  position: relative;
  z-index: 20;
  display: grid;
  grid-template-rows: 52px 34px 66px;
  grid-template-columns: minmax(0, 1fr);
  flex: none;
  min-width: 0;
  background: var(--fg2-panel);
}

.designer-top > :nth-child(1) {
  position: relative;
  z-index: 3;
}

.designer-top > :nth-child(2) {
  position: relative;
  z-index: 2;
}

.designer-top > :nth-child(3) {
  position: relative;
  z-index: 1;
}
</style>
