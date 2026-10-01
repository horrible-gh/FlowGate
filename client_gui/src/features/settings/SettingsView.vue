<template>
  <div class="settings-view">
    <header class="view-header">
      <Tabs
        :model-value="activeTab"
        :tabs="tabs"
        :aria-label="t('gui.settings.tabs_aria')"
        @change="onTabChange"
      />
    </header>
    <main class="view-content">
      <AiProvidersPanel :key="activeTab" :scope="activeTab === 'system-ai' ? 'system' : 'user'" />
    </main>
  </div>
</template>

<script setup lang="ts">
// v0.2 settings (flowgate.v02.0017 TR0003 rev7): 시스템 AI (administrators; system settings)
// and 내 AI (every user; personal settings). Agent management stays with the Agent screens.
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import Tabs, { type TabItem } from '@/components/ui/Tabs.vue'
import { useCurrentUser } from '@/core/auth/currentUser'
import AiProvidersPanel from '@/features/ai/providers/AiProvidersPanel.vue'

type SettingsTab = 'system-ai' | 'my-ai'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const { user } = useCurrentUser()
const isAdmin = computed(() => !!user.value?.isAdmin)

const tabs = computed<TabItem[]>(() => [
  ...(isAdmin.value ? [{ key: 'system-ai', label: t('gui.settings.tabs.system_ai') }] : []),
  { key: 'my-ai', label: t('gui.settings.tabs.my_ai') },
])

// Default: the administrator's own concern first; everyone else only has 내 AI.
const activeTab = computed<SettingsTab>(() => {
  const requested = route.query.tab
  if (requested === 'my-ai') return 'my-ai'
  if (requested === 'system-ai' && isAdmin.value) return 'system-ai'
  return isAdmin.value ? 'system-ai' : 'my-ai'
})

function onTabChange(tab: string) {
  if (route.query.tab !== tab) void router.replace({ query: { ...route.query, tab } })
}
</script>

<style scoped>
.settings-view {
  display: flex;
  flex-direction: column;
  height: 100%;
  min-height: 0;
  padding: 18px 24px 24px;
  overflow: hidden;
}

.view-header {
  margin-bottom: 18px;
  flex: none;
}

.view-content {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
}
</style>
