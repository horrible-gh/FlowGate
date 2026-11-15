<template>
  <!-- v0.2 Group = v0.1 module. Single click selects the Group (reported upward) and opens
       or closes its Flow list (flowgate.v02test.0002 R0001 #5; a double click toggles once);
       Enter / the caret / Left-Right open or close it too.
       flowgate.v02gui.0027: the trailing ⋯ opens the same Group menu as a right click (shown on hover, selection
       or focus). Its click stops at the button, so it never selects or toggles the row. An archived Group (only in
       an "아카이브 포함" tree) is drawn dimmed, like an archived Flow. -->
  <ExplorerRowFrame
    :class="{ 'is-archived': group.archived }"
    :row-id="group.id"
    :level="2"
    :label="group.name"
    :expandable="true"
    :expanded="expanded"
    :selected="selected"
    test-id="project-explorer-group"
    @select="emit('select', group)"
    @activate="emit('toggle', group.id)"
    @toggle="emit('toggle', group.id)"
  >
    <ExplorerIcon class="row-icon" :name="expanded ? 'folder-open' : 'folder'" />
    <span class="row-label" data-test="project-explorer-label">{{ group.name }}</span>
    <span class="row-count" aria-hidden="true">{{ group.flows.length }}</span>
    <button
      type="button"
      class="row-menu"
      tabindex="-1"
      :aria-label="t('gui.designer.project_explorer.group_menu')"
      :title="t('gui.designer.project_explorer.group_menu')"
      aria-haspopup="menu"
      data-test="project-explorer-group-menu"
      @click.stop="emit('menu', $event)"
      @dblclick.stop
    >⋯</button>
    <template #children>
      <slot />
    </template>
  </ExplorerRowFrame>
</template>

<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import ExplorerIcon from '../shared/ExplorerIcon.vue'
import ExplorerRowFrame from './ExplorerRowFrame.vue'
import type { ExplorerGroup } from './projectExplorerTree'

defineProps<{
  group: ExplorerGroup
  expanded: boolean
  selected: boolean
}>()

const emit = defineEmits<{
  select: [group: ExplorerGroup]
  toggle: [id: string]
  /** The ⋯ button was clicked: open the Group menu at it (the click event, its target is the button). */
  menu: [event: MouseEvent]
}>()

const { t } = useI18n()
</script>

<style scoped>
/* Only the Group's own line is dimmed; its Flows keep their own look (an archived Flow dims itself). */
.is-archived > :deep(.explorer-row) {
  opacity: 0.5;
}

.row-icon {
  color: var(--fg2-purple);
}

.row-label {
  flex: 1;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.row-count {
  flex: none;
  font-size: 11px;
  color: var(--fg2-muted-2);
}

.row-menu {
  display: inline-flex;
  flex: none;
  align-items: center;
  justify-content: center;
  width: 20px;
  height: 20px;
  padding: 0;
  border: 0;
  border-radius: var(--fg2-control-radius-sm);
  background: transparent;
  color: var(--fg2-muted);
  font: inherit;
  font-size: 13px;
  line-height: 1;
  cursor: pointer;
  visibility: hidden;
}

.row-menu:hover {
  background: rgba(255, 255, 255, 0.08);
  color: var(--fg2-text);
}

.row-menu:focus-visible {
  outline: 2px solid var(--fg2-focus);
  outline-offset: -2px;
}

/* Hover, selection or keyboard focus of this Group's own line (not of a Flow inside it). */
:deep(.explorer-row:hover) > .row-menu,
:deep(.explorer-row.is-selected) > .row-menu,
.row-menu:focus-visible,
.explorer-node:focus > :deep(.explorer-row) > .row-menu {
  visibility: visible;
}
</style>
