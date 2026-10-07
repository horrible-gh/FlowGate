<template>
  <!-- Final container for the bottom Dock. Lives inside the CenterColumn, so it is only ever
       as wide as the Center. flowgate.v02.0036 (0035 v7): the Inspector on the left and the
       Minimap on the right, 7 : 3; the Minimap keeps its placeholder until its own group.
       flowgate.v02.0039 T#3: with no work target the Inspector keeps only its frame and the
       Minimap stays on screen dimmed and unavailable (confirmed initial-state design). -->
  <section
    class="designer-bottom-dock"
    data-test="designer-bottom-dock"
    :style="{ height: `${height}px`, gridTemplateColumns: DESIGNER_BOTTOM_DOCK_COLUMNS }"
  >
    <DesignerInspector :node="selectedNode" :unselected="unselected" />
    <aside
      class="designer-bottom-minimap"
      :class="{ 'is-unavailable': unselected }"
      :aria-disabled="unselected ? 'true' : undefined"
      data-test="designer-bottom-minimap"
    >
      <ShellPlaceholder
        area="minimap"
        :label="t('gui.designer.shell.minimap_label')"
        :parts="t('gui.designer.shell.minimap_parts')"
      />
    </aside>
  </section>
</template>

<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import type { FlowGraphNode } from '../center/graph/flowGraph'
import DesignerInspector from '../inspector/DesignerInspector.vue'
import { DESIGNER_BOTTOM_DOCK_COLUMNS } from './designerLayout'
import ShellPlaceholder from './ShellPlaceholder.vue'

withDefaults(
  defineProps<{
    /** bottomHeight from the Shell's layout state. */
    height: number
    /** The selected Node of the active Flow (Center session), or null. */
    selectedNode: FlowGraphNode | null
    /** No work target yet (0039 T#3). */
    unselected?: boolean
  }>(),
  { unselected: false },
)

const { t } = useI18n()
</script>

<style scoped>
.designer-bottom-dock {
  display: grid;
  flex: none;
  grid-template-rows: minmax(0, 1fr);
  min-width: 0;
  min-height: 0;
  overflow: hidden;
  border-top: 1px solid var(--fg2-line-strong);
  background: var(--fg2-panel);
}

.designer-bottom-minimap {
  min-width: 0;
  min-height: 0;
  overflow: auto;
  border-left: 1px solid var(--fg2-line);
}

.designer-bottom-minimap.is-unavailable {
  opacity: 0.46;
  filter: saturate(0.55);
  pointer-events: none;
  user-select: none;
}
</style>
