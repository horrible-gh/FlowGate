<template>
  <ModelessShell ref="shell" kind-color="#a78bfa" :open="open" :minimized="minimized" :title="t('gui.taskbar.question_policy_window')"
    :initial-x="72" :initial-y="initialY" :initial-width="initialWidth" :initial-height="initialHeight" :min-width="340" :min-height="260"
    @update:minimized="emit('update:minimized', $event)" @close="emit('close')">
    <div class="ai-policy-window" data-test="question-policy-window">
      <QuestionPolicyPanel />
    </div>
  </ModelessShell>
</template>
<script setup lang="ts">
// "AI 관리 · 질의 방침" as a Designer window, opened from the taskbar menu card. Only the frame is
// here; the policy box is QuestionPolicyPanel, the same component the AI 관리 screen shows.
import { ref } from 'vue'
import { useI18n } from 'vue-i18n'
import ModelessShell from '@/components/ui/ModelessShell.vue'
import QuestionPolicyPanel from '../policy/QuestionPolicyPanel.vue'
defineProps<{ open: boolean; minimized: boolean }>()
const emit = defineEmits<{ close: []; 'update:minimized': [value: boolean] }>()
const { t } = useI18n()
const shell = ref<InstanceType<typeof ModelessShell> | null>(null)
const initialWidth = Math.min(560, Math.max(340, window.innerWidth - 120))
const initialHeight = Math.min(440, Math.max(260, window.innerHeight - 200))
// Opens above the taskbar, a step right of and above the 내 AI window so both title bars stay in reach.
const initialY = Math.max(70, window.innerHeight - initialHeight - 114)
defineExpose({ bringToFront: () => shell.value?.bringToFront() })
</script>
<style scoped>
/* Slot content keeps this component's scope when the shell teleports it; the panel's own parts are reached with :deep(). */
.ai-policy-window { height: 100%; min-height: 0; font-size: 13px; }
/* The text box takes whatever height the window has instead of the page's fixed 13 rows. */
.ai-policy-window :deep(.question-policy) { display: flex; flex-direction: column; height: 100%; max-width: none; padding: 0; box-sizing: border-box; }
.ai-policy-window :deep(textarea#question-policy-body) { flex: 1 1 auto; min-height: 96px; margin: 10px 0; resize: none; }
.ai-policy-window :deep(.question-policy > header), .ai-policy-window :deep(.question-policy > footer) { flex: none; flex-wrap: wrap; }
.ai-policy-window :deep(.question-policy > p[role="alert"]) { flex: none; margin: 8px 0 0; }
</style>
