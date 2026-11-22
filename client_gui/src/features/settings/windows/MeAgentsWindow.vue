<template>
  <ModelessShell ref="shell" kind-color="#94a3b8" :open="open" :minimized="minimized" :title="t('gui.taskbar.agents_window')"
    :initial-x="96" :initial-y="initialY" :initial-width="initialWidth" :initial-height="initialHeight" :min-width="360" :min-height="240"
    @update:minimized="emit('update:minimized', $event)" @close="emit('close')">
    <div class="me-agents-window" data-test="me-agents-window">
      <SystemAgentSettingsView personal embedded />
    </div>
  </ModelessShell>
</template>
<script setup lang="ts">
// "내 설정 · 에이전트" as a Designer window, opened from the taskbar menu card. Only the frame is
// here; the list, registration and edit dialogs are the settings page's own view in its embedded mode.
import { ref } from 'vue'
import { useI18n } from 'vue-i18n'
import ModelessShell from '@/components/ui/ModelessShell.vue'
import SystemAgentSettingsView from '../views/SystemAgentSettingsView.vue'
defineProps<{ open: boolean; minimized: boolean }>()
const emit = defineEmits<{ close: []; 'update:minimized': [value: boolean] }>()
const { t } = useI18n()
const shell = ref<InstanceType<typeof ModelessShell> | null>(null)
const initialWidth = Math.min(600, Math.max(360, window.innerWidth - 144))
const initialHeight = Math.min(420, Math.max(240, window.innerHeight - 200))
// Opens above the taskbar, a step right of and above the other menu windows so every title bar stays in reach.
const initialY = Math.max(70, window.innerHeight - initialHeight - 138)
defineExpose({ bringToFront: () => shell.value?.bringToFront() })
</script>
<style scoped>
/* Slot content keeps this component's scope when the shell teleports it; the view's own parts are reached with :deep(). */
.me-agents-window { height: 100%; min-height: 0; font-size: 13px; }
/* The window title already names the screen and the list, so only the register button stays above the rows. */
.me-agents-window :deep(.agent-page-head) { justify-content: flex-end; margin-bottom: 10px; }
.me-agents-window :deep(.agent-page-head h2), .me-agents-window :deep(.fgs-phead) { display: none; }
.me-agents-window :deep(.fgs-panel) { margin: 0; border: 0; border-radius: 0; background: none; box-shadow: none; }
.me-agents-window :deep(.fgs-panel-body) { padding: 0; }
.me-agents-window :deep(.agent-row) { padding: 10px 12px; gap: 12px; }
</style>
