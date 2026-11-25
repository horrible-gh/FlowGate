<template>
  <ModelessShell ref="shell" kind-color="#a78bfa" :open="open" :minimized="minimized" :title="title"
    :initial-x="48" :initial-y="initialY" :initial-width="initialWidth" :initial-height="initialHeight" :min-width="420" :min-height="380"
    @update:minimized="emit('update:minimized', $event)" @close="emit('close')">
    <div class="ai-manage-window" data-test="ai-manage-window">
      <AiManagePanel :tab="tab" mode="window" @update:tab="onTab" />
    </div>
  </ModelessShell>
</template>
<script setup lang="ts">
// v02test.0096 F96-3: "AI 관리" as ONE Designer window (it replaces the 내 AI and 질의 방침 windows). The taskbar menu's
// 내 AI / 기타 설정 / 방침 cards open it on that tab, or switch the open window to it. Only the frame is here; the tabs
// are AiManagePanel, the component the full-screen AI 관리 shows. The title follows the tab: "AI 관리 · 내 AI".
import { computed, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import ModelessShell from '@/components/ui/ModelessShell.vue'
import AiManagePanel from '../manage/AiManagePanel.vue'
import { isAiWindowTab, type AiManageTab, type AiWindowTab } from '../manage/aiManageTabs'
import { installAiManageMessages } from '../manage/messages'
const props = withDefaults(defineProps<{ open: boolean; minimized: boolean; tab?: AiWindowTab }>(), { tab: 'ai' })
const emit = defineEmits<{ close: []; 'update:minimized': [value: boolean]; 'update:tab': [value: AiWindowTab] }>()
const composer = useI18n({ useScope: 'global' }); installAiManageMessages(composer)
const { t } = composer
const shell = ref<InstanceType<typeof ModelessShell> | null>(null)
const TAB_NAMES: Record<AiWindowTab, string> = { ai: 'gui.ai.tabs.my_ai', other: 'gui.aiManage.tabs.other', policy: 'gui.aiManage.tabs.policy' }
const title = computed(() => t('gui.aiManage.window_title', { tab: t(TAB_NAMES[props.tab]) }))
function onTab(next: AiManageTab) { if (isAiWindowTab(next) && next !== props.tab) emit('update:tab', next) }
// Large enough for 4+ list rows and for the policy editor beside its sub-item list.
const initialWidth = Math.min(860, Math.max(420, window.innerWidth - 96))
const initialHeight = Math.min(640, Math.max(380, window.innerHeight - 200))
// Opens above the taskbar, clear of the menu board's place.
const initialY = Math.max(70, window.innerHeight - initialHeight - 90)
defineExpose({ bringToFront: () => shell.value?.bringToFront() })
</script>
<style scoped>
/* Slot content keeps this component's scope when the shell teleports it; the panels' own parts are reached with :deep(). */
.ai-manage-window { height: 100%; min-height: 0; font-size: 13px; }
/* 내 AI: the tab names the list. The list box takes all the height the window gives; on a short window the card list
   shrinks and scrolls (v02test.0089 F89-7, now without the cards that sat under it). */
.ai-manage-window :deep([data-body="ai"]) { overflow: hidden; display: flex; flex-direction: column; }
.ai-manage-window :deep(.ai-providers-panel) { flex: 1 1 auto; height: 100%; min-height: 0; display: flex; flex-direction: column; }
.ai-manage-window :deep(.ai-providers-panel .page-title) { display: none; }
.ai-manage-window :deep(.ai-providers-panel .detail-head) { margin-bottom: 8px; }
.ai-manage-window :deep(.ai-providers-panel .detail-panel) { flex: 1 1 auto; min-height: 0; margin-bottom: 0; }
.ai-manage-window :deep(.ai-providers-panel .provider-cards) { flex: 1; min-height: 0; overflow: auto; }
.ai-manage-window :deep(.ai-providers-panel .empty-row) { margin: auto 0; }
.ai-manage-window :deep(.ai-providers-panel .detail-toolbar) { height: auto; min-height: 52px; padding: 8px 12px; flex-wrap: wrap; gap: 8px 12px; }
.ai-manage-window :deep(.ai-providers-panel .detail-search) { min-width: 120px; flex: 1 1 120px; }
</style>
