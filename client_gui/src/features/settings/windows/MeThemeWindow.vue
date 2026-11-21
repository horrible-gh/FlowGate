<template>
  <ModelessShell ref="shell" kind-color="#94a3b8" :open="open" :minimized="minimized" :title="t('gui.taskbar.theme_window')"
    :initial-x="24" :initial-y="initialY" :initial-width="initialWidth" :initial-height="210" :min-width="300" :min-height="150"
    @update:minimized="emit('update:minimized', $event)" @close="emit('close')">
    <div class="me-menu-window" data-test="me-theme-window">
      <p class="me-menu-window-hint">{{ t('gui.taskbar.theme_hint') }}</p>
      <MeThemePanel field-id="taskbar-style-window" />
    </div>
  </ModelessShell>
</template>
<script setup lang="ts">
// "내 설정 · 테마" as a Designer window, opened from the taskbar menu card. Only the frame is
// here; the setting is MeThemePanel, the same component the settings page shows.
import { ref } from 'vue'
import { useI18n } from 'vue-i18n'
import ModelessShell from '@/components/ui/ModelessShell.vue'
import MeThemePanel from '../components/MeThemePanel.vue'
defineProps<{ open: boolean; minimized: boolean }>()
const emit = defineEmits<{ close: []; 'update:minimized': [value: boolean] }>()
const { t } = useI18n()
const shell = ref<InstanceType<typeof ModelessShell> | null>(null)
const initialWidth = Math.min(520, Math.max(300, window.innerWidth - 48))
// Opens just above the menu board's place, clear of the taskbar.
const initialY = Math.max(70, window.innerHeight - 330)
defineExpose({ bringToFront: () => shell.value?.bringToFront() })
</script>
<style scoped>
/* Slot content keeps this component's scope when the shell teleports it; the panel's own parts are reached with :deep(). */
.me-menu-window-hint { margin: 0 0 6px; font-size: 12px; line-height: 1.45; color: var(--fg2-muted); }
.me-menu-window :deep(.fgs-row) { min-height: 0; padding: 8px 0; gap: 12px; flex-wrap: wrap; }
.me-menu-window :deep(.fgs-row-ctrl) { max-width: none; justify-content: flex-start; }
.me-menu-window :deep(.fgs-row-ctrl .fgs-input) { width: 180px; }
</style>
