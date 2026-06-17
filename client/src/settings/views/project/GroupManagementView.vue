<template>
  <div>
    <div class="flex justify-between items-center" style="margin-bottom:20px; gap:16px; flex-wrap:wrap;">
      <div>
        <h1 class="s-page-title">
          <i class="fa-solid fa-layer-group" style="color:var(--primary); margin-right:8px;"></i>
          {{ $t('settings.project.group_management') }}
        </h1>
        <p class="s-page-sub" style="margin-bottom:0;">{{ $t('settings.project.group_management_view.subtitle') }}</p>
      </div>
      <div class="group-management-toolbar">
        <label class="text-sm text-s">{{ $t('settings.project.project_settings_view.label_9') }}</label>
        <select
          class="form-ctrl"
          style="width:180px;"
          :value="settings.currentProjectId"
          @change="settings.setCurrentProject($event.target.value)"
        >
          <option v-for="p in settings.projects" :key="p.project_id" :value="p.project_id">{{ p.project_name }}</option>
        </select>
        <button
          v-if="auth.can('project.group.manage')"
          class="btn btn-primary"
          :disabled="!settings.currentProjectId"
          @click="openCreate"
        >
          <i class="fa-solid fa-plus"></i> {{ $t('settings.project.group_management_view.btn_create') }}
        </button>
      </div>
    </div>

    <div v-if="!settings.currentProjectId" class="alert alert-info">
      <i class="fa-solid fa-circle-info"></i>
      {{ $t('settings.project.project_settings_view.text_35') }}
    </div>

    <div v-else class="card">
      <div class="card-hd">
        <span class="card-title">{{ $t('settings.project.group_management_view.card_title', { name: currentProjectName }) }}</span>
        <span class="badge badge-gray">{{ groups.length }}</span>
      </div>
      <div class="card-bd group-table-wrap">
        <table class="tbl">
          <thead>
            <tr>
              <th style="width:140px;">{{ $t('settings.project.group_management_view.col_id') }}</th>
              <th>{{ $t('settings.project.group_management_view.col_title') }}</th>
              <th style="width:120px;">{{ $t('settings.project.group_management_view.col_module') }}</th>
              <th style="width:120px;">{{ $t('settings.project.group_management_view.col_priority') }}</th>
              <th style="width:120px;">{{ $t('settings.project.group_management_view.col_created_at') }}</th>
              <th style="width:96px;">{{ $t('settings.project.group_management_view.col_action') }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-if="loading">
              <td colspan="6" class="group-empty">
                <i class="fa-solid fa-spinner fa-spin group-empty-icon"></i>
                {{ $t('common.loading') }}
              </td>
            </tr>
            <tr v-else-if="groups.length === 0">
              <td colspan="6" class="group-empty">
                <i class="fa-solid fa-layer-group group-empty-icon"></i>
                {{ $t('settings.project.group_management_view.empty') }}
              </td>
            </tr>
            <template v-else>
              <tr v-for="group in groups" :key="group.group_id">
                <td>
                  <span class="group-id mono">{{ group.group_id }}</span>
                </td>
                <td>
                  <div class="group-title-cell">
                    <span class="group-title-icon">
                      <i class="fa-solid fa-folder-tree"></i>
                    </span>
                    <div>
                      <div class="group-title-main">{{ group.title }}</div>
                      <div class="group-title-meta mono">{{ group.group_id }}</div>
                    </div>
                  </div>
                </td>
                <td>
                  <span class="badge" :class="group.module ? 'badge-blue' : 'badge-gray'">
                    {{ group.module_title || group.module || 'none' }}
                  </span>
                </td>
                <td>
                  <span v-if="group.priority" class="badge badge-yellow">{{ group.priority }}</span>
                  <span v-else class="text-xs text-m">—</span>
                </td>
                <td>
                  <span class="text-xs text-s">{{ formatDate(group.created_at) }}</span>
                </td>
                <td>
                  <div v-if="auth.can('project.group.manage')" class="tbl-actions group-actions">
                    <button class="btn btn-sm btn-secondary" :title="$t('common.edit')" @click="openEdit(group)">
                      <i class="fa-solid fa-pen"></i>
                    </button>
                    <button
                      class="btn btn-sm btn-ghost"
                      style="color:var(--danger);"
                      :title="$t('common.delete')"
                      @click="confirmDelete(group)"
                    >
                      <i class="fa-solid fa-trash"></i>
                    </button>
                  </div>
                  <span v-else class="text-xs text-m">—</span>
                </td>
              </tr>
            </template>
          </tbody>
        </table>
      </div>
    </div>

    <CreateEditGroupModal
      v-model:visible="showModal"
      :mode="modalMode"
      :project-id="settings.currentProjectId || ''"
      :group="selectedGroup"
      @saved="onSaved"
    />

    <!-- Delete confirmation (shared ConfirmModal, no native confirm()) -->
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

<script setup>
import { computed, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import { getRequest, putRequest } from '@shared/api'
import { useSettingsStore } from '../../stores/settings.js'
import { useAuthStore } from '../../stores/auth.js'
import { useExplorerStore } from '@main/stores/explorer'
import CreateEditGroupModal from '@main/components/CreateEditGroupModal.vue'
import ConfirmModal from '@main/components/ConfirmModal.vue'
import { useToast } from '@main/components/common/useToast'

const settings = useSettingsStore()
const auth = useAuthStore()
const explorerStore = useExplorerStore()
const { t } = useI18n()
const { showToast } = useToast()

// Custom confirm dialog state (replaces native confirm())
const confirmVisible = ref(false)
const confirmTitle = ref('')
const confirmMessage = ref('')
const confirmLabel = ref('')
const confirmDanger = ref(false)
let confirmAction = null
function askConfirm(opts) {
  confirmTitle.value = opts.title
  confirmMessage.value = opts.message
  confirmLabel.value = opts.confirmLabel || ''
  confirmDanger.value = !!opts.danger
  confirmAction = opts.action
  confirmVisible.value = true
}
function onConfirmAccept() {
  const action = confirmAction
  confirmAction = null
  if (action) action()
}

const groups = ref([])
const loading = ref(false)
const showModal = ref(false)
const modalMode = ref('create')
const selectedGroup = ref(null)

const currentProjectName = computed(() => {
  const project = settings.projects.find((p) => p.project_id === settings.currentProjectId)
  return project?.project_name || settings.currentProjectId || ''
})

async function fetchGroups() {
  if (!settings.currentProjectId) {
    groups.value = []
    return
  }
  loading.value = true
  try {
    const res = await getRequest('/api/v1/groups', {
      project_id: settings.currentProjectId,
    })
    groups.value = (res.data?.groups ?? [])
  } finally {
    loading.value = false
  }
}

function openCreate() {
  if (!settings.currentProjectId) return
  modalMode.value = 'create'
  selectedGroup.value = null
  showModal.value = true
}

function openEdit(group) {
  modalMode.value = 'edit'
  selectedGroup.value = {
    group_id: group.group_id,
    title: group.title,
    module: group.module,
    priority: group.priority,
  }
  showModal.value = true
}

function formatDate(value) {
  return value ? value.slice(0, 10) : '—'
}

function confirmDelete(group) {
  askConfirm({
    title: t('settings.project.group_management_view.delete_confirm_title'),
    message: t('settings.project.group_management_view.delete_confirm', { name: group.title }),
    confirmLabel: t('common.delete'),
    danger: true,
    action: async () => {
      try {
        await putRequest(`/api/v1/groups/${encodeURIComponent(group.group_id)}/archive`, {})
        if (settings.currentProjectId) explorerStore.invalidateProject(settings.currentProjectId)
        await fetchGroups()
      } catch (e) {
        showToast(e?.response?.data?.detail ?? t('settings.project.group_management_view.delete_failed'), 'danger')
      }
    },
  })
}

async function onSaved() {
  if (settings.currentProjectId) explorerStore.invalidateProject(settings.currentProjectId)
  await fetchGroups()
}

watch(() => settings.currentProjectId, fetchGroups, { immediate: true })
</script>

<style scoped>
.group-management-toolbar {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.group-table-wrap {
  overflow-x: auto;
}

.group-id {
  display: inline-flex;
  max-width: 120px;
  overflow: hidden;
  text-overflow: ellipsis;
  color: var(--text-s);
  font-size: .72rem;
}

.group-title-cell {
  display: flex;
  align-items: center;
  gap: 10px;
  min-width: 220px;
}

.group-title-icon {
  width: 30px;
  height: 30px;
  border-radius: var(--r);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: var(--primary-l);
  color: var(--primary);
  flex-shrink: 0;
}

.group-title-main {
  font-size: .875rem;
  font-weight: 600;
  color: var(--text);
}

.group-title-meta {
  margin-top: 2px;
  font-size: .68rem;
  color: var(--text-m);
}

.group-actions {
  justify-content: flex-end;
}

.group-empty {
  text-align: center;
  padding: 40px 16px !important;
  color: var(--text-m);
}

.group-empty-icon {
  display: block;
  margin-bottom: 10px;
  font-size: 1.45rem;
  opacity: .45;
}
</style>
