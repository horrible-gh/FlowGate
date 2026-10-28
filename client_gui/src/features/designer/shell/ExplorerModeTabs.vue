<template>
  <!-- Left Dock header (0025 T0006 §2): the [프로젝트] [파일] switch is the Dock's title row and
       carries the collapse button on its right. The Shell owns the value (activeLeftMode);
       arrow-key switching comes from the Tabs primitive. flowgate.v02.0039 T#3: `disabled` (no
       work target yet) keeps the row on screen but makes the switch and the collapse button
       unavailable, as the confirmed initial-state design draws the left navigation. -->
  <header class="explorer-mode-header" :class="{ 'is-unavailable': disabled }" data-test="designer-explorer-header">
    <Tabs
      class="explorer-mode-tabs"
      :tabs="tabs"
      :model-value="modelValue"
      :aria-label="t('gui.designer.shell.mode_tabs_label')"
      data-test="designer-explorer-mode-tabs"
      @update:model-value="onSelect"
    />
    <IconButton
      class="explorer-mode-collapse"
      size="sm"
      :label="t('gui.designer.shell.collapse_left')"
      :title="t('gui.designer.shell.collapse_left')"
      :disabled="disabled"
      data-test="designer-left-collapse"
      @click="emit('collapse')"
    >
      <svg viewBox="0 0 16 16" aria-hidden="true">
        <path d="M10 4L6 8l4 4" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" />
      </svg>
    </IconButton>
  </header>
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import IconButton from '../../../components/ui/IconButton.vue'
import Tabs, { type TabItem } from '../../../components/ui/Tabs.vue'
import { DESIGNER_LEFT_MODES, type DesignerLeftMode } from './designerLayout'

const props = withDefaults(
  defineProps<{
    modelValue: DesignerLeftMode
    /** True while the Designer has no work target (0039 T#3). */
    disabled?: boolean
  }>(),
  { disabled: false },
)

const emit = defineEmits<{ 'update:modelValue': [mode: DesignerLeftMode]; collapse: [] }>()

const { t } = useI18n()

const tabs = computed<TabItem[]>(() =>
  DESIGNER_LEFT_MODES.map((mode) => ({ key: mode, label: t(`gui.designer.shell.mode_${mode}`), disabled: props.disabled })),
)

function onSelect(key: string) {
  const mode = DESIGNER_LEFT_MODES.find((m) => m === key)
  if (mode) emit('update:modelValue', mode)
}
</script>

<style scoped>
.explorer-mode-header {
  display: flex;
  flex: none;
  align-items: center;
  gap: 4px;
  padding: 0 4px 0 6px;
  border-bottom: 1px solid var(--fg2-line);
}

/* NR §4.2: equal-width choices within the existing header and collapse control. */
.explorer-mode-tabs {
  flex: 1 1 auto;
  min-width: 0;
  border-bottom: 0;
  padding: 4px 0;
  flex-wrap: nowrap;
}

.explorer-mode-tabs :deep(.ui-tab) {
  flex: 1 1 0;
  box-sizing: border-box;
  min-width: 0;
  height: var(--fg2-control-h-md);
  padding: 0 8px;
  overflow: hidden;
  white-space: nowrap;
  text-overflow: ellipsis;
  border: 1px solid var(--fg2-line-strong);
  border-bottom: 2px solid transparent;
  border-radius: 6px;
  background: var(--fg2-control-surface-flat);
}

.explorer-mode-tabs :deep(.ui-tab.is-active) {
  background: var(--fg2-status-info-bg);
  border-color: var(--fg2-status-info-border);
  border-bottom-color: var(--fg2-blue);
}

.explorer-mode-collapse {
  flex: none;
}

/* No work target (0039 T#3, confirmed design v7): dimmed and unavailable. */
.explorer-mode-header.is-unavailable {
  opacity: 0.46;
  filter: saturate(0.55);
  user-select: none;
}
</style>
