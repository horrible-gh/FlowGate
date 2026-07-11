<template>
  <div>
    <div class="flex justify-between items-center" style="margin-bottom:20px; gap:16px; flex-wrap:wrap;">
      <div>
        <h1 class="s-page-title">
          <AppIcon name="code" style="color:var(--primary); margin-right:8px;" />
          {{ $t('settings.system.env_variables.title') }}
        </h1>
        <p class="s-page-sub" style="margin-bottom:0;">{{ $t('settings.system.env_variables.sub', { pattern: '${var}' }) }}</p>
      </div>
      <button class="btn btn-primary" @click="openCreate">
        <AppIcon name="plus" /> {{ $t('settings.system.env_variables.btn_create') }}
      </button>
    </div>

    <div class="card">
      <div class="card-bd">
        <table class="tbl">
          <thead>
            <tr>
              <th>{{ $t('settings.system.env_variables.col_name') }}</th>
              <th>{{ $t('settings.system.env_variables.col_value') }}</th>
              <th style="width:120px;">{{ $t('settings.system.env_variables.col_created_at') }}</th>
              <th style="width:96px;">{{ $t('settings.system.env_variables.col_action') }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-if="loading">
              <td colspan="4" class="ev-empty">
                <AppIcon name="spinner" spin class="ev-empty-icon" />
                {{ $t('common.loading') }}
              </td>
            </tr>
            <tr v-else-if="envVars.length === 0">
              <td colspan="4" class="ev-empty">
                <AppIcon name="code" class="ev-empty-icon" />
                {{ $t('settings.system.env_variables.empty') }}
              </td>
            </tr>
            <template v-else>
              <tr v-for="ev in envVars" :key="ev.var_id">
                <td><span class="mono ev-name">{{ ev.name }}</span></td>
                <td><span class="ev-value">{{ ev.value ?? '—' }}</span></td>
                <td><span class="text-xs text-s">{{ formatDate(ev.created_at) }}</span></td>
                <td>
                  <div class="tbl-actions">
                    <button class="btn btn-sm btn-secondary" :title="$t('common.edit')" @click="openEdit(ev)">
                      <AppIcon name="pencil-simple" />
                    </button>
                    <button
                      class="btn btn-sm btn-ghost"
                      style="color:var(--danger);"
                      :title="$t('common.delete')"
                      @click="confirmDelete(ev)"
                    >
                      <AppIcon name="trash" />
                    </button>
                  </div>
                </td>
              </tr>
            </template>
          </tbody>
        </table>
      </div>
    </div>

    <!-- Create/edit modal -->
    <div v-if="showModal" class="modal-bg" role="dialog" aria-modal="true">
      <div class="modal-box">
        <div class="modal-hd">
          <span class="modal-title">
            {{ modalMode === 'create'
              ? $t('settings.system.env_variables.modal_create')
              : $t('settings.system.env_variables.modal_edit') }}
          </span>
        </div>
        <div class="modal-bd">
          <div class="form-group">
            <label class="form-label req">{{ $t('settings.system.env_variables.label_name') }}</label>
            <input
              ref="nameInput"
              type="text"
              class="form-ctrl"
              :class="{ 'is-invalid': nameError }"
              v-model="form.name"
              :placeholder="$t('settings.system.env_variables.placeholder_name')"
              :disabled="modalMode === 'edit'"
            >
            <p v-if="nameError" class="form-hint" style="color:var(--danger);">{{ nameError }}</p>
            <p v-else class="form-hint">{{ $t('settings.system.env_variables.hint_name') }}</p>
          </div>
          <div class="form-group">
            <label class="form-label">{{ $t('settings.system.env_variables.label_value') }}</label>
            <input
              type="text"
              class="form-ctrl"
              v-model="form.value"
              :placeholder="$t('settings.system.env_variables.placeholder_value')"
            >
          </div>
          <p v-if="apiError" class="form-hint" style="color:var(--danger);">{{ apiError }}</p>
        </div>
        <div class="modal-ft">
          <button class="btn btn-secondary" @click="closeModal">{{ $t('common.cancel') }}</button>
          <button class="btn btn-primary" :disabled="saving" @click="submitModal">
            <AppIcon v-if="saving" name="spinner" spin />
            {{ $t('common.save') }}
          </button>
        </div>
      </div>
    </div>

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
import AppIcon from '@shared/AppIcon.vue'
import { ref, nextTick, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { getRequest, postRequest, putRequest, deleteRequest } from '@shared/api'
import { useToast } from '../../../main/components/common/useToast'
import ConfirmModal from '@main/components/ConfirmModal.vue'

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

const envVars = ref([])
const loading = ref(false)
const showModal = ref(false)
const modalMode = ref('create')
const saving = ref(false)
const nameError = ref('')
const apiError = ref('')
const nameInput = ref(null)
const selectedId = ref(null)

const form = ref({ name: '', value: '' })

const NAME_RE = /^[A-Za-z_][A-Za-z0-9_]*$/

async function fetchEnvVars() {
  loading.value = true
  try {
    const res = await getRequest('/api/v1/env-vars')
    envVars.value = res.data?.env_vars ?? []
  } finally {
    loading.value = false
  }
}

function openCreate() {
  modalMode.value = 'create'
  selectedId.value = null
  form.value = { name: '', value: '' }
  nameError.value = ''
  apiError.value = ''
  showModal.value = true
  nextTick(() => nameInput.value?.focus())
}

function openEdit(ev) {
  modalMode.value = 'edit'
  selectedId.value = ev.var_id
  form.value = { name: ev.name, value: ev.value ?? '' }
  nameError.value = ''
  apiError.value = ''
  showModal.value = true
}

function closeModal() {
  showModal.value = false
}

function validateForm() {
  nameError.value = ''
  if (!form.value.name.trim()) {
    nameError.value = 'Please enter a variable name.'
    return false
  }
  if (!NAME_RE.test(form.value.name.trim())) {
    nameError.value = 'Variable name may only contain letters, numbers, and underscores.'
    return false
  }
  return true
}

async function submitModal() {
  if (!validateForm()) return
  saving.value = true
  apiError.value = ''
  try {
    if (modalMode.value === 'create') {
      await postRequest('/api/v1/env-vars', {
        kind: 'user',
        name: form.value.name.trim(),
        value: form.value.value || null,
      })
      showToast(t('settings.system.env_variables.toast_added'), 'success')
    } else {
      await putRequest(`/api/v1/env-vars/${encodeURIComponent(selectedId.value)}`, {
        value: form.value.value || null,
      })
      showToast(t('settings.system.env_variables.toast_updated'), 'success')
    }
    closeModal()
    await fetchEnvVars()
  } catch (e) {
    const detail = e?.response?.data?.detail
    if (e?.response?.status === 409) {
      apiError.value = detail ?? 'A variable with that name already exists.'
    } else {
      apiError.value = detail ?? 'An error occurred while saving.'
    }
  } finally {
    saving.value = false
  }
}

function confirmDelete(ev) {
  askConfirm({
    title: t('settings.system.env_variables.delete_confirm_title'),
    message: t('settings.system.env_variables.delete_confirm', { name: ev.name }),
    confirmLabel: t('common.delete'),
    danger: true,
    action: async () => {
      try {
        await deleteRequest(`/api/v1/env-vars/${encodeURIComponent(ev.var_id)}`)
        showToast(t('settings.system.env_variables.toast_deleted'), 'success')
        await fetchEnvVars()
      } catch (e) {
        showToast(e?.response?.data?.detail ?? t('common.toast.delete_failed'), 'danger')
      }
    },
  })
}

function formatDate(value) {
  return value ? value.slice(0, 10) : '—'
}

onMounted(fetchEnvVars)
</script>

<style scoped>
.ev-empty {
  text-align: center;
  padding: 40px 16px !important;
  color: var(--text-m);
}
.ev-empty-icon {
  display: block;
  margin-bottom: 10px;
  font-size: 1.45rem;
  opacity: .45;
}
.ev-name {
  font-size: .82rem;
  color: var(--primary);
}
.ev-value {
  font-size: .85rem;
  color: var(--text);
  word-break: break-all;
}
</style>
