<template>
  <!-- v0.2 Flow = v0.1 group. Single click selects; double click keeps that selection and
       adds the activate/open intent (Enter activates). Opening a tab or loading the Canvas is
       the Designer's job, so nothing here touches the central workspace.
       The active (Designer-opened) state is row styling only — no badge or text
       ; ExplorerRowFrame draws it and exposes it as aria-current.
       flowgate.v02test.0077: the Flow's number (`F77`) stands ahead of its name, dimmer, in a column as wide as the
       Group's widest number, so every name starts at the same place; an archived Flow (only in an "아카이브 포함"
       tree) says 「아카이브」 after its name on top of the dimming. Hiding the number later = dropping the one span. -->
  <ExplorerRowFrame
    :class="{ 'is-archived': flow.archived }"
    :row-id="flow.id"
    :level="3"
    :label="flow.label ? `${flow.label} ${flow.name}` : flow.name"
    :expandable="false"
    :expanded="false"
    :selected="selected"
    :active="active"
    test-id="project-explorer-flow"
    @select="emit('select', flow)"
    @activate="emit('activate', flow)"
  >
    <ExplorerIcon class="row-icon" :class="{ 'is-active': active }" name="flow" />
    <span
      v-if="flow.label"
      class="row-number"
      :style="labelWidth ? { minWidth: `${labelWidth}ch` } : undefined"
      data-test="project-explorer-flow-number"
    >{{ flow.label }}</span>
    <span class="row-label" data-test="project-explorer-label">{{ flow.name }}</span>
    <span v-if="flow.archived" class="row-archived" data-test="project-explorer-archived">{{ t('gui.designer.project_explorer.archived') }}</span>
  </ExplorerRowFrame>
</template>

<script setup lang="ts">
import { useI18n } from 'vue-i18n'
import ExplorerIcon from '../shared/ExplorerIcon.vue'
import ExplorerRowFrame from './ExplorerRowFrame.vue'
import type { ExplorerFlow } from './projectExplorerTree'

defineProps<{
  flow: ExplorerFlow
  selected: boolean
  active: boolean
  /** The Group's widest Flow number in characters (flowgate.v02test.0077); 0 / absent = the number's own width. */
  labelWidth?: number
}>()

const emit = defineEmits<{
  select: [flow: ExplorerFlow]
  activate: [flow: ExplorerFlow]
}>()

const { t } = useI18n()
</script>

<style scoped>
.is-archived { opacity: 0.5; }
.row-icon {
  color: var(--fg2-cyan);
}

.row-icon.is-active {
  color: var(--fg2-blue);
}

/* Monospace, so `ch` is exactly one digit and the column lines up whatever the numbers are. */
.row-number {
  flex: none;
  font-family: var(--fg2-font-mono, ui-monospace, SFMono-Regular, Consolas, monospace);
  font-size: 11.5px;
  font-weight: 400;
  color: var(--fg2-muted-2);
  white-space: nowrap;
}

.row-label {
  flex: 0 1 auto;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.row-archived {
  flex: none;
  padding: 0 5px;
  border: 1px solid var(--fg2-line);
  border-radius: 8px;
  font-size: 10.5px;
  font-weight: 400;
  line-height: 15px;
  color: var(--fg2-muted);
}
</style>
