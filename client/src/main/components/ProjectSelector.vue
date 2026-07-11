<template>
  <div class="proj-sw-wrap">
    <button
      class="proj-sw"
      type="button"
      :aria-label="t('main.nav.project_select')"
      @click.stop="open = !open"
    >
      <span class="proj-sw-dot" :style="{ background: currentProjectColor }"></span>
      <span>{{ currentProjectName }}</span>
      <AppIcon name="caret-down" class="proj-sw-caret" :class="{ open }" />
    </button>
    <div v-if="open" class="proj-dd">
      <div class="proj-dd-hd">{{ t('main.nav.project_menu') }}</div>
      <div v-if="projectStore.loading" class="proj-dd-item">
        {{ t('common.loading') }}
      </div>
      <template v-else-if="projectStore.projects.length > 0">
        <button
          v-for="p in projectStore.projects"
          :key="p.project_id"
          type="button"
          class="proj-dd-item"
          :class="{ active: p.project_id === projectStore.currentProjectId }"
          @click="selectProject(p)"
        >
          <span class="proj-dd-dot" :style="{ background: projectColor(p) }"></span>
          <span>{{ p.project_name }}</span>
          <AppIcon name="check" v-if="p.project_id === projectStore.currentProjectId" style="margin-left:auto; font-size:.65rem; opacity:.5;" />
        </button>
      </template>
      <div v-else-if="projectStore.error" class="proj-dd-item">
        {{ projectStore.error }}
      </div>
      <div v-else class="proj-dd-item">
        {{ t('main.nav.project_select') }}
      </div>
      <template v-if="isAdmin">
        <div class="proj-dd-div"></div>
        <a href="/settings/projects" class="proj-dd-item" @click="open = false">
          <AppIcon name="grid-four" style="width:14px;text-align:center;color:rgba(255,255,255,.4);" />
          <span>{{ t('main.nav.project_list') }}</span>
        </a>
        <a href="/settings/projects?new=1" class="proj-dd-item" @click="open = false">
          <AppIcon name="plus" style="width:14px;text-align:center;color:rgba(255,255,255,.4);" />
          <span>{{ t('main.nav.new_project') }}</span>
        </a>
      </template>
    </div>

    <!-- Close-tabs confirmation (shared ConfirmModal, no native confirm()) -->
    <ConfirmModal
      v-model:visible="confirmVisible"
      :title="confirmTitle"
      :message="confirmMessage"
      :confirm-label="confirmLabel || undefined"
      :danger="confirmDanger"
      @confirm="onConfirmAccept"
    />
  </div>
</template>

<script setup lang="ts">
import AppIcon from '@shared/AppIcon.vue'
import { onBeforeUnmount, onMounted, ref, computed } from 'vue'
import { useI18n } from 'vue-i18n'
import { useTabsStore } from '../stores/tabs'
import { useExplorerStore } from '../stores/explorer'
import { useProjectStore, type Project } from '../stores/project'
import { isAdminFromToken } from '@shared/auth'
import ConfirmModal from './ConfirmModal.vue'

const { t } = useI18n()
const tabsStore = useTabsStore()
const explorerStore = useExplorerStore()
const projectStore = useProjectStore()
const projectColors = ['#2563eb', '#7c3aed', '#0891b2', '#16a34a', '#d97706']

const open = ref(false)
const isAdmin = computed(() => isAdminFromToken())

// Custom confirm dialog state (replaces native window.confirm())
const confirmVisible = ref(false)
const confirmTitle = ref('')
const confirmMessage = ref('')
const confirmLabel = ref('')
const confirmDanger = ref(false)
let confirmAction: (() => void) | null = null
function askConfirm(opts: {
  title: string
  message: string
  confirmLabel?: string
  danger?: boolean
  action: () => void
}) {
  confirmTitle.value = opts.title
  confirmMessage.value = opts.message
  confirmLabel.value = opts.confirmLabel ?? ''
  confirmDanger.value = !!opts.danger
  confirmAction = opts.action
  confirmVisible.value = true
}
function onConfirmAccept() {
  const action = confirmAction
  confirmAction = null
  if (action) action()
}

const currentProjectName = computed(() => {
  if (projectStore.currentProjectId && projectStore.currentProject) {
    return projectStore.currentProject.project_name
  }
  return t('main.nav.project_select')
})

const currentProjectColor = computed(() => {
  return projectStore.currentProject ? projectColor(projectStore.currentProject) : '#2563eb'
})

const emit = defineEmits<{ projectChanged: [pid: string] }>()

function projectColor(project: Project) {
  const index = projectStore.projects.findIndex((p) => p.project_id === project.project_id)
  return project.color || projectColors[Math.max(index, 0) % projectColors.length]
}

async function loadProjects() {
  try {
    await projectStore.fetchProjects()
    
    if (projectStore.currentProjectId) {
      const projectExists = projectStore.projects.some(
        (p) => p.project_id === projectStore.currentProjectId,
      )
      if (!projectExists) {
        projectStore.currentProjectId = null
        localStorage.removeItem('fg_current_project_id')
      }
    }
    
    if (projectStore.projects.length > 0 && !projectStore.currentProjectId) {
      selectProject(projectStore.projects[0])
    }
  } catch (e) {
    console.error('Failed to load projects:', e)
  }
}

function selectProject(p: Project) {
  if (p.project_id === projectStore.currentProjectId) {
    open.value = false
    return
  }
  if (tabsStore.tabs.length > 0) {
    askConfirm({
      title: t('main.confirm.close_tabs_title'),
      message: t('main.confirm.close_tabs', { count: tabsStore.tabs.length }),
      confirmLabel: t('common.confirm'),
      action: () => {
        tabsStore.closeAll()
        applyProjectSelection(p)
      },
    })
    open.value = false
    return
  }
  applyProjectSelection(p)
}

function applyProjectSelection(p: Project) {
  explorerStore.invalidateProject(p.project_id)
  projectStore.setCurrentProject(p.project_id)
  open.value = false
  emit('projectChanged', p.project_id)
}

function closeProjectMenu() {
  open.value = false
}

onMounted(() => {
  loadProjects()
  document.addEventListener('click', closeProjectMenu)
})
onBeforeUnmount(() => document.removeEventListener('click', closeProjectMenu))
</script>
