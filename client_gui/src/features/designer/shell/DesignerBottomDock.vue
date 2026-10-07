<template>
  <!-- Final container for the bottom Dock. Lives inside the CenterColumn, so it is only ever
       as wide as the Center. flowgate.v02.0036 (0035 v7): the Inspector on the left and the
       Minimap on the right, 7 : 3; the Minimap keeps its placeholder until its own group. -->
  <section
    class="designer-bottom-dock"
    data-test="designer-bottom-dock"
    :style="{ height: `${height}px`, gridTemplateColumns: DESIGNER_BOTTOM_DOCK_COLUMNS }"
  >
    <DesignerInspector :node="selectedNode" />
    <aside class="designer-bottom-minimap" data-test="designer-bottom-minimap">
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

defineProps<{
  /** bottomHeight from the Shell's layout state. */
  height: number
  /** The selected Node of the active Flow (Center session), or null. */
  selectedNode: FlowGraphNode | null
}>()

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
</style>
