<template>
  <!-- [프로젝트] [파일] switch of the left Dock. The Shell owns the value (activeLeftMode);
       arrow-key switching comes from the Tabs primitive. -->
  <Tabs
    class="explorer-mode-tabs"
    :tabs="tabs"
    :model-value="modelValue"
    :aria-label="t('gui.designer.shell.mode_tabs_label')"
    data-test="designer-explorer-mode-tabs"
    @update:model-value="onSelect"
  />
</template>

<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import Tabs, { type TabItem } from '../../../components/ui/Tabs.vue'
import { DESIGNER_LEFT_MODES, type DesignerLeftMode } from './designerLayout'

defineProps<{
  modelValue: DesignerLeftMode
}>()

const emit = defineEmits<{ 'update:modelValue': [mode: DesignerLeftMode] }>()

const { t } = useI18n()

const tabs = computed<TabItem[]>(() =>
  DESIGNER_LEFT_MODES.map((mode) => ({ key: mode, label: t(`gui.designer.shell.mode_${mode}`) })),
)

function onSelect(key: string) {
  const mode = DESIGNER_LEFT_MODES.find((m) => m === key)
  if (mode) emit('update:modelValue', mode)
}
</script>

<style scoped>
.explorer-mode-tabs {
  flex: none;
  padding: 0 6px;
}
</style>
