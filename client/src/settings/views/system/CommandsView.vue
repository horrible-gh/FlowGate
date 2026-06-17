<template>
  <div>
    <div class="flex justify-between items-center" style="margin-bottom:20px; gap:16px; flex-wrap:wrap;">
      <div>
        <h1 class="s-page-title">
          <i class="fa-solid fa-terminal" style="color:var(--primary); margin-right:8px;"></i>
          {{ $t('settings.system.commands.title') }}
        </h1>
        <p class="s-page-sub" style="margin-bottom:0;">{{ $t('settings.system.commands.sub', { pattern: '${var}' }) }}</p>
      </div>
      <button class="btn btn-primary" @click="openCreate">
        <i class="fa-solid fa-plus"></i> {{ $t('settings.system.commands.btn_create') }}
      </button>
    </div>

    <div class="card">
      <div class="card-bd">
        <table class="tbl">
          <thead>
            <tr>
              <th style="width:160px;">{{ $t('settings.system.commands.col_name') }}</th>
              <th>{{ $t('settings.system.commands.col_template') }}</th>
              <th style="width:120px;">{{ $t('settings.system.commands.col_created_at') }}</th>
              <th style="width:96px;">{{ $t('settings.system.commands.col_action') }}</th>
            </tr>
          </thead>
          <tbody>
            <tr v-if="loading">
              <td colspan="4" class="cmd-empty">
                <i class="fa-solid fa-spinner fa-spin cmd-empty-icon"></i>
                {{ $t('common.loading') }}
              </td>
            </tr>
            <tr v-else-if="commands.length === 0">
              <td colspan="4" class="cmd-empty">
                <i class="fa-solid fa-terminal cmd-empty-icon"></i>
                {{ $t('settings.system.commands.empty') }}
              </td>
            </tr>
            <template v-else>
              <tr v-for="cmd in commands" :key="cmd.command_id">
                <td><span class="mono cmd-name">{{ cmd.name }}</span></td>
                <td><span class="cmd-template">{{ cmd.template }}</span></td>
                <td><span class="text-xs text-s">{{ formatDate(cmd.created_at) }}</span></td>
                <td>
                  <div class="tbl-actions">
                    <button class="btn btn-sm btn-secondary" :title="$t('common.edit')" @click="openEdit(cmd)">
                      <i class="fa-solid fa-pen"></i>
                    </button>
                    <button
                      class="btn btn-sm btn-ghost"
                      style="color:var(--danger);"
                      :title="$t('common.delete')"
                      @click="confirmDelete(cmd)"
                    >
                      <i class="fa-solid fa-trash"></i>
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
      <div class="modal-box" style="max-width:560px;">
        <div class="modal-hd">
          <span class="modal-title">
            {{ modalMode === 'create'
              ? $t('settings.system.commands.modal_create')
              : $t('settings.system.commands.modal_edit') }}
          </span>
        </div>
        <div class="modal-bd">
          <div class="form-group">
            <label class="form-label req">{{ $t('settings.system.commands.label_name') }}</label>
            <input
              ref="nameInput"
              type="text"
              class="form-ctrl"
              :class="{ 'is-invalid': nameError }"
              v-model="form.name"
              :placeholder="$t('settings.system.commands.placeholder_name')"
              :disabled="modalMode === 'edit'"
            >
            <p v-if="nameError" class="form-hint" style="color:var(--danger);">{{ nameError }}</p>
            <p v-else class="form-hint">{{ $t('settings.system.commands.hint_name') }}</p>
          </div>
          <div class="form-group">
            <label class="form-label req">{{ $t('settings.system.commands.label_template') }}</label>
            <textarea
              class="form-ctrl cmd-textarea"
              :class="{ 'is-invalid': templateError }"
              v-model="form.template"
              :placeholder="$t('settings.system.commands.placeholder_template')"
              rows="3"
              @input="updatePreview"
            ></textarea>
            <p v-if="templateError" class="form-hint" style="color:var(--danger);">{{ templateError }}</p>
          </div>

          <!-- Preview -->
          <div class="cmd-preview-box">
            <div class="cmd-preview-label">
              <i class="fa-solid fa-eye" style="margin-right:4px;"></i>
              {{ $t('settings.system.commands.preview_title') }}
            </div>
            <p class="form-hint" style="margin-bottom:6px;">{{ $t('settings.system.commands.preview_hint', { pattern: '${var}' }) }}</p>
            <div class="cmd-preview-result" v-html="previewHtml"></div>
            <div v-if="previewUnresolved.length > 0" class="cmd-preview-unresolved">
              <span class="text-xs" style="color:var(--text-m);">
                {{ $t('settings.system.commands.unresolved_label') }}
              </span>
              <span
                v-for="v in previewUnresolved"
                :key="v"
                class="badge badge-red"
                style="margin-left:4px; font-size:.7rem;"
              >{{ v }}</span>
            </div>
          </div>

          <p v-if="apiError" class="form-hint" style="color:var(--danger); margin-top:8px;">{{ apiError }}</p>
        </div>
        <div class="modal-ft">
          <button class="btn btn-secondary" @click="closeModal">{{ $t('common.cancel') }}</button>
          <button class="btn btn-primary" :disabled="saving" @click="submitModal">
            <i v-if="saving" class="fa-solid fa-spinner fa-spin"></i>
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

