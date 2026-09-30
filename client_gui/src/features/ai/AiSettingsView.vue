<template>
  <div class="ai-settings-view">
    <!-- No Project Selected -->
    <div v-if="!currentProject" class="no-project-box">
      <EmptyState
        :title="t('gui.ai.no_project_title')"
        :description="t('gui.ai.no_project_desc')"
      >
        <template #action>
          <Button variant="primary" @click="projectStore.openDialog()">
            {{ t('gui.ai.select_project_action') }}
          </Button>
        </template>
      </EmptyState>
    </div>

    <!-- Active Project Content -->
    <template v-else>
      <header class="view-header">
        <div class="header-left">
          <Tabs
            v-model="activeTab"
            :tabs="tabs"
            :aria-label="t('gui.ai.tabs_aria')"
            @change="onTabChange"
          />
        </div>
        <Button variant="primary" @click="startOpen = true">{{ t('gui.ai.start.open') }}</Button>
      </header>

      <div v-if="loading" class="view-loading">
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
        <!-- Overview Tab -->
        <template v-if="activeTab === 'overview'">
          <div v-if="sectionErrors.overview" class="view-error">
            <ErrorState
              :title="t('gui.ai.load_failed_title')"
              :message="sectionErrors.overview"
            >
              <template #action>
                <Button variant="secondary" @click="loadData">
                  {{ t('gui.common.retry') }}
                </Button>
              </template>
            </ErrorState>
          </div>
          <AiOverviewPanel
            v-else
            :overview="overviewData"
            :providers="overviewProviders"
            :evaluations="evaluations"
            :groups="groups"
            :section-errors="sectionErrors"
            @navigate="onNavigateTab"
          />
        </template>

        <!-- Providers Tab -->
        <template v-else-if="activeTab === 'providers'">
          <div v-if="sectionErrors.settings" class="view-error">
            <ErrorState
              :title="t('gui.ai.load_failed_title')"
              :message="sectionErrors.settings"
            >
              <template #action>
                <Button variant="secondary" @click="loadData">
                  {{ t('gui.common.retry') }}
                </Button>
              </template>
            </ErrorState>
          </div>
          <AiProvidersPanel
            v-else-if="settingsData"
            :project-id="currentProject.id"
            :initial-settings="settingsData"
            @changed="loadData"
          />
        </template>

        <!-- Evaluation Tab -->
        <template v-else-if="activeTab === 'evaluation'">
          <AiEvaluationPanel
            :project-id="currentProject.id"
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
            :project-id="currentProject.id"
            :providers="activeProviders"
            :initial-groups="groups"
            @changed="loadData"
          />
        </template>
      </main>
    </template>
    <AiStartDialog v-if="currentProject" :open="startOpen" :project="currentProject" @close="startOpen = false" />
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import Button from '@/components/ui/Button.vue'
import EmptyState from '@/components/ui/EmptyState.vue'
import ErrorState from '@/components/ui/ErrorState.vue'
import LoadingState from '@/components/ui/LoadingState.vue'
import Tabs, { type TabItem } from '@/components/ui/Tabs.vue'
import { useProjectStore } from '@/features/project'
import {
  fetchAiOverview,
  fetchEvaluationHistory,
  fetchProjectAiSettings,
  listAiGroups,
} from './api'
import type {
  AiEvaluation,
  AiGroup,
  AiOverviewData,
  ProjectAiSettings,
  ProviderItem,
} from './types'
import AiOverviewPanel from './overview/AiOverviewPanel.vue'
import AiProvidersPanel from './providers/AiProvidersPanel.vue'
import AiEvaluationPanel from './evaluation/AiEvaluationPanel.vue'
import AiGroupsPanel from './groups/AiGroupsPanel.vue'
import AiStartDialog from './AiStartDialog.vue'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const projectStore = useProjectStore()

type TabKey = 'overview' | 'providers' | 'evaluation' | 'groups'
const activeTab = ref<TabKey>('overview')
const startOpen = ref(false)

const currentProject = computed(() => projectStore.currentProject)

const loading = ref(false)
const error = ref<string | null>(null)

const overviewData = ref<AiOverviewData | null>(null)
const settingsData = ref<ProjectAiSettings | null>(null)
const evaluations = ref<AiEvaluation[]>([])
const groups = ref<AiGroup[]>([])
const sectionErrors = ref<{
  overview?: string | null
  settings?: string | null
  evaluations?: string | null
  groups?: string | null
}>({})

