<template>
  <ModelessShell ref="shell" kind-color="#94a3b8" :open="open" :minimized="minimized" :title="t('gui.taskbar.account_window')"
    :initial-x="168" :initial-y="initialY" :initial-width="initialWidth" :initial-height="initialHeight" :min-width="320" :min-height="200"
    @update:minimized="emit('update:minimized', $event)" @close="emit('close')">
    <div class="me-account-window" data-test="me-account-window">
      <MeAccountSettingsView embedded />
    </div>
  </ModelessShell>
</template>
<script setup lang="ts">
// "My settings / Account and display" as a Designer window, opened from the taskbar menu card. Only
// the frame is here; the sign-in information and the language buttons are the settings page's own view
// in its embedded mode.
import { ref } from 'vue'
import { useI18n } from 'vue-i18n'
import ModelessShell from '@/components/ui/ModelessShell.vue'
import MeAccountSettingsView from '../views/MeAccountSettingsView.vue'
defineProps<{ open: boolean; minimized: boolean }>()
const emit = defineEmits<{ close: []; 'update:minimized': [value: boolean] }>()
const { t } = useI18n()
const shell = ref<InstanceType<typeof ModelessShell> | null>(null)
const initialWidth = Math.min(520, Math.max(320, window.innerWidth - 216))
const initialHeight = Math.min(300, Math.max(200, window.innerHeight - 200))
// Opens above the taskbar, a step right of and above the other menu windows so every title bar stays in reach.
const initialY = Math.max(70, window.innerHeight - initialHeight - 174)
defineExpose({ bringToFront: () => shell.value?.bringToFront() })
</script>
<style scoped>
/* Slot content keeps this component's scope when the shell teleports it; the view's own parts are reached with :deep(). */
.me-account-window { height: 100%; min-height: 0; font-size: 13px; }
/* The window title already names the screen, so the page heading is left out. */
.me-account-window :deep(.fgs-phead) { display: none; }
.me-account-window :deep(.fgs-panel) { margin: 0; border: 0; border-radius: 0; background: none; box-shadow: none; }
.me-account-window :deep(.fgs-panel-body) { padding: 0; }
.me-account-window :deep(.fgs-sec) { margin-bottom: 14px; }
.me-account-window :deep(.fgs-sec:last-child) { margin-bottom: 0; }
/* Rows are shorter than on the settings page so both sections fit the window without scrolling. */
.me-account-window :deep(.fgs-row) { min-height: 42px; padding: 7px 14px; }
.me-account-window :deep(.fgs-sec-head) { margin-bottom: 8px; }
</style>