const commands = ref([])
const loading = ref(false)
const showModal = ref(false)
const modalMode = ref('create')
const saving = ref(false)
const nameError = ref('')
const templateError = ref('')
const apiError = ref('')
const nameInput = ref(null)
const selectedId = ref(null)

const form = ref({ name: '', template: '' })

// Preview state
const previewHtml = ref('')
const previewUnresolved = ref([])

// Current environment variable map (for preview)
const envVarMap = ref({})

const NAME_RE = /^[A-Za-z_][A-Za-z0-9_]*$/
const VAR_PATTERN = /\$\{([^}]+)\}/g

async function fetchCommands() {
  loading.value = true
  try {
    const res = await getRequest('/api/v1/commands')
    commands.value = res.data?.commands ?? []
  } finally {
    loading.value = false
  }
}

async function fetchEnvVarMap() {
  try {
    const res = await getRequest('/api/v1/env-vars')
    const vars = res.data?.env_vars ?? []
    const map = {}
    for (const ev of vars) {
      if (ev.value !== null && ev.value !== undefined) {
        map[ev.name] = ev.value
      }
    }
    envVarMap.value = map
  } catch {
    // Fetch failure only affects preview
  }
}

function localResolve(template) {
  const unresolved = []
  const resolved = template.replace(VAR_PATTERN, (match, name) => {
    if (name.startsWith('__')) {
      if (name === '__now__') {
        return new Date().toISOString().replace(/\.\d{3}Z$/, 'Z')
      }
      unresolved.push(name)
      return match
    }
    if (name in envVarMap.value) {
      return envVarMap.value[name]
    }
    unresolved.push(name)
    return match
  })
  return { resolved, unresolved: [...new Set(unresolved)] }
}

function buildPreviewHtml(resolved, unresolved) {
  if (!resolved) return '<span class="text-s text-xs">—</span>'
  // Highlight unresolved variables in red
  let html = resolved.replace(/</g, '&lt;').replace(/>/g, '&gt;')
  for (const v of unresolved) {
    const escaped = v.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
    html = html.replace(
      new RegExp(`\\$\\{${escaped}\\}`, 'g'),
      `<span class="cmd-unresolved-var">\${${v}}</span>`
    )
  }
  return `<code class="cmd-preview-code">${html}</code>`
}

function updatePreview() {
  const tmpl = form.value.template
  if (!tmpl) {
    previewHtml.value = ''
    previewUnresolved.value = []
    return
  }
  const { resolved, unresolved } = localResolve(tmpl)
  previewHtml.value = buildPreviewHtml(resolved, unresolved)
  previewUnresolved.value = unresolved
}

function openCreate() {
  modalMode.value = 'create'
  selectedId.value = null
  form.value = { name: '', template: '' }
  nameError.value = ''
  templateError.value = ''
  apiError.value = ''
  previewHtml.value = ''
  previewUnresolved.value = []
  showModal.value = true
  nextTick(() => nameInput.value?.focus())
}

