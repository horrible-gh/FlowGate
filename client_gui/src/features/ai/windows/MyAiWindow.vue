<template>
  <ModelessShell ref="shell" kind-color="#a78bfa" :open="open" :minimized="minimized" :title="t('gui.taskbar.my_ai_window')"
    :initial-x="48" :initial-y="initialY" :initial-width="initialWidth" :initial-height="initialHeight" :min-width="420" :min-height="380"
    @update:minimized="emit('update:minimized', $event)" @close="emit('close')">
    <div class="ai-my-window" data-test="my-ai-window">
      <AiProvidersPanel mine-only />
    </div>
  </ModelessShell>
</template>
<script setup lang="ts">
// "AI 관리 · 내 AI" as a Designer window, opened from the taskbar menu card. Only the frame is
// here; the list is AiProvidersPanel, the component the AI management screen shows, limited to my own AI.
import { ref } from 'vue'
import { useI18n } from 'vue-i18n'
import ModelessShell from '@/components/ui/ModelessShell.vue'
import AiProvidersPanel from '../providers/AiProvidersPanel.vue'
defineProps<{ open: boolean; minimized: boolean }>()
const emit = defineEmits<{ close: []; 'update:minimized': [value: boolean] }>()
const { t } = useI18n()
const shell = ref<InstanceType<typeof ModelessShell> | null>(null)
// v02test.0089: larger first size so the list keeps 4+ rows above the repeat limit and title AI cards.
const initialWidth = Math.min(860, Math.max(420, window.innerWidth - 96))
const initialHeight = Math.min(640, Math.max(380, window.innerHeight - 200))
// Opens above the taskbar, clear of the menu board's place.
const initialY = Math.max(70, window.innerHeight - initialHeight - 90)
defineExpose({ bringToFront: () => shell.value?.bringToFront() })
</script>
<style scoped>
/* Slot content keeps this component's scope when the shell teleports it; the panel's own parts are reached with :deep(). */
.ai-my-window { height: 100%; min-height: 0; }
/* The window title already names the list. My own repeat limit and title AI cards show under it (v02test.0089). */
.ai-my-window :deep(.ai-providers-panel .page-title) { display: none; }
.ai-my-window :deep(.ai-providers-panel .repeat-card) { margin-top: 8px; }
.ai-my-window :deep(.ai-providers-panel .detail-head) { margin-bottom: 8px; }
.ai-my-window :deep(.ai-providers-panel .detail-panel) { margin-bottom: 0; }
.ai-my-window :deep(.ai-providers-panel .detail-toolbar) { height: auto; min-height: 52px; padding: 8px 12px; flex-wrap: wrap; gap: 8px 12px; }
.ai-my-window :deep(.ai-providers-panel .detail-search) { min-width: 120px; flex: 1 1 120px; }
</style>