let requestEpoch = 0

const tabs = computed<TabItem[]>(() => [
  { key: 'overview', label: t('gui.ai.tabs.overview') },
  { key: 'providers', label: t('gui.ai.tabs.providers') },
  { key: 'evaluation', label: t('gui.ai.tabs.evaluation') },
  { key: 'groups', label: t('gui.ai.tabs.groups') },
])

// Effective enabled providers for evaluation runnable targets and group selectable roles
const activeProviders = computed<ProviderItem[]>(() => {
  const list =
    settingsData.value?.mode === 'custom'
      ? (settingsData.value?.effective?.providers ?? (settingsData.value?.providers ?? []).filter((p) => p.enabled))
      : (settingsData.value?.effective?.providers ?? [])
  return list.filter((p) => p.enabled !== false)
})

// All configured providers for overview display (preserving inactive status chips)
const overviewProviders = computed<ProviderItem[]>(() => {
  if (settingsData.value?.mode === 'custom') {
    return settingsData.value.providers ?? []
  }
  return settingsData.value?.effective?.providers ?? []
})

function parseTab(tabParam: unknown): TabKey {
  if (tabParam === 'providers' || tabParam === 'evaluation' || tabParam === 'groups') {
    return tabParam
  }
  return 'overview'
}

function onTabChange(newTab: string) {
  activeTab.value = newTab as TabKey
  if (route.query.tab !== newTab) {
    void router.replace({ query: { ...route.query, tab: newTab } })
  }
}

function onNavigateTab(targetTab: 'providers' | 'evaluation' | 'groups') {
  onTabChange(targetTab)
}

async function loadData() {
  const pId = currentProject.value?.id
  if (!pId) return
  const epoch = ++requestEpoch
  loading.value = true
  error.value = null

  try {
    const [ovRes, setRes, evalRes, grpRes] = await Promise.allSettled([
      fetchAiOverview(pId),
      fetchProjectAiSettings(pId),
      fetchEvaluationHistory(pId, undefined, 20),
      listAiGroups(pId),
    ])

    if (epoch !== requestEpoch || currentProject.value?.id !== pId) return

    const errs: Record<string, string | null> = {}

    if (ovRes.status === 'fulfilled') {
      overviewData.value = ovRes.value
    } else {
      overviewData.value = null
      errs.overview = ovRes.reason instanceof Error ? ovRes.reason.message : 'Failed to load AI overview'
    }

    if (setRes.status === 'fulfilled') {
      settingsData.value = setRes.value
    } else {
      settingsData.value = null
      errs.settings = setRes.reason instanceof Error ? setRes.reason.message : 'Failed to load project AI settings'
    }

    if (evalRes.status === 'fulfilled') {
      evaluations.value = evalRes.value.items || []
    } else {
      evaluations.value = []
      errs.evaluations = evalRes.reason instanceof Error ? evalRes.reason.message : 'Failed to load evaluation history'
    }

    if (grpRes.status === 'fulfilled') {
      groups.value = grpRes.value
    } else {
      groups.value = []
      errs.groups = grpRes.reason instanceof Error ? grpRes.reason.message : 'Failed to load AI groups'
    }

    sectionErrors.value = errs

    // Overall fatal error if all sections failed
    if (
      ovRes.status === 'rejected' &&
      setRes.status === 'rejected' &&
      evalRes.status === 'rejected' &&
      grpRes.status === 'rejected'
    ) {
      error.value = 'Failed to load project AI configuration.'
    }
  } catch (err: unknown) {
    if (epoch === requestEpoch) {
      error.value = err instanceof Error ? err.message : 'Failed to load AI settings'
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
    if (tabVal) {
      activeTab.value = parseTab(tabVal)
    }
  },
  { immediate: true }
)

watch(
  () => currentProject.value?.id,
  (newId) => {
    startOpen.value = false
    if (newId) {
      void loadData()
    } else {
      overviewData.value = null
      settingsData.value = null
      evaluations.value = []
      groups.value = []
      sectionErrors.value = {}
    }
  },
  { immediate: true }
)

onMounted(() => {
  if (currentProject.value?.id && !settingsData.value) {
    void loadData()
  }
})
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

.no-project-box {
  display: flex;
  align-items: center;
  justify-content: center;
  flex: 1;
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