function openEdit(cmd) {
  modalMode.value = 'edit'
  selectedId.value = cmd.command_id
  form.value = { name: cmd.name, template: cmd.template }
  nameError.value = ''
  templateError.value = ''
  apiError.value = ''
  showModal.value = true
  nextTick(() => updatePreview())
}

function closeModal() {
  showModal.value = false
}

function validateForm() {
  nameError.value = ''
  templateError.value = ''
  let ok = true
  if (!form.value.name.trim()) {
  nameError.value = 'Please enter a command name.'
    ok = false
  } else if (!NAME_RE.test(form.value.name.trim())) {
  nameError.value = 'Command name may only contain letters, numbers, and underscores.'
    ok = false
  }
  if (!form.value.template.trim()) {
    templateError.value = 'Please enter a template.'
    ok = false
  }
  return ok
}

async function submitModal() {
  if (!validateForm()) return
  saving.value = true
  apiError.value = ''
  try {
    if (modalMode.value === 'create') {
      await postRequest('/api/v1/commands', {
        kind: 'user',
        name: form.value.name.trim(),
        template: form.value.template.trim(),
      })
      showToast(t('settings.system.commands.toast_added'), 'success')
    } else {
      await putRequest(`/api/v1/commands/${encodeURIComponent(selectedId.value)}`, {
        template: form.value.template.trim(),
      })
      showToast(t('settings.system.commands.toast_updated'), 'success')
    }
    closeModal()
    await fetchCommands()
  } catch (e) {
    const detail = e?.response?.data?.detail
    if (e?.response?.status === 409) {
      apiError.value = detail ?? 'A command with that name already exists.'
    } else {
      apiError.value = detail ?? 'An error occurred while saving.'
    }
  } finally {
    saving.value = false
  }
}

function confirmDelete(cmd) {
  askConfirm({
    title: t('settings.system.commands.delete_confirm_title'),
    message: t('settings.system.commands.delete_confirm', { name: cmd.name }),
    confirmLabel: t('common.delete'),
    danger: true,
    action: async () => {
      try {
        await deleteRequest(`/api/v1/commands/${encodeURIComponent(cmd.command_id)}`)
        showToast(t('settings.system.commands.toast_deleted'), 'success')
        await fetchCommands()
      } catch (e) {
        showToast(e?.response?.data?.detail ?? t('common.toast.delete_failed'), 'danger')
      }
    },
  })
}

function formatDate(value) {
  return value ? value.slice(0, 10) : '—'
}

onMounted(async () => {
  await Promise.all([fetchCommands(), fetchEnvVarMap()])
})
</script>

<style scoped>
.cmd-empty {
  text-align: center;
  padding: 40px 16px !important;
  color: var(--text-m);
}
.cmd-empty-icon {
  display: block;
  margin-bottom: 10px;
  font-size: 1.45rem;
  opacity: .45;
}
.cmd-name {
  font-size: .82rem;
  color: var(--primary);
}
.cmd-template {
  font-size: .82rem;
  font-family: var(--font-mono, monospace);
  word-break: break-all;
  color: var(--text);
}
.cmd-textarea {
  font-family: var(--font-mono, monospace);
  font-size: .85rem;
  resize: vertical;
}
.cmd-preview-box {
  background: var(--bg-s, #f8f9fa);
  border: 1px solid var(--border, #e2e8f0);
  border-radius: var(--r, 6px);
  padding: 12px 14px;
  margin-top: 8px;
}
.cmd-preview-label {
  font-size: .8rem;
  font-weight: 600;
  color: var(--text-m);
  margin-bottom: 4px;
}
.cmd-preview-result {
  min-height: 28px;
}
.cmd-preview-code {
  display: block;
  font-family: var(--font-mono, monospace);
  font-size: .84rem;
  color: var(--text);
  white-space: pre-wrap;
  word-break: break-all;
}
.cmd-preview-unresolved {
  margin-top: 8px;
  display: flex;
  align-items: center;
  flex-wrap: wrap;
  gap: 4px;
}
:deep(.cmd-unresolved-var) {
  color: var(--danger, #dc2626);
  font-weight: 600;
}
</style>
