<template>
  <div class="ai-manage" :class="`is-${mode}`" :data-tab="tab" data-test="ai-manage">
    <header class="ai-manage-head">
      <Tabs :model-value="tab" :tabs="tabItems" :aria-label="t('gui.ai.tabs_aria')" @change="select" />
    </header>

    <!-- The list and 기타 설정 read their data each time they open, so a change made in one shows in the other. -->
    <main v-if="tab === 'ai'" class="ai-manage-body" data-body="ai">
      <AiProvidersPanel :mine-only="mode === 'window'" @changed="emit('providers-changed')" />
    </main>
    <main v-else-if="tab === 'other'" class="ai-manage-body" data-body="other">
      <AiOtherSettingsPanel />
    </main>
    <!-- 방침 stays mounted once opened: unsaved policy text survives a look at another tab. -->
    <main v-if="policyOpened" v-show="tab === 'policy'" class="ai-manage-body" data-body="policy">
      <AiPolicyPanel />
    </main>
    <!-- Tabs only the full screen has (AI 평가, AI 그룹) are drawn by that screen. -->
    <slot v-if="!isCoreTab" :tab="tab" />
  </div>
</template>

<script setup lang="ts">
// v02test.0096 F96-3: the AI 관리 tabs, one component for the full screen (AiSettingsView) and the taskbar window
// (AiManageWindow). Window: 내 AI / 기타 설정 / 방침 with the list limited to my own AI. Full screen: AI 목록 (system AI
// and mine) · the screen's own extra tabs · 기타 설정 · 방침. The parent owns the open tab (v-model:tab).
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import Tabs, { type TabItem } from '@/components/ui/Tabs.vue'
import AiProvidersPanel from '../providers/AiProvidersPanel.vue'
import AiPolicyPanel from '../policy/AiPolicyPanel.vue'
import { installPolicyMessages } from '../policy/messages'
import AiOtherSettingsPanel from './AiOtherSettingsPanel.vue'
import { parseAiManageTab, type AiManageTab } from './aiManageTabs'
import { installAiManageMessages } from './messages'

const props = withDefaults(defineProps<{
  tab: AiManageTab
  mode?: 'window' | 'page'
  /** Tabs of the full screen placed between the list and 기타 설정; their content comes through the default slot. */
  extraTabs?: TabItem[]
}>(), { mode: 'page', extraTabs: () => [] })
const emit = defineEmits<{ 'update:tab': [tab: AiManageTab]; 'providers-changed': [] }>()

const composer = useI18n({ useScope: 'global' }); installPolicyMessages(composer); installAiManageMessages(composer)
const { t } = composer

const tabItems = computed<TabItem[]>(() => [
  { key: 'ai', label: props.mode === 'window' ? t('gui.ai.tabs.my_ai') : t('gui.ai.tabs.ai') },
  ...props.extraTabs,
  { key: 'other', label: t('gui.aiManage.tabs.other') },
  { key: 'policy', label: t('gui.aiManage.tabs.policy') },
])
const isCoreTab = computed(() => props.tab === 'ai' || props.tab === 'other' || props.tab === 'policy')

const policyOpened = ref(props.tab === 'policy')
watch(() => props.tab, (tab) => { if (tab === 'policy') policyOpened.value = true })

function select(key: string) {
  const tab = parseAiManageTab(key)
  if (tab !== props.tab) emit('update:tab', tab)
}
</script>

<style scoped>
.ai-manage { display: flex; flex-direction: column; height: 100%; min-height: 0; }
.ai-manage-head { flex: none; display: flex; align-items: center; margin-bottom: 18px; }
.ai-manage.is-window .ai-manage-head { margin-bottom: 10px; }
.ai-manage-body { flex: 1; min-height: 0; overflow-y: auto; }
.ai-manage-body[data-body="policy"] { overflow: hidden; display: flex; flex-direction: column; }
.ai-manage-body[data-body="policy"] > :deep(.ai-policy) { flex: 1 1 auto; }
</style>
