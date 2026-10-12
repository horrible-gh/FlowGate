<template>
  <!-- Final container for the Designer's top band. flowgate.v02.0038 (0007-T §5): stacks the
       real Header / Menu Bar / Toolbar parts and owns only their order and row heights. What
       each part shows comes from the Shell as props, and every part event passes straight up
       to the Shell — this container handles none of them itself. flowgate.v02.0039 T#3 /
       v02.0043: the Shell's menu targets go to the Menu Bar (item availability) and the open
       Flow among them to the Toolbar (dimmed and unavailable without one); the recent Project /
       Group / Flow lists go to the Menu Bar's recent submenus. flowgate.v02.0053 T#9: the open Flow's
       server run status goes to the Toolbar's run dot. flowgate.v02.0059 T#2: the menu targets also go to
       the Toolbar's 정렬 / 배치 ▾ (the same availability as the Edit menu's 정렬 / 배치). -->
  <header class="designer-top" data-test="designer-top">
    <DesignerHeader @command="emit('command', $event)" @search="emit('search', $event)" />
    <DesignerMenuBar
      :run-mode="runMode"
      :density="density"
      :window-state="windowState"
      :targets="targets"
      :recent="recent"
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
      :disabled="!targets.flow"
      :can-validate="canValidate"
      :can-undo="targets.canUndo !== false"
      :can-redo="targets.canRedo !== false"
      :run-status="runStatus"
      :select-mode="selectMode"
      :targets="targets"
      @command="emit('command', $event)"
      @update:density="emit('update:density', $event)"
      @update:zoom="emit('update:zoom', $event)"
    />
  </header>
</template>

<script setup lang="ts">
import DesignerHeader from '../top/DesignerHeader.vue'
import DesignerMenuBar from '../top/DesignerMenuBar.vue'
import DesignerToolbar from '../top/DesignerToolbar.vue'
import {
  EMPTY_RECENT_LISTS,
  type DesignerDensity,
  type DesignerMenuTargets,
  type DesignerRecentLists,
  type DesignerRecentRequest,
  type DesignerRunMode,
  type DesignerSearchRequest,
  type DesignerTopCommand,
  type DesignerWindowPanel,
  type DesignerWindowState,
  type DesignerWindowTab,
} from '../top/designerTop'

withDefaults(
  defineProps<{
    runMode: DesignerRunMode
    density: DesignerDensity
    /** Zoom of the active Flow's Canvas, in percent. */
    zoom: number
    windowState: DesignerWindowState
    /** Project context / chosen Group / open Flow (0039 T#3, 0042, 0043). */
    targets: DesignerMenuTargets
    /** Recent Project / Group / Flow entries for the File menu (0043). */
    recent?: DesignerRecentLists
    /** flowgate.v02.0045 TR0020 rev2: the Toolbar's 검증 is available. */
    canValidate?: boolean
    /** flowgate.v02.0053 T#9: the open Flow's server run status, or null. */
    runStatus?: string | null
    /** flowgate.v02.0059 T#1: the Toolbar's [선택] mode is on (the Center session's). */
    selectMode?: boolean
  }>(),
  { recent: () => EMPTY_RECENT_LISTS, canValidate: false, runStatus: null, selectMode: false },
)

const emit = defineEmits<{
  command: [command: DesignerTopCommand]
  search: [request: DesignerSearchRequest]
  'toggle-panel': [panel: DesignerWindowPanel]
  'show-tab': [tab: DesignerWindowTab]
  'update:density': [density: DesignerDensity]
  /** A zoom preset of the Toolbar, in percent. */
  'update:zoom': [percent: number]
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
