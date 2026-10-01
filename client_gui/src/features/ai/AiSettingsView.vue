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
      </header>

      <!-- Full-screen loading only before the first data arrives: a refresh after an edit
           keeps the open tab (and its selection/dialog state) mounted. -->
      <div v-if="loading && !overviewData && !policyData" class="view-loading">
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
            @navigate="onNavigateTab"
            @register-evaluation="onRegisterEvaluation"
          />
        </template>

        <!-- Usage policy Tab (the project allows and picks AI; it owns none) -->
        <template v-else-if="activeTab === 'policy'">
          <div v-if="sectionErrors.policy" class="view-error">
            <ErrorState
              :title="t('gui.ai.load_failed_title')"
              :message="sectionErrors.policy"
            >
              <template #action>
                <Button variant="secondary" @click="loadData">
                  {{ t('gui.common.retry') }}
                </Button>
              </template>
            </ErrorState>
          </div>
          <AiPolicyPanel
            v-else-if="policyData"
            :project-id="currentProject.id"
            :initial-policy="policyData"
            @changed="loadData"
          />
        </template>

        <!-- Evaluation Tab -->
        <template v-else-if="activeTab === 'evaluation'">
          <AiEvaluationPanel
            :project-id="currentProject.id"
            :providers="activeProviders"
            :open-new="pendingNewEvaluation"
            @new-opened="pendingNewEvaluation = false"
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
  fetchProjectAiPolicy,
  listAiGroups,
} from './api'
import type {
  AiGroup,
  AiOverviewData,
  ProjectAiPolicy,
  ProviderItem,
} from './types'
import AiOverviewPanel from './overview/AiOverviewPanel.vue'
import AiPolicyPanel from './policy/AiPolicyPanel.vue'
import AiEvaluationPanel from './evaluation/AiEvaluationPanel.vue'
import AiGroupsPanel from './groups/AiGroupsPanel.vue'

const { t } = useI18n()
const route = useRoute()
const router = useRouter()
const projectStore = useProjectStore()

type TabKey = 'overview' | 'policy' | 'evaluation' | 'groups'
const activeTab = ref<TabKey>('overview')
// Overview's "register evaluation" opens the new-evaluation dialog once the tab is shown.
const pendingNewEvaluation = ref(false)

const currentProject = computed(() => projectStore.currentProject)

const loading = ref(false)
const error = ref<string | null>(null)

const overviewData = ref<AiOverviewData | null>(null)
const policyData = ref<ProjectAiPolicy | null>(null)
const groups = ref<AiGroup[]>([])
const sectionErrors = ref<{
  overview?: string | null
  policy?: string | null
  groups?: string | null
}>({})

let requestEpoch = 0

const tabs = computed<TabItem[]>(() => [
  { key: 'overview', label: t('gui.ai.tabs.overview') },
  { key: 'policy', label: t('gui.ai.tabs.policy') },
  { key: 'evaluation', label: t('gui.ai.tabs.evaluation') },
  { key: 'groups', label: t('gui.ai.tabs.groups') },
])

// AI this project can use right now (evaluation target/judge, group members)
const activeProviders = computed<ProviderItem[]>(() =>
  (policyData.value?.effective?.providers ?? []).filter((p) => p.enabled !== false)
)

function parseTab(tabParam: unknown): TabKey {
  if (tabParam === 'policy' || tabParam === 'evaluation' || tabParam === 'groups') {
    return tabParam
  }
  // Links from before the usage policy still open the same tab.
  if (tabParam === 'providers') return 'policy'
  return 'overview'
}

function onTabChange(newTab: string) {
  activeTab.value = newTab as TabKey
  if (route.query.tab !== newTab) {
    void router.replace({ query: { ...route.query, tab: newTab } })
  }
}

function onNavigateTab(targetTab: 'policy' | 'evaluation' | 'groups') {
  onTabChange(targetTab)
}

function onRegisterEvaluation() {
  pendingNewEvaluation.value = true
  onTabChange('evaluation')
}

async function loadData() {
  const pId = currentProject.value?.id
  if (!pId) return
  const epoch = ++requestEpoch
  loading.value = true
  error.value = null

  try {
    const [ovRes, setRes, grpRes] = await Promise.allSettled([
      fetchAiOverview(pId),
      fetchProjectAiPolicy(pId),
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
      policyData.value = setRes.value
    } else {
      policyData.value = null
      errs.policy = setRes.reason instanceof Error ? setRes.reason.message : 'Failed to load the project AI policy'
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
    pendingNewEvaluation.value = false
    overviewData.value = null
    policyData.value = null
    if (newId) {
      void loadData()
    } else {
      overviewData.value = null
      policyData.value = null
      groups.value = []
      sectionErrors.value = {}
    }
  },
  { immediate: true }
)

onMounted(() => {
  if (currentProject.value?.id && !policyData.value) {
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