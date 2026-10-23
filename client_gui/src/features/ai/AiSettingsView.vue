<template>
  <div class="ai-settings-view">
    <header class="view-header">
      <div class="header-left">
        <Tabs
          :model-value="activeTab"
          :tabs="tabs"
          :aria-label="t('gui.ai.tabs_aria')"
          @change="onTabChange"
        />
      </div>
    </header>

    <!-- AI list: system AI and my AI in one list. -->
    <main v-if="activeTab === 'ai'" class="view-content">
      <AiProvidersPanel @changed="onProvidersChanged" />
    </main>

    <template v-else>
      <!-- Full-screen loading only before the first data arrives: a refresh after an edit
           keeps the open tab (and its selection/dialog state) mounted. -->
      <div v-if="loading && !loaded" class="view-loading">
        <LoadingState :label="t('gui.common.loading')" />
      </div>

      <div v-else-if="error" class="view-error">
        <ErrorState
          :title="t('gui.ai.load_failed_title')"
          :message="error"
        >
          <template #action>
            <Button variant="secondary" @click="loadData">
              {{ t('gui.common.retry') }}
            </Button>
          </template>
        </ErrorState>
      </div>

      <main v-else class="view-content">
        <!-- Evaluation Tab -->
        <template v-if="activeTab === 'evaluation'">
          <AiEvaluationPanel
            :providers="activeProviders"
            @changed="loadData"
          />
        </template>

        <!-- Groups Tab -->
        <template v-else-if="activeTab === 'groups'">
          <div v-if="sectionErrors.groups" class="view-error">
            <ErrorState
              :title="t('gui.ai.load_failed_title')"
              :message="sectionErrors.groups"
            >
              <template #action>
                <Button variant="secondary" @click="loadData">
                  {{ t('gui.common.retry') }}
                </Button>
              </template>
            </ErrorState>
          </div>
          <AiGroupsPanel
            v-else
            :providers="activeProviders"
            :initial-groups="groups"
            @changed="loadData"
          />
        </template>
      </main>
    </template>
  </div>
</template>

<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import Button from '@/components/ui/Button.vue'
import ErrorState from '@/components/ui/ErrorState.vue'
import LoadingState from '@/components/ui/LoadingState.vue'
import Tabs, { type TabItem } from '@/components/ui/Tabs.vue'
import { fetchVisibleSystemAi, listAiGroups } from './api'
import type { AiGroup, ProviderItem } from './types'
import AiProvidersPanel from './providers/AiProvidersPanel.vue'
import AiEvaluationPanel from './evaluation/AiEvaluationPanel.vue'
import AiGroupsPanel from './groups/AiGroupsPanel.vue'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()

// AI 관리 (flowgate.v02.0017 CH0006): one screen that needs no project. The AI list, the
// evaluations and the groups all belong to the system, not to a project.
type TabKey = 'ai' | 'evaluation' | 'groups'
const activeTab = ref<TabKey>('ai')

const loading = ref(false)
const loaded = ref(false)
const error = ref<string | null>(null)

// The AI evaluation and groups can use: every enabled system AI (the server runs no other).
const systemProviders = ref<ProviderItem[]>([])
const groups = ref<AiGroup[]>([])
const sectionErrors = ref<{ groups?: string | null }>({})

let requestEpoch = 0

const tabs = computed<TabItem[]>(() => [
  { key: 'ai', label: t('gui.ai.tabs.ai') },
  { key: 'evaluation', label: t('gui.ai.tabs.evaluation') },
  { key: 'groups', label: t('gui.ai.tabs.groups') },
])

const activeProviders = computed<ProviderItem[]>(() =>
  systemProviders.value.filter((p) => p.enabled !== false)
)

// Links from before AI 관리 (overview / system-ai / my-ai / policy / providers) open the AI list.
function parseTab(tabParam: unknown): TabKey {
  if (tabParam === 'evaluation' || tabParam === 'groups') return tabParam
  return 'ai'
}

function onTabChange(newTab: string) {
  activeTab.value = parseTab(newTab)
  if (route.query.tab !== newTab) {
    void router.replace({ query: { ...route.query, tab: newTab } })
  }
}

// Evaluation and groups list the usable AI, so they are reloaded after an edit in the list.
function onProvidersChanged() {
  if (loaded.value) void loadData()
}

async function loadData() {
  const epoch = ++requestEpoch
  loading.value = true
  error.value = null

  try {
    const [systemRes, grpRes] = await Promise.allSettled([
      fetchVisibleSystemAi(),
      listAiGroups(),
    ])

    if (epoch !== requestEpoch) return

    systemProviders.value = systemRes.status === 'fulfilled' ? systemRes.value.providers ?? [] : []

    if (grpRes.status === 'fulfilled') {
      groups.value = grpRes.value
      sectionErrors.value = {}
    } else {
      groups.value = []
      sectionErrors.value = {
        groups: grpRes.reason instanceof Error && grpRes.reason.message ? grpRes.reason.message : t('gui.ai.groups.load_failed'),
      }
    }

    if (systemRes.status === 'rejected') {
      error.value = systemRes.reason instanceof Error && systemRes.reason.message ? systemRes.reason.message : t('gui.ai.request_failed.settings_load')
    }
    loaded.value = true
  } catch (err: unknown) {
    if (epoch === requestEpoch) {
      error.value = err instanceof Error && err.message ? err.message : t('gui.ai.request_failed.settings_load')
    }
  } finally {
    if (epoch === requestEpoch) {
      loading.value = false
    }
  }
}

watch(
  () => route.query.tab,
  (tabVal) => {
    activeTab.value = parseTab(tabVal)
  },
  { immediate: true }
)

// Evaluation and groups need the usable AI; the AI list loads its own data.
watch(
  activeTab,
  (tab) => {
    if (tab !== 'ai' && !loaded.value && !loading.value) void loadData()
  },
  { immediate: true }
)
</script>

<style scoped>
.ai-settings-view {
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
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 16px;
}

.view-loading,
.view-error {
  display: flex;
  align-items: center;
  justify-content: center;
  flex: 1;
}

.view-content {
  flex: 1;
  min-height: 0;
  overflow-y: auto;
}
</style>
