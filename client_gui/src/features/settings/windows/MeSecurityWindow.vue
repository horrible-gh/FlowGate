<template>
  <ModelessShell ref="shell" kind-color="#94a3b8" :open="open" :minimized="minimized" :title="t('gui.taskbar.security_window')"
    :initial-x="192" :initial-y="initialY" :initial-width="initialWidth" :initial-height="initialHeight" :min-width="360" :min-height="240"
    @update:minimized="emit('update:minimized', $event)" @close="emit('close')">
    <div class="me-security-window" data-test="me-security-window">
      <MeSecuritySettingsView embedded />
    </div>
  </ModelessShell>
</template>
<script setup lang="ts">
// "My settings / Security and sessions" as a Designer window, opened from the taskbar menu card. Only
// the frame is here; the password change and the session list are the settings page's own view in its
// embedded mode, so both places send the same requests.
import { ref } from 'vue'
import { useI18n } from 'vue-i18n'
import ModelessShell from '@/components/ui/ModelessShell.vue'
import MeSecuritySettingsView from '../views/MeSecuritySettingsView.vue'
defineProps<{ open: boolean; minimized: boolean }>()
const emit = defineEmits<{ close: []; 'update:minimized': [value: boolean] }>()
const { t } = useI18n()
const shell = ref<InstanceType<typeof ModelessShell> | null>(null)
const initialWidth = Math.min(620, Math.max(360, window.innerWidth - 240))
const initialHeight = Math.min(420, Math.max(240, window.innerHeight - 200))
// Opens above the taskbar, a step right of and above the other menu windows so every title bar stays in reach.
const initialY = Math.max(70, window.innerHeight - initialHeight - 198)
defineExpose({ bringToFront: () => shell.value?.bringToFront() })
</script>
<style scoped>
/* Slot content keeps this component's scope when the shell teleports it; the view's own parts are reached with :deep(). */
.me-security-window { height: 100%; min-height: 0; font-size: 13px; }
/* The window title already names the screen, so the page heading is left out. */
.me-security-window :deep(.fgs-phead) { display: none; }
.me-security-window :deep(.fgs-panel) { margin: 0; border: 0; border-radius: 0; background: none; box-shadow: none; }
.me-security-window :deep(.fgs-panel-body) { padding: 0; }
.me-security-window :deep(.fgs-sec) { margin-bottom: 14px; }
.me-security-window :deep(.fgs-sec:last-child) { margin-bottom: 0; }
.me-security-window :deep(.fgs-sec-head) { margin-bottom: 8px; }
/* Rows and table cells are tighter than on the settings page so the session table fits the window width. */
.me-security-window :deep(.fgs-row) { min-height: 42px; padding: 7px 14px; }
.me-security-window :deep(.fgs-t th) { padding: 8px 10px; }
.me-security-window :deep(.fgs-t td) { padding: 8px 10px; font-size: 12px; }
</style>
